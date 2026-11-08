# Faster Mac Use (`fmu`)

Background-first, multi-cursor browser and native macOS computer-use engine for AI agents, modeled after and extending OpenAI Codex's `cua_repl` architecture.

- **Website & Interactive Benchmark Explorer:** [https://lifan-builds.github.io/faster-mac-use/](https://lifan-builds.github.io/faster-mac-use/) ([`docs/index.html`](docs/index.html))
- **Architecture & 5-Workload Benchmark Report:** [`docs/architecture_and_benchmarks.md`](docs/architecture_and_benchmarks.md)
- **Market Comparison & Positioning Brief:** [`docs/gtm.md`](docs/gtm.md)

## Why `faster-mac-use`?

Most computer-use tools force a painful choice:
- **Pixel screenshot loops** (`computer` tool) hijack your physical mouse cursor, steal window focus, and burn 40k–80k tokens across 10–30 turns.
- **Standard Accessibility/Playwright MCPs** (`@playwright/mcp`, `chrome-devtools-mcp`) inject 4,000–12,500 tokens of tool schemas upfront and return full ARIA trees on every click.
- **Single-session REPLs** (`cua_repl`) are fast for one agent, but serialize multi-agent workflows and fall back to screenshots on custom-rendered macOS windows.

`faster-mac-use` combines a **268-token single-tool MCP/CLI schema**, a **stateful async `cua_repl` sandbox**, **persistent `@u1..@uN` / `@a1..@aN` element UIDs with pre-registered table folding**, **churn-free `+/~/-` diffs**, **stale-observation masking**, and **true multi-cursor parallel execution** across unfocused Chrome `"🤖 Agent"` tab groups and native macOS Accessibility + Apple Silicon `Vision.framework` OCR — verified at **0.0 px physical cursor movement** and **0 focus changes**.

| Workload | `faster-mac-use` (Cumulative Tokens / Engine Time) | OpenAI Codex `cua_repl` Ref | `@playwright/mcp` / Stacked MCPs | Anthropic Pixel CUA |
| :--- | :--- | :--- | :--- | :--- |
| **A. Enterprise SaaS Console (10 ops)** | **1,290 tok / 81 ms** (WebMCP)<br>**2,024 tok / 118 ms** (DOM Diff) | 5,732 tok / 339 ms | 231,125 tok / 1,850 ms | 83,215 tok / 3,400 ms |
| **B. macOS Native Calculator (11 ops)** | **1,264 tok / 102 ms** | 1,499 tok / 188 ms | 48,488 tok / 3,581 ms (`ocu`) | — |
| **C. Hybrid Web + macOS (14 ops)** | **2,176 tok / 306 ms** | 2,434 tok / 531 ms | 129,010 tok / 3,200 ms | — |
| **D. 3-Agent Multi-Cursor Swarm (25 ops)** | **1,510 tok / 256 ms** (2 turns) | 15,848 tok / 954 ms (6 turns) | 293,950 tok / 4,850 ms | 430,800 tok / 7,600 ms |
| **E. Long-Horizon 30-Op + Window OCR** | **4,581 tok / 443 ms** (0 vision tok) | 8,352 tok / 871 ms | 400,725 tok / 6,100 ms | 620,535 tok / 9,200 ms |

---

## Core Architecture

1. **Tier 1 — Native Browser Engine ([`BrowserEngine`](src/fmu/browser_engine.py))**
   - Drives Chrome in unfocused, per-agent color-coded **`"🤖 Agent"` Tab Groups** (`active: false`) via [`extension/background.js`](extension/background.js) and a thread-safe Playwright worker (`fmu-pw-worker`) that avoids Chrome 136+/144+'s default-profile `DevToolsActivePort` modal popup.
   - Enables per-tab background focus and lifecycle emulation (`Emulation.setFocusEmulationEnabled({enabled: true})` + `Page.setWebLifecycleState({state: "active"})`) so `document.hasFocus() === true`, `requestAnimationFrame` stays at 60fps, and focus-dependent dropdowns work while you work elsewhere.
   - Assigns persistent in-page `WeakMap`/`WeakRef` element identities (`@u1..@uN`) with **Pre-Registered Table Row Folding** (`foldTables`), indexing all rows in `uidToRef` so agents can target folded rows by label or UID while cutting initial dense table snapshots by >2.5x.
   - Emits per-agent compact `+/~/-` UID deltas with CRC-32 unchanged short-circuiting (`= unchanged (crc=...)`) and detects + executes **WebMCP** (`navigator.modelContext`) tools directly (`tab.callTool(name, args)`).
   - Intercepts `<input type="file">` uploads via CDP (`DOM.setFileInputFiles`) in exact step sequence so native macOS `NSOpenPanel` dialogs never steal focus.

2. **Tier 2 — Native macOS Accessibility & Vision OCR Fallback ([`fmu_ax.swift`](src/native/fmu_ax.swift) / [`MacNativeEngine`](src/fmu/mac_engine.py))**
   - Concurrent, thread-safe Unix-domain socket daemon (`/tmp/fmu-ax.sock`, binary `bin/fmu-ax`) keeping a warm 4,000-element `AXUIElement` LRU cache, PID/window cache, per-agent `+/~/-` diff baselines, and per-agent virtual cursors (`VirtualCursorState`) across turns.
   - Batches accessibility attribute fetches with `AXUIElementCopyMultipleAttributeValues` and skips redundant pre-action walks when baseline state is already cached.
   - Lazy accessibility wakeup (`AXManualAccessibility = true` / `AXEnhancedUserInterface = true`) only for target background PIDs.
   - Stable `@a1..@aN` UIDs (`CFHash` + structural fallback), `+/~/-` UID diffs, and zero-focus background actions (`AXPress` → `AXConfirm` → background `CGEvent.postToPid` click fallback, plus 16-char UTF-16 batched `CGEvent.postToPid` keystrokes).
   - Built-in Apple Silicon **`Vision.framework` (`VNRecognizeTextRequest`) + `CGWindowListCreateImage`** OCR fallback (`op: "ocr"` / `app.ocr()`) for GPU/canvas windows with 0 LLM vision tokens.

3. **Tier 3 — Unified Multi-Cursor / Multi-Agent Stateful REPL, Warm Daemon & Context Hygiene ([`FasterMacUseRuntime`](src/fmu/cua_runtime.py))**
   - Compact single-tool schema (**268 tokens**) supporting OpenAI Codex-style stateful `cua_repl` scripts (`cua.createBrowserTab`, `cua.getTab`, `cua.getApp`, `cua.getState`, `cua.getCursors`), 1-trip declarative `steps` batches, and concurrent multi-agent swarms (`{"parallel": [{"agent": "agent-1", "code": "..."}, ...]}`).
   - Per-agent isolated `AgentSession` (`agent` / `cursor` parameter) with independent REPL handle namespaces, independent `ContextWindowTracker` stale-snapshot masking, and independent virtual cursor `(x, y)` coordinates (`tab.moveCursor` / `app.moveCursor`).
   - Concurrent persistent runtime daemon (`/tmp/fmu-runtime.sock`) keeps Playwright browser tabs, `WeakMap` UIDs, and `cua_repl` handles warm across parallel CLI/MCP invocations (~81–118 ms for a 10-action web workflow; ~256 ms for a 3-agent 25-action parallel swarm).
   - [`BackgroundIntrusionGuard`](src/fmu/guard.py) polls CoreGraphics (`CGEventGetLocation` + `CGEventSourceSecondsSinceLastEventType`) and `lsappinfo front` at 50Hz to prove `0.0 px` cursor displacement and `0` automated focus changes.

---

## Quick Start

### 1. Install & Build the Native Swift Daemon
```bash
git clone https://github.com/lifan-builds/faster-mac-use.git
cd faster-mac-use
pip install -e .
swiftc -O src/native/fmu_ax.swift -o bin/fmu-ax
```
*(Note: if `bin/fmu-ax` is not pre-built, `MacNativeEngine` automatically compiles `src/native/fmu_ax.swift` on first run.)*

### 2. Add to Your MCP Client (Claude Code, Codex, Cursor, Antigravity)
```json
{
  "mcpServers": {
    "faster-mac-use": {
      "command": "/path/to/faster-mac-use/bin/fmu",
      "args": ["mcp"]
    }
  }
}
```

### 3. CLI Usage (`./bin/fmu`)
```bash
# Native macOS Calculator in background (zero focus steal)
./bin/fmu '{"surface":"app","app":"Calculator","steps":[{"op":"key","name":"escape"},{"op":"click","name":"7"},{"op":"click","name":"Multiply"},{"op":"click","name":"6"},{"op":"click","name":"Equals"}]}'

# Codex-style stateful cua_repl script
./bin/fmu '{"code":"let tab = await cua.createBrowserTab(\"chrome\", \"https://example.com\", {sessionName: \"🤖 Agent\"});"}'

# Multi-cursor / multi-agent parallel execution across Chrome tabs + native macOS apps
./bin/fmu '{"parallel":[{"agent":"agent-1","code":"let t = await cua.createBrowserTab(\"chrome\", \"https://example.com\"); await t.move(\"More information\");"},{"agent":"agent-2","code":"let a = await cua.getApp(\"Calculator\"); await a.pressKey(\"escape\"); await a.click(\"8\");"}]}'
```

### 4. Run Unit & Integration Tests
```bash
python3 tests/test_fmu.py
```

### 5. Run the 5-Workload Competitor Benchmark Suite
```bash
PYTHONPATH=src python3 benchmarks/run_benchmarks.py
```
Results are written to [`benchmarks/results/latest_benchmark.md`](benchmarks/results/latest_benchmark.md) and [`benchmarks/results/latest_benchmark.json`](benchmarks/results/latest_benchmark.json).

## License

MIT © [Lifan Chen](https://clifan.com)

