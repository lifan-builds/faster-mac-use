# Go-to-Market, Broader Market Comparison & Positioning Brief

**Working position, November 2026.** `faster-mac-use` (`fmu`) is a **background-first, multi-cursor browser and native macOS computer-use engine for AI agents**. It gives coding and desktop agents (Claude Code, OpenAI Codex CLI, Cursor, Antigravity, and custom MCP hosts) a single 268-token tool that drives unfocused Chrome `"🤖 Agent"` tab groups and native macOS applications in parallel—without moving the physical mouse cursor (`0.0 px`), stealing window focus (`0` frontmost-app switches), popping Chrome 136+/144+ remote-debugging modals, or flooding the model's context window with full ARIA trees and screenshots.

The launch message is: **“Background computer use for macOS that never touches your cursor—and uses 10× to 114× fewer tokens.”**

---

## 1. Audience & Concrete Problem

**Who has the problem:**
- Developers and technical builders on macOS who run local coding agents (Claude Code, Codex CLI, Cursor, Antigravity) alongside their own active work.
- Agent framework authors building long-horizon web + desktop workflows or multi-agent swarms that need to verify UI changes, scrape authenticated SaaS dashboards, upload files, or operate native macOS apps (Calculator, Finder, Notes, System Settings, Electron apps, native dialogs).

**Why existing computer-use tools break down in daily use:**
1. **Foreground hijacking:** Pixel screenshot loops (`Anthropic computer` tool) and naive `CGEvent` clickers move the physical OS cursor and bring target windows to the front, making the Mac unusable while the agent works.
2. **Chrome 136+/144+ remote-debugging friction:** Chrome 136 blocked `--remote-debugging-port` on default profiles, and Chrome 144+'s `DevToolsActivePort` triggers a focus-stealing `"Allow remote debugging?"` modal on every new WebSocket connection.
3. **Quadratic $O(N^2)$ context window bloat:** Standard MCP servers (`@playwright/mcp`, `chrome-devtools-mcp`) inject 4,150–12,592 tokens of tool schemas upfront and append 3,000–4,000 tokens of full ARIA snapshots on every single click, consuming **143k–231k cumulative input tokens** on a 10-operation SaaS workflow.
4. **Index-shift diff avalanches:** Ref-based CLIs (`agent-browser`, legacy `agy-cua`) renumber sequential `[1]..[200]` indices when a banner inserts at the top of the DOM, turning a 2-element update into a 290-line diff.
5. **Single-cursor serialization:** Even OpenAI Codex's stateful `cua_repl` serializes multi-agent UI tasks through a single session and falls back to 1,500-token screenshots on custom-rendered/canvas macOS windows.

---

## 2. Broader Market Comparison (4 Generations & 15 Measured Configurations)

We benchmarked `faster-mac-use` against 15 representative closed-source and open-source configurations across 5 workloads (`A: Web SaaS`, `B: macOS Native`, `C: Hybrid Web+Mac`, `D: 3-Agent Multi-Cursor Swarm`, `E: Long-Horizon 30-Op + Window OCR`; full data in [`benchmarks/results/latest_benchmark.md`](../benchmarks/results/latest_benchmark.md)).

### Market Landscape by Generation

| Category / System | Type & Surface | What It Proves | Where It Falls Short | Where `faster-mac-use` Wins |
| :--- | :--- | :--- | :--- | :--- |
| **OpenAI Codex CUA (`cua_repl`)** | Closed-source reference (Web + macOS AX) | Stateful JS REPL + 285-tok schema + persistent handles (`tab`, `app`) collapses 10 ops into 3 turns (`5,732` cum. tok on Workload A) | Single-session / single-cursor (serializes 3-agent swarm to 6 turns / `15,848` tok); no pre-registered table folding; uses screenshot fallback on canvas windows | **2.8× lower SaaS tokens** (`2,024` vs `5,732`), **10.5× lower 3-agent swarm tokens** (`1,510` vs `15,848` in 260 ms via parallel virtual cursors), **0-vision-token Apple Silicon OCR**, and **open-source MCP/CLI** for any agent |
| **Anthropic `Claude in Chrome` + Pixel `computer` tool** | Closed-source MV3 Extension + VLM pixel loop | `Claude in Chrome` popularized session-scoped Chrome Tab Groups; `computer` tool handles arbitrary Linux/VM pixels | `Claude in Chrome` takes 9 turns / `55,108` tok on Workload A and cannot control native macOS apps; Pixel CUA takes `83,215`–`620,535` tok and hijacks the physical Mac cursor | **27×–135× lower cumulative tokens**, unified Chrome Tab Groups + native macOS AX in one 268-tok tool, and `0.0 px` background safety |
| **Microsoft `@playwright/mcp` & `@playwright/cli`** | Open-source Web MCP / CLI | Rock-solid Playwright actionability; `@playwright/cli` cuts MCP token bloat by ~8× (`28,356` vs `231,125` tok) | `@playwright/mcp` has a `4,380`-tok schema and burns `231,125` cum. tok (10 turns) on Workload A; neither supports Chrome Tab Groups, native macOS AX, or stateful in-memory REPL loops | **114× lower tokens than `@playwright/mcp`** and **14× lower than `@playwright/cli`**, plus native macOS AX and multi-cursor swarms |
| **Google `chrome-devtools-mcp` (`--autoConnect`)** | Open-source Web CDP MCP | Official Chrome 144+ `DevToolsActivePort` integration and performance trace inspection | `4,150`-tok schema; `143,313` cum. tok on Workload A; triggers Chrome 144+'s focus-stealing remote-debugging consent modal on default profiles | **70× lower cumulative tokens**, zero-popup background focus emulation, and native W3C WebMCP (`navigator.modelContext`) auto-execution |
| **`vercel-labs/agent-browser` (v0.41.2)** | Open-source Rust CLI + Node daemon | Fast Rust CLI dispatch, `@e1` interactive refs, and `diff snapshot` | Sequential ref renumbering causes index-shift diff avalanches on top-banner DOM mutations (`23,288` cum. tok on Workload A); web-only | Persistent `WeakMap` `@u1..@uN` UIDs + pre-registered table folding cut Workload A to `2,024` tok (**11.5× lower**) |
| **`OpenBrowser MCP` & `Reflex MCP`** | Open-source CodeAgent / Scoped Web MCPs | Validate code-mode execution (`OpenBrowser`: `7,855` tok) and scoped DOM reads (`Reflex`: `11,409` tok) | Browser-only; no persistent UID `+/~/-` diffs, no stale-snapshot masking, and no macOS native app/dialog control | **3.9×–5.6× lower tokens**, cross-surface Web + macOS AX in one runtime, and per-agent virtual cursors |
| **`Browserbase Stagehand v4` & `browser-use v2`** | Open-source SDK / Autonomous Web Agent | `Stagehand` caches `observe()` selectors; `browser-use v2` runs async multi-agent CDP loops | `Stagehand` invokes an internal LLM inside `act()`/`extract()` (double inference latency: `11.81s`); `browser-use` owns the outer loop (`21,384` tok on Workload D) and is web-only | Single-inference outer-agent control (**2.77s–4.04s E2E**), **14× lower multi-agent tokens** (`1,510` vs `21,384`), and native macOS support |
| **`trycua/cua` (`cua-driver`) & `steipete/Peekaboo` (`AXorcist`)** | Open-source Swift macOS AX MCPs | Prove background macOS AX + `CGEvent.postToPid` / `ScreenCaptureKit` works without moving the hardware cursor | Multi-tool MCP schemas (`980`–`1,120` tok) require 5–6 turns (`9,540`–`9,779` cum. tok) on Workload B (`Calculator`); no stateful REPL or integrated browser engine | **7.5×–7.7× lower native macOS tokens** (`1,264` tok in 2 turns / 94 ms) via warm `/tmp/fmu-ax.sock` daemon + `@a1` UID diffs |
| **`open-computer-use` (`ocu` v0.2.1)** | Open-source macOS MCP | Accessible entry point for macOS tool calls | `1,507`-tok schema, 11 turns, `48,477` cum. tok, and `2,694 ms` engine time on Workload B | **38× lower tokens** and **28× faster engine execution** (`94 ms` vs `2,694 ms`) |

---

## 3. Where We Win: Four Defensible Wedges

1. **The Only Unified Multi-Cursor Engine Across Web + Native macOS (`{"parallel": [...]}`)**
   - Multiple subagents (`agent-1`, `agent-2`, `agent-3`) can simultaneously operate separate Chrome `"🤖 Agent"` tab groups and background macOS apps with independent virtual `(x, y)` cursors, isolated `+/~/-` diff baselines, and zero physical cursor drift (`0.0 px`).
2. **Order-of-Magnitude Token & Context Efficiency (Verified on `o200k_base`)**
   - Combines a **268-token schema**, **stateful `cua_repl` batching**, **persistent `@u1`/`@a1` UIDs**, **pre-registered table row folding** (indexing all rows so agents can click folded rows directly without unfolding), and **stale-observation masking** (`ContextWindowTracker` 18-token tombstones).
   - On a 30-operation workflow plus Window OCR (Workload E), peak context stays at **1,341 tokens** and cumulative tokens at **4,581**—compared to **400,725** for stacked MCPs and **620,535** for pixel CUA.
3. **Hardware-Verified Background Safety (`BackgroundIntrusionGuard`)**
   - Every benchmark run is audited at 50Hz via CoreGraphics (`CGEventGetLocation` + `CGEventSourceSecondsSinceLastEventType`) and `lsappinfo front` to guarantee `0.0 px` cursor movement and `0` focus changes, plus background CDP file uploads (`DOM.setFileInputFiles`) that bypass `NSOpenPanel`.
4. **Forward-Compatible with W3C WebMCP (`navigator.modelContext`) & Apple Silicon `Vision.framework`**
   - Automatically detects and calls page-registered WebMCP tools (`79 ms`, `1,290` cumulative tokens on Workload A) and uses on-device Apple Silicon `VNRecognizeTextRequest` for zero-vision-token background window OCR (`<70 ms`).

### Material Limitations (State Plainly Alongside Claims)

- **macOS-first native tier:** The compiled Swift accessibility + Vision OCR daemon (`src/native/fmu_ax.swift`) requires macOS (Apple Silicon / macOS 14+ recommended). The browser tier uses standard Playwright/CDP, but `faster-mac-use` is architected specifically for macOS workstations.
- **Chrome Tab Group surface boundary:** Creating and coloring `"🤖 Agent"` tab groups inside a user's live visible Chrome window uses the included Manifest V3 extension (`extension/background.js`) because Chromium's `chrome.tabGroups` API is extension-only. Without the extension loaded, `BrowserEngine` runs out-of-the-box in a background `chrome-headless-shell` worker with CDP Focus Emulation (zero popups, zero focus steal).
- **Minimized windows & Window OCR:** While `AXUIElement` tree walks and `AXPress` actions work on minimized and off-Space macOS windows, macOS unloads backing pixel buffers for windows minimized to the Dock—so `app.ocr()` (`CGWindowListCreateImage`) requires the target window to be unminimized (it can be completely occluded behind other windows).

---

## 4. Initial Promotion Plan (Focused, High-Signal Channels First)

Rather than an all-channel spray, start with developer venues where **reproducible token/latency benchmarks** and **background macOS ergonomics** immediately resonate:

| Priority | Channel | Target Audience & Angle | Primary Proof / Asset | Next Action & Gate |
| :--- | :--- | :--- | :--- | :--- |
| **1 (Done locally)** | **GitHub (`lifan-builds/faster-mac-use`) & Project Site (`docs/index.html`)** | Open-source builders & MCP users looking for a drop-in replacement for `@playwright/mcp` / `agent-browser` / `peekaboo` | Interactive 5-workload benchmark explorer + copy-paste MCP config + reproducible `benchmarks/run_benchmarks.py` | Repo created & pushed; GitHub Pages serves `docs/index.html` |
| **2** | **X (`@LifanBuilds`) — Build-in-Public Thread** | AI engineers using Claude Code, Codex, and Cursor on Mac | Side-by-side comparison table (`2,024` vs `231,125` tokens on SaaS; `1,510` tok on 3-agent parallel swarm) + short clip of 3 virtual cursors driving Chrome + Calculator while typing uninterrupted | Draft 3-post thread for review before posting |
| **3** | **Hacker News (`Show HN`)** | Systems & AI infrastructure engineers who care about token math ($O(N^2)$ context growth), Chrome 144+ CDP quirks, and Swift AX internals | Deep architectural writeup (`docs/architecture_and_benchmarks.md`): how `WeakMap` UIDs + table folding + `AXUIElementCopyMultipleAttributeValues` + stale-snapshot masking work | Provide factual notes; human-authored Show HN submission per HN rules |
| **4** | **Reddit (`r/LocalLLaMA`, `r/ClaudeAI`, `r/mcp`)** | Power users frustrated by `@playwright/mcp` burning their Claude Pro/Max or API quota in 5 minutes | "How we cut browser + macOS MCP token usage from 231k to 2k tokens (with reproducible benchmark script)" | Check subreddit promotion/AI rules & prepare community-specific post for approval |
| **5** | **Substack (`clifan.substack.com`) & DEV (`dev.to/lifanbuilds`)** | Long-form engineering readers | Canonical technical essay: *"Why Computer-Use Agents Burn 100k Tokens (And How Stateful REPLs + Stable UID Diffs Fix It)"* | Draft essay after initial GitHub feedback |
| **6** | **MCP Directories (`awesome-mcp-servers`, Glama, Smithery)** | Developers searching for macOS / browser MCP servers | Single-tool `268`-token MCP schema (`bin/fmu mcp`) | Prepare PR / listing metadata for approval |

### External Action Boundary

Per `/self-promoting`, **no external social posts, forum threads, directory PRs, or newsletter deliveries are executed without explicit review of the exact draft and destination.**
