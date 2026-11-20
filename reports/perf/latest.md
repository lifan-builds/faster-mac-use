# Faster Mac Use (`fmu`) Performance & Field Audit Report (`v0.4.4`)

**Latest Field Audit (`2026-11-20` — `3,963` calls cleared after shipping fixes)**:
- **True Parallel MV3 Dispatch + Daemon Timeout Cascade Fix**: Bypassed Playwright's single-threaded `_run_sync` worker queue for MV3 extension sessions (`3x 380ms` parallel MV3 tabs drop from `~1,140ms` serialized to `<500ms` concurrent; 4-tab `25s` Google Flights swarm drops from `54.4s` serialized to `~25s`), extended `cli.py` wait-budget estimation across `parallel[]` and `code` `waitFor()`, and stopped catching `TimeoutError` inside `except OSError:` so live warm daemons on `:19222` are never shadowed by fallback-port runtimes.
- **MV3 `DOMContentLoaded` Load Settlement & `nav_wait` Phase Separation**: `waitForTabLoad` / `waitForNavigationSettlement` in `extension/background.js` now resolve once `document.readyState` is `interactive`/`complete` with a populated `<body>` instead of stalling up to `10,000ms` on third-party subresources (`status === "complete"`), and separate `nav_wait` from `bridge_overhead` (`rpc_timeout` raised to `max(18s, step_wait_s + 12s)`).
- **Zero-Config MV3 File Uploads (`inlineFiles` `DataTransfer`)**: Local files (`<= 25MB`) now upload automatically via in-page `File` + `DataTransfer` injection even when Chrome's `"Allow access to file URLs"` toggle is disabled.
- **`setNativeValue` `Illegal invocation` Fix & JS REPL `!` Operator**: Unwraps `<label>.control` and Shadow DOM hosts (`getShadowRoot(root)`), resolves realm-accurate prototypes strictly for `<input>`/`<textarea>`/`<select>`, falls back to synthetic `InputEvent`s on custom `role="textbox"` widgets, and transpiles unary `!` in JS-flavored REPL `code`.
- **`16,000`-Char Default `eval` Cap, Strict-CSP Host Cache, Bot/CAPTCHA Fast-Fail & Browser `AXWebArea` Pruning**: Default `eval` output capped at `16,000` chars (with per-step `maxChars` override), strict-CSP domains cached after first `eval_csp` (`eval_csp_cached`), `/sorry`/`/captcha`/`/help/bots` interstitials aborted immediately during `waitFor` with title/heading timeout context, dedicated browser `AXWebArea` subtrees pruned in `fmu_ax.swift` (with `axTrusted` status diagnostics + single-verb shorthand steps), and session logs upgraded with `entrypoint`/`batch_id` correlation, `evalValue`/`screenshot`/`layoutAudit` coverage, and `fmu --perf --clear`.

---

## Earlier `v0.4.0` Audit (`~/.fmu/logs/perf-2026-11-08.jsonl` .. `perf-2026-11-15.jsonl`, `731` calls)

## 1. YYC Car Rental Field Audit Telemetry (`731` calls — Before `v0.4.0`)

| Bottleneck | Field Measurement (`YYC Car Rental Price Comparison`) | Root Cause |
| --- | --- | --- |
| Blind `.wait(ms)` polling sleeps | `514s` (`46.7%` of total `1,100s` wall time) | No event-driven `waitFor` step; agents chained `.wait(3000)`–`.wait(8000)` waiting for SPA spinners, promo codes, and checkout confirmations |
| `eval("document.body.innerText")` & repeated `<footer>` links | `~142,000` tokens (`35%+` of total `402,937` tokens) | `collectSnapshot` omitted primary `<main>` static text (`Confirmation Number`, totals) and uncovered `<tr>` rows while repeating 30–60 `<footer>` links on every full snapshot |
| `cua.getTab()` / `reuseExisting` tab clobbering & `about:blank` | Multiple `chrome-error://chromewebdata/` + stray `about:blank` tabs | Bare-domain `getTab("hertz.com")` / `reuseExisting` navigated over live deep URLs (`/us/en/book/vehicles`) or lacked `https://`, cross-agent `getTab("audit:t1")` fell through to `window="audit:t1"`, and no `cua.listTabs()` existed |
| Low `compact_diff_ratio` (`47.8%`) & per-call IPC overhead | `52.2%` full snapshots; 75KB `runtimeJs` + `ensure_runtime` + canvas check every call | Chained `tab.click(...).snapshot()` forced `diff=False`, `history.pushState('?vehicle=suv')` invalidated baselines, and `runtimeJs` was re-serialized over WS on every MV3 batch |
| JS-vs-Python REPL (`code`) syntax errors & silent bare `evalValue` | `SyntaxError` / `AttributeError` / empty `""` return | Multiline quoted JS strings in `tab.eval("...\n...")`, template literals `` `${a}:${b}` ``, destructuring `const { a, b } = ...`, `.includes()`/`.slice()`/`.join()`, and bare `await tab.evalValue(...)` |

## 2. `v0.4.0` Fixes Shipped

1. **Event-Driven `waitFor` (`tab.waitFor(...)` / `op: "waitFor"`)**:
   - Implemented `waitForCondition` in [`src/fmu/browser_engine.py`](file:///Users/lfan/Project/faster-mac-use/src/fmu/browser_engine.py) (`IN_PAGE_RUNTIME_JS`) using `MutationObserver` + `popstate`/`hashchange`/`DOMContentLoaded` listeners + 50ms fallback tick (`text`, `selector`, `gone`, `url`, `fn`, `timeoutMs`, `optional`), exposed on `TabHandle.waitFor` / `wait_for` / `waitUntil` in [`src/fmu/cua_runtime.py`](file:///Users/lfan/Project/faster-mac-use/src/fmu/cua_runtime.py) with dynamic RPC/daemon deadlines and post-navigation continuation in [`extension/background.js`](file:///Users/lfan/Project/faster-mac-use/extension/background.js).

2. **Primary `<main>` Static Text, Uncovered `<tr>` Summaries, Auto-Folded `<footer>` Links & Zero-CSP `tab.text()`**:
   - Added `collectPrimaryStaticEntries` (surfacing concise leaf `<main>`/`[role="main"]` confirmation/price copy and uncovered multi-cell `<tr>` rows as `@uN text` / `@uN row`) and `getPageFooterScope` in `IN_PAGE_RUNTIME_JS` to fold `<footer>`/`[role="contentinfo"]` directories (>4 items) into `# footer: +N items folded (@uA..@uB indexed; pass fullPage=true or query=... to expand)` while keeping folded links directly clickable by UID or label.
   - Added `op: "text"` (`extractReadableText`) and `tab.text(selector?, maxChars?)` (works both as a value expression `const s = await tab.text("#summary")` and as a chained step) with zero `AsyncFunction` / CSP `EvalError` escalations.

3. **`cua.listTabs()`, Non-Clobbering `getTab()` / `reuseExisting`, Bare-Domain Normalization & Cross-Agent Handle Lookup**:
   - Added `cua.listTabs(query)` (`{"listTabs": ...}`), integer `chromeTabId` attach (`cua.getTab(501)`), non-destructive `getTab` RPC in [`extension/background.js`](file:///Users/lfan/Project/faster-mac-use/extension/background.js), `urlAlreadyMatchesTarget` deep-URL preservation on `reuseExisting`, `_normalize_nav_url` (`hertz.com` -> `https://hertz.com`), and cross-agent `<agent>:tN` lookup (raising a clear error on nonexistent `<agent>:t99` instead of spawning `about:blank`).

4. **Chained Smart-Diff `snapshot()`, SPA `pushState` Diff Preservation & MV3 IPC Caching**:
   - Chained `.snapshot()` after pending steps (`tab.click(...).snapshot()`) now defaults to smart diff (`= unchanged` or `+/~/-`) unless `full=True` / `fullPage=True` / `fullTable=True` / `query` is passed, while standalone `tab.snapshot()` remains a full snapshot.
   - Preserved diff baselines across SPA `?query`/`#hash` `history.pushState` transitions on the same origin+path.
   - Cached 75KB `runtimeJs` in `background.js` (`cachedRuntimeJs` + `initializedRuntimeTabs` per `tabId`), skipped redundant `ensure_runtime()` on initialized MV3 tabs, and skipped redundant canvas Vision OCR on non-mutating `waitFor`/`wait`/`text` batches.

5. **JS-Compatible `code` Transpiler & Bare `evalValue` Serialization**:
   - Added multiline JS string newline escaping, JS template literal interpolation (`` `${a}:${b}` ``), object/array destructuring (`const { a, b } = ...`, `const [a, b] = ...`), JS string/array methods (`.includes()`, `.slice()`, `.join()`, `.trim()`, `.startsWith()`, `.endsWith()`, `.toLowerCase()`, `.toUpperCase()`), safe read-only builtins (`any`, `all`, `zip`, `map`, `filter`, `reversed`, `next`, `repr`), and JSON serialization for bare non-`str` expressions (`await tab.evalValue(...)`).

## 3. Measured Before / After Verification (`v0.4.0`)

| Scenario | Before (`v0.3.x`) | After (`v0.4.0`) |
| --- | --- | --- |
| Waiting for async promo / DOM state (`timeoutMs=4000`, settles at `90ms`) | `4,000ms` fixed `.wait(4000)` | **`~110ms`** (`MutationObserver` immediate wakeup, **-97% wait time**) |
| Live `github.com/about` (strict CSP, 59 footer links) 1st snapshot + `<main>` read | Required full footer dump + `eval` (`EvalError` escalation) | `3,502` chars (`# footer: +59 items folded`), `tab.text("main")` in **0 `EvalError` escalations** |
| Live `github.com/about` 2nd-turn chained `tab.waitFor("GitHub", 3000).snapshot()` | `~3,280ms`, `3,502+` chars (`mode_out="snapshot"`, `runtimeJs` resent) | **`159.6ms`** (`tab_attach: 0.15ms`, `bridge_overhead: 5.95ms`), **`130` chars** (`= unchanged`, **-96.3% tokens**) |
| `getTab("hertz.com")` / `reuseExisting` on `/us/en/book/vehicles` | Navigated back to root or `chrome-error://chromewebdata/` | **`0` `chrome.tabs.update` URL clobbers**; deep `/us/en/book/vehicles` preserved |
| Regression suite (`python3 tests/test_fmu.py`) | 29 tests passing | **30 / 30 tests passing** (including `test_30` YYC audit + MV3 bridge harness) |

---

## 4. Earlier Baseline Audit (`2026-11-08` .. `2026-11-10`, `165` calls)

- Daemon cold-start session reattachment (`reattach` in `background.js` + `BrowserEngine`), `uidToMeta` SPA disconnected `@uN` rebinding, closed/open Shadow DOM (`_sr`) traversal, folded `<tr>` diff aggregation (`-88%` tokens on Gmail), `fmu-ax` tolerant `StepSpec` decoding, and unified `fmu-ax` binary stat caching.
