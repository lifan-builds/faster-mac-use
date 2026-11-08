# Faster Mac Use — Competitor Benchmark Results

### A: Web SaaS (10 ops)

| System | Mode | Schema Tok | Turns | Raw Obs Tok | Peak Context Tok | Cumulative Input Tok | Engine (ms) | Est E2E (s) | BG Safe (0px/0-focus) | Agent TabGroup | Mac AX | Multi-Cursor |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :---: | :---: | :---: | :---: |
| **faster-mac-use + WebMCP (`navigator.modelContext`)** | `live` | 268 | 2 | 658 | **1,103** | **1,290** | 94 | 2.78s | YES | YES | YES | YES |
| **faster-mac-use + Virtual WebMCP Macro (`~/.fmu/macros/`)** | `live` | 268 | 2 | 671 | **1,108** | **1,290** | 97 | 2.67s | YES | YES | YES | YES |
| **faster-mac-use (Ours: Warm Daemon + REPL + Diff + Mask)** | `live` | 268 | 3 | 810 | **1,103** | **2,024** | 133 | 4.06s | YES | YES | YES | YES |
| **faster-mac-use (Unmasked History)** | `live` | 268 | 3 | 810 | **1,538** | **3,069** | 133 | 4.06s | YES | YES | YES | YES |
| **OpenAI Codex CUA (`cua_repl` Reference)** | `protocol-exact` | 285 | 3 | 2,477 | **3,222** | **5,732** | 359 | 4.28s | YES | YES | YES | NO |
| **OpenBrowser MCP (`openbrowser-ai` CodeAgent)** | `protocol-exact` | 520 | 3 | 3,449 | **4,427** | **7,855** | 640 | 4.54s | YES | NO | NO | NO |
| **Reflex MCP (Batched Scoped Reader)** | `protocol-exact` | 890 | 4 | 2,693 | **4,047** | **11,409** | 820 | 5.65s | YES | NO | NO | NO |
| **agy-cua (Legacy v0 Baseline)** | `live` | 334 | 3 | 4,489 | **5,403** | **10,132** | 2,120 | 7.55s | YES | NO | YES | NO |
| **vercel-labs/agent-browser (v0.41.2)** | `live` | 380 | 4 | 12,685 | **13,572** | **23,288** | 1,763 | 7.13s | YES | NO | NO | NO |
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
| **faster-mac-use (Ours: Swift Daemon + UID Diff)** | `live` | 268 | 2 | 267 | **826** | **1,264** | 395 | 3.06s | YES | YES | YES | YES |
| **OpenAI Codex CUA (`cua_repl` Reference)** | `protocol-exact` | 285 | 2 | 420 | **1,132** | **1,502** | 554 | 3.22s | YES | YES | YES | NO |
| **trycua/cua (`cua-driver` Background AX MCP)** | `protocol-exact` | 980 | 6 | 609 | **2,011** | **9,779** | 1,180 | 7.18s | YES | NO | YES | NO |
| **steipete/Peekaboo (`AXorcist` Swift MCP)** | `protocol-exact` | 1,120 | 5 | 1,025 | **2,565** | **9,540** | 980 | 6.11s | YES | NO | YES | NO |
| **agy-cua (Legacy v0 Baseline)** | `protocol-exact` | 334 | 2 | 1,602 | **2,434** | **2,941** | 744 | 4.29s | YES | NO | YES | NO |
| **open-computer-use (`ocu` v0.2.1 MCP)** | `live` | 1,507 | 11 | 5,467 | **7,467** | **48,477** | 12,718 | 23.86s | YES | NO | YES | NO |

### C: Hybrid Web+Mac (14 ops)

| System | Mode | Schema Tok | Turns | Raw Obs Tok | Peak Context Tok | Cumulative Input Tok | Engine (ms) | Est E2E (s) | BG Safe (0px/0-focus) | Agent TabGroup | Mac AX | Multi-Cursor |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :---: | :---: | :---: | :---: |
| **faster-mac-use (Ours: Full Architecture)** | `live` | 268 | 3 | 236 | **1,083** | **2,179** | 335 | 5.75s | YES | YES | YES | YES |
| **OpenAI Codex CUA (`cua_repl` Reference)** | `protocol-exact` | 285 | 3 | 783 | **1,647** | **2,437** | 568 | 5.98s | YES | YES | YES | NO |
| **Stacked MCPs (@playwright/mcp + open-computer-use)** | `protocol-exact` | 5,888 | 14 | 6,314 | **12,650** | **129,010** | 3,200 | 16.32s | NO | NO | YES | NO |

### D: Multi-Cursor Swarm (3 Agents, 25 ops)

| System | Mode | Schema Tok | Turns | Raw Obs Tok | Peak Context Tok | Cumulative Input Tok | Engine (ms) | Est E2E (s) | BG Safe (0px/0-focus) | Agent TabGroup | Mac AX | Multi-Cursor |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :---: | :---: | :---: | :---: |
| **faster-mac-use (Ours: 3-Cursor Parallel Swarm + Diff + Mask)** | `live` | 268 | 2 | 925 | **1,547** | **1,510** | 326 | 7.00s | YES | YES | YES | YES |
| **OpenAI Codex CUA (3 Agents Serialized Single-Session)** | `protocol-exact` | 285 | 6 | 3,343 | **5,172** | **15,923** | 1,122 | 21.15s | YES | YES | YES | NO |
| **trycua/cua + OpenBrowser MCP (Multi-Agent Stack)** | `protocol-exact` | 1,500 | 9 | 6,857 | **9,586** | **47,678** | 1,780 | 20.42s | YES | NO | YES | YES |
| **browser-use v2 (Asyncio Multi-Agent CDP — Web Only)** | `protocol-exact` | 740 | 6 | 5,346 | **7,024** | **21,429** | 1,540 | 13.99s | YES | NO | NO | YES |
| **Stacked MCPs (@playwright/mcp --isolated + open-computer-use)** | `protocol-exact` | 5,888 | 25 | 11,275 | **17,738** | **293,950** | 4,850 | 28.91s | NO | NO | YES | NO |
| **Anthropic Pixel CUA (Single Physical Cursor — Collides)** | `protocol-exact` | 310 | 25 | 34,225 | **35,185** | **430,800** | 7,600 | 32.60s | NO | NO | YES | NO |

### E: Long-Horizon + Window OCR (30 ops)

| System | Mode | Schema Tok | Turns | Raw Obs Tok | Peak Context Tok | Cumulative Input Tok | Engine (ms) | Est E2E (s) | BG Safe (0px/0-focus) | Agent TabGroup | Mac AX | Multi-Cursor |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :---: | :---: | :---: | :---: |
| **faster-mac-use (Ours: 5-Turn REPL + Diff + Mask + Native OCR)** | `live` | 268 | 5 | 991 | **1,597** | **4,584** | 494 | 8.97s | YES | YES | YES | YES |
| **faster-mac-use (Unmasked History)** | `live` | 268 | 5 | 991 | **1,947** | **5,618** | 494 | 8.97s | YES | YES | YES | YES |
| **OpenAI Codex CUA (`cua_repl` + Screenshot Fallback)** | `protocol-exact` | 285 | 5 | 2,956 | **3,929** | **8,355** | 942 | 9.42s | YES | YES | YES | NO |
| **Stacked MCPs (@playwright/mcp + open-computer-use)** | `protocol-exact` | 5,888 | 30 | 14,430 | **20,968** | **400,725** | 6,100 | 35.35s | NO | NO | YES | NO |
| **Anthropic Pixel CUA (30-Step Screenshot Loop)** | `protocol-exact` | 310 | 30 | 41,070 | **42,090** | **620,535** | 9,200 | 39.20s | NO | NO | YES | NO |
