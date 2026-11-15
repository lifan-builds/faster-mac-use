## Project guidance

- Run `python3 tests/test_fmu.py` for unit and `BackgroundIntrusionGuard` regression checks (`tests/` has no `__init__.py`; invoke directly rather than `python3 -m unittest tests.test_fmu`).
- After changing `src/fmu/` or `extension/`, sync updated files to `/Users/lfan/Project/agent/scripts/fmu/`, reload the MV3 extension (`./bin/fmu '{"reloadExtension": true}'`) and warm daemon (`./bin/fmu --shutdown`), and confirm `./bin/fmu '{"code": "return await cua.getBridgeStatus();"}'` reports the expected `version` with `connected=true`.
- Do not rely solely on synthetic `BenchWebServer` fixtures and mocked MV3 harnesses: run a quick non-destructive live smoke check via `agy-cua` over the real Chrome MV3 bridge against real pages relevant to the change (strict CSP, cross-origin iframes, SPA cards/navigation), and close any temporary tabs via `await tab.close()` when done.
- When reviewing or optimizing performance, inspect privacy-safe sub-phase latencies and token metrics in `~/.fmu/logs/perf-*.jsonl` via `./bin/fmu --perf 7` (or `--json`), follow `PERF_REVIEW_PLAYBOOK.md`, and sync fixes back to `/Users/lfan/Project/agent/scripts/fmu/`.
- Never read or probe Chrome's default-profile `DevToolsActivePort`.
