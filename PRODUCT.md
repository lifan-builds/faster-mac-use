# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

delegated: Static HTML/CSS/JS (`docs/index.html` + `docs/site.css`, deployed at `https://clifan.com/faster-mac-use/` and mirrored via GitHub Pages at `https://lifan-builds.github.io/faster-mac-use/`). Inferred from the repository's existing `/docs` structure and `/self-promoting` static project-site workflow.

## Users

- **Primary users:** macOS developers, AI engineers, and agent framework builders who run coding and computer-use agents (Claude Code, OpenAI Codex CLI, Cursor, Antigravity, or custom MCP clients) alongside their active foreground work.
- **Situation & job:** Evaluating or adopting a computer-use runtime that can drive browser tabs and native macOS apps in parallel without hijacking the physical OS cursor, stealing window focus, popping Chrome 136+/144+ remote-debugging modals, or exhausting the LLM context window.

## Product Purpose

`faster-mac-use` (`fmu`) provides a background-first, multi-cursor browser and native macOS computer-use engine exposed through a single 268-token MCP tool and CLI (`./bin/fmu`). Success means a visiting engineer can immediately verify how `fmu` works, inspect reproducible 5-workload benchmark data against 15 competitor configurations, understand its material macOS/extension boundaries, and copy a working install/MCP snippet in under a minute.

## Positioning

The only open-source computer-use engine that unifies unfocused Chrome `"🤖 Agent"` tab groups (with CDP Focus Emulation, `WeakMap` `@u1..@uN` UIDs, pre-registered table folding, and W3C WebMCP auto-execution) and a persistent native Swift macOS Accessibility + Apple Silicon `Vision.framework` OCR daemon (`/tmp/fmu-ax.sock`, `@a1..@aN` UIDs) inside a multi-cursor stateful `cua_repl` runtime—verified at `0.0 px` physical cursor displacement and `10×` to `114×` lower cumulative token usage.

## Operating Context

Evaluated alongside terminal sessions, MCP configuration files (`mcp_config.json`, `config.toml`), reproducible Python/Swift benchmarks (`benchmarks/run_benchmarks.py`), and technical architecture reports (`docs/architecture_and_benchmarks.md`, `docs/gtm.md`).

## Capabilities and Constraints

- **Confirmed capabilities:** Single-tool 268-token MCP/CLI schema; stateful `cua_repl` execution (`cua.createBrowserTab`, `cua.getTab`, `cua.getApp`, `cua.getCursors`); 1-trip declarative `steps` batches; concurrent multi-cursor swarms (`{"parallel": [...]}`); persistent `@u1`/`@a1` UIDs with `+/~/-` diffs and CRC-32 short-circuiting; pre-registered table row folding (`foldTables`); stale-observation masking (`ContextWindowTracker`); 50Hz `BackgroundIntrusionGuard`; zero-dialog CDP file uploads; Apple Silicon `Vision.framework` Window OCR (`app.ocr()`).
- **Material constraints (must be stated alongside claims):**
  1. Native `fmu-ax` accessibility + Vision OCR daemon requires macOS (Apple Silicon / macOS 14+ recommended) and Accessibility permission.
  2. Creating color-coded `"🤖 Agent"` tab groups inside a user's visible Chrome window uses the included MV3 extension (`extension/background.js`); without the extension loaded, `fmu` runs in a background Chromium worker with CDP Focus Emulation.
  3. `app.ocr()` (`CGWindowListCreateImage`) requires the target macOS window to be unminimized (it may be completely occluded behind other windows).

## Brand Commitments

- **Name:** Faster Mac Use (`faster-mac-use` / `fmu`)
- **Author & canonical identity:** Lifan Chen (`https://clifan.com`, GitHub `https://github.com/lifan-builds/faster-mac-use`, X `@LifanBuilds`)
- **Voice:** Plain, concrete, technical, evidence-first; zero fabricated metrics or marketing clichés.

## Evidence on Hand

- 14+ unit and integration tests in `tests/test_fmu.py`
- 5-workload, 15-system benchmark dataset in `benchmarks/results/latest_benchmark.json` and `benchmarks/results/latest_benchmark.md` (measured with `o200k_base` tokenizer and 50Hz `BackgroundIntrusionGuard`)
- Full architecture writeup in `docs/architecture_and_benchmarks.md` and market/GTM brief in `docs/gtm.md`
- No external customer testimonials or third-party press exist yet; none may be fabricated.

## Product Principles

1. **Prove with reproducible telemetry:** Lead with exact, verifiable token counts, latency measurements, and hardware-guard proofs (`0.0 px` cursor drift, `0` focus switches).
2. **State boundaries next to claims:** Always pair benchmark wins with exact platform and extension constraints.
3. **Make the first visit actionable:** Keep the live trace inspector, 15-system benchmark explorer, architecture breakdown, and copy-paste MCP/CLI setup on one scannable surface.
