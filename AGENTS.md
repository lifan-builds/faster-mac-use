## Project guidance

- Run `python3 tests/test_fmu.py` directly (`tests/` has no `__init__.py`).
- After editing `src/fmu/` or `extension/`, copy updated files to `/Users/lfan/Project/agent/scripts/fmu/`, reload the MV3 extension (`./bin/fmu '{"reloadExtension": true}'`) and warm daemon (`./bin/fmu --shutdown`), and confirm `./bin/fmu '{"code": "return await cua.getBridgeStatus();"}'` reports `connected=true`.
- Inspect field latency/token telemetry via `./bin/fmu --perf 7` (`~/.fmu/logs/perf-*.jsonl`; see `PERF_REVIEW_PLAYBOOK.md`).
