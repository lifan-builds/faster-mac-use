---
name: Faster Mac Use (fmu)
description: Background-first, multi-cursor browser and native macOS computer-use engine for AI agents
colors:
  accent: "#095c49"
  accent-hover: "#064436"
  accent-soft: "#d7efe8"
  signal: "#b53f15"
  signal-soft: "#fae6de"
  ground: "#eaf0f4"
  surface: "#f7fafc"
  surface-strong: "#ffffff"
  ink: "#0c1720"
  muted: "#344b5c"
  bar-neutral: "#7c93a6"
  rule: "#c4d1dc"
  rule-strong: "#9fb2c2"
  code-bg: "#101c26"
  code-ink: "#e6f0f7"
  code-control: "#203444"
  code-control-hover: "#2b4559"
  code-border: "#38556b"
typography:
  display:
    fontFamily: "Chivo, Avenir Next, Helvetica Neue, -apple-system, BlinkMacSystemFont, sans-serif"
    fontSize: "clamp(2.125rem, 3.5vw, 2.875rem)"
    fontWeight: 800
    lineHeight: 1.08
    letterSpacing: "-0.03em"
  headline:
    fontFamily: "Chivo, Avenir Next, Helvetica Neue, -apple-system, BlinkMacSystemFont, sans-serif"
    fontSize: "clamp(1.5rem, 2.3vw, 1.875rem)"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "-0.02em"
  title:
    fontFamily: "Chivo, Avenir Next, Helvetica Neue, -apple-system, BlinkMacSystemFont, sans-serif"
    fontSize: "1.1875rem"
    fontWeight: 700
    lineHeight: 1.32
  body:
    fontFamily: "Chivo, Avenir Next, Helvetica Neue, -apple-system, BlinkMacSystemFont, sans-serif"
    fontSize: "1.0625rem"
    fontWeight: 400
    lineHeight: 1.62
  subhead:
    fontFamily: "Chivo, Avenir Next, Helvetica Neue, -apple-system, BlinkMacSystemFont, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 600
    lineHeight: 1.5
  label:
    fontFamily: "Chivo, Avenir Next, Helvetica Neue, -apple-system, BlinkMacSystemFont, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 600
    lineHeight: 1.35
  code:
    fontFamily: "JetBrains Mono, SF Mono, Menlo, Consolas, ui-monospace, monospace"
    fontSize: "0.8125rem"
    fontWeight: 600
    lineHeight: 1.5
rounded:
  sm: "6px"
  md: "8px"
  lg: "12px"
  xl: "14px"
spacing:
  xs: "0.45rem"
  sm: "0.75rem"
  md: "1rem"
  lg: "1.5rem"
  xl: "2.5rem"
  xxl: "3.75rem"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.surface-strong}"
    rounded: "{rounded.md}"
    padding: "0.7rem 1.15rem"
  button-primary-hover:
    backgroundColor: "{colors.accent-hover}"
  button-secondary:
    backgroundColor: "{colors.surface-strong}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "0.7rem 1.15rem"
  chip-active:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.surface-strong}"
    rounded: "{rounded.sm}"
    padding: "0.42rem 0.78rem"
---

# Design System: Faster Mac Use (`fmu`)

## Overview

**Creative North Star: "The Anodized Systems Workbench"**

`faster-mac-use` is a precision systems engine for macOS and Chrome. Its web presence rejects dark-neon AI tropes and floating SaaS feature cards in favor of a bright, high-contrast anodized-aluminum specification sheet paired with a deep-slate telemetry workbench. Every visual element encodes verifiable measurement: 50Hz hardware intrusion guard badges, multi-cursor virtual coordinates, and interactive `o200k_base` benchmark comparisons.

**Key Characteristics:**
- Cool anodized-aluminum ground (`#eaf0f4`) with crisp white specification ledgers (`#ffffff`) and structural 1px rules (`#9fb2c2`, `#c4d1dc`).
- Deep slate code/trace chambers (`#101c26`) reserved strictly for live `cua_repl` traces, CLI commands, and MCP configuration snippets.
- `Chivo` geometric grotesque for assertive headings and readable prose paired with `JetBrains Mono` (`tabular-nums`) for token counts, latencies, and virtual cursor coordinates.

## Colors

The palette pairs cool machined-metal neutrals with phosphor-emerald verification accents and copper-vermilion delta highlights.

### Primary
- **Phosphor Emerald** (`#095c49`, hover `#064436`, soft tint `#d7efe8`): Primary actions, active workload/metric tabs, verified `PASS` / `YES` status badges, and `faster-mac-use` benchmark bars.

### Secondary
- **Copper Signal** (`#b53f15`, soft tint `#fae6de`): Keyboard focus rings, token-reduction multipliers, and `NO` capability badges in competitor comparison tables.

### Neutral
- **Anodized Ground** (`#eaf0f4`): Page canvas and bar-chart track background.
- **Ledger Surface** (`#f7fafc`) & **Pure Sheet** (`#ffffff`): Specification strips, benchmark explorer panels, and architecture ledgers.
- **Deep Slate Ink** (`#0c1720`) & **Tempered Slate** (`#344b5c`): High-contrast primary copy and secondary technical prose (both exceeding WCAG AA/AAA contrast on light surfaces).
- **Competitor Bar Slate** (`#7c93a6`) & **Hairline Rules** (`#c4d1dc`, `#9fb2c2`): Structural borders and baseline bars.
- **Terminal Chamber** (`#101c26`), **Terminal Ink** (`#e6f0f7`), & **Control Slate** (`#203444`, hover `#2b4559`, border `#38556b`): High-contrast code and trace blocks.

### Named Rules
**The Structural Hairline Rule.** Divide related specification cells and architectural tiers with 1px structural rules inside a unified ledger rather than chopping content into disconnected floating cards.

## Typography

**Display Font:** `Chivo` (with `Avenir Next`, `Helvetica Neue`, `-apple-system` fallbacks)
**Body Font:** `Chivo` (with system sans fallbacks)
**Label/Mono Font:** `JetBrains Mono` (with `SF Mono`, `Menlo`, `Consolas` fallbacks)

**Character:** Assertive, mechanical grotesque headings paired with a disciplined tabular monospace for measurements and code.

### Hierarchy
- **Display** (`800`, `clamp(2.125rem, 3.5vw, 2.875rem)`, line-height `1.08`, tracking `-0.03em`): Hero thesis statement (`max-width: 19ch`, `text-wrap: balance`).
- **Headline** (`700`, `clamp(1.5rem, 2.3vw, 1.875rem)`, line-height `1.2`, tracking `-0.02em`): Section headings with generous top margin (`3.75rem` above vs `1rem` below).
- **Title** (`700`, `1.1875rem`, line-height `1.32`): Panel and architectural tier titles.
- **Body** (`400`, `1.0625rem`, line-height `1.62`): Technical explanations constrained to `68ch` max measure (`text-wrap: pretty`).
- **Subhead** (`600`, `0.9375rem`, line-height `1.5`): Navigation links, primary CTAs, and architecture list items.
- **Label** (`600`, `0.875rem`, line-height `1.35`): Interactive workload/metric tab controls and bar chart labels.
- **Code / Telemetry** (`600`, `0.8125rem`, line-height `1.5`, `tabular-nums`): Live trace inspector, benchmark data tables, and status pills.

### Named Rules
**The Tabular Telemetry Rule.** Every numeric comparison, token count, latency figure, and virtual cursor coordinate must render with `font-variant-numeric: tabular-nums` so columns align vertically across switches.

## Layout

Centered `1180px` maximum container (`calc(100% - 3rem)` on desktop, `calc(100% - 2.25rem)` under `600px`). The opening viewport splits `1.02fr / 0.98fr` between the thesis + specification ledger on the left and the interactive Live Trace & Virtual Cursor Inspector on the right, collapsing cleanly to a single column below `960px` with `min-width: 0` on all grid children to prevent horizontal overflow.

## Elevation & Depth

This system is flat and structural by default. Depth is conveyed through tonal layering (`#eaf0f4` ground → `#f7fafc` header strip → `#ffffff` ledger panel → `#101c26` recessed code chamber) and crisp `1px` borders (`#9fb2c2`), without decorative drop shadows or glowing halos.

### Named Rules
**The One-Elevation Rule.** Declare container boundaries once via a `1px` structural border (`#9fb2c2`); never pair a hairline border with a diffuse drop shadow.

## Shapes

- **Outer Ledgers & Workbenches:** `14px` radius (`--rounded-xl`) with `overflow: hidden` so internal hairlines meet the frame cleanly.
- **Inner Code Chambers & Panels:** `12px` radius (`--rounded-lg`).
- **Primary & Secondary Action Buttons:** `8px` radius (`--rounded-md`).
- **Filter Tabs, Copy Controls, & Status Badges:** `6px` radius (`--rounded-sm`).

## Components

### Buttons
- **Shape:** Gently rounded technical controls (`8px` radius).
- **Primary:** Phosphor Emerald (`#095c49`) fill with `#ffffff` text and `0.7rem 1.15rem` padding; darkens to `#064436` on hover.
- **Secondary:** Pure white (`#ffffff`) fill with `#0c1720` text and `1px solid #9fb2c2` border; shifts to `#f7fafc` on hover.

### Chips
- **Style:** Compact `6px`-radius interactive tabs (`0.42rem 0.78rem`, `0.875rem` font size) for switching recorded traces, benchmark workloads (`A`–`E`), metrics, and MCP client configs.
- **State:** Unselected tabs use `#ffffff` background with `#344b5c` text and `1px solid #c4d1dc`; selected tabs (`aria-selected="true"` / `aria-pressed="true"`) invert to `#095c49` with `#ffffff` text.

### Cards / Containers
- **Corner Style:** `12px`–`14px` radius with `1px solid #9fb2c2` border and zero drop shadow.
- **Background:** `#ffffff` for primary panels, `#f7fafc` for header strips, and `#101c26` for code blocks.
- **Internal Padding:** `1.35rem 1.5rem` on panels and `1.4rem 1.5rem` on hairlined ledger columns.

### Navigation
- **Style:** Top horizontal bar with breadcrumb identity (`Lifan Chen / faster-mac-use`) and `v0.1.0 (MIT)` badge on the left, and section anchor links (`0.9375rem`, weight `600`, `#344b5c` → `#0c1720` on hover) on the right.

## Do's and Don'ts

### Do:
- **Do** use exact measured numbers from `benchmarks/results/latest_benchmark.json` across every chart, table, and trace caption.
- **Do** state material platform and extension boundaries alongside performance claims.
- **Do** maintain custom browser surface theming (`::selection`, `scrollbar-color`, `:focus-visible` in `#b53f15`, and `tabular-nums`).

### Don't:
- **Don't** place uppercase eyebrow/kicker labels above section headings (`h1`, `h2`, `h3`).
- **Don't** nest bordered cards inside bordered cards; use `1px` structural hairlines (`#c4d1dc`) to subdivide panels.
- **Don't** use gradient text, neon dark-mode glows, or unverified marketing superlatives.
