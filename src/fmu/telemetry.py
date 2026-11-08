"""Zero-overhead, privacy-safe performance & invocation telemetry for Faster Mac Use (fmu).

Writes structured JSONL records to ``~/.fmu/logs/perf-YYYY-MM-DD.jsonl`` (overridable via
``FMU_LOG_DIR``; disableable via ``FMU_LOG=0``) and provides ``build_perf_report()`` for
automated latency, token-footprint, diff-efficiency, and error/escalation bottleneck reviews.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import queue
import re
import threading
import time
from typing import Any
from urllib.parse import urlparse

DEFAULT_RETENTION_DAYS = 30
_PATH_TOKEN_RE = re.compile(r"/(?:Users|private|var|tmp)/[^\s\"';)]+")

_log_queue: queue.SimpleQueue[tuple[Path, str]] = queue.SimpleQueue()
_writer_started = False
_writer_lock = threading.Lock()
_pruned_dirs: set[str] = set()


def telemetry_enabled() -> bool:
    val = os.environ.get("FMU_LOG", "1").strip().lower()
    return val not in ("0", "false", "off", "no")


def get_log_dir() -> Path:
    custom = os.environ.get("FMU_LOG_DIR", "").strip()
    if custom:
        base = Path(custom).expanduser()
    else:
        fmu_home = Path(os.environ.get("FMU_HOME", str(Path.home() / ".fmu"))).expanduser()
        base = fmu_home / "logs"
    base.mkdir(parents=True, exist_ok=True)
    return base


def sanitize_url_target(raw_url: str | None) -> str:
    """Return ``host/path`` without query strings, fragments, or embedded credentials."""
    if not raw_url:
        return ""
    s = str(raw_url).strip()
    if s in ("about:blank", "*"):
        return s
    if s.startswith("file://"):
        return "file://<local>"
    try:
        parsed = urlparse(s if "://" in s else f"https://{s}")
        host = (parsed.hostname or "").lower()
        port = f":{parsed.port}" if parsed.port and parsed.port not in (80, 443) else ""
        path = parsed.path or "/"
        if len(path) > 64:
            path = path[:61] + "..."
        return f"{host}{port}{path}" if host else s[:64]
    except Exception:
        return s[:64]


def sanitize_error(err: Any) -> str:
    """Strip local filesystem paths and cap length so logs never leak sensitive values."""
    msg = str(err or "").replace("\n", " ").strip()
    msg = _PATH_TOKEN_RE.sub("<path>", msg)
    return msg[:220]


def _prune_old_logs(log_dir: Path) -> None:
    key = str(log_dir)
    if key in _pruned_dirs:
        return
    _pruned_dirs.add(key)
    try:
        keep_days = max(1, int(os.environ.get("FMU_LOG_RETENTION_DAYS", str(DEFAULT_RETENTION_DAYS))))
    except ValueError:
        keep_days = DEFAULT_RETENTION_DAYS
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=keep_days)
    try:
        for p in log_dir.glob("perf-*.jsonl"):
            m = re.match(r"^perf-(\d{4}-\d{2}-\d{2})\.jsonl$", p.name)
            if not m:
                continue
            try:
                fdate = datetime.strptime(m.group(1), "%Y-%m-%d").date()
                if fdate < cutoff:
                    p.unlink(missing_ok=True)
            except ValueError:
                continue
    except OSError:
        pass


def _append_line(file_path: Path, line: str) -> None:
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(file_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, (line + "\n").encode("utf-8", errors="replace"))
        finally:
            os.close(fd)
    except OSError:
        pass


def _writer_loop() -> None:
    while True:
        file_path, line = _log_queue.get()
        _append_line(file_path, line)


def _ensure_writer() -> None:
    global _writer_started
    if _writer_started:
        return
    with _writer_lock:
        if not _writer_started:
            t = threading.Thread(target=_writer_loop, name="fmu-telemetry-writer", daemon=True)
            t.start()
            _writer_started = True


def record_perf_event(event: dict[str, Any], *, sync: bool = False) -> None:
    """Record a single structured performance event without blocking the hot path.

    Pass ``sync=True`` for short-lived one-shot CLI processes (e.g. direct ``fmu-ax`` calls)
    that exit immediately after returning.
    """
    if not telemetry_enabled():
        return
    try:
        now = datetime.now(timezone.utc)
        log_dir = get_log_dir()
        _prune_old_logs(log_dir)
        payload: dict[str, Any] = {
            "ts": now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            **event,
        }
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        target_file = log_dir / f"perf-{now.strftime('%Y-%m-%d')}.jsonl"
        if sync:
            _append_line(target_file, line)
        else:
            _ensure_writer()
            _log_queue.put_nowait((target_file, line))
    except Exception:
        pass


def flush_telemetry(timeout: float = 0.25) -> None:
    """Wait briefly for queued background telemetry lines to flush (useful in tests/shutdown)."""
    deadline = time.perf_counter() + timeout
    while not _log_queue.empty() and time.perf_counter() < deadline:
        time.sleep(0.005)


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return round(sorted_vals[0], 2)
    idx = (len(sorted_vals) - 1) * (pct / 100.0)
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return round(sorted_vals[lo], 2)
    frac = idx - lo
    return round(sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac, 2)


def load_perf_events(days: int = 7, log_dir: Path | None = None) -> list[dict[str, Any]]:
    base = log_dir or get_log_dir()
    if not base.is_dir():
        return []
    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=max(1, days))
    cutoff_date = cutoff_dt.date()
    events: list[dict[str, Any]] = []
    for p in sorted(base.glob("perf-*.jsonl")):
        m = re.match(r"^perf-(\d{4}-\d{2}-\d{2})\.jsonl$", p.name)
        if m:
            try:
                fdate = datetime.strptime(m.group(1), "%Y-%m-%d").date()
                if fdate < cutoff_date:
                    continue
            except ValueError:
                pass
        try:
            for raw_line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    rec = json.loads(raw_line)
                    if isinstance(rec, dict):
                        events.append(rec)
                except ValueError:
                    continue
        except OSError:
            continue
    return events


def summarize_perf_events(events: list[dict[str, Any]], *, days: int = 7, log_dir: Path | None = None) -> dict[str, Any]:
    base = log_dir or get_log_dir()
    total_calls = len(events)
    if total_calls == 0:
        return {
            "window_days": days,
            "log_dir": str(base),
            "total_calls": 0,
            "surfaces": {},
            "phases_avg_ms": {},
            "tokens": {"avg": 0, "p95": 0, "max": 0, "diff_or_unchanged_ratio": 0.0},
            "cold_starts": 0,
            "error_calls": 0,
            "escalations": {},
            "slowest_targets": [],
            "top_errors": [],
            "recommendations": ["No telemetry events recorded yet in the selected window."],
        }

    by_surface: dict[str, list[float]] = defaultdict(list)
    phase_sums: dict[str, float] = defaultdict(float)
    phase_counts: dict[str, int] = defaultdict(int)
    target_latencies: dict[str, list[float]] = defaultdict(list)
    target_tokens: dict[str, list[int]] = defaultdict(list)
    tokens_all: list[float] = []
    mode_counts: Counter[str] = Counter()
    esc_counts: Counter[str] = Counter()
    err_counts: Counter[str] = Counter()
    cold_starts = 0
    error_calls = 0

    for ev in events:
        surf = str(ev.get("surface") or "unknown")
        bridge = str(ev.get("bridge") or "")
        surf_key = f"{surf}:{bridge}" if bridge else surf
        timings = ev.get("timings_ms") if isinstance(ev.get("timings_ms"), dict) else {}
        tot = float(timings.get("total", ev.get("total_ms", 0.0)) or 0.0)
        by_surface[surf_key].append(tot)

        for pk, pv in timings.items():
            if pk == "total":
                continue
            try:
                fval = float(pv)
                phase_sums[pk] += fval
                phase_counts[pk] += 1
            except (TypeError, ValueError):
                pass

        tgt = str(ev.get("target") or "").strip()
        if tgt:
            target_latencies[tgt].append(tot)

        est_tok = int(ev.get("est_tokens") or 0)
        if est_tok > 0:
            tokens_all.append(float(est_tok))
            if tgt:
                target_tokens[tgt].append(est_tok)

        mout = str(ev.get("mode_out") or "")
        if mout:
            mode_counts[mout] += 1

        if ev.get("cold_start"):
            cold_starts += 1

        errs = ev.get("errors") if isinstance(ev.get("errors"), list) else []
        if int(ev.get("error_count") or 0) > 0 or errs:
            error_calls += 1
            for e in errs:
                err_counts[str(e)] += 1

        escs = ev.get("escalations") if isinstance(ev.get("escalations"), list) else []
        for esc_r in escs:
            esc_counts[str(esc_r)] += 1

    surfaces_summary: dict[str, dict[str, Any]] = {}
    for sk, vals in sorted(by_surface.items(), key=lambda kv: -len(kv[1])):
        svals = sorted(vals)
        surfaces_summary[sk] = {
            "calls": len(svals),
            "avg_ms": round(sum(svals) / len(svals), 2),
            "p50_ms": _percentile(svals, 50),
            "p95_ms": _percentile(svals, 95),
            "p99_ms": _percentile(svals, 99),
            "max_ms": round(svals[-1], 2),
        }

    phases_avg = {
        k: round(phase_sums[k] / phase_counts[k], 2)
        for k in sorted(phase_sums.keys())
        if phase_counts[k] > 0
    }

    sorted_toks = sorted(tokens_all)
    obs_total = sum(mode_counts[m] for m in ("unchanged", "diff", "snapshot"))
    compact_hits = mode_counts["unchanged"] + mode_counts["diff"]
    diff_ratio = round(compact_hits / obs_total, 3) if obs_total > 0 else 0.0

    slowest_targets = []
    for tgt, vals in target_latencies.items():
        svals = sorted(vals)
        toks = target_tokens.get(tgt) or [0]
        slowest_targets.append(
            {
                "target": tgt,
                "calls": len(svals),
                "p50_ms": _percentile(svals, 50),
                "p95_ms": _percentile(svals, 95),
                "max_ms": round(svals[-1], 2),
                "avg_tokens": round(sum(toks) / len(toks), 1),
            }
        )
    slowest_targets.sort(key=lambda x: (-float(x["p95_ms"]), -int(x["calls"])))

    recommendations: list[str] = []
    if phases_avg.get("in_page_settle", 0.0) > 35.0:
        recommendations.append(
            f"DOM settle wait averages {phases_avg['in_page_settle']}ms — consider tuning waitQuietDom(quietMs, maxWaitMs) or passing settle=false on read-only batches."
        )
    if phases_avg.get("bridge_overhead", 0.0) > 25.0:
        recommendations.append(
            f"Extension/CDP bridge IPC averages {phases_avg['bridge_overhead']}ms — batch multi-step flows into single steps[] / chained TabHandle calls instead of per-step flushes."
        )
    if obs_total >= 5 and diff_ratio < 0.55:
        recommendations.append(
            f"Compact diff/unchanged hit rate is {round(diff_ratio * 100, 1)}% ({mode_counts['snapshot']} full snapshots vs {compact_hits} diffs) — prefer .diff() over .snapshot() after initial tab attach."
        )
    if cold_starts > max(2, total_calls // 5):
        recommendations.append(
            f"Detected {cold_starts}/{total_calls} daemon cold starts — check if --shutdown/--reset is being called unnecessarily between turns."
        )
    if esc_counts.get("eval_csp", 0) > 0:
        recommendations.append(
            f"{esc_counts['eval_csp']} strict-CSP eval_csp escalations occurred — consider pre-routing known strict-CSP domains (e.g. x.com) directly to CDP Runtime.evaluate to save 1 in-page round-trip."
        )
    if error_calls > 0:
        recommendations.append(
            f"{error_calls}/{total_calls} calls reported step/bridge errors — inspect top_errors to harden selectors, aliases, or permission diagnostics."
        )
    if not recommendations:
        recommendations.append("All latency phases and diff ratios are within healthy sub-50ms bounds.")

    return {
        "window_days": days,
        "log_dir": str(base),
        "total_calls": total_calls,
        "cold_starts": cold_starts,
        "error_calls": error_calls,
        "surfaces": surfaces_summary,
        "phases_avg_ms": phases_avg,
        "modes": dict(mode_counts),
        "tokens": {
            "avg": round(sum(sorted_toks) / len(sorted_toks), 1) if sorted_toks else 0,
            "p95": int(_percentile(sorted_toks, 95)) if sorted_toks else 0,
            "max": int(sorted_toks[-1]) if sorted_toks else 0,
            "diff_or_unchanged_ratio": diff_ratio,
        },
        "escalations": dict(esc_counts),
        "slowest_targets": slowest_targets[:8],
        "top_errors": [{"error": k, "count": v} for k, v in err_counts.most_common(6)],
        "recommendations": recommendations,
    }


def format_perf_report(summary: dict[str, Any], fmt: str = "markdown") -> str:
    if fmt == "json":
        return json.dumps(summary, indent=2, ensure_ascii=False)
    lines = [
        f"# fmu-perf window={summary['window_days']}d calls={summary['total_calls']} "
        f"cold_starts={summary.get('cold_starts', 0)} errors={summary.get('error_calls', 0)} "
        f"logs=\"{summary['log_dir']}\""
    ]
    if summary["total_calls"] == 0:
        lines.append("No telemetry events recorded yet.")
        return "\n".join(lines)

    tok = summary["tokens"]
    lines.append(
        f"# tokens: avg={tok['avg']} p95={tok['p95']} max={tok['max']} "
        f"compact_diff_ratio={round(tok['diff_or_unchanged_ratio'] * 100, 1)}% modes={json.dumps(summary.get('modes', {}))}"
    )
    if summary.get("surfaces"):
        lines.append("## Latency by Surface (ms)")
        for sk, st in summary["surfaces"].items():
            lines.append(
                f"- {sk}: calls={st['calls']} p50={st['p50_ms']}ms p95={st['p95_ms']}ms "
                f"p99={st['p99_ms']}ms max={st['max_ms']}ms (avg={st['avg_ms']}ms)"
            )
    if summary.get("phases_avg_ms"):
        phase_parts = [f"{k}={v}ms" for k, v in summary["phases_avg_ms"].items()]
        lines.append("## Phase Breakdown (avg): " + ", ".join(phase_parts))
    if summary.get("escalations"):
        lines.append("## Escalations: " + json.dumps(summary["escalations"]))
    if summary.get("slowest_targets"):
        lines.append("## Slowest Targets")
        for item in summary["slowest_targets"][:5]:
            lines.append(
                f"- {item['target']}: calls={item['calls']} p50={item['p50_ms']}ms "
                f"p95={item['p95_ms']}ms max={item['max_ms']}ms avg_tokens={item['avg_tokens']}"
            )
    if summary.get("top_errors"):
        lines.append("## Top Errors")
        for err in summary["top_errors"]:
            lines.append(f"- ({err['count']}x) {err['error']}")
    if summary.get("recommendations"):
        lines.append("## Optimization Hints")
        for rec in summary["recommendations"]:
            lines.append(f"- {rec}")
    return "\n".join(lines)


def build_perf_report(days: int = 7, fmt: str = "markdown", log_dir: Path | None = None) -> str:
    flush_telemetry()
    events = load_perf_events(days=days, log_dir=log_dir)
    summary = summarize_perf_events(events, days=days, log_dir=log_dir)
    return format_perf_report(summary, fmt=fmt)
