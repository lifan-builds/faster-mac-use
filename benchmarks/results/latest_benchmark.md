# Faster Mac Use — Competitor Benchmark Results

### A: Web SaaS (10 ops)

| System | Mode | Schema Tok | Turns | Raw Obs Tok | Peak Context Tok | Cumulative Input Tok | Engine (ms) | Est E2E (s) | BG Safe (0px/0-focus) | Agent TabGroup | Mac AX | Multi-Cursor |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :---: | :---: | :---: | :---: |
| **faster-mac-use + WebMCP (`navigator.modelContext`)** | `live` | 268 | 2 | 652 | **1,097** | **1,290** | 81 | 2.77s | YES | YES | YES | YES |
| **faster-mac-use (Ours: Warm Daemon + REPL + Diff + Mask)** | `live` | 268 | 3 | 804 | **1,097** | **2,024** | 118 | 4.04s | YES | YES | YES | YES |
| **faster-mac-use (Unmasked History)** | `live` | 268 | 3 | 804 | **1,532** | **3,057** | 118 | 4.04s | YES | YES | YES | YES |
| **OpenAI Codex CUA (`cua_repl` Reference)** | `protocol-exact` | 285 | 3 | 2,477 | **3,222** | **5,732** | 339 | 4.26s | YES | YES | YES | NO |
| **OpenBrowser MCP (`openbrowser-ai` CodeAgent)** | `protocol-exact` | 520 | 3 | 3,449 | **4,427** | **7,855** | 640 | 4.54s | YES | NO | NO | NO |
| **Reflex MCP (Batched Scoped Reader)** | `protocol-exact` | 890 | 4 | 2,693 | **4,047** | **11,409** | 820 | 5.65s | YES | NO | NO | NO |
| **agy-cua (Legacy v0 Baseline)** | `live` | 334 | 3 | 4,489 | **5,403** | **10,132** | 1,748 | 7.17s | YES | NO | YES | NO |
| **vercel-labs/agent-browser (v0.41.2)** | `live` | 380 | 4 | 12,685 | **13,572** | **23,288** | 1,583 | 6.95s | YES | NO | NO | NO |
| **playwriter (v0.4.0 MCP)** | `protocol-exact` | 12,592 | 3 | 8,161 | **21,244** | **51,169** | 910 | 5.22s | YES | NO | NO | NO |
| **Microsoft @playwright/mcp (v0.0.84)** | `protocol-exact` | 4,380 | 10 | 40,720 | **45,580** | **231,125** | 1,850 | 11.97s | NO | NO | NO | NO |
| **Microsoft @playwright/cli** | `protocol-exact` | 420 | 5 | 8,153 | **8,958** | **28,356** | 1,450 | 6.14s | YES | NO | NO | NO |
| **Google chrome-devtools-mcp (--autoConnect)** | `protocol-exact` | 4,150 | 8 | 32,952 | **37,524** | **143,352** | 1,620 | 9.32s | NO | NO | NO | NO |
| **Browserbase Stagehand v4 (Cold Run)** | `protocol-exact` | 650 | 4 | 12,245 | **4,972** | **18,106** | 1,350 | 11.81s | YES | NO | NO | NO |
| **Anthropic Claude in Chrome (Extension MCP)** | `protocol-exact` | 2,650 | 9 | 7,565 | **10,628** | **55,108** | 2,100 | 10.54s | YES | YES | NO | NO |
| **Anthropic Pixel CUA (`computer` Vision Loop)** | `protocol-exact` | 310 | 11 | 15,059 | **15,851** | **83,215** | 3,400 | 14.40s | NO | NO | YES | NO |

### B: macOS Native (Calculator)

| System | Mode | Schema Tok | Turns | Raw Obs Tok | Peak Context Tok | Cumulative Input Tok | Engine (ms) | Est E2E (s) | BG Safe (0px/0-focus) | Agent TabGroup | Mac AX | Multi-Cursor |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :---: | :---: | :---: | :---: |
| **faster-mac-use (Ours: Swift Daemon + UID Diff)** | `live` | 268 | 2 | 261 | **823** | **1,264** | 102 | 2.76s | YES | YES | YES | YES |
| **OpenAI Codex CUA (`cua_repl` Reference)** | `protocol-exact` | 285 | 2 | 414 | **1,126** | **1,499** | 188 | 2.85s | YES | YES | YES | NO |
| **trycua/cua (`cua-driver` Background AX MCP)** | `protocol-exact` | 980 | 6 | 600 | **2,002** | **9,755** | 1,180 | 7.18s | YES | NO | YES | NO |
| **steipete/Peekaboo (`AXorcist` Swift MCP)** | `protocol-exact` | 1,120 | 5 | 1,010 | **2,550** | **9,510** | 980 | 6.11s | YES | NO | YES | NO |
| **agy-cua (Legacy v0 Baseline)** | `protocol-exact` | 334 | 2 | 1,599 | **2,431** | **2,938** | 744 | 4.29s | YES | NO | YES | NO |
| **open-computer-use (`ocu` v0.2.1 MCP)** | `live` | 1,508 | 11 | 5,467 | **7,468** | **48,488** | 3,581 | 14.72s | YES | NO | YES | NO |

### C: Hybrid Web+Mac (14 ops)

| System | Mode | Schema Tok | Turns | Raw Obs Tok | Peak Context Tok | Cumulative Input Tok | Engine (ms) | Est E2E (s) | BG Safe (0px/0-focus) | Agent TabGroup | Mac AX | Multi-Cursor |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :---: | :---: | :---: | :---: |
| **faster-mac-use (Ours: Full Architecture)** | `live` | 268 | 3 | 233 | **1,080** | **2,176** | 306 | 5.72s | YES | YES | YES | YES |
| **OpenAI Codex CUA (`cua_repl` Reference)** | `protocol-exact` | 285 | 3 | 780 | **1,644** | **2,434** | 531 | 5.94s | YES | YES | YES | NO |
| **Stacked MCPs (@playwright/mcp + open-computer-use)** | `protocol-exact` | 5,888 | 14 | 6,314 | **12,650** | **129,010** | 3,200 | 16.32s | NO | NO | YES | NO |

### D: Multi-Cursor Swarm (3 Agents, 25 ops)

| System | Mode | Schema Tok | Turns | Raw Obs Tok | Peak Context Tok | Cumulative Input Tok | Engine (ms) | Est E2E (s) | BG Safe (0px/0-focus) | Agent TabGroup | Mac AX | Multi-Cursor |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :---: | :---: | :---: | :---: |
| **faster-mac-use (Ours: 3-Cursor Parallel Swarm + Diff + Mask)** | `live` | 268 | 2 | 913 | **1,538** | **1,510** | 256 | 6.93s | YES | YES | YES | YES |
| **OpenAI Codex CUA (3 Agents Serialized Single-Session)** | `protocol-exact` | 285 | 6 | 3,307 | **5,136** | **15,848** | 954 | 20.98s | YES | YES | YES | NO |
| **trycua/cua + OpenBrowser MCP (Multi-Agent Stack)** | `protocol-exact` | 1,500 | 9 | 6,806 | **9,535** | **47,474** | 1,780 | 20.42s | YES | NO | YES | YES |
| **browser-use v2 (Asyncio Multi-Agent CDP — Web Only)** | `protocol-exact` | 740 | 6 | 5,328 | **7,006** | **21,384** | 1,540 | 13.99s | YES | NO | NO | YES |
| **Stacked MCPs (@playwright/mcp --isolated + open-computer-use)** | `protocol-exact` | 5,888 | 25 | 11,275 | **17,738** | **293,950** | 4,850 | 28.91s | NO | NO | YES | NO |
| **Anthropic Pixel CUA (Single Physical Cursor — Collides)** | `protocol-exact` | 310 | 25 | 34,225 | **35,185** | **430,800** | 7,600 | 32.60s | NO | NO | YES | NO |

### E: Long-Horizon + Window OCR (30 ops)

| System | Mode | Schema Tok | Turns | Raw Obs Tok | Peak Context Tok | Cumulative Input Tok | Engine (ms) | Est E2E (s) | BG Safe (0px/0-focus) | Agent TabGroup | Mac AX | Multi-Cursor |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :---: | :---: | :---: | :---: |
| **faster-mac-use (Ours: 5-Turn REPL + Diff + Mask + Native OCR)** | `live` | 268 | 5 | 714 | **1,320** | **4,581** | 443 | 8.92s | YES | YES | YES | YES |
| **faster-mac-use (Unmasked History)** | `live` | 268 | 5 | 714 | **1,670** | **5,615** | 443 | 8.92s | YES | YES | YES | YES |
| **OpenAI Codex CUA (`cua_repl` + Screenshot Fallback)** | `protocol-exact` | 285 | 5 | 2,953 | **3,926** | **8,352** | 871 | 9.35s | YES | YES | YES | NO |
| **Stacked MCPs (@playwright/mcp + open-computer-use)** | `protocol-exact` | 5,888 | 30 | 14,430 | **20,968** | **400,725** | 6,100 | 35.35s | NO | NO | YES | NO |
| **Anthropic Pixel CUA (30-Step Screenshot Loop)** | `protocol-exact` | 310 | 30 | 41,070 | **42,090** | **620,535** | 9,200 | 39.20s | NO | NO | YES | NO |
