"""Browser Interaction Engine: Unfocused '🤖 Agent' tabs, Outbound MV3 WebSocket Bridge, Multi-Cursor Isolation, Focus Emulation, WeakRef UIDs, Native + Virtual WebMCP Macros, <canvas> Vision OCR Fusion, and CRC-32 Delta Reader."""

from __future__ import annotations
import base64
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import socket
import struct
import threading
import time
from urllib.parse import urlparse
from typing import Any, Callable

IN_PAGE_RUNTIME_JS = r"""
(() => {
  if (window.__fmu) return true;

  const elToUid = new WeakMap();
  const elToStableLabel = new WeakMap();
  const uidToRef = new Map();
  const agentStates = new Map();
  const virtualTools = new Map();
  const ocrTargets = new Map();
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

  function registerVirtualTools(tools) {
    if (!tools) return 0;
    const list = Array.isArray(tools) ? tools : Object.values(tools);
    for (const t of list) {
      if (t && t.name && Array.isArray(t.steps)) {
        virtualTools.set(String(t.name), {
          name: String(t.name),
          description: String(t.description || ""),
          steps: t.steps
        });
      }
    }
    return virtualTools.size;
  }

  function registerOcrTargets(items) {
    ocrTargets.clear();
    if (Array.isArray(items)) {
      let idx = 1;
      for (const item of items) {
        if (!item || !item.label) continue;
        const uid = item.uid || ("@ocr" + (idx++));
        ocrTargets.set(uid, {
          uid,
          label: String(item.label),
          x: Math.round(Number(item.x || 0)),
          y: Math.round(Number(item.y || 0)),
          w: Math.round(Number(item.w || 0)),
          h: Math.round(Number(item.h || 0)),
          canvasUid: String(item.canvasUid || "")
        });
      }
    }
    mutationSeq++;
    return ocrTargets.size;
  }

  function resolveOcrTarget(target) {
    if (!target || ocrTargets.size === 0) return null;
    const t = String(target).trim();
    if (t.startsWith("@ocr")) {
      return ocrTargets.get(t) || null;
    }
    const lower = t.toLowerCase();
    for (const item of ocrTargets.values()) {
      if (item.label.toLowerCase() === lower) return item;
    }
    for (const item of ocrTargets.values()) {
      if (item.label.toLowerCase().includes(lower)) return item;
    }
    return null;
  }

  function dispatchCanvasClick(ocrItem) {
    const x = ocrItem.x;
    const y = ocrItem.y;
    const targetEl = (ocrItem.canvasUid && resolveEl(ocrItem.canvasUid))
      || document.elementFromPoint(x, y)
      || document.querySelector("canvas")
      || document.body;
    if (!targetEl) return false;
    const evInit = {
      bubbles: true,
      cancelable: true,
      view: window,
      clientX: x,
      clientY: y,
      screenX: x,
      screenY: y,
      button: 0,
      buttons: 1
    };
    try { targetEl.dispatchEvent(new PointerEvent("pointerdown", evInit)); } catch (_) {}
    targetEl.dispatchEvent(new MouseEvent("mousedown", evInit));
    try { targetEl.dispatchEvent(new PointerEvent("pointerup", evInit)); } catch (_) {}
    targetEl.dispatchEvent(new MouseEvent("mouseup", evInit));
    targetEl.dispatchEvent(new MouseEvent("click", evInit));
    mutationSeq++;
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

  function isContentEditableEl(el) {
    if (!el || !el.getAttribute) return false;
    const ce = el.getAttribute("contenteditable");
    return Boolean(
      el.isContentEditable
      || ce === "true"
      || ce === "plaintext-only"
      || (ce === "" && el.hasAttribute("contenteditable"))
    );
  }

  function getCandidateAliases(el) {
    const aliases = [getLabel(el)];
    if (el && el.getAttribute) {
      for (const attr of ["aria-label", "placeholder", "data-placeholder", "aria-placeholder", "data-testid", "name", "title"]) {
        const v = el.getAttribute(attr);
        if (v) aliases.push(cleanStr(v));
      }
      if (el.parentElement && el.parentElement.closest) {
        const anc = el.parentElement.closest("[aria-label], [data-placeholder], [aria-placeholder], [data-testid]");
        if (anc) {
          for (const attr of ["aria-label", "data-placeholder", "aria-placeholder", "data-testid"]) {
            const v = anc.getAttribute(attr);
            if (v) aliases.push(cleanStr(v));
          }
        }
      }
    }
    return aliases.filter(Boolean).map(s => s.toLowerCase());
  }

  function resolveEl(target) {
    if (!target) return null;
    const t = String(target).trim();
    if (t.startsWith("@ocr")) return null;
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
    const candidates = document.querySelectorAll("button, a, input, select, textarea, canvas, [contenteditable='true'], [contenteditable=''], [contenteditable='plaintext-only'], [role], label, h1, h2, h3, [data-testid], [data-placeholder], [aria-placeholder]");
    for (const el of candidates) {
      const aliases = getCandidateAliases(el);
      if (aliases.includes(lower)) return el;
    }
    for (const el of candidates) {
      const aliases = getCandidateAliases(el);
      if (aliases.some(a => a.includes(lower))) return el;
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
    if (isContentEditableEl(el)) return "textbox";
    const tag = el.tagName.toLowerCase();
    if (tag === "a") return "link";
    if (tag === "button") return "button";
    if (tag === "select") return "select";
    if (tag === "textarea") return "textbox";
    if (tag === "canvas") return "canvas";
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
    const aria = el.getAttribute("aria-label")
      || el.getAttribute("title")
      || el.getAttribute("placeholder")
      || el.getAttribute("data-placeholder")
      || el.getAttribute("aria-placeholder");
    if (aria) {
      const cleaned = cleanStr(aria);
      if (isContentEditableEl(el)) elToStableLabel.set(el, cleaned);
      return cleaned;
    }
    if (el.id) {
      try {
        const lbl = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
        if (lbl) return cleanStr(lbl.innerText || lbl.textContent);
      } catch (_) {}
    }
    if (isContentEditableEl(el)) {
      const parent = el.parentElement;
      if (parent) {
        const ph = parent.querySelector(".public-DraftEditorPlaceholder-root, [data-placeholder], .placeholder");
        if (ph && ph !== el) {
          const phText = ph.getAttribute("data-placeholder") || ph.innerText || ph.textContent;
          if (phText) {
            const cleaned = cleanStr(phText);
            elToStableLabel.set(el, cleaned);
            return cleaned;
          }
        }
        const ancestor = parent.closest && parent.closest("[aria-label], [data-placeholder], [aria-placeholder]");
        if (ancestor) {
          const ancLabel = ancestor.getAttribute("aria-label")
            || ancestor.getAttribute("data-placeholder")
            || ancestor.getAttribute("aria-placeholder");
          if (ancLabel) {
            const cleaned = cleanStr(ancLabel);
            if (!elToStableLabel.has(el)) elToStableLabel.set(el, cleaned);
            return elToStableLabel.get(el) || cleaned;
          }
        }
      }
      if (elToStableLabel.has(el)) {
        return elToStableLabel.get(el);
      }
      const fallback = cleanStr(el.getAttribute("name") || el.getAttribute("data-testid") || el.id || "richtext");
      elToStableLabel.set(el, fallback);
      return fallback;
    }
    const tag = el.tagName.toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select" || tag === "canvas") {
      return cleanStr(el.name || el.id || tag);
    }
    return cleanStr(el.innerText || el.textContent || "");
  }

  function getValue(el) {
    if (isContentEditableEl(el)) {
      return cleanStr(el.innerText || el.textContent || "", 80);
    }
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
    const sel = "button, a[href], input, select, textarea, canvas, [role='button'], [role='link'], [role='tab'], [role='menuitem'], [role='checkbox'], [role='switch'], [role='option'], [role='textbox'], [role='searchbox'], [role='combobox'], [contenteditable='true'], [contenteditable=''], [contenteditable='plaintext-only'], h1, h2, h3, [data-status], [role='status'], [role='alert'], output, .result, #result, #status";
    const nodes = document.querySelectorAll(sel);
    const currentMap = new Map();
    const lines = [];
    const canvases = [];
    const tbodyRows = new Map();
    const foldedStats = new Map();
    const maxVisibleTableRows = 6;

    for (const el of nodes) {
      if (currentMap.size >= maxItems) break;
      if (!isVisible(el)) continue;
      if (
        el.parentElement
        && el.parentElement.closest("[contenteditable='true'], [contenteditable=''], [contenteditable='plaintext-only']")
      ) {
        const tag = el.tagName.toLowerCase();
        if (tag !== "button" && tag !== "a" && tag !== "input" && tag !== "select") {
          continue;
        }
      }
      const role = getRole(el);
      const label = getLabel(el);
      const val = getValue(el);
      if (role === "canvas") {
        const uid = getUid(el);
        const rect = el.getBoundingClientRect();
        const cx = Math.round(rect.left);
        const cy = Math.round(rect.top);
        const cw = Math.round(rect.width);
        const ch = Math.round(rect.height);
        let dataUrl = "";
        try { dataUrl = el.toDataURL("image/png"); } catch (_) {}
        canvases.push({ uid, label, x: cx, y: cy, w: cw, h: ch, dataUrl });
        const body = `canvas "${label}" (${cx},${cy}) [${cw}x${ch}]`;
        const fullLine = `${uid} ${body}`;
        if (!q || label.toLowerCase().includes(q) || "canvas".includes(q)) {
          currentMap.set(uid, { uid, role: "canvas", label, val: "", body, fullLine });
          lines.push(fullLine);
        }
        continue;
      }
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

    for (const [oUid, oItem] of ocrTargets.entries()) {
      if (q && !oItem.label.toLowerCase().includes(q) && !oUid.toLowerCase().includes(q) && !"canvas-text".includes(q)) {
        continue;
      }
      const body = `canvas-text "${oItem.label}" (${oItem.x},${oItem.y})`;
      const fullLine = `${oUid} ${body}`;
      currentMap.set(oUid, { uid: oUid, role: "canvas-text", label: oItem.label, val: "", body, fullLine });
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
    return { currentMap, lines, canvases, newCrc, totalCount: currentMap.size };
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

  function setNativeValue(el, value, append = false) {
    let target = el;
    const tag0 = target.tagName ? target.tagName.toLowerCase() : "";
    if (tag0 !== "input" && tag0 !== "textarea" && tag0 !== "select" && !isContentEditableEl(target)) {
      const inner = target.querySelector && target.querySelector("[contenteditable='true'], [contenteditable=''], [contenteditable='plaintext-only'], textarea, input");
      if (inner) target = inner;
    }

    const strVal = String(value ?? "");
    if (isContentEditableEl(target)) {
      target.focus({ preventScroll: true });
      const selectContents = (collapseToEnd) => {
        const sel = window.getSelection && window.getSelection();
        if (sel) {
          const range = document.createRange();
          range.selectNodeContents(target);
          if (collapseToEnd) range.collapse(false);
          sel.removeAllRanges();
          sel.addRange(range);
        }
      };
      const normWs = (s) => String(s || "").replace(/\s+/g, " ").trim();
      const prevNorm = normWs(target.innerText || target.textContent || "");
      selectContents(append);
      if (!append && prevNorm.length > 0) {
        try {
          const delBi = new InputEvent("beforeinput", { bubbles: true, cancelable: true, inputType: "deleteContentBackward", data: null });
          target.dispatchEvent(delBi);
        } catch (_) {}
        if (normWs(target.innerText || target.textContent || "").length > 0) {
          selectContents(false);
          try {
            document.execCommand("delete", false, null);
          } catch (_) {}
        }
        if (normWs(target.innerText || target.textContent || "").length > 0) {
          const leaves = target.querySelectorAll ? target.querySelectorAll("[data-text='true']") : [];
          if (leaves && leaves.length === 1) {
            leaves[0].textContent = "";
          } else {
            target.textContent = "";
          }
          target.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "deleteContentBackward", data: null }));
        }
      }
      if (strVal.length > 0) {
        selectContents(true);
        const expNorm = normWs(strVal);
        const expectedFullNorm = append && prevNorm.length > 0 ? normWs(prevNorm + " " + expNorm) : expNorm;
        try {
          const bi = new InputEvent("beforeinput", { bubbles: true, cancelable: true, inputType: "insertText", data: strVal });
          target.dispatchEvent(bi);
        } catch (_) {}
        const afterBiNorm = normWs(target.innerText || target.textContent || "");
        const okAfterBi = append
          ? (afterBiNorm === expectedFullNorm || (afterBiNorm.endsWith(expNorm) && afterBiNorm.length > prevNorm.length))
          : (afterBiNorm === expNorm);
        let inserted = okAfterBi;
        if (!okAfterBi) {
          selectContents(true);
          try {
            inserted = Boolean(document.execCommand("insertText", false, strVal));
          } catch (_) {}
        }
        const afterExecNorm = normWs(target.innerText || target.textContent || "");
        const okAfterExec = append ? afterExecNorm.endsWith(expNorm) : (afterExecNorm === expNorm);
        if (!inserted && !okAfterExec) {
          const leaf = target.querySelector && target.querySelector("[data-text='true']");
          if (leaf) {
            leaf.textContent = append ? ((leaf.textContent || "") + strVal) : strVal;
          } else {
            target.textContent = append ? ((target.textContent || "") + strVal) : strVal;
          }
          target.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: strVal }));
        }
      }
      target.dispatchEvent(new Event("change", { bubbles: true }));
      mutationSeq++;
      return;
    }

    const tag = target.tagName.toLowerCase();
    const finalVal = append ? (String(target.value || "") + strVal) : strVal;
    const proto = tag === "textarea"
      ? window.HTMLTextAreaElement.prototype
      : window.HTMLInputElement.prototype;
    const desc = Object.getOwnPropertyDescriptor(proto, "value");
    if (desc && desc.set) {
      desc.set.call(target, finalVal);
    } else {
      target.value = finalVal;
    }
    target.dispatchEvent(new InputEvent("input", { bubbles: true, data: finalVal }));
    target.dispatchEvent(new Event("change", { bubbles: true }));
    mutationSeq++;
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

  function interpolateTemplate(val, args) {
    if (typeof val === "string") {
      const exactMatch = val.match(/^(?:\{\{\s*([a-zA-Z0-9_.-]+)\s*\}\}|\$([a-zA-Z0-9_]+))$/);
      if (exactMatch) {
        const key = exactMatch[1] || exactMatch[2];
        if (args && Object.prototype.hasOwnProperty.call(args, key)) {
          return args[key];
        }
      }
      return val
        .replace(/\{\{\s*([a-zA-Z0-9_.-]+)\s*\}\}/g, (_, k) => (args && args[k] !== undefined ? String(args[k]) : `{{${k}}}`))
        .replace(/\$([a-zA-Z0-9_]+)/g, (m, k) => (args && args[k] !== undefined ? String(args[k]) : m));
    }
    if (Array.isArray(val)) {
      return val.map(v => interpolateTemplate(v, args));
    }
    if (val && typeof val === "object") {
      const out = {};
      for (const [k, v] of Object.entries(val)) {
        out[k] = interpolateTemplate(v, args);
      }
      return out;
    }
    return val;
  }

  async function invokeWebMcp(toolName, args) {
    const ctx = navigator.modelContext;
    if (ctx) {
      const tools = Array.isArray(ctx.tools) ? ctx.tools : [];
      const found = tools.find(t => t.name === toolName);
      if (found && typeof found.execute === "function") {
        return await found.execute(args || {});
      }
      if (typeof ctx.callTool === "function" && (!virtualTools.has(toolName) || found)) {
        try {
          return await ctx.callTool(toolName, args || {});
        } catch (err) {
          if (!virtualTools.has(toolName)) throw err;
        }
      }
    }
    const vTool = virtualTools.get(String(toolName));
    if (vTool) {
      const interpolatedSteps = interpolateTemplate(vTool.steps, args || {});
      const subRes = await executeBatch({
        agent: "macro",
        steps: interpolatedSteps,
        skipSnapshot: true,
        settle: true
      });
      if (subRes.errors && subRes.errors.length > 0) {
        throw new Error(`Virtual WebMCP macro '${toolName}' failed: ${subRes.errors.join("; ")}`);
      }
      return {
        ok: true,
        macro: toolName,
        stepsExecuted: interpolatedSteps.length,
        outputs: subRes.evalOutputs || []
      };
    }
    if (!ctx) throw new Error("navigator.modelContext (WebMCP) not available on page and no virtual macro registered");
    throw new Error(`WebMCP tool not found or not executable: ${toolName}`);
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

  const VALID_WEB_OPS = new Set([
    "click", "upload", "fill", "set", "type", "select", "move",
    "press", "key", "wait", "settle", "eval", "webmcp", "calltool",
    "goto", "navigate", "open", "close", "snapshot", "diff", "ocr"
  ]);

  async function executeBatch(req) {
    if (req.virtualTools) {
      registerVirtualTools(req.virtualTools);
    }
    if (req.ocrTargets) {
      registerOcrTargets(req.ocrTargets);
    }
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
        canvases: [],
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
      const s = steps[i] || {};
      let op = String(s.op || s.action || "").trim().toLowerCase();
      const targetRef = s.uid || s.ref || s.target || s.selector || s.name;
      const stepValue = s.value !== undefined ? s.value : s.text;
      if (!op) {
        if (!s.optional) {
          errors.push(`step ${i + 1}: missing 'op' (or 'action') field; valid ops: click, fill, set, type, select, upload, move, press, key, wait, eval, callTool`);
        }
        continue;
      }
      if (op === "press" && targetRef && String(targetRef).startsWith("@") && !s.key) {
        op = "click";
      }
      if (op === "hover" || op === "cursor" || op === "point") {
        op = "move";
      }
      if (!VALID_WEB_OPS.has(op)) {
        if (!s.optional) {
          errors.push(`step ${i + 1}: unsupported op '${op}'; valid ops: click, fill, set, type, select, upload, move, press, key, wait, eval, callTool`);
        }
        continue;
      }
      try {
        if (op === "wait" || op === "settle") {
          await new Promise(r => setTimeout(r, Number(s.ms || 25)));
          continue;
        }
        if (op === "snapshot" || op === "diff" || op === "ocr") {
          continue;
        }
        if (op === "eval") {
          const code = String(s.code || stepValue || "");
          const body = code.includes("return ") ? code : `return (${code})`;
          const fn = new Function(body);
          const res = await fn();
          if (res !== undefined) evalOutputs.push(typeof res === "string" ? res : JSON.stringify(res));
          continue;
        }
        if (op === "webmcp" || op === "calltool") {
          const tName = s.tool || s.name || s.target;
          const res = await invokeWebMcp(tName, s.args || stepValue || {});
          mutationSeq++;
          if (res !== undefined) {
            evalOutputs.push(`webmcp(${tName}): ${typeof res === "string" ? res : JSON.stringify(res)}`);
          }
          continue;
        }
        if (op === "press" || op === "key") {
          const key = s.key || s.name || stepValue || "Enter";
          const active = document.activeElement || document.body;
          active.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
          active.dispatchEvent(new KeyboardEvent("keyup", { key, bubbles: true }));
          if (key === "Enter" && active && active.form) {
            active.form.requestSubmit ? active.form.requestSubmit() : active.form.submit();
          }
          continue;
        }

        if ((op === "move" || op === "click") && typeof s.x === "number" && typeof s.y === "number" && !targetRef) {
          const hitEl = document.elementFromPoint(s.x, s.y);
          updateAgentCursor(st, hitEl, op, s.x, s.y);
          if (op === "click") {
            dispatchCanvasClick({ x: s.x, y: s.y, label: "@xy", uid: "@xy", canvasUid: "" });
          }
          continue;
        }

        if (!targetRef) {
          if (!s.optional) {
            errors.push(`step ${i + 1} (${op}): missing target (provide 'uid', 'name', 'target', 'selector', or 'x'/'y')`);
          }
          continue;
        }

        const el = resolveEl(targetRef);
        if (!el) {
          const ocrItem = resolveOcrTarget(targetRef);
          if (ocrItem) {
            st.cursor = {
              agent: st.agent,
              x: ocrItem.x,
              y: ocrItem.y,
              uid: ocrItem.uid,
              label: ocrItem.label,
              op
            };
            if (op === "move") {
              continue;
            } else if (op === "click") {
              dispatchCanvasClick(ocrItem);
              continue;
            } else if (op === "fill" || op === "set" || op === "type") {
              dispatchCanvasClick(ocrItem);
              const textVal = String(stepValue ?? "");
              const active = document.activeElement || document.body;
              for (const ch of textVal) {
                active.dispatchEvent(new KeyboardEvent("keydown", { key: ch, bubbles: true }));
                active.dispatchEvent(new KeyboardEvent("keyup", { key: ch, bubbles: true }));
              }
              continue;
            }
          }
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
            if (!fileEl) {
              const lbl = el.tagName.toLowerCase() === "label" ? el : (el.closest && el.closest("label"));
              if (lbl) {
                fileEl = lbl.querySelector && lbl.querySelector("input[type='file']");
                if (!fileEl && lbl.htmlFor) {
                  const forEl = document.getElementById(lbl.htmlFor);
                  if (forEl && forEl.tagName.toLowerCase() === "input" && (forEl.type || "").toLowerCase() === "file") {
                    fileEl = forEl;
                  }
                }
              }
            }
            if (!fileEl) {
              let anc = el.parentElement;
              for (let d = 0; d < 4 && anc && anc !== document.body; d++, anc = anc.parentElement) {
                const candidate = anc.querySelector && anc.querySelector("input[type='file']");
                if (candidate) {
                  fileEl = candidate;
                  break;
                }
              }
            }
            if (!fileEl) {
              fileEl = document.querySelector("input[type='file']");
            }
          }
          if (fileEl || s.files || op === "upload") {
            const targetFileEl = fileEl || el;
            const fUid = getUid(targetFileEl);
            if (fileEl) {
              fileEl.setAttribute("data-fmu-upload-uid", fUid);
            }
            escalations.push({ step: i + 1, reason: "file_input", uid: fileEl ? fUid : "" });
            continue;
          }
          el.focus({ preventScroll: true });
          el.click();
        } else if (op === "fill" || op === "set" || op === "type") {
          el.focus({ preventScroll: true });
          setNativeValue(el, stepValue ?? "", Boolean(s.append));
        } else if (op === "select") {
          el.focus({ preventScroll: true });
          const desired = String(stepValue ?? "");
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
      return { errors, evalOutputs, escalations, cursor: st.cursor, canvases: [], lines: [] };
    }

    if ((steps.length > 0 || req.hadPreSteps) && req.settle !== false) {
      await waitQuietDom(14, 100);
    }

    const prevMap = st.lastLinesByUid;
    const prevCrc = st.lastCrc;
    st.preBatchLinesByUid = prevMap;
    st.preBatchCrc = prevCrc;
    const snap = collectSnapshot(query, 180, foldTables);
    if (!query) {
      st.lastLinesByUid = snap.currentMap;
      st.lastCrc = snap.newCrc;
      st.lastSnapSeq = mutationSeq;
    }
    st.lastQuery = query;
    st.lastFoldTables = foldTables;

    const nativeTools = (navigator.modelContext && Array.isArray(navigator.modelContext.tools))
      ? navigator.modelContext.tools.map(t => t.name)
      : [];
    const webMcpTools = Array.from(new Set([...nativeTools, ...virtualTools.keys()]));

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
      canvases: snap.canvases || [],
      useDiff,
      errors,
      evalOutputs,
      escalations,
      lines: outLines
    };
  }

  function refreshAfterOcr(opts) {
    const agentId = String((opts && opts.agent) || "default");
    const st = getAgentState(agentId);
    if (opts && opts.ocrTargets) {
      registerOcrTargets(opts.ocrTargets);
    }
    const query = String((opts && opts.query) || "");
    const foldTables = (opts && opts.fullTable) !== true;
    const useDiff = Boolean(opts && opts.useDiff);
    const prevMap = st.preBatchLinesByUid || st.lastLinesByUid;
    const prevCrc = st.preBatchCrc || st.lastCrc;
    const snap = collectSnapshot(query, 180, foldTables);
    if (!query) {
      st.lastLinesByUid = snap.currentMap;
      st.lastCrc = snap.newCrc;
      st.lastSnapSeq = mutationSeq;
    }
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
      crc: snap.newCrc,
      seq: mutationSeq,
      count: snap.totalCount,
      lines: outLines
    };
  }

  window.__fmu = {
    executeBatch,
    collectSnapshot,
    resolveEl,
    invokeWebMcp,
    registerVirtualTools,
    registerOcrTargets,
    resolveOcrTarget,
    refreshAfterOcr,
    getAgentState,
    resetAgent
  };
  return true;
})();
"""

_GROUP_COLORS = ["blue", "purple", "cyan", "green", "orange", "pink", "yellow", "red"]
_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_OCR_LINE_RE = re.compile(
    r'^(@ocr\d+)\s+canvas-text\s+"([^"]+)"\s+\((-?\d+),(-?\d+)\)(?:\s+\[(\d+)x(\d+)\])?'
)


class ExtensionWebSocketBridge:
    """RFC 6455 localhost WebSocket server for the outbound MV3 extension bridge (0 Chrome 144 modal popups)."""

    def __init__(self, preferred_port: int | None = None) -> None:
        if preferred_port is None:
            preferred_port = int(os.environ.get("FMU_BRIDGE_PORT", "19222"))
        self.preferred_port = preferred_port
        self.port: int = 0
        self.port_fallback_reason: str | None = None
        self._srv_sock: socket.socket | None = None
        self._client_sock: socket.socket | None = None
        self._client_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._ready_event = threading.Event()
        self._hello_info: dict[str, Any] = {}
        self._next_req_id = 1
        self._pending: dict[int, tuple[threading.Event, list[Any]]] = {}
        self._pending_lock = threading.Lock()
        self._stopped = False
        self._start_server()

    def _start_server(self) -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        bound = False
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                srv.bind(("127.0.0.1", self.preferred_port))
                bound = True
                break
            except OSError as exc:
                last_err = exc
                if attempt < 2:
                    time.sleep(0.04)
        if not bound:
            self.port_fallback_reason = str(last_err) if last_err else "address in use"
            srv.bind(("127.0.0.1", 0))
        srv.listen(8)
        self.port = int(srv.getsockname()[1])
        self._srv_sock = srv
        t = threading.Thread(target=self._accept_loop, name="fmu-ext-ws-bridge", daemon=True)
        t.start()

    def ensure_preferred_port(self) -> None:
        if self._stopped or self.port == self.preferred_port or self.is_connected():
            return
        try:
            test_srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            test_srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            test_srv.bind(("127.0.0.1", self.preferred_port))
        except OSError:
            return
        old_srv = self._srv_sock
        test_srv.listen(8)
        self._srv_sock = test_srv
        self.port = int(test_srv.getsockname()[1])
        self.port_fallback_reason = None
        if old_srv is not None:
            try:
                old_srv.close()
            except Exception:
                pass
        t = threading.Thread(target=self._accept_loop, name="fmu-ext-ws-bridge", daemon=True)
        t.start()

    def is_connected(self) -> bool:
        with self._client_lock:
            return self._client_sock is not None and self._ready_event.is_set()

    def wait_connected(self, timeout: float = 2.0) -> bool:
        self.ensure_preferred_port()
        return self._ready_event.wait(timeout=timeout) and self.is_connected()

    def get_status(self) -> dict[str, Any]:
        self.ensure_preferred_port()
        return {
            "connected": self.is_connected(),
            "port": self.port,
            "preferredPort": self.preferred_port,
            "wsUrl": f"ws://127.0.0.1:{self.preferred_port}/ext",
            "portFallbackReason": self.port_fallback_reason,
            "client": dict(self._hello_info),
        }

    def _accept_loop(self) -> None:
        while not self._stopped and self._srv_sock is not None:
            try:
                conn, _ = self._srv_sock.accept()
            except OSError:
                break
            threading.Thread(
                target=self._handle_client, args=(conn,), name="fmu-ext-ws-client", daemon=True
            ).start()

    @staticmethod
    def _recv_exact(sock: socket.socket, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("WebSocket closed")
            buf.extend(chunk)
        return bytes(buf)

    def _read_ws_frame(self, sock: socket.socket) -> tuple[int, bytes]:
        hdr = self._recv_exact(sock, 2)
        opcode = hdr[0] & 0x0F
        masked = (hdr[1] & 0x80) != 0
        length = hdr[1] & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._recv_exact(sock, 2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._recv_exact(sock, 8))[0]
        mask_key = self._recv_exact(sock, 4) if masked else b""
        payload = bytearray(self._recv_exact(sock, length)) if length > 0 else bytearray()
        if masked and mask_key:
            for i in range(len(payload)):
                payload[i] ^= mask_key[i & 3]
        return opcode, bytes(payload)

    def _send_ws_frame(self, sock: socket.socket, payload: bytes, opcode: int = 0x1) -> None:
        header = bytearray([0x80 | (opcode & 0x0F)])
        n = len(payload)
        if n < 126:
            header.append(n)
        elif n <= 65535:
            header.append(126)
            header.extend(struct.pack("!H", n))
        else:
            header.append(127)
            header.extend(struct.pack("!Q", n))
        with self._send_lock:
            sock.sendall(bytes(header) + payload)

    def _handle_client(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(10.0)
            req_bytes = b""
            while b"\r\n\r\n" not in req_bytes and len(req_bytes) < 16384:
                chunk = conn.recv(4096)
                if not chunk:
                    conn.close()
                    return
                req_bytes += chunk
            header_text = req_bytes.decode("utf-8", errors="replace")
            key_match = re.search(r"(?i)^Sec-WebSocket-Key:\s*(.+?)\s*$", header_text, re.M)
            if not key_match:
                conn.close()
                return
            ws_key = key_match.group(1).strip()
            accept_val = base64.b64encode(
                hashlib.sha1((ws_key + _WS_GUID).encode("ascii")).digest()
            ).decode("ascii")
            resp = (
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept_val}\r\n\r\n"
            )
            conn.sendall(resp.encode("ascii"))
            conn.settimeout(None)

            with self._client_lock:
                if self._client_sock is not None:
                    try:
                        self._client_sock.close()
                    except Exception:
                        pass
                self._client_sock = conn
                self._ready_event.set()

            while not self._stopped:
                opcode, data = self._read_ws_frame(conn)
                if opcode == 0x8:  # Close
                    break
                if opcode == 0x9:  # Ping
                    self._send_ws_frame(conn, data, opcode=0xA)
                    continue
                if opcode == 0x1:  # Text
                    msg = json.loads(data.decode("utf-8", errors="replace"))
                    if msg.get("type") == "hello":
                        self._hello_info = msg
                        self._ready_event.set()
                        continue
                    req_id = msg.get("id")
                    if req_id is not None:
                        with self._pending_lock:
                            waiter = self._pending.pop(int(req_id), None)
                        if waiter is not None:
                            ev, box = waiter
                            box.append(msg.get("result"))
                            box.append(msg.get("error"))
                            ev.set()
        except Exception:
            pass
        finally:
            with self._client_lock:
                if self._client_sock is conn:
                    self._client_sock = None
                    self._ready_event.clear()
            with self._pending_lock:
                for _, (ev, box) in list(self._pending.items()):
                    if not box:
                        box.extend([None, "Extension WebSocket disconnected"])
                    ev.set()
                self._pending.clear()
            try:
                conn.close()
            except Exception:
                pass

    def call_rpc(self, method: str, params: dict[str, Any] | None = None, timeout: float = 10.0) -> Any:
        with self._client_lock:
            sock = self._client_sock
        if sock is None:
            raise ConnectionError("No MV3 extension connected to WebSocket bridge")
        with self._pending_lock:
            req_id = self._next_req_id
            self._next_req_id += 1
            ev = threading.Event()
            box: list[Any] = []
            self._pending[req_id] = (ev, box)
        payload = json.dumps({"id": req_id, "method": method, "params": params or {}}).encode("utf-8")
        try:
            self._send_ws_frame(sock, payload, opcode=0x1)
        except Exception as exc:
            with self._pending_lock:
                self._pending.pop(req_id, None)
            raise ConnectionError(f"Failed sending to extension bridge: {exc}") from exc
        if not ev.wait(timeout=timeout):
            with self._pending_lock:
                self._pending.pop(req_id, None)
            raise TimeoutError(f"Extension bridge RPC '{method}' timed out after {timeout}s")
        if len(box) >= 2 and box[1]:
            raise RuntimeError(str(box[1]))
        return box[0] if box else None

    def shutdown(self) -> None:
        self._stopped = True
        with self._client_lock:
            if self._client_sock is not None:
                try:
                    self._client_sock.close()
                except Exception:
                    pass
                self._client_sock = None
        if self._srv_sock is not None:
            try:
                self._srv_sock.close()
            except Exception:
                pass
            self._srv_sock = None


class _ExtensionCdpAdapter:
    def __init__(self, bridge: ExtensionWebSocketBridge, chrome_tab_id: int) -> None:
        self._bridge = bridge
        self.chrome_tab_id = chrome_tab_id

    def send(self, method: str, params: dict[str, Any] | None = None) -> Any:
        cmd_params = params or {}
        return self._bridge.call_rpc(
            "cdp",
            {
                "tabId": self.chrome_tab_id,
                "method": method,
                "cdpMethod": method,
                "commandParams": cmd_params,
                "cdpParams": cmd_params,
            },
        )


class _ExtensionPageAdapter:
    def __init__(
        self,
        bridge: ExtensionWebSocketBridge,
        chrome_tab_id: int,
        initial_url: str = "about:blank",
        window_id: int | None = None,
    ) -> None:
        self._bridge = bridge
        self.chrome_tab_id = chrome_tab_id
        self.url = initial_url
        self.window_id = window_id
        self._closed = False

    def is_closed(self) -> bool:
        return self._closed or not self._bridge.is_connected()

    def goto(self, url: str, wait_until: str = "domcontentloaded", timeout: int = 15000) -> dict[str, Any]:
        res = self._bridge.call_rpc(
            "navigate",
            {"tabId": self.chrome_tab_id, "url": url, "timeoutMs": timeout},
            timeout=max(5.0, timeout / 1000.0 + 2.0),
        )
        if isinstance(res, dict) and res.get("url"):
            self.url = str(res["url"])
        else:
            self.url = url
        return res or {"url": self.url}

    def set_input_files(self, selector: str, files: str | list[str]) -> dict[str, Any]:
        file_list = [str(files)] if isinstance(files, (str, Path)) else [str(f) for f in files]
        res = self._bridge.call_rpc(
            "setInputFiles",
            {"tabId": self.chrome_tab_id, "selector": selector, "files": file_list},
            timeout=10.0,
        )
        return res if isinstance(res, dict) else {"ok": True}

    def evaluate(self, expression: str, arg: Any = None) -> Any:
        expr_str = expression.strip()
        if expr_str == IN_PAGE_RUNTIME_JS.strip():
            return self._bridge.call_rpc(
                "evaluate",
                {"tabId": self.chrome_tab_id, "expression": expr_str, "runtimeInit": True},
            )
        if "window.__fmu.executeBatch" in expr_str and isinstance(arg, dict):
            res = self._bridge.call_rpc(
                "executeBatch",
                {**arg, "tabId": self.chrome_tab_id, "runtimeJs": IN_PAGE_RUNTIME_JS},
            )
            if isinstance(res, dict) and res.get("url"):
                self.url = str(res["url"])
            return res
        return self._bridge.call_rpc(
            "evaluate",
            {"tabId": self.chrome_tab_id, "expression": expr_str, "arg": arg},
        )

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self._bridge.call_rpc("closeTab", {"tabId": self.chrome_tab_id}, timeout=2.0)
            except Exception:
                pass


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
        is_extension: bool = False,
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
        self.is_extension = is_extension
        self.documented = False
        self._documented_agents: set[str] = set()
        self._focus_emulated = False
        self._ocr_populated = False
        self._last_ocr_items: list[dict[str, Any]] = []

    def enable_background_focus_emulation(self) -> None:
        if self._focus_emulated:
            return
        if self.is_extension:
            self._focus_emulated = True
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
        self._last_tab_steps: dict[str, list[dict[str, Any]]] = {}
        self._next_tab_num: int = 1
        self.cursors: dict[str, dict[str, Any]] = {}
        self._agent_colors: dict[str, str] = {}
        self._ext_bridge_inst: ExtensionWebSocketBridge | None = None
        self._bridge_lock = threading.Lock()

        self._cmd_q: queue.Queue[tuple[Callable[[], Any], threading.Event, list[Any]] | None] = queue.Queue()
        self._worker_tid: int = 0
        self._worker = threading.Thread(target=self._worker_loop, name="fmu-pw-worker", daemon=True)
        self._worker.start()

    @property
    def _ext_bridge(self) -> ExtensionWebSocketBridge:
        with self._bridge_lock:
            if self._ext_bridge_inst is None:
                self._ext_bridge_inst = ExtensionWebSocketBridge()
            else:
                self._ext_bridge_inst.ensure_preferred_port()
            return self._ext_bridge_inst

    def ensure_bridge(self) -> ExtensionWebSocketBridge:
        return self._ext_bridge

    @property
    def bridge_port(self) -> int:
        return self._ext_bridge.port

    @property
    def extension_connected(self) -> bool:
        return self._ext_bridge.is_connected()

    def get_bridge_status(self) -> dict[str, Any]:
        return self._ext_bridge.get_status()

    @staticmethod
    def _macros_dir() -> Path:
        custom = os.environ.get("FMU_MACROS_DIR", "").strip()
        base = Path(custom).expanduser() if custom else (Path.home() / ".fmu" / "macros")
        base.mkdir(parents=True, exist_ok=True)
        return base

    @staticmethod
    def _domain_from_url(url: str | None) -> str:
        if not url or url in ("*", "global", "about:blank"):
            return "global"
        raw = str(url).strip()
        if "://" not in raw:
            raw = "http://" + raw
        try:
            parsed = urlparse(raw)
            host = (parsed.hostname or "global").lower()
            return re.sub(r"[^a-z0-9._-]", "_", host) or "global"
        except Exception:
            return "global"

    @staticmethod
    def _is_external_url(url: str | None) -> bool:
        if not url:
            return False
        raw = str(url).strip()
        if not raw.startswith(("http://", "https://")):
            return False
        try:
            host = (urlparse(raw).hostname or "").lower()
        except Exception:
            return False
        if not host or host in ("localhost", "127.0.0.1", "::1", "example.com", "www.example.com"):
            return False
        return True

    def save_macro(
        self,
        name: str,
        steps: list[dict[str, Any]] | None = None,
        domain: str | None = None,
        description: str = "",
        tab_id: str | None = None,
    ) -> dict[str, Any]:
        return self._run_sync(lambda: self._save_macro_inner(name, steps, domain, description, tab_id))

    def _save_macro_inner(
        self,
        name: str,
        steps: list[dict[str, Any]] | None = None,
        domain: str | None = None,
        description: str = "",
        tab_id: str | None = None,
    ) -> dict[str, Any]:
        clean_name = str(name or "").strip()
        if not clean_name:
            raise ValueError("Macro name is required")
        target_tid = tab_id or "t1"
        if steps is None:
            recorded = self._last_tab_steps.get(target_tid) or []
            if not recorded and self._last_tab_steps:
                recorded = next(iter(self._last_tab_steps.values()), [])
            steps = list(recorded)
        if not steps:
            raise ValueError(f"No steps provided or recorded for macro '{clean_name}'")

        if not domain:
            ref_url = self._tab_last_urls.get(target_tid)
            if not ref_url and self._tab_last_urls:
                ref_url = next(iter(self._tab_last_urls.values()), "global")
            domain_key = self._domain_from_url(ref_url)
        else:
            domain_key = self._domain_from_url(domain)

        macro_file = self._macros_dir() / f"{domain_key}.json"
        existing: dict[str, Any] = {}
        if macro_file.is_file():
            try:
                existing = json.loads(macro_file.read_text(encoding="utf-8"))
            except Exception:
                existing = {}
        entry = {
            "name": clean_name,
            "domain": domain_key,
            "description": str(description or f"Auto-synthesized Virtual WebMCP macro ({len(steps)} steps)"),
            "steps": list(steps),
            "updatedAt": round(time.time(), 3),
        }
        existing[clean_name] = entry
        macro_file.write_text(json.dumps(existing, indent=2), encoding="utf-8")

        for sess in list(self._tabs.values()):
            if self._is_session_alive(sess):
                sess_dom = self._domain_from_url(sess.last_url)
                if domain_key in ("global", sess_dom):
                    try:
                        sess._raw_page.evaluate(
                            "tools => window.__fmu && window.__fmu.registerVirtualTools && window.__fmu.registerVirtualTools(tools)",
                            [entry],
                        )
                    except Exception:
                        pass
        return entry

    def get_macros_for_url(self, url: str | None) -> list[dict[str, Any]]:
        domain_key = self._domain_from_url(url)
        macros_by_name: dict[str, dict[str, Any]] = {}
        mdir = self._macros_dir()
        for key in ("global", domain_key):
            f = mdir / f"{key}.json"
            if f.is_file():
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        for mname, mval in data.items():
                            if isinstance(mval, dict) and mval.get("steps"):
                                macros_by_name[mname] = mval
                except Exception:
                    pass
        return list(macros_by_name.values())

    def list_macros(self, domain: str | None = None) -> list[dict[str, Any]]:
        mdir = self._macros_dir()
        results: list[dict[str, Any]] = []
        files = [mdir / f"{self._domain_from_url(domain)}.json"] if domain else sorted(mdir.glob("*.json"))
        for f in files:
            if f.is_file():
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        for mval in data.values():
                            if isinstance(mval, dict) and mval.get("name"):
                                results.append(mval)
                except Exception:
                    pass
        return results

    def delete_macro(self, name: str, domain: str | None = None) -> bool:
        mdir = self._macros_dir()
        deleted = False
        files = [mdir / f"{self._domain_from_url(domain)}.json"] if domain else sorted(mdir.glob("*.json"))
        for f in files:
            if f.is_file():
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    if isinstance(data, dict) and name in data:
                        data.pop(name, None)
                        f.write_text(json.dumps(data, indent=2), encoding="utf-8")
                        deleted = True
                except Exception:
                    pass
        return deleted

    def _sync_canvas_ocr(
        self,
        sess: BrowserTabSession,
        canvases: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        if canvases is None:
            try:
                canvases = sess._raw_page.evaluate(
                    "() => (window.__fmu ? (window.__fmu.collectSnapshot('', 180, true).canvases || []) : [])"
                )
            except Exception:
                canvases = []
        if not canvases or not isinstance(canvases, list):
            return []

        from .mac_engine import MacNativeEngine

        mac_eng = MacNativeEngine()
        ocr_items: list[dict[str, Any]] = []
        idx = 1
        for c in canvases:
            data_url = str(c.get("dataUrl") or "")
            png_bytes = b""
            if data_url.startswith("data:image/png;base64,"):
                try:
                    png_bytes = base64.b64decode(data_url.split(",", 1)[1])
                except Exception:
                    png_bytes = b""
            if not png_bytes:
                continue
            safe_tid = re.sub(r"[^a-zA-Z0-9_-]", "_", sess.tab_id)
            safe_cuid = re.sub(r"[^a-zA-Z0-9_-]", "_", str(c.get("uid") or "c1"))
            tmp_png = Path(f"/tmp/fmu-canvas-{safe_tid}-{safe_cuid}.png")
            try:
                tmp_png.write_bytes(png_bytes)
                raw_ocr = mac_eng.execute(
                    {
                        "imagePath": str(tmp_png),
                        "clipX": float(c.get("x", 0)),
                        "clipY": float(c.get("y", 0)),
                        "clipW": float(c.get("w", 0)),
                        "clipH": float(c.get("h", 0)),
                    }
                )
                for line in raw_ocr.splitlines():
                    m = _OCR_LINE_RE.match(line.strip())
                    if m:
                        _, lbl, x_s, y_s, w_s, h_s = m.groups()
                        ocr_items.append(
                            {
                                "uid": f"@ocr{idx}",
                                "label": lbl,
                                "x": int(x_s),
                                "y": int(y_s),
                                "w": int(w_s or 0),
                                "h": int(h_s or 0),
                                "canvasUid": str(c.get("uid") or ""),
                            }
                        )
                        idx += 1
            except Exception:
                pass
        try:
            sess._raw_page.evaluate(
                "items => window.__fmu && window.__fmu.registerOcrTargets && window.__fmu.registerOcrTargets(items)",
                ocr_items,
            )
            sess._ocr_populated = True
            sess._last_ocr_items = ocr_items
        except Exception:
            pass
        return ocr_items

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
            if sess.is_extension:
                return self._ext_bridge.is_connected() and not bool(sess._raw_page.is_closed())
            return self._is_browser_alive() and not bool(sess._raw_page.is_closed())
        except Exception:
            return False

    def _evict_dead_browser(self) -> None:
        self._tabs = {k: v for k, v in self._tabs.items() if v.is_extension and self._is_session_alive(v)}
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
        window: str | None = None,
        window_id: int | None = None,
        reuse_existing: bool = False,
        require_extension: bool = False,
    ) -> BrowserTabSession:
        return self._run_sync(
            lambda: self._create_tab_inner(
                url,
                mode,
                session_name,
                tab_id,
                group_color,
                window=window,
                window_id=window_id,
                reuse_existing=reuse_existing,
                require_extension=require_extension,
            )
        )

    def _extension_diagnostic_error(self) -> str:
        ext_dir = Path(__file__).resolve().parents[2] / "extension"
        port_note = ""
        if self._ext_bridge.port != self._ext_bridge.preferred_port:
            reason = self._ext_bridge.port_fallback_reason or "port in use"
            port_note = f" (daemon bound to fallback port {self._ext_bridge.port} because {self._ext_bridge.preferred_port} was busy: {reason})"
        return (
            f"MV3 extension not connected on ws://127.0.0.1:{self._ext_bridge.preferred_port}/ext{port_note}; "
            f"load {ext_dir} in Chrome or pass mode='headless'"
        )

    def _should_use_extension_bridge(
        self,
        mode: str,
        require_extension: bool = False,
        url: str | None = None,
        window: str | None = None,
        window_id: int | None = None,
        reuse_existing: bool = False,
    ) -> bool:
        explicit_ext = mode in ("extension", "chrome-extension", "live") or require_extension
        if mode == "headless" and not explicit_ext:
            return False
        use_ext_env = os.environ.get("FMU_USE_EXTENSION", "1") != "0"
        must_use_ext = explicit_ext or (
            mode == "chrome"
            and use_ext_env
            and not self._find_chrome_devtools_ws()
            and (
                self._is_external_url(url)
                or window is not None
                or window_id is not None
                or reuse_existing
            )
        )
        if must_use_ext:
            if not self._ext_bridge.is_connected():
                self._ext_bridge.wait_connected(timeout=0.35)
            if not self._ext_bridge.is_connected():
                raise RuntimeError(self._extension_diagnostic_error())
            return True
        if not use_ext_env or self._ext_bridge_inst is None or not self._ext_bridge.is_connected():
            return False
        return True

    def _create_tab_inner(
        self,
        url: str = "about:blank",
        mode: str = "chrome",
        session_name: str = "🤖 Agent",
        tab_id: str | None = None,
        group_color: str = "blue",
        window: str | None = None,
        window_id: int | None = None,
        reuse_existing: bool = False,
        require_extension: bool = False,
    ) -> BrowserTabSession:
        tid = tab_id or f"t{self._next_tab_num}"
        effective_url = url or self._tab_last_urls.get(tid) or "about:blank"
        must_use_ext = (
            mode in ("extension", "chrome-extension", "live")
            or require_extension
            or (
                mode == "chrome"
                and os.environ.get("FMU_USE_EXTENSION", "1") != "0"
                and not self._find_chrome_devtools_ws()
                and (
                    self._is_external_url(effective_url)
                    or window is not None
                    or window_id is not None
                    or reuse_existing
                )
            )
        )
        if tid in self._tabs:
            sess = self._tabs[tid]
            if self._is_session_alive(sess) and (not must_use_ext or sess.is_extension):
                if url and url != "about:blank" and sess._raw_page.url != url:
                    sess._raw_page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    sess.last_url = url
                    self._tab_last_urls[tid] = url
                    sess._focus_emulated = False
                    sess._ocr_populated = False
                    sess.enable_background_focus_emulation()
                    sess.ensure_runtime()
                return sess
            self._tabs.pop(tid, None)

        if self._should_use_extension_bridge(
            mode,
            require_extension=require_extension,
            url=effective_url,
            window=window,
            window_id=window_id,
            reuse_existing=reuse_existing,
        ):
            try:
                rpc_params: dict[str, Any] = {
                    "url": effective_url,
                    "sessionId": tid,
                    "sessionName": session_name,
                    "color": group_color,
                }
                if window:
                    rpc_params["window"] = str(window)
                if window_id is not None:
                    rpc_params["windowId"] = int(window_id)
                if reuse_existing:
                    rpc_params["reuseExisting"] = True
                ext_res = self._ext_bridge.call_rpc("createBrowserTab", rpc_params)
                chrome_tab_id = int(ext_res.get("tabId", 1))
                chrome_win_id = ext_res.get("windowId")
                actual_url = str(ext_res.get("url") or effective_url)
                self._next_tab_num += 1
                ext_page = _ExtensionPageAdapter(
                    self._ext_bridge,
                    chrome_tab_id,
                    initial_url=actual_url,
                    window_id=int(chrome_win_id) if chrome_win_id is not None else None,
                )
                ext_cdp = _ExtensionCdpAdapter(self._ext_bridge, chrome_tab_id)
                sess = BrowserTabSession(
                    engine=self,
                    tab_id=tid,
                    page=ext_page,
                    cdp_session=ext_cdp,
                    group_name=session_name,
                    group_color=group_color,
                    last_url=actual_url,
                    is_extension=True,
                )
                self._tabs[tid] = sess
                self._tab_last_urls[tid] = actual_url
                sess.ensure_runtime()
                return sess
            except Exception:
                if must_use_ext:
                    raise

        self._ensure_browser_inner(mode=mode)
        self._next_tab_num += 1
        page = self._context.new_page()
        cdp = self._context.new_cdp_session(page)
        sess = BrowserTabSession(
            engine=self,
            tab_id=tid,
            page=page,
            cdp_session=cdp,
            group_name=session_name,
            group_color=group_color,
            last_url=effective_url,
            is_extension=False,
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
            if "MV3 extension not connected on ws://" in msg:
                raise
            if any(
                tok in msg
                for tok in (
                    "Target page, context or browser has been closed",
                    "TargetClosedError",
                    "Connection closed",
                    "Session closed",
                    "Browser has been closed",
                    "Extension WebSocket disconnected",
                    "No MV3 extension connected",
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

        if req.get("bridgeStatus"):
            st = self._ext_bridge.get_status()
            fb = f' fallbackReason="{st["portFallbackReason"]}"' if st.get("portFallbackReason") else ""
            return (
                f'# fmu-web bridge connected={str(st["connected"]).lower()} '
                f'port={st["port"]} preferredPort={st["preferredPort"]} '
                f'ws="{st["wsUrl"]}"{fb}'
            )

        if req.get("saveMacro"):
            m_spec = req["saveMacro"]
            if isinstance(m_spec, str):
                entry = self._save_macro_inner(m_spec)
            elif isinstance(m_spec, dict):
                entry = self._save_macro_inner(
                    name=m_spec.get("name", ""),
                    steps=m_spec.get("steps"),
                    domain=m_spec.get("domain"),
                    description=m_spec.get("description", ""),
                    tab_id=req.get("session") or req.get("tab"),
                )
            else:
                raise ValueError("Invalid saveMacro specification")
            return f'# fmu-web macro saved name="{entry["name"]}" domain="{entry["domain"]}" steps={len(entry["steps"])}'

        agent_id = str(req.get("agent") or req.get("cursor") or "default").strip() or "default"
        default_tab = "t1" if agent_id == "default" else f"{agent_id}:t1"
        tab_id = str(req.get("session") or req.get("tab") or default_tab)
        default_group = "🤖 Agent" if agent_id == "default" else f"🤖 {agent_id}"
        group_name = str(req.get("sessionName") or req.get("group") or default_group)
        group_color = self._get_agent_color(agent_id)
        mode = str(req.get("mode") or "chrome")
        require_ext = bool(req.get("requireExtension")) or mode in ("extension", "chrome-extension", "live")
        window_filter = str(req["window"]) if req.get("window") else None
        window_id = int(req["windowId"]) if req.get("windowId") is not None else None
        reuse_existing = bool(req.get("reuseExisting"))
        url = req.get("url")
        steps = list(req.get("steps") or [])

        if any((s.get("op") or s.get("action")) == "close" for s in steps) and len(steps) == 1:
            return self._close_tab_inner(tab_id)

        while steps and (steps[0].get("op") or steps[0].get("action")) in ("open", "goto", "navigate"):
            step_url = str(steps[0].get("url") or steps[0].get("value") or "").strip()
            if step_url:
                url = step_url
            steps = steps[1:]

        restore_url = url or self._tab_last_urls.get(tab_id) or "about:blank"
        needs_ext_for_req = (
            require_ext
            or (
                mode == "chrome"
                and os.environ.get("FMU_USE_EXTENSION", "1") != "0"
                and not self._find_chrome_devtools_ws()
                and (
                    self._is_external_url(restore_url)
                    or window_filter is not None
                    or window_id is not None
                    or reuse_existing
                )
            )
        )

        if (
            tab_id not in self._tabs
            or not self._is_session_alive(self._tabs[tab_id])
            or (needs_ext_for_req and not self._tabs[tab_id].is_extension)
        ):
            old_sess = self._tabs.pop(tab_id, None)
            if old_sess is not None and not old_sess.is_extension:
                try:
                    old_sess._raw_page.close()
                except Exception:
                    pass
            sess = self._create_tab_inner(
                url=restore_url,
                mode=mode,
                session_name=group_name,
                tab_id=tab_id,
                group_color=group_color,
                window=window_filter,
                window_id=window_id,
                reuse_existing=reuse_existing,
                require_extension=require_ext,
            )
        else:
            sess = self._tabs[tab_id]
            if url and url != sess._raw_page.url:
                sess._raw_page.goto(url, wait_until="domcontentloaded", timeout=15000)
                sess.last_url = url
                self._tab_last_urls[tab_id] = url
                sess._focus_emulated = False
                sess._ocr_populated = False
                sess.documented = False
                sess._documented_agents.clear()
                self._last_tab_steps.pop(tab_id, None)

        sess.enable_background_focus_emulation()
        sess.ensure_runtime()

        active_steps = [
            s for s in steps if (s.get("op") or s.get("action")) != "close"
        ]
        recordable_steps = [
            s
            for s in active_steps
            if str(s.get("op") or s.get("action") or "").lower()
            not in ("snapshot", "diff", "webmcp", "calltool")
        ]
        if recordable_steps:
            prev_rec = self._last_tab_steps.setdefault(tab_id, [])
            prev_rec.extend(recordable_steps)
            if len(prev_rec) > 40:
                del prev_rec[:-40]

        virtual_tools = self.get_macros_for_url(sess.last_url)

        if req.get("ocr") is not False and (not sess._ocr_populated or req.get("ocr") is True):
            try:
                has_canvas = bool(
                    sess._raw_page.evaluate("() => document.querySelectorAll('canvas').length > 0")
                )
            except Exception:
                has_canvas = False
            if has_canvas:
                self._sync_canvas_ocr(sess)

        boundary_indices = [
            idx
            for idx, s in enumerate(active_steps)
            if (
                s.get("files")
                or (s.get("op") or s.get("action")) in ("upload", "goto", "navigate", "open")
            )
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
                                "virtualTools": virtual_tools,
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
                        if not sess.is_extension and not self._is_cdp_chrome and mode == "chrome" and self._is_external_url(nav_url):
                            self._should_use_extension_bridge(mode, require_extension=require_ext, url=nav_url)
                        sess._raw_page.goto(nav_url, wait_until="domcontentloaded", timeout=15000)
                        sess.last_url = nav_url
                        self._tab_last_urls[tab_id] = nav_url
                        sess._focus_emulated = False
                        sess._ocr_populated = False
                        sess.enable_background_focus_emulation()
                        sess.ensure_runtime()
                        virtual_tools = self.get_macros_for_url(sess.last_url)
                        had_pre_on_current_page = False
                else:
                    pre_steps = active_steps[last_idx : b_idx + 1]
                    res = sess._raw_page.evaluate(
                        "req => window.__fmu.executeBatch(req)",
                        {
                            "agent": agent_id,
                            "steps": pre_steps,
                            "virtualTools": virtual_tools,
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
                        last_esc = escs[-1] if escs else {}
                        esc_uid = last_esc.get("uid") if isinstance(last_esc, dict) else None
                        already_handled = bool(last_esc.get("handled")) if isinstance(last_esc, dict) else False
                        if not already_handled:
                            sel = f'[data-fmu-upload-uid="{esc_uid}"]' if esc_uid else "input[type='file']"
                            if sess.is_extension:
                                sess._raw_page.set_input_files(sel, file_list)
                            else:
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
                    "virtualTools": virtual_tools,
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
                    "virtualTools": virtual_tools,
                    "query": req.get("query") or "",
                    "diff": req.get("diff"),
                    "fullTable": req.get("fullTable", False),
                    "settle": req.get("settle", True),
                },
            )

        if (
            req.get("ocr") is not False
            and res.get("canvases")
            and (active_steps or not sess._ocr_populated or req.get("ocr") is True)
        ):
            new_ocr = self._sync_canvas_ocr(sess, res.get("canvases"))
            try:
                refreshed = sess._raw_page.evaluate(
                    "opts => window.__fmu.refreshAfterOcr(opts)",
                    {
                        "agent": agent_id,
                        "ocrTargets": new_ocr,
                        "query": req.get("query") or "",
                        "fullTable": req.get("fullTable", False),
                        "useDiff": bool(res.get("useDiff")),
                    },
                )
                if isinstance(refreshed, dict):
                    res["crc"] = refreshed.get("crc", res.get("crc"))
                    res["seq"] = refreshed.get("seq", res.get("seq"))
                    res["count"] = refreshed.get("count", res.get("count"))
                    res["lines"] = refreshed.get("lines", res.get("lines"))
            except Exception:
                pass

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
        if sess.is_extension:
            header += ' bridge="mv3"'
            if getattr(sess._raw_page, "window_id", None) is not None:
                header += f' window={sess._raw_page.window_id}'
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
            header += "\n# api: click(uid|text), fill(uid,val), select(uid,val), move(uid|text), press(key), eval(code), callTool(name,args), saveMacro(name,steps), snapshot(), diff()"
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
        self._last_tab_steps.clear()
        self.cursors.clear()
        self._next_tab_num = 1

    def shutdown(self) -> None:
        self._run_sync(self._shutdown_inner)

    def _shutdown_inner(self) -> None:
        self._reset_inner()
        self._evict_dead_browser()
        with self._bridge_lock:
            if self._ext_bridge_inst is not None:
                self._ext_bridge_inst.shutdown()
                self._ext_bridge_inst = None


