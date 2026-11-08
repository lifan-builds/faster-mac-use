"""Browser Interaction Engine: Unfocused '🤖 Agent' tabs, Multi-Cursor Isolation, Focus Emulation, WeakRef UIDs, WebMCP, and CRC-32 Delta Reader."""

from __future__ import annotations
import json
from pathlib import Path
import queue
import threading
import time
from typing import Any, Callable

IN_PAGE_RUNTIME_JS = r"""
(() => {
  if (window.__fmu) return true;

  const elToUid = new WeakMap();
  const uidToRef = new Map();
  const agentStates = new Map();
  let nextUid = 1;
  let mutationSeq = 1;

  function getAgentState(agentId = "default") {
    const key = String(agentId || "default");
    let st = agentStates.get(key);
    if (!st) {
      st = {
        agent: key,
        lastSnapSeq: -1,
        lastLinesByUid: new Map(),
        lastCrc: "00000000",
        lastQuery: "",
        lastFoldTables: true,
        cursor: { agent: key, x: 0, y: 0, uid: "", label: "", op: "init" }
      };
      agentStates.set(key, st);
    }
    return st;
  }

  function resetAgent(agentId) {
    if (!agentId || agentId === "all") {
      agentStates.clear();
    } else {
      agentStates.delete(String(agentId));
    }
    return true;
  }

  const observer = new MutationObserver(() => { mutationSeq++; });
  if (document.documentElement) {
    observer.observe(document.documentElement, {
      subtree: true,
      childList: true,
      attributes: true,
      characterData: true
    });
  }
  window.addEventListener("input", () => { mutationSeq++; }, true);
  window.addEventListener("change", () => { mutationSeq++; }, true);

  function crc32(str) {
    let crc = 0xFFFFFFFF;
    for (let i = 0; i < str.length; i++) {
      crc ^= str.charCodeAt(i) & 0xFF;
      for (let j = 0; j < 8; j++) {
        crc = (crc >>> 1) ^ (0xEDB88320 & -(crc & 1));
      }
    }
    return ((crc ^ 0xFFFFFFFF) >>> 0).toString(16).padStart(8, "0");
  }

  function getUid(el) {
    let uid = elToUid.get(el);
    if (!uid) {
      uid = "@u" + (nextUid++);
      elToUid.set(el, uid);
    }
    uidToRef.set(uid, new WeakRef(el));
    return uid;
  }

  function resolveEl(target) {
    if (!target) return null;
    const t = String(target).trim();
    if (t.startsWith("@u") || t.startsWith("@e") || t.startsWith("@a")) {
      const norm = "@u" + t.slice(2);
      const ref = uidToRef.get(norm);
      if (ref) {
        const el = ref.deref();
        if (el && el.isConnected) return el;
      }
    }
    try {
      const bySel = document.querySelector(t);
      if (bySel) return bySel;
    } catch (_) {}
    const byId = document.getElementById(t);
    if (byId) return byId;
    const lower = t.toLowerCase();
    const candidates = document.querySelectorAll("button, a, input, select, textarea, [role], label, h1, h2, h3, [data-testid]");
    for (const el of candidates) {
      const label = getLabel(el).toLowerCase();
      if (label === lower) return el;
    }
    for (const el of candidates) {
      const label = getLabel(el).toLowerCase();
      if (label.includes(lower)) return el;
    }
    return null;
  }

  function isVisible(el) {
    if (!el || !el.isConnected) return false;
    if (el.hasAttribute && el.hasAttribute("data-fmu-cursor")) return false;
    const style = window.getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }

  function cleanStr(s, maxLen = 80) {
    const c = String(s || "").replace(/\s+/g, " ").trim();
    return c.length > maxLen ? c.slice(0, maxLen - 1) + "…" : c;
  }

  function getRole(el) {
    const explicit = el.getAttribute("role");
    if (explicit) return explicit.toLowerCase();
    const tag = el.tagName.toLowerCase();
    if (tag === "a") return "link";
    if (tag === "button") return "button";
    if (tag === "select") return "select";
    if (tag === "textarea") return "textbox";
    if (tag === "input") {
      const type = (el.getAttribute("type") || "text").toLowerCase();
      if (type === "submit" || type === "button" || type === "reset") return "button";
      if (type === "checkbox") return "checkbox";
      if (type === "radio") return "radio";
      return "input:" + type;
    }
    if (/^h[1-6]$/.test(tag)) return "heading";
    return tag;
  }

  function getLabel(el) {
    const aria = el.getAttribute("aria-label") || el.getAttribute("title") || el.getAttribute("placeholder");
    if (aria) return cleanStr(aria);
    if (el.id) {
      try {
        const lbl = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
        if (lbl) return cleanStr(lbl.innerText || lbl.textContent);
      } catch (_) {}
    }
    const tag = el.tagName.toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select") {
      return cleanStr(el.name || el.id || "");
    }
    return cleanStr(el.innerText || el.textContent || "");
  }

  function getValue(el) {
    const tag = el.tagName.toLowerCase();
    if (tag === "input") {
      const type = (el.getAttribute("type") || "text").toLowerCase();
      if (type === "checkbox" || type === "radio") return el.checked ? "checked" : "unchecked";
      return cleanStr(el.value || "", 60);
    }
    if (tag === "textarea" || tag === "select") {
      return cleanStr(el.value || "", 60);
    }
    if (el.getAttribute("aria-checked")) {
      return el.getAttribute("aria-checked") === "true" ? "checked" : "unchecked";
    }
    return "";
  }

  function collectSnapshot(query = "", maxItems = 180, foldTables = true) {
    const q = String(query || "").toLowerCase();
    const sel = "button, a[href], input, select, textarea, [role='button'], [role='link'], [role='tab'], [role='menuitem'], [role='checkbox'], [role='switch'], [role='option'], [contenteditable='true'], h1, h2, h3, [data-status], [role='status'], [role='alert'], output, .result, #result, #status";
    const nodes = document.querySelectorAll(sel);
    const currentMap = new Map();
    const lines = [];
    const tbodyRows = new Map();
    const foldedStats = new Map();
    const maxVisibleTableRows = 6;

    for (const el of nodes) {
      if (currentMap.size >= maxItems) break;
      if (!isVisible(el)) continue;
      const role = getRole(el);
      const label = getLabel(el);
      const val = getValue(el);
      if (!label && !val && !role.startsWith("input") && role !== "textbox" && role !== "select") continue;
      if (q && !label.toLowerCase().includes(q) && !val.toLowerCase().includes(q) && !role.includes(q)) continue;

      const uid = getUid(el);
      let body = role;
      if (label) body += ` "${label}"`;
      if (val && val !== label) body += ` val="${val}"`;
      if (el.disabled) body += " [disabled]";
      const fullLine = `${uid} ${body}`;
      currentMap.set(uid, { uid, role, label, val, body, fullLine });

      if (foldTables && !q) {
        const tr = el.closest ? el.closest("tbody tr") : null;
        const tbody = tr ? tr.parentElement : null;
        if (tbody && tr) {
          let rowSet = tbodyRows.get(tbody);
          if (!rowSet) {
            rowSet = new Set();
            tbodyRows.set(tbody, rowSet);
          }
          rowSet.add(tr);
          if (rowSet.size > maxVisibleTableRows) {
            let st = foldedStats.get(tbody);
            if (!st) {
              st = { rows: new Set(), count: 0, firstUid: uid, lastUid: uid };
              foldedStats.set(tbody, st);
            }
            st.rows.add(tr);
            st.count += 1;
            st.lastUid = uid;
            continue;
          }
        }
      }
      lines.push(fullLine);
    }

    for (const st of foldedStats.values()) {
      lines.push(`# ... +${st.rows.size} rows (${st.firstUid}..${st.lastUid}, ${st.count} items indexed; target by label/UID)`);
    }

    const allFullLines = [];
    for (const item of currentMap.values()) {
      allFullLines.push(item.fullLine);
    }
    const newCrc = crc32(allFullLines.join("\n"));
    return { currentMap, lines, newCrc, totalCount: currentMap.size };
  }

  function computeDiff(prevMap, curMap) {
    const delta = [];
    for (const [uid, oldItem] of prevMap.entries()) {
      if (!curMap.has(uid)) {
        delta.push(`- ${oldItem.fullLine}`);
      }
    }
    for (const [uid, curItem] of curMap.entries()) {
      const oldItem = prevMap.get(uid);
      if (!oldItem) {
        delta.push(`+ ${curItem.fullLine}`);
      } else if (oldItem.body !== curItem.body) {
        if (oldItem.val !== curItem.val) {
          delta.push(`~ ${uid} ${curItem.role} "${curItem.label}" val="${oldItem.val}" -> val="${curItem.val}"`);
        } else if (oldItem.label !== curItem.label) {
          delta.push(`~ ${uid} ${curItem.role} "${oldItem.label}" -> "${curItem.label}"`);
        } else {
          delta.push(`~ ${curItem.fullLine}`);
        }
      }
    }
    return delta;
  }

  function setNativeValue(el, value) {
    const tag = el.tagName.toLowerCase();
    const proto = tag === "textarea"
      ? window.HTMLTextAreaElement.prototype
      : window.HTMLInputElement.prototype;
    const desc = Object.getOwnPropertyDescriptor(proto, "value");
    if (desc && desc.set) {
      desc.set.call(el, String(value));
    } else {
      el.value = String(value);
    }
    el.dispatchEvent(new InputEvent("input", { bubbles: true, data: String(value) }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  async function waitQuietDom(quietMs = 15, maxWaitMs = 110) {
    const start = performance.now();
    let lastSeenSeq = mutationSeq;
    let lastChangeTime = start;
    while (performance.now() - start < maxWaitMs) {
      await new Promise(r => setTimeout(r, 6));
      if (mutationSeq !== lastSeenSeq) {
        lastSeenSeq = mutationSeq;
        lastChangeTime = performance.now();
      } else if (performance.now() - lastChangeTime >= quietMs) {
        break;
      }
    }
  }

  async function invokeWebMcp(toolName, args) {
    const ctx = navigator.modelContext;
    if (!ctx) throw new Error("navigator.modelContext (WebMCP) not available on page");
    if (typeof ctx.callTool === "function") {
      return await ctx.callTool(toolName, args || {});
    }
    const tools = Array.isArray(ctx.tools) ? ctx.tools : [];
    const found = tools.find(t => t.name === toolName);
    if (!found || typeof found.execute !== "function") {
      throw new Error(`WebMCP tool not found or not executable: ${toolName}`);
    }
    return await found.execute(args || {});
  }

  function updateAgentCursor(st, el, op, explicitX, explicitY) {
    if (typeof explicitX === "number" && typeof explicitY === "number") {
      st.cursor = {
        agent: st.agent,
        x: Math.round(explicitX),
        y: Math.round(explicitY),
        uid: el ? getUid(el) : "@xy",
        label: el ? getLabel(el) : "",
        op
      };
      return;
    }
    if (el) {
      const rect = el.getBoundingClientRect();
      st.cursor = {
        agent: st.agent,
        x: Math.round(rect.left + rect.width / 2),
        y: Math.round(rect.top + rect.height / 2),
        uid: getUid(el),
        label: getLabel(el),
        op
      };
    }
  }

  async function executeBatch(req) {
    const agentId = String(req.agent || "default");
    const st = getAgentState(agentId);
    const steps = req.steps || [];
    const query = String(req.query || "");
    const foldTables = req.fullTable !== true;
    const viewChanged = (query !== st.lastQuery) || (foldTables !== st.lastFoldTables);
    const forceFull = req.diff === false || req.fullTable === true || viewChanged;
    const hadBaseline = st.lastLinesByUid.size > 0 && !viewChanged;

    if (steps.length === 0 && req.diff === true && !forceFull && hadBaseline && mutationSeq === st.lastSnapSeq) {
      return {
        unchanged: true,
        crc: st.lastCrc,
        seq: mutationSeq,
        count: st.lastLinesByUid.size,
        cursor: st.cursor,
        lines: [`= unchanged (crc=${st.lastCrc}, seq=${mutationSeq})`]
      };
    }

    if (!hadBaseline && !query) {
      const base = collectSnapshot("", 180, foldTables);
      st.lastLinesByUid = base.currentMap;
      st.lastCrc = base.newCrc;
      st.lastSnapSeq = mutationSeq;
      st.lastQuery = "";
      st.lastFoldTables = foldTables;
    }

    const errors = [];
    const evalOutputs = [];
    const escalations = [];

    for (let i = 0; i < steps.length; i++) {
      const s = steps[i];
      let op = String(s.op || s.action || "").toLowerCase();
      const targetRef = s.uid || s.ref || s.target || s.selector || s.name;
      if (op === "press" && targetRef && String(targetRef).startsWith("@") && !s.key) {
        op = "click";
      }
      if (op === "hover" || op === "cursor" || op === "point") {
        op = "move";
      }
      try {
        if (op === "wait") {
          await new Promise(r => setTimeout(r, Number(s.ms || 25)));
          continue;
        }
        if (op === "eval") {
          const code = String(s.code || s.value || "");
          const body = code.includes("return ") ? code : `return (${code})`;
          const fn = new Function(body);
          const res = await fn();
          if (res !== undefined) evalOutputs.push(typeof res === "string" ? res : JSON.stringify(res));
          continue;
        }
        if (op === "webmcp" || op === "calltool") {
          const tName = s.tool || s.name || s.target;
          const res = await invokeWebMcp(tName, s.args || s.value || {});
          mutationSeq++;
          if (res !== undefined) {
            evalOutputs.push(`webmcp(${tName}): ${typeof res === "string" ? res : JSON.stringify(res)}`);
          }
          continue;
        }
        if (op === "press" || op === "key") {
          const key = s.key || s.name || s.value || "Enter";
          const active = document.activeElement || document.body;
          active.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
          active.dispatchEvent(new KeyboardEvent("keyup", { key, bubbles: true }));
          if (key === "Enter" && active && active.form) {
            active.form.requestSubmit ? active.form.requestSubmit() : active.form.submit();
          }
          continue;
        }

        if (op === "move" && typeof s.x === "number" && typeof s.y === "number") {
          const hitEl = document.elementFromPoint(s.x, s.y);
          updateAgentCursor(st, hitEl, "move", s.x, s.y);
          continue;
        }

        const el = resolveEl(targetRef);
        if (!el) {
          if (!s.optional) errors.push(`step ${i + 1} (${op}): target not found (${targetRef})`);
          continue;
        }

        el.scrollIntoView({ block: "center", inline: "center", behavior: "instant" });
        updateAgentCursor(st, el, op);

        if (op === "move") {
          continue;
        } else if (op === "click" || op === "upload") {
          let fileEl = (el.tagName.toLowerCase() === "input" && (el.type || "").toLowerCase() === "file") ? el : null;
          if (!fileEl && (s.files || op === "upload")) {
            fileEl = el.querySelector && el.querySelector("input[type='file']");
            if (!fileEl && el.tagName.toLowerCase() === "label" && el.htmlFor) {
              const forEl = document.getElementById(el.htmlFor);
              if (forEl && forEl.tagName.toLowerCase() === "input" && (forEl.type || "").toLowerCase() === "file") {
                fileEl = forEl;
              }
            }
          }
          if (fileEl || s.files || op === "upload") {
            const targetFileEl = fileEl || el;
            const fUid = getUid(targetFileEl);
            targetFileEl.setAttribute("data-fmu-upload-uid", fUid);
            escalations.push({ step: i + 1, reason: "file_input", uid: fUid });
            continue;
          }
          el.focus({ preventScroll: true });
          el.click();
        } else if (op === "fill" || op === "set" || op === "type") {
          el.focus({ preventScroll: true });
          setNativeValue(el, s.value ?? "");
        } else if (op === "select") {
          el.focus({ preventScroll: true });
          const desired = String(s.value ?? "");
          let matched = false;
          if (el.options) {
            for (const opt of el.options) {
              if (opt.value === desired || cleanStr(opt.text).toLowerCase() === desired.toLowerCase()) {
                el.value = opt.value;
                matched = true;
                break;
              }
            }
          }
          if (!matched) el.value = desired;
          el.dispatchEvent(new Event("input", { bubbles: true }));
          el.dispatchEvent(new Event("change", { bubbles: true }));
        }
      } catch (err) {
        errors.push(`step ${i + 1} (${op}): ${err.message || err}`);
      }
    }

    if (req.skipSnapshot) {
      return { errors, evalOutputs, escalations, cursor: st.cursor, lines: [] };
    }

    if ((steps.length > 0 || req.hadPreSteps) && req.settle !== false) {
      await waitQuietDom(14, 100);
    }

    const prevMap = st.lastLinesByUid;
    const prevCrc = st.lastCrc;
    const snap = collectSnapshot(query, 180, foldTables);
    if (!query) {
      st.lastLinesByUid = snap.currentMap;
      st.lastCrc = snap.newCrc;
      st.lastSnapSeq = mutationSeq;
    }
    st.lastQuery = query;
    st.lastFoldTables = foldTables;

    const webMcpTools = (navigator.modelContext && Array.isArray(navigator.modelContext.tools))
      ? navigator.modelContext.tools.map(t => t.name)
      : [];

    const useDiff = !forceFull && !query && (hadBaseline || steps.length > 0 || req.hadPreSteps) && (req.diff === true || ((steps.length > 0 || req.hadPreSteps) && hadBaseline));
    let outLines = [];
    if (useDiff) {
      if (prevCrc === snap.newCrc) {
        outLines = [`= unchanged (crc=${snap.newCrc}, seq=${mutationSeq})`];
      } else {
        const delta = computeDiff(prevMap, snap.currentMap);
        outLines = delta.length > 0 ? delta : [`= unchanged (crc=${snap.newCrc}, seq=${mutationSeq})`];
      }
    } else {
      outLines = snap.lines;
    }

    return {
      url: location.href,
      title: document.title,
      crc: snap.newCrc,
      seq: mutationSeq,
      count: snap.totalCount,
      cursor: st.cursor,
      webMcpTools,
      errors,
      evalOutputs,
      escalations,
      lines: outLines
    };
  }

  window.__fmu = { executeBatch, collectSnapshot, resolveEl, invokeWebMcp, getAgentState, resetAgent };
  return true;
})();
"""

_GROUP_COLORS = ["blue", "purple", "cyan", "green", "orange", "pink", "yellow", "red"]


class _ThreadProxy:
    """Forwards Playwright object attribute/method access onto the dedicated Playwright worker thread."""

    def __init__(self, engine: BrowserEngine, target: Any) -> None:
        object.__setattr__(self, "_engine", engine)
        object.__setattr__(self, "_target", target)

    def __bool__(self) -> bool:
        tgt = object.__getattribute__(self, "_target")
        return bool(tgt)

    def __getattr__(self, name: str) -> Any:
        eng: BrowserEngine = object.__getattribute__(self, "_engine")
        tgt: Any = object.__getattribute__(self, "_target")
        attr = eng._run_sync(lambda: getattr(tgt, name))
        if callable(attr):

            def _call(*args: Any, **kwargs: Any) -> Any:
                res = eng._run_sync(lambda: getattr(tgt, name)(*args, **kwargs))
                if res is not None and not isinstance(
                    res, (bool, int, float, str, bytes, list, dict, tuple, set)
                ):
                    return _ThreadProxy(eng, res)
                return res

            return _call
        if attr is not None and not isinstance(
            attr, (bool, int, float, str, bytes, list, dict, tuple, set)
        ):
            return _ThreadProxy(eng, attr)
        return attr


class BrowserTabSession:
    def __init__(
        self,
        engine: BrowserEngine,
        tab_id: str,
        page: Any,
        cdp_session: Any,
        group_name: str = "🤖 Agent",
        group_color: str = "blue",
        last_url: str = "about:blank",
    ) -> None:
        self._engine = engine
        self.tab_id = tab_id
        self._raw_page = page
        self._raw_cdp = cdp_session
        self.page = _ThreadProxy(engine, page)
        self.cdp = _ThreadProxy(engine, cdp_session)
        self.group_name = group_name
        self.group_color = group_color
        self.last_url = last_url
        self.documented = False
        self._documented_agents: set[str] = set()
        self._focus_emulated = False

    def enable_background_focus_emulation(self) -> None:
        if self._focus_emulated:
            return
        try:
            self._raw_cdp.send("Emulation.setFocusEmulationEnabled", {"enabled": True})
        except Exception:
            pass
        try:
            self._raw_cdp.send("Page.setWebLifecycleState", {"state": "active"})
        except Exception:
            pass
        self._focus_emulated = True

    def ensure_runtime(self) -> None:
        self._raw_page.evaluate(IN_PAGE_RUNTIME_JS)


class BrowserEngine:
    def __init__(self) -> None:
        self._pw: Any = None
        self._raw_browser: Any = None
        self._context: Any = None
        self._is_cdp_chrome: bool = False
        self._tabs: dict[str, BrowserTabSession] = {}
        self._tab_last_urls: dict[str, str] = {}
        self._next_tab_num: int = 1
        self.cursors: dict[str, dict[str, Any]] = {}
        self._agent_colors: dict[str, str] = {}

        self._cmd_q: queue.Queue[tuple[Callable[[], Any], threading.Event, list[Any]] | None] = queue.Queue()
        self._worker_tid: int = 0
        self._worker = threading.Thread(target=self._worker_loop, name="fmu-pw-worker", daemon=True)
        self._worker.start()

    def _worker_loop(self) -> None:
        self._worker_tid = threading.get_ident()
        while True:
            item = self._cmd_q.get()
            if item is None:
                break
            fn, ev, box = item
            try:
                box.append(fn())
                box.append(None)
            except Exception as exc:
                box.append(None)
                box.append(exc)
            finally:
                ev.set()

    def _run_sync(self, fn: Callable[[], Any]) -> Any:
        if threading.get_ident() == self._worker_tid:
            return fn()
        ev = threading.Event()
        box: list[Any] = []
        self._cmd_q.put((fn, ev, box))
        ev.wait()
        if box[1] is not None:
            raise box[1]
        return box[0]

    @property
    def _browser(self) -> Any:
        if self._raw_browser is None:
            return None
        return _ThreadProxy(self, self._raw_browser)

    @_browser.setter
    def _browser(self, val: Any) -> None:
        self._raw_browser = val

    def _get_agent_color(self, agent_id: str) -> str:
        if agent_id not in self._agent_colors:
            self._agent_colors[agent_id] = _GROUP_COLORS[len(self._agent_colors) % len(_GROUP_COLORS)]
        return self._agent_colors[agent_id]

    def _is_browser_alive(self) -> bool:
        try:
            return self._raw_browser is not None and bool(self._raw_browser.is_connected())
        except Exception:
            return False

    def _is_session_alive(self, sess: BrowserTabSession) -> bool:
        try:
            return self._is_browser_alive() and not bool(sess._raw_page.is_closed())
        except Exception:
            return False

    def _evict_dead_browser(self) -> None:
        self._tabs.clear()
        self._context = None
        try:
            if self._raw_browser:
                self._raw_browser.close()
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._raw_browser = None
        self._pw = None
        self._is_cdp_chrome = False

    def _find_chrome_devtools_ws(self) -> str | None:
        import os

        # Never read ~/Library/Application Support/Google/Chrome/DevToolsActivePort:
        # Chrome 136+/144+ pops up a focus-stealing modal ("Allow remote debugging?")
        # on every new incoming WebSocket connection to the default user profile.
        cdp_url = os.environ.get("FMU_CDP_URL", "").strip()
        return cdp_url if cdp_url else None

    def _ensure_browser(self, mode: str = "chrome") -> Any:
        return self._run_sync(lambda: self._ensure_browser_inner(mode))

    def _ensure_browser_inner(self, mode: str = "chrome") -> Any:
        if self._raw_browser is not None:
            if self._is_browser_alive():
                return self._raw_browser
            self._evict_dead_browser()

        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        if mode == "chrome":
            ws_url = self._find_chrome_devtools_ws()
            if ws_url:
                try:
                    self._raw_browser = self._pw.chromium.connect_over_cdp(ws_url, timeout=1500)
                    self._context = (
                        self._raw_browser.contexts[0]
                        if self._raw_browser.contexts
                        else self._raw_browser.new_context()
                    )
                    self._is_cdp_chrome = True
                    return self._raw_browser
                except Exception:
                    pass
        headless_shells = sorted(
            (Path.home() / "Library" / "Caches" / "ms-playwright").glob(
                "chromium_headless_shell-*/chrome-headless-shell-mac-*/chrome-headless-shell"
            )
        )
        chrome_bin = (
            str(headless_shells[-1])
            if headless_shells
            else "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        )
        launch_kwargs: dict[str, Any] = {
            "headless": True,
            "args": [
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                "--silent-debugger-extension-api",
            ],
        }
        if Path(chrome_bin).is_file():
            launch_kwargs["executable_path"] = chrome_bin
        self._raw_browser = self._pw.chromium.launch(**launch_kwargs)
        self._context = self._raw_browser.new_context(viewport={"width": 1280, "height": 800})
        self._is_cdp_chrome = False
        return self._raw_browser

    def _try_assign_chrome_tab_group(self, page_url: str, session_name: str, color: str = "blue") -> None:
        if not self._is_cdp_chrome or not self._raw_browser:
            return
        try:
            b_cdp = self._raw_browser.new_browser_cdp_session()
            targets = b_cdp.send("Target.getTargets").get("targetInfos", [])
            sw = next(
                (
                    t
                    for t in targets
                    if t.get("type") == "service_worker"
                    and str(t.get("url", "")).startswith("chrome-extension://")
                ),
                None,
            )
            if sw:
                attached = b_cdp.send(
                    "Target.attachToTarget", {"targetId": sw["targetId"], "flatten": True}
                )
                sid = attached.get("sessionId")
                if sid:
                    expr = (
                        "(async () => {"
                        "  if (!chrome?.tabGroups || !chrome?.tabs) return false;"
                        f"  const tabs = await chrome.tabs.query({{url: {json.dumps(page_url)}}});"
                        "  if (!tabs.length) return false;"
                        "  const tid = tabs[tabs.length - 1].id;"
                        f"  const gs = await chrome.tabGroups.query({{title: {json.dumps(session_name)}}});"
                        "  if (gs.length) { await chrome.tabs.group({tabIds: [tid], groupId: gs[0].id}); }"
                        "  else {"
                        "    const gid = await chrome.tabs.group({tabIds: [tid]});"
                        f"    await chrome.tabGroups.update(gid, {{title: {json.dumps(session_name)}, color: {json.dumps(color)}, collapsed: false}});"
                        "  }"
                        "  return true;"
                        "})()"
                    )
                    b_cdp.send(
                        "Runtime.evaluate",
                        {"expression": expr, "awaitPromise": True},
                    )
            b_cdp.detach()
        except Exception:
            pass

    def create_tab(
        self,
        url: str = "about:blank",
        mode: str = "chrome",
        session_name: str = "🤖 Agent",
        tab_id: str | None = None,
        group_color: str = "blue",
    ) -> BrowserTabSession:
        return self._run_sync(lambda: self._create_tab_inner(url, mode, session_name, tab_id, group_color))

    def _create_tab_inner(
        self,
        url: str = "about:blank",
        mode: str = "chrome",
        session_name: str = "🤖 Agent",
        tab_id: str | None = None,
        group_color: str = "blue",
    ) -> BrowserTabSession:
        self._ensure_browser_inner(mode=mode)
        tid = tab_id or f"t{self._next_tab_num}"
        if tid in self._tabs:
            sess = self._tabs[tid]
            if self._is_session_alive(sess):
                if url and url != "about:blank" and sess._raw_page.url != url:
                    sess._raw_page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    sess.last_url = url
                    self._tab_last_urls[tid] = url
                    sess._focus_emulated = False
                    sess.enable_background_focus_emulation()
                    sess.ensure_runtime()
                return sess
            self._tabs.pop(tid, None)

        self._next_tab_num += 1
        page = self._context.new_page()
        cdp = self._context.new_cdp_session(page)
        effective_url = url or self._tab_last_urls.get(tid) or "about:blank"
        sess = BrowserTabSession(
            engine=self,
            tab_id=tid,
            page=page,
            cdp_session=cdp,
            group_name=session_name,
            group_color=group_color,
            last_url=effective_url,
        )
        sess.enable_background_focus_emulation()
        self._tabs[tid] = sess

        if effective_url and effective_url != "about:blank":
            page.goto(effective_url, wait_until="domcontentloaded", timeout=15000)
            self._tab_last_urls[tid] = effective_url
            self._try_assign_chrome_tab_group(page.url, session_name, color=group_color)
        sess.ensure_runtime()
        return sess

    def get_tab(self, tab_id: str | None = None) -> BrowserTabSession:
        return self._run_sync(lambda: self._get_tab_inner(tab_id))

    def _get_tab_inner(self, tab_id: str | None = None) -> BrowserTabSession:
        if tab_id and tab_id in self._tabs and self._is_session_alive(self._tabs[tab_id]):
            return self._tabs[tab_id]
        for tid, sess in list(self._tabs.items()):
            if self._is_session_alive(sess):
                return sess
            self._tabs.pop(tid, None)
        return self._create_tab_inner(self._tab_last_urls.get(tab_id or "t1", "about:blank"), tab_id=tab_id)

    def close_tab(self, tab_id: str) -> str:
        return self._run_sync(lambda: self._close_tab_inner(tab_id))

    def _close_tab_inner(self, tab_id: str) -> str:
        sess = self._tabs.pop(tab_id, None)
        self._tab_last_urls.pop(tab_id, None)
        if sess is not None:
            try:
                sess._raw_page.close()
            except Exception:
                pass
        return f"# web tab={tab_id} closed"

    def execute_batch(self, req: dict[str, Any]) -> str:
        return self._run_sync(lambda: self._execute_batch_with_retry(req))

    def _execute_batch_with_retry(self, req: dict[str, Any]) -> str:
        try:
            return self._execute_batch_inner(req)
        except Exception as exc:
            msg = str(exc)
            if any(
                tok in msg
                for tok in (
                    "Target page, context or browser has been closed",
                    "TargetClosedError",
                    "Connection closed",
                    "Session closed",
                    "Browser has been closed",
                )
            ) or not self._is_browser_alive():
                tab_id = req.get("session") or req.get("tab") or "t1"
                self._tabs.pop(tab_id, None)
                if not self._is_browser_alive():
                    self._evict_dead_browser()
                return self._execute_batch_inner(req)
            raise

    def _execute_batch_inner(self, req: dict[str, Any]) -> str:
        t0 = time.perf_counter()
        if req.get("reset"):
            self._reset_inner(agent=req.get("agent") or req.get("cursor"))
            return "# fmu-web state reset"

        agent_id = str(req.get("agent") or req.get("cursor") or "default").strip() or "default"
        default_tab = "t1" if agent_id == "default" else f"{agent_id}:t1"
        tab_id = str(req.get("session") or req.get("tab") or default_tab)
        default_group = "🤖 Agent" if agent_id == "default" else f"🤖 {agent_id}"
        group_name = str(req.get("sessionName") or req.get("group") or default_group)
        group_color = self._get_agent_color(agent_id)
        mode = req.get("mode", "chrome")
        url = req.get("url")
        steps = list(req.get("steps") or [])

        if any((s.get("op") or s.get("action")) == "close" for s in steps) and len(steps) == 1:
            return self._close_tab_inner(tab_id)

        while steps and (steps[0].get("op") or steps[0].get("action")) in ("open", "goto", "navigate"):
            step_url = str(steps[0].get("url") or steps[0].get("value") or "").strip()
            if step_url:
                url = step_url
            steps = steps[1:]

        if tab_id not in self._tabs or not self._is_session_alive(self._tabs[tab_id]):
            self._tabs.pop(tab_id, None)
            restore_url = url or self._tab_last_urls.get(tab_id) or "about:blank"
            sess = self._create_tab_inner(
                url=restore_url,
                mode=mode,
                session_name=group_name,
                tab_id=tab_id,
                group_color=group_color,
            )
        else:
            sess = self._tabs[tab_id]
            if url and url != sess._raw_page.url:
                sess._raw_page.goto(url, wait_until="domcontentloaded", timeout=15000)
                sess.last_url = url
                self._tab_last_urls[tab_id] = url
                sess._focus_emulated = False
                sess.documented = False
                sess._documented_agents.clear()

        sess.enable_background_focus_emulation()
        sess.ensure_runtime()

        active_steps = [
            s for s in steps if (s.get("op") or s.get("action")) != "close"
        ]
        boundary_indices = [
            idx
            for idx, s in enumerate(active_steps)
            if s.get("files")
            or (s.get("op") or s.get("action")) in ("upload", "goto", "navigate", "open")
        ]

        if boundary_indices:
            last_idx = 0
            combined_errors: list[str] = []
            combined_evals: list[str] = []
            res: dict[str, Any] = {}
            had_pre_on_current_page = False
            for b_idx in boundary_indices:
                b_step = active_steps[b_idx]
                b_op = str(b_step.get("op") or b_step.get("action") or "").lower()
                if b_op in ("goto", "navigate", "open"):
                    pre_steps = active_steps[last_idx:b_idx]
                    if pre_steps:
                        res = sess._raw_page.evaluate(
                            "req => window.__fmu.executeBatch(req)",
                            {
                                "agent": agent_id,
                                "steps": pre_steps,
                                "query": req.get("query") or "",
                                "diff": req.get("diff"),
                                "fullTable": req.get("fullTable", False),
                                "settle": True,
                                "skipSnapshot": True,
                            },
                        )
                        combined_errors.extend(res.get("errors") or [])
                        combined_evals.extend(res.get("evalOutputs") or [])
                    nav_url = str(b_step.get("url") or b_step.get("value") or "").strip()
                    if nav_url and nav_url != sess._raw_page.url:
                        sess._raw_page.goto(nav_url, wait_until="domcontentloaded", timeout=15000)
                        sess.last_url = nav_url
                        self._tab_last_urls[tab_id] = nav_url
                        sess._focus_emulated = False
                        sess.enable_background_focus_emulation()
                        sess.ensure_runtime()
                        had_pre_on_current_page = False
                else:
                    pre_steps = active_steps[last_idx : b_idx + 1]
                    res = sess._raw_page.evaluate(
                        "req => window.__fmu.executeBatch(req)",
                        {
                            "agent": agent_id,
                            "steps": pre_steps,
                            "query": req.get("query") or "",
                            "diff": req.get("diff"),
                            "fullTable": req.get("fullTable", False),
                            "settle": False,
                            "skipSnapshot": True,
                        },
                    )
                    combined_errors.extend(res.get("errors") or [])
                    combined_evals.extend(res.get("evalOutputs") or [])
                    files = b_step.get("files") or b_step.get("value")
                    if files:
                        file_list = [files] if isinstance(files, str) else list(files)
                        escs = res.get("escalations") or []
                        esc_uid = escs[-1].get("uid") if escs else None
                        sel = f'[data-fmu-upload-uid="{esc_uid}"]' if esc_uid else "input[type='file']"
                        if esc_uid and sess._raw_page.locator(sel).count() == 0:
                            sel = "input[type='file']"
                        sess._raw_page.set_input_files(sel, file_list)
                    had_pre_on_current_page = True
                last_idx = b_idx + 1

            post_steps = active_steps[last_idx:]
            res = sess._raw_page.evaluate(
                "req => window.__fmu.executeBatch(req)",
                {
                    "agent": agent_id,
                    "steps": post_steps,
                    "query": req.get("query") or "",
                    "diff": req.get("diff"),
                    "fullTable": req.get("fullTable", False),
                    "settle": req.get("settle", True),
                    "hadPreSteps": had_pre_on_current_page or bool(post_steps),
                },
            )
            res["errors"] = combined_errors + (res.get("errors") or [])
            res["evalOutputs"] = combined_evals + (res.get("evalOutputs") or [])
        else:
            res = sess._raw_page.evaluate(
                "req => window.__fmu.executeBatch(req)",
                {
                    "agent": agent_id,
                    "steps": active_steps,
                    "query": req.get("query") or "",
                    "diff": req.get("diff"),
                    "fullTable": req.get("fullTable", False),
                    "settle": req.get("settle", True),
                },
            )

        if sess._raw_page and not sess._raw_page.is_closed() and sess._raw_page.url:
            sess.last_url = sess._raw_page.url
            self._tab_last_urls[tab_id] = sess._raw_page.url

        cur_info = res.get("cursor") or {}
        if cur_info and cur_info.get("uid"):
            self.cursors[agent_id] = {
                "agent": agent_id,
                "surface": "web",
                "target": sess.tab_id,
                "group": sess.group_name,
                "color": sess.group_color,
                "uid": cur_info.get("uid", ""),
                "label": cur_info.get("label", ""),
                "x": int(cur_info.get("x", 0)),
                "y": int(cur_info.get("y", 0)),
                "op": cur_info.get("op", ""),
            }

        if any((s.get("op") or s.get("action")) == "close" for s in steps):
            self._close_tab_inner(tab_id)

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        has_move = any((s.get("op") or s.get("action") or "").lower() in ("move", "hover", "cursor", "point") for s in active_steps)
        header = f'# web tab={sess.tab_id} group="{sess.group_name}"'
        if agent_id != "default":
            header += f' agent="{agent_id}"'
        if (agent_id != "default" or has_move) and cur_info.get("uid"):
            header += f' cursor={cur_info["uid"]}@({cur_info.get("x", 0)},{cur_info.get("y", 0)})'
        header += (
            f' els={res.get("count", 0)} crc={res.get("crc", "")} '
            f'seq={res.get("seq", 0)} ms={elapsed_ms:.1f}'
        )
        if res.get("webMcpTools"):
            header += f' webmcp={json.dumps(res["webMcpTools"])}'
        if (agent_id not in sess._documented_agents and not sess.documented) or req.get("showDocs"):
            header += "\n# api: click(uid|text), fill(uid,val), select(uid,val), move(uid|text), press(key), eval(code), callTool(name,args), snapshot(), diff()"
            sess.documented = True
            sess._documented_agents.add(agent_id)

        out = [header]
        if res.get("errors"):
            out.append("! errors: " + "; ".join(res["errors"]))
        if res.get("evalOutputs"):
            for ev in res["evalOutputs"]:
                out.append(f"> {ev}")
        out.extend(res.get("lines") or [])
        return "\n".join(out)

    def reset(self, agent: str | None = None) -> None:
        self._run_sync(lambda: self._reset_inner(agent))

    def _reset_inner(self, agent: str | None = None) -> None:
        if agent and agent not in ("default", "all"):
            prefix = f"{agent}:"
            for tid in [k for k in self._tabs if k.startswith(prefix) or k == agent]:
                self._close_tab_inner(tid)
            for sess in list(self._tabs.values()):
                try:
                    sess._raw_page.evaluate(
                        "aid => window.__fmu && window.__fmu.resetAgent && window.__fmu.resetAgent(aid)",
                        agent,
                    )
                    sess._documented_agents.discard(agent)
                except Exception:
                    pass
            self.cursors.pop(agent, None)
            return
        for sess in list(self._tabs.values()):
            try:
                sess._raw_page.close()
            except Exception:
                pass
        self._tabs.clear()
        self._tab_last_urls.clear()
        self.cursors.clear()
        self._next_tab_num = 1

    def shutdown(self) -> None:
        self._run_sync(self._shutdown_inner)

    def _shutdown_inner(self) -> None:
        self._reset_inner()
        self._evict_dead_browser()

