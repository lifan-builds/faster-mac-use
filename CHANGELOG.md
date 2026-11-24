# Changelog

## 0.5.2 (2026-11-23)

### Fixed
- Agent-spawned child tabs (`target="_blank"` / `window.open`) now inherit `opened: true` via `chrome.tabs.onCreated`, and reattaching to pre-existing unclaimed tabs inside Agent tab groups no longer downgrades them to `opened: false`, so "Close All Agent-Opened Chrome Tabs" closes them reliably.
- `retireOwner` and `closeAgentTabs` now drop non-opened claims on retirement, close legacy unowned Agent-group tabs, ungroup preserved user tabs out of closing Agent groups, and prune dead tab IDs from `fmuAgentTabClaims`.
- Persisted `fmuStrictCspHosts` in `chrome.storage.local` to eliminate cold strict-CSP eval retries across MV3 service-worker restarts.
- Auto-detached idle non-screencast CDP debuggers after 5 minutes on the `fmu-keepalive` alarm to avoid lingering Chrome debugger overhead.
- Extended the cold-boot preferred-port extension reconnect grace window to `1.8s` (while keeping warm/fallback fast-fail at `0.35s`) and increased `createBrowserTab` RPC timeout to `15.0s`.

## 0.5.1 (2026-11-22)

### Fixed
- The menu bar "Close All Agent-Opened Chrome Tabs" action now closes every agent tab. It sends `{"closeAgentTabs": true, "all": true}`, and the extension also closes tabs inside agent-titled tab groups (`Agent`, `Devin`, `Codex`, `Claude`, ...) that it has no claim record for.
- Per-session close (`{"closeAgentTabs": true, "owner": ...}`) matches that session's tab group title, and an unknown `owner` closes nothing instead of falling back to every tab.
- Agent tab claims persist in `chrome.storage.local`, so they survive an extension service-worker restart. When a harness exits, its blank tabs close, claims on tabs that no longer exist are dropped, and its remaining tabs stay claimed but unowned.
- The native `fmu-ax` daemon no longer inherits `FMU_RUNTIME_SOCK`, `FMU_LOG_DIR`, or `FMU_TRACE` from whichever process happened to spawn it.

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
- Package metadata declares the license as an SPDX expression (`License-Expression: AGPL-3.0-only`, with `LICENSE` and `ATTRIBUTION.md` as license files) and builds with setuptools 77+.
- License is now open source `AGPL-3.0-only` with Section 7 attribution terms (`LICENSE`, `ATTRIBUTION.md`). `fmu --version` and MCP `serverInfo.title` print the attribution. Contributions need a one-time CLA (`CLA.md`).
- README leads with a one-command `uvx` quickstart, a `uvx`-based MCP config, and a Proof section.

### Fixed
- Narrower exception handlers and a fail-closed cursor probe.
- `reuseExisting` now rebinds existing sessions.
- The native `fmu-ax` daemon is replaced whenever a different binary started it (tracked in `/tmp/fmu-ax.sock.bin`). Previously, a daemon left running by another install, such as 0.4.4 via `uvx`, kept serving requests with old behavior after an upgrade.
