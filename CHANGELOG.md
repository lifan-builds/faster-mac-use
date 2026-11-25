# Changelog

## 0.5.5 (2026-11-25)

### Added
- **Chrome extension listing & toggling (`management` permission)**: Added `"management"` permission in `extension/manifest.json`, `listExtensions` / `setExtensionEnabled` RPCs in `extension/background.js` (refusing to disable `chrome.runtime.id` itself so the MV3 bridge can never self-disconnect), `BrowserEngine.list_extensions()` / `BrowserEngine.set_extension_enabled()`, and `cua.listExtensions()` / `cua.setExtensionEnabled(id_or_name, enabled)` with safe unique name/ID lookup and top-level JSON/MCP request support (`{"listExtensions": ...}`, `{"setExtensionEnabled": ...}`).
- **REPL & telemetry ergonomics**: Support coordinate/dict arguments in `TabHandle.click()` / `moveCursor()`, normalize seconds timeouts (`timeout` / `timeout_s` / `timeoutSeconds`) in `waitFor()` and raw JSON steps, and seed `strictCsp` from `_strict_csp_hosts` on `executeBatch` while tagging cached `eval_csp` telemetry as `eval_csp_cached`.

### Fixed
- **Skip Chrome-discarded tabs when reusing in `createBrowserTab`**: `createBrowserTab` (`reuseExisting=true` and `reattach`) now skips `discarded: true` / `status: "unloaded"` Chrome tabs instead of attaching to sleeping tabs that immediately fail in `ensure_runtime()` / `executeBatch()` with `discarded/unloaded by Chrome`.

## 0.5.4 (2026-11-24)

### Fixed
- **OOPIF sibling disambiguation & scroll-into-viewport**: Cross-origin `<iframe>` CDP input (`dispatchCdpInput` / `_dispatch_cdp_input`) now scrolls the target iframe (and scrollable ancestors) into the visible viewport and re-measures its clamped viewport rect via `window.__fmu.prepIframeTarget` before dispatching mouse coordinates, then runs a read-only `probe` pass using `frameIndex` (`window.top[k] === window`) and `document.hasFocus()` to pin the exact Chrome `frameId` (or Playwright `Frame`) for `focus_and_select`, clearing, and verification — eliminating sibling `actual_len=0` verification failures on multi-field hosted tokenizers such as VGS Collect (`js.verygoodvault.com`).
- **Discarded / unresponsive tab fast-fail**: `executeBatch` and `evaluate` in `extension/background.js` now check `tab.discarded` / `tab.status === "unloaded"` and missing tabs upfront and run a bounded 3s debugger/renderer responsiveness probe (`verifiedRendererTabs`) when attaching or resuming idle tabs, failing immediately with an actionable error instead of hanging 18s per RPC attempt.
- **Attach-only `cua.getTab(...)`**: `CuaNamespace.getTab` no longer passes `window=stripped` when `stripped` is a URL/domain and never silently falls back to `createBrowserTab(...)` on miss, raising a clear `RuntimeError` pointing to `cua.listTabs()` or `cua.createBrowserTab(...)` instead of spawning stray tabs.
- **REPL ergonomics**: Support Python control-flow blocks, `TabHandle.id`, line-iterable `listTabs()`, and `sessionLabel` / `session_label` aliases.

## 0.5.3 (2026-11-23)

### Fixed
- Cross-origin `<iframe>` (OOPIF) inputs in both headless/CDP `BrowserEngine` and the MV3 Chrome extension (`background.js`) now route `fill`/`set`/`type`/`press`/`key` through trusted CDP focus + `SelectAll`/`Backspace`/`Delete` clearing + `Input.insertText` with per-character `Input.dispatchKeyEvent` fallback and hash-only (`FNV-1a` + length) frame verification, instead of calling `setNativeValue` on the outer `<iframe>` or falling back to macOS `AX`.
- Hardened `fmu_ax.swift` (`redactSensitivePreview` + formatted PAN digit verification) and `telemetry.sanitize_error()` (`cvv`/`password`/`pin`/`secret`/`token`/`otp` key-value pairs plus Luhn card numbers and bearer/API tokens) so unmasked payment card digits or secrets never leak into step errors or `~/.fmu/logs/perf-*.jsonl`.

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
