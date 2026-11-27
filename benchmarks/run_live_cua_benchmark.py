#!/usr/bin/env python3
"""Run a real live public-web CUA benchmark + live Clearcut retailer gap audit via gpt-delegate cua."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
import time
from datetime import datetime
from pathlib import Path

GPT_DELEGATE = "/Users/lfan/Project/agent/scripts/gpt-delegate"

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "task_id", "final_url", "passed", "observed_answer", "evidence", "tabs_done"],
    "properties": {
        "status": {"type": "string", "enum": ["completed", "partial", "blocked"]},
        "task_id": {"type": "string"},
        "final_url": {"type": ["string", "null"]},
        "passed": {"type": "boolean"},
        "observed_answer": {"type": "string"},
        "evidence": {"type": "string"},
        "tabs_done": {"type": "boolean"},
    },
}

LIVE_BENCH_TASKS = [
    {"id": "S01_poe2", "stratum": "SPA", "url": "https://poe2brain.clifan.com/", "goal": "Open the Build Index tab, search for a nonexistent term 'zzzznoexist99', confirm empty state, then clear search and confirm build rows restore without page reload."},
    {"id": "S02_clifan", "stratum": "SPA", "url": "https://clifan.com/", "goal": "Switch from Cover View to Full Workbench, return to Cover View, then open Full Workbench again; confirm zero page/console errors."},
    {"id": "S03_todomvc", "stratum": "SPA", "url": "https://todomvc.com/examples/react/dist/", "goal": "Create two todos ('Alpha task', 'Beta task'), mark 'Alpha task' completed, filter by Active, and verify only 'Beta task' is visible."},
    {"id": "S04_react", "stratum": "SPA", "url": "https://react.dev/learn", "goal": "Navigate to 'Thinking in React' via the sidebar/links and return the main article H1 heading and final URL."},
    {"id": "R01_gospec", "stratum": "SSR_Docs", "url": "https://go.dev/ref/spec", "goal": "Navigate to the 'Slice types' section and report its exact EBNF grammar production."},
    {"id": "R02_mdn", "stratum": "SSR_Docs", "url": "https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/details", "goal": "Follow the link to the <summary> element reference and state in one sentence what <summary> represents."},
    {"id": "R03_pydocs", "stratum": "SSR_Docs", "url": "https://docs.python.org/3/library/collections.html", "goal": "Locate Counter.most_common([n]) and report what omitting n returns."},
    {"id": "R05_rfc9110", "stratum": "SSR_Docs", "url": "https://www.rfc-editor.org/rfc/rfc9110.html", "goal": "Locate section 15.5.5 and report its exact HTTP status code and title."},
    {"id": "T01_datatables", "stratum": "Table", "url": "https://datatables.net/examples/basic_init/zero_configuration.html", "goal": "Search the table for 'Airi Satou', report her Office, clear the search, and advance to page 2 confirming new rows appear."},
    {"id": "T06_iana429", "stratum": "Table", "url": "https://www.iana.org/assignments/http-status-codes/http-status-codes.xhtml", "goal": "Find HTTP status code 429 in the table and report its Description and Reference."},
    {"id": "T07_pg23505", "stratum": "Table", "url": "https://www.postgresql.org/docs/current/errcodes-appendix.html", "goal": "Find SQLSTATE 23505 and report both its Condition Name and its enclosing Class heading."},
    {"id": "T08_closesnow", "stratum": "Table", "url": "https://close-snow.clifan.com/", "goal": "Open the Compare / Matrix view, filter to Ikon pass resorts, sort by a snowfall column, and report the top 2 resorts and their values."},
    {"id": "F01_selenium", "stratum": "Form", "url": "https://www.selenium.dev/selenium/web/web-form.html", "goal": "Set Text input to 'fmu-bench-42', select 'Two' in Dropdown (select), toggle the Default checkbox, and verify all 3 live DOM values without submitting."},
    {"id": "F04_freshpowder", "stratum": "Form", "url": "https://freshpowder.clifan.com/", "goal": "Search for nonexistent term 'zzznoexist99', confirm empty state, then clear/reset search and verify deal cards return cleanly."},
    {"id": "F05_calc", "stratum": "Form", "url": "https://www.calculator.net/percent-calculator.html", "goal": "Calculate 17% of 240 using the top percentage calculator and confirm the rendered result equals 40.8."},
    {"id": "I01_quotes", "stratum": "Scroll", "url": "https://quotes.toscrape.com/scroll", "goal": "Scroll down to load at least 2 additional batches of quotes (>20 quotes total) and report the author of the 21st quote."},
    {"id": "X06_iframes", "stratum": "Iframe", "url": "https://testpages.eviltester.com/styled/iframes-test.html", "goal": "Inspect the embedded iframes and report visible list/heading text from inside both iframe contexts."},
    {"id": "D01_shoelace", "stratum": "ShadowDOM", "url": "https://shoelace.style/components/switch", "goal": "Toggle the basic switch component inside its Shadow DOM, confirm checked becomes true, then toggle back to false."},
]

RETAILER_AUDIT_TASKS = [
    {"id": "RET_evo_ski", "vertical": "FreshPowder", "url": "https://www.evo.com/shop/sale/ski", "goal": "Inspect the live ski sale/outlet page: report total sale item count (if shown), top 3 discounted ski/boot brands visible, largest discount % visible, and whether any pagination/bot-gate appears."},
    {"id": "RET_backcountry_ski", "vertical": "FreshPowder", "url": "https://www.backcountry.com/rc/ski-sale", "goal": "Inspect the live ski sale page: report visible deal count, top 3 brands on sale, max discount %, and whether any bot/login gate blocks headless browsing."},
    {"id": "RET_jenson_bikes", "vertical": "Big Ring", "url": "https://www.jensonusa.com/sale-complete-bikes", "goal": "Inspect live sale complete bikes: report visible bike count, 3 sample complete bike models with sale vs original price, and whether frame sizes are listed on cards."},
    {"id": "RET_acme_tools", "vertical": "Bench Drop", "url": "https://www.acmetools.com/deals/", "goal": "Inspect the live deals page: list top 3 cordless power-tool brands promoted (DeWalt/Makita/Milwaukee/etc.), sample bare-tool vs kit pricing, and any anti-bot/Cloudflare challenge."},
    {"id": "RET_ggg_ultralight", "vertical": "Highline", "url": "https://www.garagegrowngear.com/collections/sale-1", "goal": "Inspect the live sale collection: report count of sale items, 3 sample ultralight items with sale vs MSRP, and whether weight/specs appear in titles."},
    {"id": "RET_mackkite_foil", "vertical": "Tideline", "url": "https://www.mackiteboarding.com/wing-foiling-closeouts/", "goal": "Inspect live wing-foiling closeouts: report sample wing/foil/board closeout items, discount %, and whether complete foil kits vs standalone wing packs are mixed on the same page."},
]


def run_one(task: dict, schema_path: Path, kind: str) -> dict:
    tid = task["id"]
    url = task["url"]
    prompt = (
        f"Execute this strictly READ-ONLY public headless browser task ({tid}):\n"
        f"URL: {url}\n"
        f"Goal: {task['goal']}\n"
        "Rules: Use headless mode (`cua.createBrowserTab('headless', url)`). Batch steps in <=8 calls. "
        "Do NOT log in, submit real forms, or bypass CAPTCHAs (if gated by CAPTCHA/bot-block, return status='blocked' immediately). "
        "Close your tab via tab.close() when done and return strict JSON matching the schema."
    )
    t0 = time.monotonic()
    cmd = [
        GPT_DELEGATE,
        "cua",
        "--url", url,
        "--label", f"bench-{tid}",
        "--timeout", "150",
        "--schema", str(schema_path),
        "--json",
        prompt,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    elapsed = round(time.monotonic() - t0, 2)
    out = proc.stdout.strip()
    parsed = {}
    if out:
        try:
            raw = json.loads(out)
            parsed = raw.get("result") if isinstance(raw.get("result"), dict) else raw
            if isinstance(parsed, str):
                parsed = json.loads(parsed)
        except Exception:
            parsed = {"status": "partial", "passed": False, "observed_answer": out[:300]}
    return {
        "id": tid,
        "kind": kind,
        "meta": {k: v for k, v in task.items() if k not in {"id", "url", "goal"}},
        "url": url,
        "elapsed_s": elapsed,
        "returncode": proc.returncode,
        "status": parsed.get("status", "partial"),
        "passed": bool(parsed.get("passed", False)),
        "final_url": parsed.get("final_url"),
        "observed_answer": str(parsed.get("observed_answer", ""))[:500],
        "evidence": str(parsed.get("evidence", ""))[:500],
        "stderr_tail": proc.stderr.strip()[-200:] if proc.returncode != 0 else "",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--only", choices=["all", "bench", "retailers"], default="all")
    args = ap.parse_args()

    schema_path = Path("/tmp/live_cua_bench_schema.json")
    schema_path.write_text(json.dumps(SCHEMA, indent=2), encoding="utf-8")

    items: list[tuple[str, dict]] = []
    if args.only in {"all", "bench"}:
        items.extend(("bench", t) for t in LIVE_BENCH_TASKS)
    if args.only in {"all", "retailers"}:
        items.extend(("retailer", t) for t in RETAILER_AUDIT_TASKS)

    results: list[dict] = []
    t0 = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futs = {ex.submit(run_one, task, schema_path, kind): (kind, task) for kind, task in items}
        for fut in concurrent.futures.as_completed(futs):
            kind, task = futs[fut]
            try:
                res = fut.result()
            except Exception as exc:
                res = {"id": task["id"], "kind": kind, "url": task["url"], "status": "blocked", "passed": False, "elapsed_s": 0.0, "observed_answer": "", "evidence": f"runner_error: {exc}"}
            results.append(res)
            print(f"[{res['kind']}:{res['id']}] status={res['status']} passed={res['passed']} t={res['elapsed_s']}s | {res['observed_answer'][:90]}", flush=True)

    wall = round(time.monotonic() - t0, 2)
    bench_res = sorted([r for r in results if r["kind"] == "bench"], key=lambda x: x["id"])
    ret_res = sorted([r for r in results if r["kind"] == "retailer"], key=lambda x: x["id"])

    if bench_res:
        out_dir = Path("/Users/lfan/Project/faster-mac-use/benchmarks/results")
        out_dir.mkdir(parents=True, exist_ok=True)
        passed_n = sum(1 for r in bench_res if r["passed"])
        blocked_n = sum(1 for r in bench_res if r["status"] == "blocked")
        summary = {
            "generated_at": datetime.now().isoformat(),
            "wall_time_s": wall,
            "total": len(bench_res),
            "passed": passed_n,
            "blocked": blocked_n,
            "results": bench_res,
        }
        (out_dir / "live_cua_benchmark_2026-11-27.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        md = [
            "# Faster Mac Use — Live Public-Web CUA Benchmark (18 Stratified Sites)",
            f"- **Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')} | **Passed:** `{passed_n}/{len(bench_res)}` | **Blocked:** `{blocked_n}` | **Wall:** `{wall}s`\n",
            "| ID | Stratum | Status | Passed | Time (s) | Observed Answer |",
            "|---|---|---|---:|---:|---|",
        ]
        for r in bench_res:
            md.append(f"| `{r['id']}` | {r.get('meta',{}).get('stratum','')} | {r['status']} | {'YES' if r['passed'] else 'NO'} | {r['elapsed_s']} | {r['observed_answer'].replace('|','/')[:140]} |")
        (out_dir / "live_cua_benchmark_2026-11-27.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    if ret_res:
        out_dir = Path("/Users/lfan/Project/clearcut/reports")
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "2026-11-27-live-retailer-gap-audit.json").write_text(json.dumps(ret_res, indent=2), encoding="utf-8")
        md = [
            "# Clearcut Live Retailer Sale-Page CUA Gap Audit",
            f"- **Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')} | **Retailers Audited:** `{len(ret_res)}`\n",
            "| ID | Vertical | Status | Time (s) | Live Observations |",
            "|---|---|---|---:|---|",
        ]
        for r in ret_res:
            md.append(f"| `{r['id']}` | {r.get('meta',{}).get('vertical','')} | {r['status']} | {r['elapsed_s']} | {r['observed_answer'].replace('|','/')[:180]} |")
        (out_dir / "2026-11-27-live-retailer-gap-audit.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    print(f"DONE bench={len(bench_res)} retailers={len(ret_res)} wall={wall}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
