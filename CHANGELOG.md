# Changelog

## 0.5.0 (2026-11-22)

First PyPI release since 0.4.4. It also includes the unpublished 0.4.5 work.

### Added
- `fmu replay <trace.jsonl> [-o out.html|out.svg]` (alias `fmu trace-html`) renders a recorded run as a self-contained HTML player or a looping animated SVG. Each frame draws `@u`/`@a` element bounds colored by added, changed, or removed, and a HUD with step latency, engine time, `+/~/-` diff counts, element count, and estimated tokens. Perf logs (`~/.fmu/logs/perf-*.jsonl`) replay as a latency-only timeline.
- `FMU_TRACE=<file>` appends the request, output, and wall-clock ms of every CLI or MCP call to a JSONL trace.
- `"bounds": true` request flag appends `box=x,y,w,h` to web and macOS AX element lines, adds `vp=WxH` (web) or `frame=x,y,w,h` (app) to the header, and carries over to tab/app handles inside REPL `code` requests.
- Per-session agent attribution: the warm daemon identifies the calling harness (Devin, Codex, Claude Code, Antigravity, Gemini, Cursor) via `LOCAL_PEERPID`, so each session gets its own tab namespace, titled and colored Chrome tab group, and scoped cleanup.
- Menu bar and RPC actions to close one session's or all agent-opened Chrome tabs while leaving attached user tabs open.
- Persistent Live View preference (`fmu --live-view on|off`, `~/.fmu/prefs.json`) and vector menu bar / HUD icons.
- Trusted CDP mouse clicks for `isTrusted`-gated targets, `fixed_wait` telemetry, and clearer inline upload errors.

### Changed
- Package metadata declares the license as an SPDX expression (`License-Expression: LicenseRef-lifan-builds-Proprietary`) and builds with setuptools 77+.
- License is now open source `AGPL-3.0-only` with Section 7 attribution terms (`LICENSE`, `ATTRIBUTION.md`). `fmu --version` and MCP `serverInfo.title` print the attribution. Contributions need a one-time CLA (`CLA.md`).
- README leads with a one-command `uvx` quickstart, a `uvx`-based MCP config, and a Proof section.

### Fixed
- Narrower exception handlers and a fail-closed cursor probe.
- `reuseExisting` now rebinds existing sessions.
- The native `fmu-ax` daemon is replaced whenever a different binary started it (tracked in `/tmp/fmu-ax.sock.bin`). Previously, a daemon left running by another install, such as 0.4.4 via `uvx`, kept serving requests with old behavior after an upgrade.
