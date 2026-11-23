# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 lifan-builds. Faster Mac Use; Section 7 attribution terms in ATTRIBUTION.md.
"""Trace recording and self-contained HTML / animated SVG replay for fmu runs.

Set ``FMU_TRACE=/path/run.jsonl`` to append one record per fmu call (request, output, wall ms);
traced calls request ``bounds: true`` so element lines carry ``box=x,y,w,h``. Render with
``fmu replay /path/run.jsonl [-o run.html|run.svg]``. Perf logs (``~/.fmu/logs/perf-*.jsonl``)
also replay as latency-only frames.
"""

from __future__ import annotations

from datetime import datetime, timezone
import html
import json
import math
from pathlib import Path
import re
from typing import Any

STAGE_W = 960
STAGE_H = 540
HUD_H = 56
CHART_H = 64
FRAME_W = STAGE_W
FRAME_H = HUD_H + STAGE_H + CHART_H
SVG_FRAME_S = 1.4

_HEADER_RE = re.compile(r"^# (web|app)[ =]")
_KV_RE = re.compile(r'(\w+)=("([^"]*)"|\S+)')
_ELEM_RE = re.compile(r"^(?:([+~-]) )?(@[a-z]+\d+) (\S+)(.*)$")
_BOX_RE = re.compile(r" box=(-?\d+),(-?\d+),(\d+),(\d+)$")
_QUOTED_RE = re.compile(r'"([^"]*)"')
_MARK_COLORS = {"+": "#3ecf8e", "~": "#f7c873", "-": "#f0714a", "": "#7c93a6"}


def record_trace_step(trace_path: str | Path, req: dict[str, Any], out: str, wall_ms: float) -> None:
    rec = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "req": req,
        "out": out,
        "ms": round(wall_ms, 2),
    }
    path = Path(trace_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")


def with_trace_bounds(req: dict[str, Any]) -> dict[str, Any]:
    traced = {**req, "bounds": True}
    if isinstance(req.get("parallel"), list):
        traced["parallel"] = [{**t, "bounds": True} if isinstance(t, dict) else t for t in req["parallel"]]
    return traced


def load_trace(path: str | Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for raw in Path(path).expanduser().read_text(encoding="utf-8", errors="replace").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        if isinstance(rec, dict):
            records.append(rec)
    return records


def parse_output(text: str) -> list[dict[str, Any]]:
    """Split one fmu response into per-surface blocks with parsed element lines."""
    blocks: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    for line in text.splitlines():
        hm = _HEADER_RE.match(line)
        if hm:
            meta = {m.group(1): (m.group(3) if m.group(3) is not None else m.group(2)) for m in _KV_RE.finditer(line[2:])}
            cur = {"surface": hm.group(1), "meta": meta, "elements": [], "errors": [], "unchanged": False}
            blocks.append(cur)
            continue
        if cur is None:
            continue
        if line.startswith("! "):
            cur["errors"].append(line[2:])
            continue
        if line.startswith("= unchanged"):
            cur["unchanged"] = True
            continue
        em = _ELEM_RE.match(line)
        if not em:
            continue
        rest = em.group(4)
        box = None
        bm = _BOX_RE.search(rest)
        if bm:
            box = tuple(int(v) for v in bm.groups())
            rest = rest[: bm.start()]
        if "->" in rest:
            rest = rest.split("->", 1)[1]
        qm = _QUOTED_RE.search(rest)
        cur["elements"].append(
            {
                "mark": em.group(1) or "",
                "uid": em.group(2),
                "role": em.group(3),
                "label": qm.group(1) if qm else rest.strip(),
                "box": box,
            }
        )
    return blocks


def _target_key(block: dict[str, Any]) -> str:
    meta = block["meta"]
    return f'{block["surface"]}:{meta.get("tab") or meta.get("app") or "?"}'


def _origin_and_size(block: dict[str, Any], elements: list[dict[str, Any]]) -> tuple[float, float, float, float]:
    meta = block["meta"]
    if meta.get("frame"):
        fx, fy, fw, fh = (float(v) for v in meta["frame"].split(","))
        return fx, fy, max(fw, 1.0), max(fh, 1.0)
    if meta.get("vp") and "x" in meta["vp"]:
        vw, vh = (float(v) for v in meta["vp"].split("x", 1))
        return 0.0, 0.0, max(vw, 1.0), max(vh, 1.0)
    boxes = [e["box"] for e in elements if e["box"]]
    if not boxes:
        return 0.0, 0.0, 1.0, 1.0
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    return x0, y0, max(max(b[0] + b[2] for b in boxes) - x0, 1.0), max(max(b[1] + b[3] for b in boxes) - y0, 1.0)


def build_frames(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn trace or perf records into replay frames, carrying element state across diffs per target."""
    state: dict[str, dict[str, dict[str, Any]]] = {}
    frames: list[dict[str, Any]] = []
    for idx, rec in enumerate(records, start=1):
        out = rec.get("out")
        if not isinstance(out, str):
            timings = rec.get("timings_ms") or {}
            frames.append(
                {
                    "step": idx,
                    "ts": rec.get("ts", ""),
                    "title": f'{rec.get("surface", "?")} {rec.get("target", "")}'.strip(),
                    "ops": " ".join(str(o) for o in rec.get("ops") or []),
                    "ms": float(timings.get("total") or rec.get("ms") or 0.0),
                    "engine_ms": None,
                    "counts": {"+": 0, "~": 0, "-": 0},
                    "els": rec.get("els"),
                    "tokens": rec.get("est_tokens"),
                    "errors": [str(e) for e in rec.get("errors") or []],
                    "elements": [],
                    "view": (0.0, 0.0, 1.0, 1.0),
                }
            )
            continue
        req = rec.get("req") or {}
        blocks = parse_output(out) or [{"surface": "-", "meta": {}, "elements": [], "errors": [], "unchanged": False}]
        for block in blocks:
            key = _target_key(block)
            known = state.setdefault(key, {})
            elems = block["elements"]
            is_diff = block["unchanged"] or (elems and all(e["mark"] for e in elems))
            if not is_diff:
                known.clear()
            for e in elems:
                if e["mark"] == "-":
                    continue
                prev = known.get(e["uid"])
                known[e["uid"]] = {**e, "box": e["box"] or (prev["box"] if prev else None)}
            changed = {e["uid"]: e for e in elems if e["mark"]}
            removed = [e for e in elems if e["mark"] == "-"]
            shown = [{**e, "mark": changed[uid]["mark"] if uid in changed else ""} for uid, e in known.items()] + removed
            for e in removed:
                known.pop(e["uid"], None)
            meta = block["meta"]
            frames.append(
                {
                    "step": idx,
                    "ts": rec.get("ts", ""),
                    "title": f'{block["surface"]} {meta.get("tab") or meta.get("app") or ""}'.strip(),
                    "ops": _describe_ops(req),
                    "ms": float(rec.get("ms") or 0.0),
                    "engine_ms": float(meta["ms"]) if meta.get("ms") else None,
                    "counts": {m: sum(1 for e in elems if e["mark"] == m) for m in "+~-"},
                    "els": meta.get("els"),
                    "tokens": max(1, (len(out) + 3) // 4),
                    "errors": block["errors"],
                    "elements": shown,
                    "view": _origin_and_size(block, shown),
                }
            )
    return frames


def _describe_ops(req: dict[str, Any]) -> str:
    steps = req.get("steps")
    if isinstance(steps, list) and steps:
        parts = []
        for st in steps:
            if isinstance(st, dict):
                op = st.get("op") or st.get("action") or "?"
                tgt = st.get("uid") or st.get("name") or st.get("target") or st.get("text") or st.get("key") or ""
                parts.append(f"{op}({tgt})" if tgt else str(op))
        return " → ".join(parts)
    if req.get("code"):
        code = " ".join(str(req["code"]).split())
        return code if len(code) <= 90 else code[:89] + "…"
    if isinstance(req.get("parallel"), list):
        return f'parallel × {len(req["parallel"])}'
    return str(req.get("url") or req.get("app") or "")


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _frame_svg_body(frame: dict[str, Any], frames: list[dict[str, Any]], frame_idx: int) -> str:
    parts: list[str] = []
    c = frame["counts"]
    engine = f' · engine {frame["engine_ms"]:.0f} ms' if frame["engine_ms"] is not None else ""
    parts.append(f'<rect width="{FRAME_W}" height="{HUD_H}" fill="#203444"/>')
    parts.append(
        f'<text x="16" y="23" class="hud-title">step {frame["step"]} · {_esc(frame["title"])}</text>'
        f'<text x="16" y="44" class="hud-ops">{_esc(frame["ops"] or "snapshot")}</text>'
    )
    parts.append(
        f'<text x="{FRAME_W - 16}" y="25" text-anchor="end" class="hud-ms">{frame["ms"]:.0f} ms</text>'
        f'<text x="{FRAME_W - 16}" y="44" text-anchor="end" class="hud-meta">'
        f'<tspan fill="#3ecf8e">+{c["+"]}</tspan> <tspan fill="#f7c873">~{c["~"]}</tspan> '
        f'<tspan fill="#f0714a">-{c["-"]}</tspan> · els {_esc(frame["els"] if frame["els"] is not None else "-")}'
        f' · ~{_esc(frame["tokens"] if frame["tokens"] is not None else "-")} tok{engine}</text>'
    )
    vx, vy, vw, vh = frame["view"]
    pad = 18.0
    scale = min((STAGE_W - 2 * pad) / vw, (STAGE_H - 2 * pad) / vh)
    ox = (STAGE_W - vw * scale) / 2.0
    oy = HUD_H + (STAGE_H - vh * scale) / 2.0
    if vw > 1 and vh > 1:
        parts.append(
            f'<rect x="{ox:.1f}" y="{oy:.1f}" width="{vw * scale:.1f}" height="{vh * scale:.1f}" '
            'fill="#16263a" stroke="#38556b" rx="6"/>'
        )
    boxed = [e for e in frame["elements"] if e["box"]]
    boxed.sort(key=lambda e: e["mark"] != "")
    for e in boxed:
        bx, by, bw, bh = e["box"]
        x = ox + (bx - vx) * scale
        y = oy + (by - vy) * scale
        w = max(bw * scale, 2.0)
        h = max(bh * scale, 2.0)
        color = _MARK_COLORS[e["mark"]]
        dash = ' stroke-dasharray="4 3"' if e["mark"] == "-" else ""
        fill_op = "0.18" if e["mark"] else "0.05"
        parts.append(
            f'<g><title>{_esc(e["mark"] + " " if e["mark"] else "")}{_esc(e["uid"])} {_esc(e["role"])} "{_esc(e["label"])}"</title>'
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" fill="{color}" fill-opacity="{fill_op}" '
            f'stroke="{color}" stroke-width="{1.8 if e["mark"] else 1}"{dash} rx="2"/>'
        )
        if w >= 26 and h >= 10:
            parts.append(f'<text x="{x + 3:.1f}" y="{y + 10:.1f}" class="uid" fill="{color}">{_esc(e["uid"])}</text>')
        parts.append("</g>")
    if not boxed:
        listed = frame["elements"][:14]
        msg = "no box= bounds in this step (record with FMU_TRACE=… to capture @uid bounds)" if listed else "no element changes"
        parts.append(f'<text x="{FRAME_W / 2}" y="{HUD_H + 40}" text-anchor="middle" class="note">{_esc(msg)}</text>')
        for i, e in enumerate(listed):
            color = _MARK_COLORS[e["mark"]]
            parts.append(
                f'<text x="40" y="{HUD_H + 80 + i * 24}" class="line" fill="{color}">'
                f'{_esc((e["mark"] or " ") + " " + e["uid"] + " " + e["role"] + " " + e["label"][:80])}</text>'
            )
    for i, err in enumerate(frame["errors"][:3]):
        parts.append(
            f'<text x="16" y="{HUD_H + STAGE_H - 14 - i * 18}" class="err">! {_esc(err[:130])}</text>'
        )
    chart_y = HUD_H + STAGE_H
    parts.append(f'<rect y="{chart_y}" width="{FRAME_W}" height="{CHART_H}" fill="#101c26"/>')
    max_ms = max((f["ms"] for f in frames), default=1.0) or 1.0
    bar_w = (FRAME_W - 32) / max(len(frames), 1)
    for i, f in enumerate(frames):
        bh = max(2.0, (CHART_H - 22) * math.sqrt(max(f["ms"], 0.0) / max_ms))
        fill = "#7cc4fa" if i == frame_idx else ("#f0714a" if f["errors"] else "#38556b")
        parts.append(
            f'<rect x="{16 + i * bar_w:.1f}" y="{chart_y + CHART_H - 8 - bh:.1f}" width="{max(bar_w - 2, 1):.1f}" '
            f'height="{bh:.1f}" fill="{fill}" rx="1"/>'
        )
    parts.append(
        f'<text x="{FRAME_W - 16}" y="{chart_y + 14}" text-anchor="end" class="chart">√ms scale · max {max_ms:.0f} ms · '
        f'{frame_idx + 1}/{len(frames)}</text>'
    )
    return "".join(parts)


_SVG_STYLE = (
    "<style>"
    "text{font-family:'SF Mono',Menlo,ui-monospace,monospace}"
    ".hud-title{font-size:15px;font-weight:700;fill:#e6f0f7}"
    ".hud-ops{font-size:12px;fill:#9fb2c2}"
    ".hud-ms{font-size:20px;font-weight:700;fill:#7cc4fa}"
    ".hud-meta{font-size:12px;fill:#c4d1dc}"
    ".uid{font-size:9px;font-weight:700}"
    ".note{font-size:13px;fill:#9fb2c2}"
    ".line{font-size:13px}"
    ".err{font-size:12px;fill:#f0714a}"
    ".chart{font-size:10px;fill:#7c93a6}"
    "</style>"
)


def render_svg(frames: list[dict[str, Any]]) -> str:
    """Animated SVG (SMIL) that loops through frames; renders inline in GitHub READMEs."""
    total = SVG_FRAME_S * max(len(frames), 1)
    groups = []
    for i, frame in enumerate(frames):
        key_times = f"0;{i / len(frames):.4f};{(i + 1) / len(frames):.4f};1"
        groups.append(
            f'<g visibility="{"visible" if i == 0 else "hidden"}">'
            f'<animate attributeName="visibility" values="hidden;visible;hidden;hidden" keyTimes="{key_times}" '
            f'calcMode="discrete" dur="{total:.2f}s" repeatCount="indefinite"/>'
            f"{_frame_svg_body(frame, frames, i)}</g>"
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {FRAME_W} {FRAME_H}" width="{FRAME_W}" height="{FRAME_H}">'
        f'{_SVG_STYLE}<rect width="{FRAME_W}" height="{FRAME_H}" fill="#101c26"/>{"".join(groups)}</svg>\n'
    )


def render_html(frames: list[dict[str, Any]], title: str = "fmu replay") -> str:
    svgs = "".join(
        f'<svg class="frame" data-i="{i}" viewBox="0 0 {FRAME_W} {FRAME_H}"{" hidden" if i else ""}>'
        f"{_SVG_STYLE}{_frame_svg_body(f, frames, i)}</svg>"
        for i, f in enumerate(frames)
    )
    total_ms = sum(f["ms"] for f in frames)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(title)}</title>
<style>
body{{margin:0;background:#0c1720;color:#e6f0f7;font:14px/1.5 -apple-system,BlinkMacSystemFont,'Helvetica Neue',sans-serif}}
main{{max-width:{FRAME_W}px;margin:24px auto;padding:0 16px}}
h1{{font-size:18px;margin:0 0 4px}}p{{margin:0 0 12px;color:#9fb2c2}}
.stage svg{{width:100%;height:auto;display:block;border-radius:10px;border:1px solid #38556b}}
.bar{{display:flex;gap:8px;align-items:center;margin:12px 0}}
button{{background:#203444;color:#e6f0f7;border:1px solid #38556b;border-radius:6px;padding:6px 12px;font:inherit;cursor:pointer}}
button:hover{{background:#2b4559}}input[type=range]{{flex:1}}
.legend span{{margin-right:14px}}.frame[hidden]{{display:none!important}}
</style></head><body><main>
<h1>{_esc(title)}</h1>
<p>{len(frames)} frames · {total_ms:.0f} ms total · <span class="legend"><span style="color:#3ecf8e">■ added</span><span style="color:#f7c873">■ changed</span><span style="color:#f0714a">■ removed</span><span style="color:#7c93a6">■ unchanged</span></span></p>
<div class="stage">{svgs}</div>
<div class="bar"><button id="prev">◀</button><button id="play">Play</button><button id="next">▶</button>
<input id="seek" type="range" min="0" max="{max(len(frames) - 1, 0)}" value="0"><span id="pos">1/{len(frames)}</span></div>
<p>Keys: ←/→ step, space play/pause. Hover a box for its @uid line.</p>
</main><script>
const frames=[...document.querySelectorAll('.frame')];let i=0,timer=null;
const show=n=>{{i=(n+frames.length)%frames.length;frames.forEach((f,k)=>f.toggleAttribute('hidden',k!==i));seek.value=i;pos.textContent=(i+1)+'/'+frames.length;}};
const toggle=()=>{{if(timer){{clearInterval(timer);timer=null;play.textContent='Play';}}else{{timer=setInterval(()=>show(i+1),{int(SVG_FRAME_S * 1000)});play.textContent='Pause';}}}};
prev.onclick=()=>show(i-1);next.onclick=()=>show(i+1);play.onclick=toggle;seek.oninput=()=>show(+seek.value);
document.addEventListener('keydown',e=>{{if(e.key==='ArrowLeft')show(i-1);else if(e.key==='ArrowRight')show(i+1);else if(e.key===' '){{e.preventDefault();toggle();}}}});
</script></body></html>
"""


def render_trace(trace_path: str | Path, out_path: str | Path | None = None) -> Path:
    src = Path(trace_path).expanduser()
    frames = build_frames(load_trace(src))
    if not frames:
        raise ValueError(f"No replayable records in {src}")
    dest = Path(out_path).expanduser() if out_path else src.with_suffix(".html")
    body = render_svg(frames) if dest.suffix.lower() == ".svg" else render_html(frames, title=f"fmu replay · {src.name}")
    dest.write_text(body, encoding="utf-8")
    return dest
