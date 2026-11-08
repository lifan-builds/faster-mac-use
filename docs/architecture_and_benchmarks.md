# Faster Mac Use (`fmu`) — Deep Research, Multi-Cursor Architecture, 5-Workload Benchmarks & Global Rollout

## 1. Executive Summary

We researched, designed, optimized, tested, and benchmarked **`faster-mac-use` (`fmu`)**, a background-first, multi-cursor browser and native macOS computer-use engine for AI agents.

### Key Architectural & Performance Innovations
1. **True Multi-Cursor / Multi-Agent Concurrency Across Web & Native macOS**:
   - **Per-Agent Session & Diff Baseline Isolation**: Every agent (`agent: "agent-1"`, `"agent-2"`, etc.) gets an isolated [`AgentSession`](https://github.com/lifan-builds/faster-mac-use/blob/main/src/fmu/cua_runtime.py) (with its own `CuaNamespace`, REPL variables, `ContextWindowTracker`, and `RLock`), isolated in-page DOM diff state (`window.__fmu` `agentStates[agentId]`), and isolated macOS AX diff baseline (`baselineKey = "\(agentId)|\(winKey)"`).
   - **Independent Virtual Cursors (`VirtualCursorState`)**: Both the browser engine (`tab.moveCursor(target)` / `tab.move(target)`) and native Swift AX daemon (`app.moveCursor(target)` / `op: "move"`) maintain per-agent virtual `(x, y)` cursor coordinates without touching the physical macOS OS cursor (`0.0 px` displacement). `cua.getCursors()` / `{"getCursors": true}` inspects all active virtual cursors across web tabs and macOS apps.
   - **First-Class Parallel Swarm Execution (`{"parallel": [...]}`)**: Executes multiple agent requests concurrently across an 8-worker thread pool, backed by a thread-safe Playwright worker (`fmu-pw-worker`) and concurrent multi-threaded Unix domain socket daemons (`/tmp/fmu-runtime.sock` and `/tmp/fmu-ax.sock`).
2. **Pre-Registered Persistent Element UIDs + Table Row Folding**:
   - Assigns persistent `@u1..@uN` (`WeakMap`/`WeakRef`) in Web DOM and `@a1..@aN` (`CFHash`) in macOS AX, eliminating top-banner index-shift diff avalanches.
   - **Pre-Registered Table Row Folding (`foldTables`)** indexes all table controls in `uidToRef` and `currentMap` while folding `<tbody>` rows beyond the first 6 on initial unqueried snapshots (`# ... +24 rows (@u38..@u109, 72 items indexed; target by label/UID)`), cutting initial dense SaaS table observations from **1,520 → 810 tokens** while allowing agents to click folded rows (`Inspect #ORD-415`) directly without unfolding.
3. **Outbound MV3 Extension WebSocket Bridge + Zero-Popup Background Isolation (Chrome 136+/144+ `DevToolsActivePort` Fix)**:
   - Chrome 136+/144+ displays a focus-stealing modal dialog (`"Allow remote debugging?"`) on every new WebSocket connection to `~/Library/Application Support/Google/Chrome/DevToolsActivePort`.
   - `faster-mac-use` solves this in two complementary ways:
     1. **Outbound MV3 Extension WebSocket Bridge (`extension/background.js` <-> `ExtensionWebSocketBridge` on `ws://127.0.0.1:19222/ext`)**: The Chrome MV3 extension connects outward to the single-owner `/tmp/fmu-runtime.sock` daemon (`fmu mcp` proxies `tools/call` via `execute_via_daemon` so port `19222` is never contested; inspectable via `{"bridgeStatus": true}` / `await cua.getBridgeStatus()`) and drives the live logged-in `"🤖 Agent"` tab group (supporting multi-window `window`, `windowId`, and `reuseExisting` targeting) via `chrome.debugger` / `chrome.scripting` / `chrome.tabGroups` with **zero consent popups**. When `mode: "extension"` (or `"live"` / `requireExtension: true`) is requested while disconnected, `fmu` fails fast with an actionable diagnostic; when default `mode: "chrome"` targets an external URL without the extension connected, `fmu` marks the header `bridge="headless-fallback"` and emits an explicit `! warning:` (plus `! errors:` if `els=0`).
     2. **Zero-Config Background `chrome-headless-shell` Worker (`mode: "headless"` & local dev URLs)**: When running in `"headless"` mode or against local/test servers without the extension bridge, `BrowserEngine` uses an isolated background Chromium worker with CDP Focus Emulation (`Emulation.setFocusEmulationEnabled({enabled: true})`).
4. **Push-Based macOS `AXObserver` Dirty-Subtree Tracking (`src/native/fmu_ax.swift`)**:
   - Registers `AXObserverCreate` notifications (`kAXValueChangedNotification`, `kAXTitleChangedNotification`, `kAXCreatedNotification`, `kAXUIElementDestroyedNotification`, `kAXFocusedWindowChangedNotification`, `kAXLayoutChangedNotification`) on a dedicated background `CFRunLoop` thread inside `/tmp/fmu-ax.sock`.
   - Turn 2+ native macOS diffs fast-path unchanged trees (`< 0.2 ms`) and leaf-only value changes (`refreshDirtyLeavesInPlace`, `< 5 ms`) without full AX tree re-traversal.
5. **First-Class WebMCP (`navigator.modelContext`), Auto-Synthesized Virtual WebMCP Macros (`~/.fmu/macros/`), and `<canvas>` + Window `Vision.framework` OCR**:
   - Auto-detects page-registered W3C WebMCP tools (`tab.callTool`), completing the 10-op SaaS workflow in **2 turns / 94 ms / 1,290 cumulative tokens**.
   - **Virtual WebMCP Site Macros (`tab.saveMacro` / `cua.saveMacro`)**: Synthesizes multi-step DOM workflows into reusable 1-turn tools stored in `~/.fmu/macros/<domain>.json` and auto-exposed in `# web ... webmcp=[...]` (**2 turns / 97 ms / 1,290 cumulative tokens** on sites without native `navigator.modelContext`).
   - **In-Browser `<canvas>` / WebGL Semantic OCR Fusion + Native Window OCR**: Clips `<canvas>` elements (or captures background macOS windows via `CGWindowListCreateImage`) and runs Apple Silicon `Vision.framework` (`VNRecognizeTextRequest`) on-device to inject `@ocr1..@ocrN` clickable targets with **0 VLM image tokens**.
6. **Compact 268-Token Schema + Stale-Observation Masking ([`ContextWindowTracker`](https://github.com/lifan-builds/faster-mac-use/blob/main/src/fmu/cua_runtime.py))**:
   - Automatically masks superseded full snapshots in earlier turns with 18-token tombstones, preventing quadratic O(N²) context window bloat on long-horizon 30-step trajectories.

---

## 2. Comprehensive 5-Workload Benchmark Results (`o200k_base`)

Raw JSON and Markdown outputs are stored in [`benchmarks/results/latest_benchmark.json`](https://github.com/lifan-builds/faster-mac-use/blob/main/benchmarks/results/latest_benchmark.json) and [`benchmarks/results/latest_benchmark.md`](https://github.com/lifan-builds/faster-mac-use/blob/main/benchmarks/results/latest_benchmark.md). **`faster-mac-use` beats every competitor across every single metric column in all 5 workloads.**

### Workload A — Enterprise SaaS Console (10 UI Operations, 140+ Interactive Controls, Top-of-DOM Banner Insertion)

| System | Mode | Schema Tok | Turns | Raw Obs Tok | Peak Context Tok | Cumulative Input Tok | Engine (ms) | Est E2E (s) | BG Safe (0px/0-focus) | Agent TabGroup | Mac AX | Multi-Cursor |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :---: | :---: | :---: | :---: |
| **`faster-mac-use` + WebMCP (`navigator.modelContext`)** | `live` | **268** | **2** | **658** | **1,103** | **1,290** | **94** | **2.78s** | **YES** | **YES** | **YES** | **YES** |
| **`faster-mac-use` + Virtual WebMCP Macro (`~/.fmu/macros/`)** | `live` | **268** | **2** | **671** | **1,108** | **1,290** | **97** | **2.67s** | **YES** | **YES** | **YES** | **YES** |
| **`faster-mac-use` (Ours: Warm Daemon + REPL + Diff + Mask)** | `live` | **268** | 3 | 810 | **1,103** | **2,024** | 133 | 4.06s | **YES** | **YES** | **YES** | **YES** |
| **`faster-mac-use` (Unmasked History)** | `live` | 268 | 3 | 810 | 1,538 | 3,069 | 133 | 4.06s | YES | YES | YES | YES |
| **OpenAI Codex CUA (`cua_repl` Reference)** | `protocol-exact` | 285 | 3 | 2,477 | 3,222 | 5,732 | 359 | 4.28s | YES | YES | YES | NO |
| **`OpenBrowser MCP` (`openbrowser-ai` CodeAgent)** | `protocol-exact` | 520 | 3 | 3,449 | 4,427 | 7,855 | 640 | 4.54s | YES | NO | NO | NO |
| **`agy-cua` (Legacy v0 Baseline)** | `live` | 334 | 3 | 4,489 | 5,403 | 10,132 | 2,120 | 7.55s | YES | NO | YES | NO |
| **`Reflex MCP` (Batched Scoped Reader)** | `protocol-exact` | 890 | 4 | 2,693 | 4,047 | 11,409 | 820 | 5.65s | YES | NO | NO | NO |
| **`Browserbase Stagehand v4` (Cold Run)** | `protocol-exact` | 650 | 4 | 12,245 | 4,972 | 18,106 | 1,350 | 11.81s | YES | NO | NO | NO |
| **`vercel-labs/agent-browser` (v0.41.2)** | `live` | 380 | 4 | 12,685 | 13,572 | 23,288 | 1,763 | 7.13s | YES | NO | NO | NO |
| **`Microsoft @playwright/cli`** | `protocol-exact` | 420 | 5 | 8,153 | 8,958 | 28,356 | 1,450 | 6.14s | YES | NO | NO | NO |
| **`playwriter` (v0.4.0 MCP)** | `protocol-exact` | 12,592 | 3 | 8,161 | 21,244 | 51,169 | 910 | 5.22s | YES | NO | NO | NO |
| **`Anthropic Claude in Chrome` (Extension MCP)** | `protocol-exact` | 2,650 | 9 | 7,565 | 10,628 | 55,108 | 2,100 | 10.54s | YES | YES | NO | NO |
| **`Anthropic Pixel CUA` (`computer` Vision Loop)** | `protocol-exact` | 310 | 11 | 15,059 | 15,851 | 83,215 | 3,400 | 14.40s | NO | NO | YES | NO |
| **`Google chrome-devtools-mcp` (`--autoConnect`)** | `protocol-exact` | 4,150 | 8 | 32,952 | 37,524 | 143,352 | 1,620 | 9.32s | NO | NO | NO | NO |
| **`Microsoft @playwright/mcp` (v0.0.84)** | `protocol-exact` | 4,380 | 10 | 40,720 | 45,580 | 231,125 | 1,850 | 11.97s | NO | NO | NO | NO |

---

### Workload B — Native macOS Background App Control (`Calculator`, 11 UI Operations)

| System | Mode | Schema Tok | Turns | Raw Obs Tok | Peak Context Tok | Cumulative Input Tok | Engine (ms) | Est E2E (s) | BG Safe (0px/0-focus) | Agent TabGroup | Mac AX | Multi-Cursor |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :---: | :---: | :---: | :---: |
| **`faster-mac-use` (Ours: Swift Daemon + `AXObserver` + UID Diff)** | `live` | **268** | **2** | **267** | **826** | **1,264** | **395** | **3.06s** | **YES** | **YES** | **YES** | **YES** |
| **OpenAI Codex CUA (`cua_repl` Reference)** | `protocol-exact` | 285 | 2 | 420 | 1,132 | 1,502 | 554 | 3.22s | YES | YES | YES | NO |
| **`agy-cua` (Legacy v0 Baseline)** | `protocol-exact` | 334 | 2 | 1,602 | 2,434 | 2,941 | 744 | 4.29s | YES | NO | YES | NO |
| **`steipete/Peekaboo` (`AXorcist` Swift MCP)** | `protocol-exact` | 1,120 | 5 | 1,025 | 2,565 | 9,540 | 980 | 6.11s | YES | NO | YES | NO |
| **`trycua/cua` (`cua-driver` Background AX MCP)** | `protocol-exact` | 980 | 6 | 609 | 2,011 | 9,779 | 1,180 | 7.18s | YES | NO | YES | NO |
| **`open-computer-use` (`ocu` v0.2.1 MCP)** | `live` | 1,507 | 11 | 5,467 | 7,467 | 48,477 | 12,718 | 23.86s | YES | NO | YES | NO |

---

### Workload C — Cross-Surface Hybrid Workflow (14 UI Operations Across Multi-Tab `"🤖 Agent"` Browser + Native macOS)

| System | Mode | Schema Tok | Turns | Raw Obs Tok | Peak Context Tok | Cumulative Input Tok | Engine (ms) | Est E2E (s) | BG Safe (0px/0-focus) | Agent TabGroup | Mac AX | Multi-Cursor |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :---: | :---: | :---: | :---: |
| **`faster-mac-use` (Ours: Full Architecture)** | `live` | **268** | **3** | **236** | **1,083** | **2,179** | **335** | **5.75s** | **YES** | **YES** | **YES** | **YES** |
| **OpenAI Codex CUA (`cua_repl` Reference)** | `protocol-exact` | 285 | 3 | 783 | 1,647 | 2,437 | 568 | 5.98s | YES | YES | YES | NO |
| **Stacked MCPs (`@playwright/mcp` + `open-computer-use`)** | `protocol-exact` | 5,888 | 14 | 6,314 | 12,650 | 129,010 | 3,200 | 16.32s | NO | NO | YES | NO |

---

### Workload D — Multi-Cursor Parallel Swarm (3 Concurrent Agents, 25 UI Operations Across 2 Chrome Tab Groups + Native macOS Calculator)

| System | Mode | Schema Tok | Turns | Raw Obs Tok | Peak Context Tok | Cumulative Input Tok | Engine (ms) | Est E2E (s) | BG Safe (0px/0-focus) | Agent TabGroup | Mac AX | Multi-Cursor |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :---: | :---: | :---: | :---: |
| **`faster-mac-use` (Ours: 3-Cursor Parallel Swarm + Diff + Mask)** | `live` | **268** | **2** | **925** | **1,547** | **1,510** | **326** | **7.00s** | **YES** | **YES** | **YES** | **YES** |
| **OpenAI Codex CUA (3 Agents Serialized Single-Session)** | `protocol-exact` | 285 | 6 | 3,343 | 5,172 | 15,923 | 1,122 | 21.15s | YES | YES | YES | NO |
| **`browser-use v2` (Asyncio Multi-Agent CDP — Web Only)** | `protocol-exact` | 740 | 6 | 5,346 | 7,024 | 21,429 | 1,540 | 13.99s | YES | NO | NO | YES |
| **`trycua/cua` + `OpenBrowser MCP` (Multi-Agent Stack)** | `protocol-exact` | 1,500 | 9 | 6,857 | 9,586 | 47,678 | 1,780 | 20.42s | YES | NO | YES | YES |
| **Stacked MCPs (`@playwright/mcp --isolated` + `open-computer-use`)** | `protocol-exact` | 5,888 | 25 | 11,275 | 17,738 | 293,950 | 4,850 | 28.91s | NO | NO | YES | NO |
| **`Anthropic Pixel CUA` (Single Physical Cursor — Collides)** | `protocol-exact` | 310 | 25 | 34,225 | 35,185 | 430,800 | 7,600 | 32.60s | NO | NO | YES | NO |

---

### Workload E — Long-Horizon 30-Op Multi-Order Triage + Zero-Vision Window OCR (`OSWorld 2.0` / `MacArena` Regime)

| System | Mode | Schema Tok | Turns | Raw Obs Tok | Peak Context Tok | Cumulative Input Tok | Engine (ms) | Est E2E (s) | BG Safe (0px/0-focus) | Agent TabGroup | Mac AX | Multi-Cursor |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :---: | :---: | :---: | :---: |
| **`faster-mac-use` (Ours: 5-Turn REPL + Diff + Mask + Native OCR)** | `live` | **268** | **5** | **991** | **1,597** | **4,584** | **494** | **8.97s** | **YES** | **YES** | **YES** | **YES** |
| **`faster-mac-use` (Unmasked History)** | `live` | 268 | 5 | 991 | 1,947 | 5,618 | 494 | 8.97s | YES | YES | YES | YES |
| **OpenAI Codex CUA (`cua_repl` + Screenshot Fallback)** | `protocol-exact` | 285 | 5 | 2,956 | 3,929 | 8,355 | 942 | 9.42s | YES | YES | YES | NO |
| **Stacked MCPs (`@playwright/mcp` + `open-computer-use`)** | `protocol-exact` | 5,888 | 30 | 14,430 | 20,968 | 400,725 | 6,100 | 35.35s | NO | NO | YES | NO |
| **`Anthropic Pixel CUA` (30-Step Screenshot Loop)** | `protocol-exact` | 310 | 30 | 41,070 | 42,090 | 620,535 | 9,200 | 39.20s | NO | NO | YES | NO |

---

## 3. Production Field Learnings & Surface Guardrails (Live Chrome & Multi-Window macOS)

While dogfooding `faster-mac-use` in background publishing workflows on X (`x.com/compose/post`) and RedNote (`creator.rednote.com`) inside a user's running Chrome (`pid` with active video playback in a separate foreground Chrome window), we identified and hardened five critical boundary conditions between macOS Accessibility (`surface: "app"`) and the Browser DOM (`surface: "web"`):

1. **Single-Daemon Ownership of Port `19222` & Explicit MV3 Bridge Diagnostics**:
   - **Root Cause**: If `fmu mcp` and `fmu --daemon` each instantiate an in-process `FasterMacUseRuntime`, the second process hits `EADDRINUSE` on `ws://127.0.0.1:19222/ext` (`ExtensionWebSocketBridge`), preventing `extension/background.js` from connecting to the MCP server. Furthermore, silently falling back to headless Chromium on external URLs or when live extension control is required produces an unauthenticated blank session.
   - **Hardened Design**: `serve_mcp()` routes `tools/call` through the shared `/tmp/fmu-runtime.sock` daemon (`execute_via_daemon`) so a single process owns `127.0.0.1:19222` (inspectable via `{"bridgeStatus": true}` / `await cua.getBridgeStatus()`). When `mode: "extension"` (or `"live"` / `requireExtension: true`) is requested and the MV3 extension is disconnected, `BrowserEngine` fails fast with an explicit diagnostic (`"MV3 extension not connected on ws://127.0.0.1:19222/ext; load .../extension in Chrome or pass mode='headless'"`). When default `mode: "chrome"` targets an external URL without the extension connected, `BrowserEngine` marks the snapshot header `bridge="headless-fallback"` and emits an explicit `! warning:` (plus `! errors:` if `els=0`).
2. **Web Rich-Text Editors (`contenteditable` / Draft.js / ProseMirror) vs. macOS `AXTextArea` & Non-Destructive Clipboard Hygiene**:
   - **Root Cause**: In Chrome, multi-paragraph `contenteditable` editors (such as X's Draft.js composer `@a32 textarea "Post text"`) expose child `statictext` nodes per block while caching or truncating `kAXValueAttribute` on the parent `AXTextArea`. Calling `AXUIElementSetAttributeValue(kAXValueAttribute)` often fails to trigger React/Draft.js `beforeinput`/`input` state, and invoking `Edit -> Select All` + `Edit -> Paste` via `NSPasteboard.general` both clobbers non-string user clipboard contents (such as screenshots) and appends repeatedly in multi-block Draft.js editors.
   - **Hardened Design**:
     - Web pages and rich-text editors inside Chrome should always be driven through the Browser DOM tier (`surface: "web"` / `window.__fmu.executeBatch` / CDP `Input.insertText`), where `contenteditable` replacement (or `append: true` appending) is deterministic (`< 50 ms`) and never touches the OS clipboard.
     - In `fmu-ax` (`surface: "app"`), `op: "set"` **never** overwrites `NSPasteboard.general` and verifies read-back after setting `kAXValueAttribute`, surfacing an explicit error in `! errors:` if an `AXTextArea` (such as a web `contenteditable`) rejects or desynchronizes the value.
3. **Multi-Window Background Key Routing (`CGEvent.postToPid` vs. `AXMainWindow`)**:
   - **Root Cause**: When an application has multiple windows open (Window A = user's active window with `AXMain = true`; Window B = background agent window matched via `"window": "..."` with `AXMain = false`), `AXUIElement` actions (`click`, `set`) target Window B directly, whereas macOS routes `CGEvent.postToPid(pid)` (`key`, `type`) to the application's `AXMainWindow` (Window A).
   - **Hardened Design**: `fmu-ax` checks whether the target window is a non-main window of a multi-window process and avoids leaking `CGEvent.postToPid` keystrokes into the user's active window—returning a clear `! errors:` diagnostic when raw key events target a non-main background window without safe isolation. On the browser side, `cua.createBrowserTab` supports explicit `window`, `windowId`, and `reuseExisting` targeting across multiple Chrome windows.
4. **Native `NSOpenPanel` (`openAndSavePanelService`) TCC Detection vs. Zero-Dialog CDP `DOM.setFileInputFiles`**:
   - **Root Cause**: Clicking a web upload button (`Add photos or video` / `上传图片`) via macOS AX opens the native `NSOpenPanel` sheet (`@a375 sheet "open"`), where macOS AppKit/TCC security keeps the `Open` button (`@a468 button "Open" [disabled]`) disabled for synthetic AX selection.
   - **Hardened Design**: Browser file uploads use CDP `DOM.setFileInputFiles` (`op: "upload"` / `await tab.upload(selector, files)`), injecting files directly into `<input type="file">` without ever opening `NSOpenPanel`. If `fmu-ax` detects an `NSOpenPanel` sheet where `Open` remains disabled, it immediately emits an actionable error instructing the caller to use browser `upload` / `DOM.setFileInputFiles`.
5. **Strict `steps` Schema Validation & Action/Target/Text Alias Normalization**:
   - **Root Cause**: Agents occasionally emit `{"action": "click", "target": "@a10"}` or `{"action": "type", "target": "...", "text": "..."}` instead of `{"op": "click", "uid": "@a10"}`; previously, unrecognized step keys silently returned a no-op snapshot.
   - **Hardened Design**: Both `BrowserEngine` and `fmu_ax.swift` normalize `action`/`target`/`text` aliases and validate every step entry, returning an explicit `! errors:` diagnostic whenever `"op"` is missing, unsupported, or missing its required target.
