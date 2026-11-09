# `faster-mac-use` (`fmu`) — Launch Kit & Channel Copy

> **Confirmation Boundary (`/self-promoting`)**: All external social posts, forum threads, and directory PRs below are staged and ready for review. Nothing has been posted externally.

- **GitHub Repository:** [https://github.com/lifan-builds/faster-mac-use](https://github.com/lifan-builds/faster-mac-use)
- **Interactive Benchmark Site:** [https://clifan.com/faster-mac-use/](https://clifan.com/faster-mac-use/)
- **Visual Showcase Asset:** [`docs/assets/swarm-vs-mcp-demo.svg`](assets/swarm-vs-mcp-demo.svg)

---

## 1. X (`@LifanBuilds`) & RedNote (小红书) — Polished via `post-polisher`

### A. 微调原味版 (Lightly Polished Original)
每次在 Mac 上跑 browser / computer-use agent，最烦的就两件事：要么像截图流 CUA 一样直接抢你鼠标和窗口焦点，电脑根本没法同时用；要么像 `@playwright/mcp` 一样，光 schema 就塞进 4,380 tokens，每次点一下再把整棵 ARIA tree 往上下文里糊，10 步操作直接烧掉 23 万 tokens。

这周把我自己一直在用的底层引擎整理开源了：**`faster-mac-use` (`fmu`)** —— 专门给 macOS 做的后台多光标（multi-cursor）浏览器 + 原生 App 引擎。

几个核心设计：
1. **0.0 px 物理鼠标位移 & 0 抢焦点**：50Hz CoreGraphics + `lsappinfo` 实时审计。支持 Chrome 扩展 WebSocket 反向桥接控制已登录的 `"🤖 Agent"` 标签组（彻底绕开 Chrome 144 `DevToolsActivePort` 每次弹窗确认的坑），也支持无配置后台 `chrome-headless-shell` + CDP Focus Emulation。
2. **单工具 268 tokens + 状态化 `cua_repl` + `+/~/-` 增量 Diff**：DOM 用 `WeakMap` 持久化 `@u1..@uN`，macOS 原生 AX 用 `CFHash` 持久化 `@a1..@aN` + `AXObserver` 推送脏节点（未变化 diff `< 1 ms`）。顶部插 banner 不会触发全表序号雪崩，密集表格自动折叠并预注册索引（不用展开就能直接点折叠行）。
3. **Virtual WebMCP 宏合成 (`~/.fmu/macros/`) + 0 视觉 Token 的 `<canvas>` / 窗口 OCR**：没有原生 `navigator.modelContext` 的网站可以把多步操作录成 1-turn Virtual WebMCP 工具；遇到 `<canvas>` / WebGL 或无 AX 树的窗口，直接调 Apple Silicon 原生 `Vision.framework` (`VNRecognizeTextRequest`) 注入 `@ocr1..@ocrN` 可点击坐标，0 VLM 图片 token。
4. **真正的 Multi-Cursor 并发 Swarm (`{"parallel": [...]}`)**：3 个 subagent 同时在后台跑两个 Chrome 标签组 + macOS 原生计算器，25 步操作 2 个 turn、326 ms 引擎耗时、总共只花 1,510 tokens（对比 `@playwright/mcp` 29.3 万 tokens，OpenAI Codex 单会话串联 1.59 万 tokens）。

代码、15 个系统的 5 组实测 benchmark 脚本都在 GitHub：`https://github.com/lifan-builds/faster-mac-use`

---

### B. X 版本 (`@LifanBuilds`)

#### X 中文版 (Single Long Post / Thread Starter)
在 Mac 上跑 Computer-Use Agent，最痛的两个反人类设计：
1. 截图流每隔几秒抢一次物理鼠标和窗口焦点，Agent 干活时你只能干瞪眼；
2. `@playwright/mcp` 这种标准 MCP 每次点击都把整棵 ARIA 树塞进上下文，10 步 SaaS 操作直接烧掉 231,125 tokens（O(N²) 爆炸）。

开源了 **`faster-mac-use` (`fmu`)** —— 专为 macOS 打造的后台、多光标（Multi-Cursor）浏览器与原生 App 自动化引擎：

- **0.0 px 鼠标偏移 / 0 焦点抢占**：50Hz CoreGraphics 审计。支持 MV3 扩展反向 WebSocket 桥接直连已登录 Chrome `"🤖 Agent"` 标签组（零 Chrome 144 调试弹窗），或免配置后台 Focus Emulation。
- **268-token 极简 Schema + 状态化 `cua_repl`**：`WeakMap` (`@u1`) 与 Swift `AXObserver` (`@a1`, `<1ms` 脏子树快路径) 持久化 UID，配合表格预注册折叠与过期快照墓碑化，10 步 SaaS 仅需 **1,290–2,024 tokens**（省 114 倍）。
- **Virtual WebMCP 宏 + 0 视觉 Token `<canvas>` OCR**：一键把多步轨迹合成为 `tab.callTool` 站点宏；内置 Apple Silicon `Vision.framework` 识别 `<canvas>` 与 GPU 窗口并生成 `@ocr1..@ocrN` 坐标，0 图片 token。
- **3-Cursor 并发 Swarm**：3 个 Agent 后台同时操控多个 Chrome 标签组 + macOS 原生计算器，25 步操作 326ms 跑完，累计仅 **1,510 tokens**。

GitHub + 15 系统交互式 Benchmark：`https://github.com/lifan-builds/faster-mac-use`

#### X 英文版 (3-Post Build-in-Public Launch Thread)

**Post 1/3 (Hook + Core Numbers + Visual Demo)**
Running computer-use agents on a Mac today forces a painful tradeoff:
- Pixel screenshot loops hijack your physical cursor & window focus.
- `@playwright/mcp` dumps full ARIA trees on every click — burning **231,125 tokens** on a 10-step SaaS workflow.

I built and open-sourced **`faster-mac-use` (`fmu`)**: a background-first, multi-cursor browser + native macOS engine.

- **0.0 px** physical cursor drift & **0** focus switches (audited at 50Hz)
- **268-token** single-tool schema + stateful `cua_repl`
- **1,290–2,024 tokens** on the 10-op SaaS benchmark (**114× lower** than `@playwright/mcp`)
- **1,510 tokens / 326 ms** for a 3-agent, 25-op parallel swarm across Chrome `"🤖 Agent"` tab groups + native macOS Calculator

Repo & interactive benchmarks: https://github.com/lifan-builds/faster-mac-use

*(Attach `docs/assets/swarm-vs-mcp-demo.svg` or screen recording)*

**Post 2/3 (How the Architecture Works Under the Hood)**
Why do standard browser/OS MCPs burn 100k+ tokens?

In an N-turn tool loop appending snapshots of size S, cumulative input tokens grow quadratically as ½·N²·S. `fmu` attacks both N and S across 3 tiers:

1. **Outbound MV3 Extension WS Bridge + CDP Focus Emulation**: Drives live logged-in Chrome `"🤖 Agent"` tab groups with zero Chrome 144 `DevToolsActivePort` consent popups, or falls back to background `chrome-headless-shell`.
2. **Persistent `@u1` / `@a1` UIDs + Pre-Registered Table Folding**: Folds dense `<tbody>` rows while indexing every row in `uidToRef` so agents click folded rows without unfolding. Top-of-DOM banners never trigger index-shift diff avalanches.
3. **Push-Based Swift `AXObserver` Daemon (`/tmp/fmu-ax.sock`)**: Tracks `kAXValueChanged` / `kAXCreated` notifications on a background runloop so unchanged/leaf-dirty macOS AX diffs return in `< 1 ms`.
4. **Virtual WebMCP Macros + `<canvas>` Vision OCR**: Synthesize multi-step trajectories into 1-turn `tab.callTool()` macros (`~/.fmu/macros/`), and fuse Apple Silicon `VNRecognizeTextRequest` into `@ocr1..@ocrN` `<canvas>` targets with 0 VLM image tokens.

**Post 3/3 (Reproducible 15-System Benchmark + 1-Line Install)**
Every number in the repo is reproducible via `benchmarks/run_benchmarks.py` (`o200k_base` tokenizer) across 5 workloads:
- Workload A (10-op SaaS): `1,290` tok (WebMCP / Virtual Macro) | `2,024` tok (DOM Diff) vs `231,125` (`@playwright/mcp`)
- Workload B (macOS Calculator 11 ops): `1,264` tok vs `48,477` (`open-computer-use`)
- Workload D (3-Cursor Swarm, 25 ops): `1,510` tok (2 turns) vs `15,923` (Codex single-session) & `293,950` (Stacked MCPs)
- Workload E (30-op Long-Horizon + Window OCR): `4,584` tok (`1,597` peak context via stale-snapshot masking) vs `620,535` (Pixel CUA)

Works with Claude Code, OpenAI Codex CLI, Cursor, and Antigravity:
- Interactive Explorer: https://clifan.com/faster-mac-use/
- GitHub (MIT): https://github.com/lifan-builds/faster-mac-use

---

### C. 小红书防限流版 (Safe for RedNote / Xiaohongshu)

**备选标题（3 选 1）：**
1. 写了套 Mac 后台多光标 Agent 引擎：省 114 倍 Token，还不抢鼠标
2. 为什么 Browser MCP 动不动烧掉 20 万 Token？我开源了套解法
3. 让 3 个 AI 同时在 Mac 后台跑浏览器和原生应用，鼠标 0 偏移

**正文：**
平时在 Mac 上重度用 Claude Code、Codex 和 Cursor 的朋友应该都遇到过这个痛点：
只要让 Agent 去操作浏览器或者桌面应用，体验就非常割裂。
要么是视觉截图流，每隔几秒抓一次全屏、直接把你的物理鼠标抢过去，电脑根本没法同时打字；要么是挂载 Playwright 这类 MCP，还没开始干活光工具定义就占掉几千 tokens，每点一下按钮再把整棵网页节点树塞进上下文，10 步常规后台操作下来，直接烧掉 23 万+ tokens。

把我自己打磨了一段时间的底层方案整理发布出来了，叫 **`faster-mac-use` (`fmu`)**，一套专门针对 macOS 的后台多光标（Multi-Cursor）计算机操控引擎。

分享几个核心工程细节：

1️⃣ **真正的后台执行（0.0 px 鼠标位移 / 0 窗口焦点切换）**
引擎内置了 50Hz 的 CoreGraphics 监听器。不管 Agent 在后台点按 Chrome 里的 `"🤖 Agent"` 专属标签组，还是通过 macOS 原生 `AXPress` / `CGEvent.postToPid` 操作原生 App，你前台的鼠标和当前输入焦点都不会动一下。针对 Chrome 144 烦人的远程调试弹窗，做了一套 MV3 扩展反向 WebSocket 桥接，既能复用已登录态，又不会弹窗打断。

2️⃣ **把 O(N²) 的上下文膨胀压回 1,000+ Tokens 量级**
很多 Agent 慢且贵，是因为回合数 N 和单次快照体积 S 相乘导致上下文指数级膨胀。`fmu` 只暴露一个 268 tokens 的统一工具，内部用状态化 `cua_repl` 把连续操作合并成 1-2 个回合；网页节点用 `WeakMap` 绑定持久 `@u1` ID，macOS 原生组件用 Swift 守护进程 + `AXObserver` 推送脏节点状态（未变化 diff 小于 1ms）。哪怕网页顶部突然弹出横幅，也不会引发全页面序号重排；密集表格还会自动折叠并预建索引，不用展开就能直接点第 25 行。

3️⃣ **Virtual WebMCP 站点宏 + Apple Silicon 零视觉 Token OCR**
对于还没接入 `navigator.modelContext` 的普通网站，可以把 Agent 跑通的多步操作直接保存成 `~/.fmu/macros/` 下的虚拟工具，下次再来直接 1 个回合调用完成。遇到 `<canvas>` 画布或者没有无障碍树的窗口，直接调用芯片原生的 `Vision.framework` 识别文字并映射成 `@ocr1..@ocrN` 坐标，不需要把截图发给大模型烧视觉 Token。

4️⃣ **多光标并发 Swarm（Multi-Cursor）**
支持多个子 Agent 各自带着独立的虚拟光标 `(x, y)` 和独立的 Diff 基线，同时在后台操作不同的浏览器标签页和 Mac 原生应用。实测 3 个 Agent 并发执行 25 步跨端操作，326ms 引擎耗时、2 个回合跑完，累计只消耗 1,510 tokens。

项目已完整开源（MIT 协议，搜索 `lifan-builds/faster-mac-use` 即可找到），里面附带了对比 15 种主流方案的 5 组自动化跑分脚本和交互式看板。欢迎正在折腾本地 Agent 的朋友交流拍砖！

#AI工具 #独立开发 #程序员 #macOS #ClaudeCode #Agent开发 #开源项目 #MCP #效率工具

---

### 修改说明 (Diagnostic Note)
- **保留原味与硬核技术颗粒度**：完整保留了开发者一线的痛点切入（抢鼠标焦点、`@playwright/mcp` 231,125 tokens 的 O(N²) 上下文爆炸、Chrome 144 `DevToolsActivePort` 弹窗）以及具体工程原语（`WeakMap` `@u1`、`AXObserver` `<1ms` 脏子树、Virtual WebMCP 宏、`Vision.framework` `@ocr1`、3-Cursor Swarm `1,510` tokens）。
- **平台适配与防限流处理**：
  - **X 中/英文版**：采用高信息密度的 Build-in-Public 叙事，首条直接亮出可验证的极端对比数据（`2,024` vs `231,125` tokens，`0.0 px` drift），第二条拆解 ½·N²·S 的数学本质与 4 个系统层解法，第三条给出可复现跑分清单与仓库链接。
  - **小红书版**：移除了所有外链 URL 和容易触发硬广判定的导流词（如“快去 Star”、“链接放评论区”），改为自然提及开源仓库名 `lifan-builds/faster-mac-use`，并用清晰的符号分段让移动端阅读更顺畅。

---

## 2. Hacker News (`Show HN`) Submission

- **Title:** `Show HN: Faster-mac-use – Background multi-cursor computer use for macOS (0px drift, 114x fewer tokens)`
- **URL:** `https://github.com/lifan-builds/faster-mac-use`

### First Comment (Author Technical Context)
Hi HN — I built `faster-mac-use` (`fmu`) because running browser and desktop tools from local coding agents (Claude Code, Codex CLI, Cursor) on macOS kept hitting two bottlenecks:

1. **Foreground intrusion:** Pixel screenshot loops (`computer` tool) move the physical mouse cursor and steal window focus, and Chrome 144+'s `DevToolsActivePort` pops a modal `"Allow remote debugging?"` dialog on every new WebSocket connection to the default profile.
2. **Quadratic O(N²) token bloat:** In an N-turn tool loop where each action appends an accessibility snapshot of size S, cumulative billed input tokens scale as ½·N²·S. Standard MCP servers like `@playwright/mcp` inject 4,380 tokens of tool definitions upfront and return ~4,000 tokens of full ARIA trees on every click — burning **231,125 cumulative input tokens** on a 10-operation enterprise SaaS workflow.

`fmu` attacks both problems with a 3-tier engine exposed through a single **268-token MCP/CLI tool**:

- **Stateful `cua_repl` + Multi-Cursor Parallel Swarms (`{"parallel": [...]}`):** Modeled on and extending OpenAI Codex's `cua_repl`, agents batch multi-step JS trajectories (`cua.createBrowserTab`, `cua.getApp`) in 1–3 turns. Multiple subagents (`agent-1`, `agent-2`, `agent-3`) get isolated `AgentSession` namespaces, independent virtual cursor `(x, y)` coordinates, and isolated diff baselines so they can drive Chrome tabs and background macOS apps concurrently.
- **Persistent `@u1` / `@a1` UIDs + Pre-Registered Table Folding:** DOM elements are tracked via `WeakMap`/`WeakRef` (`@u1..@uN`) and macOS AX elements via `CFHash` (`@a1..@aN`). Inserting a banner at the top of the page produces a 1-line `+ @u110` delta instead of renumbering every element below it. Dense `<tbody>` tables fold rows beyond the first 6 on initial snapshot while pre-registering all rows in `uidToRef` so the agent can click a folded row by label or UID without a separate unfold round-trip.
- **Outbound MV3 Extension WebSocket Bridge + Background Focus Emulation:** To control a live logged-in Chrome profile without Chrome 144's `DevToolsActivePort` consent modal, the included MV3 extension connects outward to `/tmp/fmu-runtime.sock`'s single-owner `ws://127.0.0.1:19222/ext` bridge and routes `chrome.debugger` / `chrome.tabGroups` commands into an unfocused `"🤖 Agent"` tab group (failing fast with a setup diagnostic if `mode: "extension"` / `"live"` / `requireExtension: true` is requested without the extension connected, or emitting a `bridge="headless-fallback"` `! warning:` when default `mode: "chrome"` targets an external URL while disconnected). In `mode: "headless"` or on local dev URLs, `fmu` runs in a background `chrome-headless-shell` worker with CDP `Emulation.setFocusEmulationEnabled({enabled: true})` and `DOM.setFileInputFiles` (bypassing macOS `NSOpenPanel` file dialogs where AppKit/TCC disables synthetic AX clicks on `Open`).
- **Push-Based Swift `AXObserver` Daemon (`/tmp/fmu-ax.sock`) & Non-Destructive Hygiene:** A compiled universal2 Swift daemon registers `AXObserverCreate` notifications (`kAXValueChangedNotification`, `kAXCreatedNotification`, `kAXUIElementDestroyedNotification`) on a dedicated `CFRunLoop` thread and batches reads via `AXUIElementCopyMultipleAttributeValues`, returning unchanged or leaf-dirty native macOS diffs in `< 1 ms`. Unlike naive AX wrappers, `fmu-ax` never overwrites `NSPasteboard.general` (protecting user clipboard images/text), verifies `AXTextArea` read-back (detecting when web `contenteditable` / Draft.js editors need the DOM bridge), and guards `CGEvent.postToPid` keystroke routing across multi-window processes.
- **Virtual WebMCP Macros (`~/.fmu/macros/`) + 0-Token `<canvas>` / Window OCR:** Sites with `navigator.modelContext` expose tools directly (`tab.callTool`), and for sites without it, `tab.saveMacro()` synthesizes reusable 1-turn Virtual WebMCP macros. For HTML5 `<canvas>` / WebGL elements and custom-rendered macOS windows, `fmu` clips and runs Apple Silicon `Vision.framework` (`VNRecognizeTextRequest`) on-device to inject clickable `@ocr1..@ocrN` coordinates with 0 VLM image tokens.

Every benchmark in [`benchmarks/run_benchmarks.py`](https://github.com/lifan-builds/faster-mac-use/blob/main/benchmarks/run_benchmarks.py) runs a 50Hz CoreGraphics (`CGEventGetLocation`) + `lsappinfo front` intrusion guard verifying `0.0 px` physical cursor movement and `0` focus changes.

Interactive benchmark explorer: https://clifan.com/faster-mac-use/

Happy to answer any questions about the Swift `AXObserver` daemon, Chrome 144 CDP changes, Draft.js/`NSOpenPanel` AX quirks, or the token math!

---

## 3. Reddit Drafts (`r/ClaudeAI`, `r/mcp`, `r/LocalLLaMA`)

### A. `r/ClaudeAI`
**Title:** How we cut browser + macOS MCP token usage in Claude Code from 231k to 2k tokens without touching the mouse cursor (open-source + reproducible benchmark)

**Body:**
If you use `@playwright/mcp` or `chrome-devtools-mcp` inside Claude Code on a Mac, you've probably noticed two things:
1. A 10-step web task eats 150k–230k input tokens because the MCP server injects ~4,300 tokens of tool schemas every turn and appends a full 4,000-token ARIA snapshot on every single click.
2. If you use desktop/computer-use tools, they hijack your mouse cursor or pop Chrome 144's `"Allow remote debugging?"` modal right over your IDE.

I built **`faster-mac-use` (`fmu`)** (MIT, open-source) to fix both for Claude Code and other MCP clients on macOS:
- **Single 268-token MCP tool (`fmu mcp`)** supporting both stateful JS batches (`code`) and 1-trip declarative `steps`.
- **0.0 px cursor movement & 0 focus theft**: Verified at 50Hz via CoreGraphics. Drives Chrome in unfocused `"🤖 Agent"` tab groups (via an outbound MV3 Extension WebSocket bridge that avoids Chrome 144's `DevToolsActivePort` popup, or a zero-config background headless worker with CDP Focus Emulation) and drives native macOS apps via background `AXPress` / `CGEvent.postToPid`.
- **Persistent `@u1` / `@a1` UIDs + `+/~/-` diffs + Stale-Snapshot Masking**: Cuts a 10-operation SaaS workflow from **231,125 cumulative tokens** (`@playwright/mcp`) down to **2,024 tokens** (or **1,290 tokens** with WebMCP / Virtual WebMCP site macros).
- **3-Cursor Parallel Swarms (`{"parallel": [...]}`)**: Let 3 Claude subagents simultaneously operate 2 Chrome tabs and macOS Calculator in the background in 2 turns (`1,510` tokens total, `326 ms` engine time).
- **0-Vision-Token `<canvas>` & Window OCR**: Uses Apple Silicon `Vision.framework` (`VNRecognizeTextRequest`) to turn `<canvas>` text and GPU windows into clickable `@ocr1..@ocrN` targets without sending screenshots to the model.

**Quick install for Claude Code:**
```bash
git clone https://github.com/lifan-builds/faster-mac-use.git && cd faster-mac-use && pip install -e .
```
Add to `.mcp.json` / `claude_desktop_config.json`:
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

- **GitHub & 5-Workload Benchmark Suite:** https://github.com/lifan-builds/faster-mac-use
- **Interactive Benchmark Explorer:** https://clifan.com/faster-mac-use/

---

### B. `r/mcp`
**Title:** `faster-mac-use` (`fmu`): Single 268-token MCP server unifying Chrome "🤖 Agent" Tab Groups, push-based macOS `AXObserver`, Virtual WebMCP macros, and 3-cursor parallel swarms

**Body:**
Sharing an open-source MCP server and CLI I built for macOS agent workflows: **`faster-mac-use` (`fmu`)** ([GitHub](https://github.com/lifan-builds/faster-mac-use) | [Interactive Benchmarks](https://clifan.com/faster-mac-use/)).

**What it solves for MCP hosts:**
Instead of loading a 4,380-token browser MCP + a 1,508-token macOS MCP (`5,888` tokens of tool schemas before Turn 1 even starts), `fmu` exposes a single **268-token** `fmu` tool that unifies:
1. **Browser Automation (`BrowserEngine`):** Outbound MV3 Extension WebSocket bridge (`ws://127.0.0.1:19222/ext`) for live logged-in `"🤖 Agent"` tab groups with zero Chrome 144 popups + automatic fallback to background `chrome-headless-shell` with CDP Focus Emulation.
2. **Native macOS Automation (`fmu_ax.swift`):** Persistent `/tmp/fmu-ax.sock` daemon with push-based `AXObserver` dirty-subtree tracking (`< 1 ms` unchanged/leaf-dirty diffs), `AXUIElementCopyMultipleAttributeValues` batching, and background `AXPress` / `CGEvent.postToPid`.
3. **Auto-Synthesized Virtual WebMCP Macros (`~/.fmu/macros/`):** Record multi-step DOM trajectories with `tab.saveMacro(name, steps, description)` and auto-expose them in the `# web ... webmcp=[...]` header so future sessions replay them in 1 turn via `tab.callTool()`.
4. **0-Vision-Token `<canvas>` & Window OCR:** Clip-captures HTML5 `<canvas>` elements and macOS windows into Apple Silicon `Vision.framework` (`VNRecognizeTextRequest`), injecting `@ocr1..@ocrN` clickable targets into the text snapshot.
5. **Multi-Cursor Parallel Swarms (`{"parallel": [...]}`):** Concurrent execution across multiple isolated `AgentSession` instances with independent virtual cursors and `+/~/-` diff baselines.

Includes `smithery.yaml`, `glama.json`, Homebrew formula (`Formula/faster-mac-use.rb`), and a 15-system benchmark suite (`benchmarks/run_benchmarks.py`).

---

### C. `r/LocalLLaMA`
**Title:** Keeping 30-step computer-use trajectories under 1,600 peak context tokens on macOS (stateful REPL + persistent UID diffs + Apple Silicon Vision OCR, 0 VLM tokens)

**Body:**
When running local or API-hosted models on computer-use tasks, context length and KV-cache churn are usually the biggest killers:
- VLM screenshot loops add ~1,500 vision tokens per step (30 steps = 42k peak context, 620k cumulative input tokens).
- Standard DOM/accessibility MCPs dump 3k–4k tokens of raw ARIA trees per step.

I open-sourced **`faster-mac-use` (`fmu`)**, a macOS browser + native desktop engine designed specifically to keep peak context tiny (~800–1,600 tokens) and eliminate VLM image tokens completely:
- **268-token tool schema** + stateful JS REPL (`cua_repl`) so a model can emit a loop or multi-step batch in a single generation.
- **Persistent `@u1` (DOM `WeakMap`) and `@a1` (macOS `CFHash` + `AXObserver`) UIDs** with `+/~/-` delta encoding and CRC-32 unchanged short-circuiting (`= unchanged`).
- **Pre-registered table folding**: Dense tables fold after 6 rows while indexing all rows in memory so the model can still click `@u94` or `"Inspect #ORD-415"` without unfolding.
- **Stale-observation masking (`ContextWindowTracker`)**: Automatically replaces superseded full snapshots from earlier turns with 18-token tombstones, keeping a **30-operation Web + macOS workflow at 1,597 peak context tokens** (`4,584` cumulative tokens across 5 turns).
- **On-device Apple Silicon `Vision.framework` (`VNRecognizeTextRequest`) OCR**: Extracts clickable `@ocr1..@ocrN` text + bounding boxes from `<canvas>`/WebGL elements and native macOS windows in `< 70 ms` using **0 vision tokens**.

Repo (MIT, Python + universal2 Swift binary): https://github.com/lifan-builds/faster-mac-use
Interactive benchmark data (`o200k_base`): https://clifan.com/faster-mac-use/

---

## 4. MCP Directory & Package Registry Submissions

### A. `punkpeye/awesome-mcp-servers` & `appcypher/awesome-mcp-servers` (Browser Automation / OS Automation)
```markdown
- **[faster-mac-use (`fmu`)](https://github.com/lifan-builds/faster-mac-use)** - Background-first, multi-cursor browser (`"🤖 Agent"` Chrome tab groups, WebMCP, `<canvas>` OCR) and native macOS (`AXObserver` + Apple Silicon `Vision.framework` OCR) computer-use engine in a single 268-token MCP tool with 0.0 px cursor movement and 10x–114x lower token usage.
```

### B. Smithery (`smithery.yaml`) & Glama (`glama.json`)
Both manifest files are committed in the repository root:
- [`smithery.yaml`](../smithery.yaml)
- [`glama.json`](../glama.json)
- [`Formula/faster-mac-use.rb`](../Formula/faster-mac-use.rb)
