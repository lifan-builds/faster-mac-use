# Faster Mac Use (`fmu`) — Continuous Performance & Reliability Review Playbook

Every real-world `fmu` / `agy-cua` call automatically logs privacy-safe, sub-phase latency and token-footprint telemetry to `~/.fmu/logs/perf-YYYY-MM-DD.jsonl` (30-day rolling retention, override via `FMU_LOG_DIR`, disable via `FMU_LOG=0`).

## 1. Inspect Recent Field Telemetry

Run the built-in analyzer across the desired window (default 7 days):

```bash
/Users/lfan/Project/faster-mac-use/bin/fmu --perf 7
/Users/lfan/Project/faster-mac-use/bin/fmu --perf 7 --json
```

Or inside `cua` / `agy-cua`:

```json
{"perfReport": {"days": 7, "format": "markdown"}}
```

## 2. What Each Metric Measures

- **Surfaces (`web:mv3`, `web:playwright`, `app:ax`, `daemon:unix_sock`)**: `p50` / `p95` / `p99` / `max` end-to-end latency per batch.
- **Sub-Phase Breakdown (`timings_ms`)**:
  - `tab_attach`: Tab lookup, `createBrowserTab`, navigation (`domcontentloaded`), and `ensure_runtime`.
  - `bridge_overhead`: Out-of-page IPC serialization (`in_page_wall_ms - totalInPageMs`) across the MV3 WebSocket + `chrome.debugger` or Playwright pipe.
  - `in_page_exec`: DOM action execution (`click`, `fill`, `select`, `eval`, `webmcp`).
  - `in_page_settle`: Post-mutation `waitQuietDom(quietMs, maxWaitMs)` stabilization window.
  - `in_page_snap`: `collectSnapshot` DOM/WeakRef walk + CRC-32 `computeDiff`.
  - `escalation`: Out-of-page boundary steps (`eval_csp` via `Runtime.evaluate`, `setInputFiles`).
  - `ocr`: Canvas Vision OCR (`_sync_canvas_ocr`).
  - `native_ax` / `ipc_overhead` / `daemon_boot`: Native Swift `fmu-ax` Accessibility tree walk vs Python Unix-socket round-trip.
- **Token & Diff Efficiency**:
  - `compact_diff_ratio`: Share of observations served as `= unchanged` or `+/~/-` diffs rather than full snapshots. Target: `>= 70%` on multi-turn workflows.

## 3. High-Leverage Optimization Patterns

1. **`in_page_settle` > 35ms**: Check if noisy sites continuously mutate invisible trackers (`mutationSeq++`), keeping `waitQuietDom` near `maxWaitMs` (100ms). Ignore offscreen/aria-hidden tracker mutations in the `MutationObserver` or tune `quietMs`.
2. **`bridge_overhead` > 25ms**: Check if callers are sending single-op requests instead of batching `steps[]`, or if `runtimeJs` is being re-sent unnecessarily when `window.__fmu` is already initialized on `attachedDebuggers` tabs.
3. **`in_page_snap` > 20ms on large SPAs**: Profile `collectSnapshot` candidate selectors (`querySelectorAll`) and prune hidden subtrees early before `getComputedStyle` layout reads.
4. **Recurring `eval_csp` escalations on specific domains**: Cache strict-CSP domains per tab session after the first `EvalError` so subsequent `op: "eval"` steps route directly to CDP `Runtime.evaluate` without an extra round-trip.
5. **Recurring `top_errors`**: Turn field selector/editor/permission edge cases into targeted unit tests in `tests/test_fmu.py` (`BenchWebServer` + Node MV3 harness).

## 4. Verification & Sync Checklist After Any Optimization

1. Run the full test suite (`PYTHONPATH=src pytest -q` in `/Users/lfan/Project/faster-mac-use`) — synthetic unit tests automatically isolate `FMU_LOG_DIR` so `~/.fmu/logs/` stays pure field data.
2. Sync updated runtime files to `/Users/lfan/Project/agent/scripts/fmu/` (`cp src/fmu/*.py /Users/lfan/Project/agent/scripts/fmu/` and `extension/*` if touched), restart the warm daemon (`./bin/fmu --shutdown && ./bin/fmu '{"bridgeStatus": true}'`), and hot-reload the MV3 extension (`./bin/fmu '{"reloadExtension": true}'`) if `extension/` changed.
3. Run `/Users/lfan/Project/agent/scripts/harness-sync.py snapshot` + `verify`, and write a concise summary of measured before/after latency & token deltas to `/Users/lfan/Project/faster-mac-use/reports/perf/latest.md`.
