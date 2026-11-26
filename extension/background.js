// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 lifan-builds. Faster Mac Use; Section 7 attribution terms in ATTRIBUTION.md.
const DEFAULT_RELAY_URL = "ws://127.0.0.1:19222/ext";
let relayUrl = DEFAULT_RELAY_URL;
let ws = null;
let isConnecting = false;
let reconnectTimer = null;
const sessionGroups = new Map();
const sessionTabs = new Map();
const attachedDebuggers = new Set();
const debuggerLastUsedAt = new Map();
const verifiedRendererTabs = new Map();
const IDLE_DEBUGGER_DETACH_MS = 5 * 60 * 1000;
const initializedRuntimeTabs = new Map();
const STRICT_CSP_HOSTS_KEY = "fmuStrictCspHosts";
const strictCspHosts = new Set();
let strictCspHostsLoaded = null;
const pendingNetRequests = new Map();
let cachedRuntimeJs = "";
let cachedRuntimeVersion = "";
let activeScreencastTabId = null;
let screencastIdleTimer = null;
let lastScreencastPushAt = 0;
let pendingPreviewTimer = null;

const AGENT_TAB_CLAIMS_KEY = "fmuAgentTabClaims";
const AGENT_GROUP_TITLE_RE = /^(?:(?:🤖\s*)?Agent(?:$|\s)|(?:Devin|Codex|Claude|Antigravity|Gemini|Cursor|OpenCode|Goose|Amp)(?:$|\s+\d+$| · ))/;
const agentTabClaims = new Map();
let agentTabClaimsLoaded = null;

function claimStorage() {
  return (chrome.storage && (chrome.storage.local || chrome.storage.session)) || null;
}

function loadAgentTabClaims() {
  if (!agentTabClaimsLoaded) {
    const store = claimStorage();
    agentTabClaimsLoaded = store
      ? store.get(AGENT_TAB_CLAIMS_KEY).then(saved => {
          for (const [tabId, claim] of Object.entries((saved && saved[AGENT_TAB_CLAIMS_KEY]) || {})) {
            agentTabClaims.set(Number(tabId), claim);
          }
        })
      : Promise.resolve();
  }
  return agentTabClaimsLoaded;
}

function saveAgentTabClaims() {
  const store = claimStorage();
  return store ? store.set({ [AGENT_TAB_CLAIMS_KEY]: Object.fromEntries(agentTabClaims) }) : Promise.resolve();
}

async function isAgentGroupId(groupId) {
  if (groupId === undefined || groupId === null || groupId < 0) return false;
  if (!chrome.tabGroups || typeof chrome.tabGroups.get !== "function") return false;
  try {
    const g = await chrome.tabGroups.get(groupId);
    return Boolean(g && AGENT_GROUP_TITLE_RE.test(String(g.title || "").trim()));
  } catch (_) {
    return false;
  }
}

async function claimTab(tabId, owner, opened, agent = "") {
  await loadAgentTabClaims();
  const prev = agentTabClaims.get(tabId);
  agentTabClaims.set(tabId, {
    owner: owner || (prev ? prev.owner : ""),
    opened: Boolean(opened || (prev && prev.opened)),
    agent: agent || (prev && prev.agent) || ""
  });
  await saveAgentTabClaims();
}

async function bindSessionTab(sessionId, tabId, owner, prevGroupId = undefined, agent = "") {
  sessionTabs.set(sessionId, tabId);
  await loadAgentTabClaims();
  const prev = agentTabClaims.get(tabId);
  const inheritedOpened = !prev && (await isAgentGroupId(prevGroupId));
  await claimTab(tabId, owner, inheritedOpened, agent);
}

async function foreignTabIds(owner) {
  if (!owner) return new Set();
  await loadAgentTabClaims();
  return new Set([...agentTabClaims].filter(([, c]) => c.owner && c.owner !== owner).map(([tabId]) => tabId));
}

async function releaseTabClaims(tabIds) {
  await loadAgentTabClaims();
  let changed = false;
  for (const tabId of tabIds) changed = agentTabClaims.delete(tabId) || changed;
  if (changed) await saveAgentTabClaims();
}

function loadStrictCspHosts() {
  if (!strictCspHostsLoaded) {
    const store = claimStorage();
    strictCspHostsLoaded = store
      ? store.get(STRICT_CSP_HOSTS_KEY).then(saved => {
          const list = (saved && saved[STRICT_CSP_HOSTS_KEY]) || [];
          if (Array.isArray(list)) {
            for (const host of list) {
              if (host) strictCspHosts.add(String(host));
            }
          }
        })
      : Promise.resolve();
  }
  return strictCspHostsLoaded;
}

async function rememberStrictCspHost(host) {
  if (!host) return;
  await loadStrictCspHosts();
  if (strictCspHosts.has(host)) return;
  strictCspHosts.add(host);
  if (strictCspHosts.size > 256) {
    const oldest = strictCspHosts.values().next().value;
    if (oldest) strictCspHosts.delete(oldest);
  }
  const store = claimStorage();
  if (store) await store.set({ [STRICT_CSP_HOSTS_KEY]: Array.from(strictCspHosts) });
}

chrome.tabs.onRemoved.addListener(tabId => {
  debuggerLastUsedAt.delete(tabId);
  verifiedRendererTabs.delete(tabId);
  attachedDebuggers.delete(tabId);
  initializedRuntimeTabs.delete(tabId);
  return releaseTabClaims([tabId]);
});

if (chrome.tabs && chrome.tabs.onCreated && typeof chrome.tabs.onCreated.addListener === "function") {
  chrome.tabs.onCreated.addListener(tab => {
    if (!tab || tab.id === undefined) return;
    void (async () => {
      await loadAgentTabClaims();
      if (agentTabClaims.has(tab.id)) return;
      const openerClaim = tab.openerTabId !== undefined ? agentTabClaims.get(tab.openerTabId) : null;
      let inheritedOwner = "";
      let inheritedAgent = "";
      let shouldClaim = false;
      if (openerClaim && openerClaim.opened) {
        inheritedOwner = openerClaim.owner || "";
        inheritedAgent = openerClaim.agent || "";
        shouldClaim = true;
      } else if (await isAgentGroupId(tab.groupId)) {
        inheritedOwner = (openerClaim && openerClaim.owner) || "";
        inheritedAgent = (openerClaim && openerClaim.agent) || "";
        shouldClaim = true;
      } else if (!openerClaim && tab.openerTabId !== undefined && chrome.tabs && typeof chrome.tabs.get === "function") {
        try {
          const openerTab = await chrome.tabs.get(tab.openerTabId);
          if (openerTab && (await isAgentGroupId(openerTab.groupId))) {
            shouldClaim = true;
          }
        } catch (_) {}
      }
      if (shouldClaim) await claimTab(tab.id, inheritedOwner, true, inheritedAgent);
    })();
  });
}

function scheduleReconnect(delayMs = 500) {
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      connectRelay();
    }
  }, delayMs);
}

async function detachIdleDebuggers(now = Date.now(), maxIdleMs = IDLE_DEBUGGER_DETACH_MS) {
  if (!chrome.debugger || typeof chrome.debugger.detach !== "function") return 0;
  let detached = 0;
  for (const tabId of Array.from(attachedDebuggers)) {
    if (tabId === activeScreencastTabId) continue;
    const lastUsed = debuggerLastUsedAt.get(tabId) || 0;
    if (now - lastUsed < maxIdleMs) continue;
    attachedDebuggers.delete(tabId);
    debuggerLastUsedAt.delete(tabId);
    verifiedRendererTabs.delete(tabId);
    try {
      await chrome.debugger.detach({ tabId });
      detached += 1;
    } catch (_) {}
  }
  return detached;
}

chrome.alarms.create("fmu-keepalive", { periodInMinutes: 0.25 });
chrome.alarms.onAlarm.addListener(() => {
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    connectRelay();
  }
  void detachIdleDebuggers();
});
if (chrome.runtime && chrome.runtime.onStartup) {
  chrome.runtime.onStartup.addListener(() => connectRelay());
}
if (chrome.runtime && chrome.runtime.onInstalled) {
  chrome.runtime.onInstalled.addListener(() => connectRelay());
}

function getHostname(rawUrl) {
  try {
    const u = new URL(normalizeNavUrl(rawUrl));
    return (u.hostname || "").toLowerCase();
  } catch (_) {
    return "";
  }
}

function hasUrlScheme(rawUrl) {
  return /^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(String(rawUrl || "").trim());
}

function normalizeNavUrl(rawUrl) {
  const s = String(rawUrl || "").trim();
  if (!s || s === "about:blank") return s || "about:blank";
  return hasUrlScheme(s) ? s : `https://${s}`;
}

async function findGroupWindowId(sessionName = "Agent", targetWindowId = undefined) {
  const cacheKey = targetWindowId !== undefined ? `${sessionName}@${targetWindowId}` : sessionName;
  const cachedGid = sessionGroups.get(cacheKey);
  if (cachedGid !== undefined && chrome.tabGroups && typeof chrome.tabGroups.get === "function") {
    try {
      const g = await chrome.tabGroups.get(cachedGid);
      if (g && g.windowId !== undefined && (targetWindowId === undefined || g.windowId === targetWindowId)) {
        return { groupId: g.id, windowId: g.windowId };
      }
    } catch (_) {
      sessionGroups.delete(cacheKey);
    }
  }
  if (chrome.tabGroups && typeof chrome.tabGroups.query === "function") {
    try {
      const q = { title: sessionName };
      if (targetWindowId !== undefined) q.windowId = targetWindowId;
      const existing = await chrome.tabGroups.query(q);
      if (existing && existing.length > 0) {
        const chosen = targetWindowId !== undefined
          ? existing.find(g => g.windowId === targetWindowId) || existing[0]
          : existing[0];
        if (chosen && (targetWindowId === undefined || chosen.windowId === targetWindowId)) {
          sessionGroups.set(cacheKey, chosen.id);
          sessionGroups.set(sessionName, chosen.id);
          return { groupId: chosen.id, windowId: chosen.windowId };
        }
      }
    } catch (_) {}
  }
  return { groupId: undefined, windowId: undefined };
}

async function ensureAgentGroup(tabId, sessionName = "Agent", color = "blue", targetWindowId = undefined) {
  const { groupId: existingGid } = await findGroupWindowId(sessionName, targetWindowId);
  let groupId = existingGid;
  if (groupId !== undefined) {
    try {
      await chrome.tabs.group({ tabIds: [tabId], groupId });
      return groupId;
    } catch (_) {
      sessionGroups.delete(sessionName);
      if (targetWindowId !== undefined) sessionGroups.delete(`${sessionName}@${targetWindowId}`);
      groupId = undefined;
    }
  }
  const groupOpts = { tabIds: [tabId] };
  if (targetWindowId !== undefined) {
    groupOpts.createProperties = { windowId: targetWindowId };
  }
  try {
    groupId = await chrome.tabs.group(groupOpts);
  } catch (_) {
    groupId = await chrome.tabs.group({ tabIds: [tabId] });
  }
  await chrome.tabGroups.update(groupId, {
    title: sessionName,
    color,
    collapsed: false
  });
  sessionGroups.set(sessionName, groupId);
  if (targetWindowId !== undefined) {
    sessionGroups.set(`${sessionName}@${targetWindowId}`, groupId);
  }
  return groupId;
}

async function waitForTabLoad(tabId, timeoutMs = 10000, expectedUrl = "", prevUrl = "") {
  if (!chrome.tabs || typeof chrome.tabs.get !== "function") return;
  const start = Date.now();
  const deadline = start + Math.max(500, timeoutMs);
  const wantRealUrl = Boolean(expectedUrl && expectedUrl !== "about:blank");
  let lastDomProbeAt = 0;
  while (Date.now() < deadline) {
    try {
      const t = await chrome.tabs.get(tabId);
      if (!t) return;
      const curUrl = String(t.url || "");
      const pendingUrl = String(t.pendingUrl || "");
      const elapsed = Date.now() - start;
      if (t.status === "complete" && !pendingUrl) {
        if (!wantRealUrl || (curUrl && curUrl !== "about:blank") || elapsed > 450) {
          return;
        }
      }
        if (
          !pendingUrl
          && curUrl
          && curUrl !== "about:blank"
          && !curUrl.startsWith("chrome://")
          && (!prevUrl || curUrl !== prevUrl || elapsed >= 400)
          && elapsed >= 350
          && Date.now() - lastDomProbeAt >= 150
          && chrome.debugger
          && typeof chrome.debugger.sendCommand === "function"
        ) {
          lastDomProbeAt = Date.now();
          try {
            await ensureDebuggerAndFocusEmulation(tabId);
            const rs = await chrome.debugger.sendCommand({ tabId }, "Runtime.evaluate", {
              expression: "(location.href !== 'about:blank' && (document.readyState === 'interactive' || document.readyState === 'complete') && Boolean(document.body && document.body.children && document.body.children.length > 0))",
              returnByValue: true
            });
            if (rs && rs.result && rs.result.value === true) {
              return;
            }
          } catch (_) {}
        }
    } catch (_) {
      return;
    }
    await new Promise(r => setTimeout(r, 35));
  }
}

async function ensureDebuggerAndFocusEmulation(tabId) {
  debuggerLastUsedAt.set(tabId, Date.now());
  if (attachedDebuggers.has(tabId)) return;
  try {
    await chrome.debugger.attach({ tabId }, "1.3");
  } catch (err) {
    const msg = String((err && err.message) || err || "").toLowerCase();
    if (!msg.includes("already attached")) {
      throw err;
    }
  }
  attachedDebuggers.add(tabId);
  debuggerLastUsedAt.set(tabId, Date.now());
  try {
    await chrome.debugger.sendCommand({ tabId }, "Emulation.setFocusEmulationEnabled", { enabled: true });
  } catch (_) {}
  try {
    await chrome.debugger.sendCommand({ tabId }, "Page.setWebLifecycleState", { state: "active" });
  } catch (_) {}
  try {
    await chrome.debugger.sendCommand({ tabId }, "Runtime.enable", {});
  } catch (_) {}
  try {
    await chrome.debugger.sendCommand({ tabId }, "Log.enable", {});
  } catch (_) {}
  try {
    await chrome.debugger.sendCommand({ tabId }, "Network.enable", {
      maxPostDataSize: 0,
      maxResourceBufferSize: 0,
      maxTotalBufferSize: 0
    });
  } catch (_) {}
}

async function sendDebuggerCommand(tabId, method, commandParams = {}) {
  await ensureDebuggerAndFocusEmulation(tabId);
  try {
    return await chrome.debugger.sendCommand({ tabId }, method, commandParams);
  } catch (err) {
    const msg = String((err && err.message) || err || "").toLowerCase();
    if (msg.includes("not attached") || msg.includes("detached")) {
      attachedDebuggers.delete(tabId);
      await ensureDebuggerAndFocusEmulation(tabId);
      return await chrome.debugger.sendCommand({ tabId }, method, commandParams);
    }
    throw err;
  }
}

if (chrome.debugger && chrome.debugger.onDetach) {
  chrome.debugger.onDetach.addListener((source) => {
    if (source.tabId) {
      attachedDebuggers.delete(source.tabId);
      debuggerLastUsedAt.delete(source.tabId);
      verifiedRendererTabs.delete(source.tabId);
      initializedRuntimeTabs.delete(source.tabId);
      if (activeScreencastTabId === source.tabId) {
        activeScreencastTabId = null;
      }
    }
  });
}

if (chrome.tabs && chrome.tabs.onUpdated && typeof chrome.tabs.onUpdated.addListener === "function") {
  chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
    if (changeInfo && changeInfo.status === "loading") {
      initializedRuntimeTabs.delete(tabId);
    }
  });
}

function emitTabDiagnostic(tabId, entry) {
  if (!ws || ws.readyState !== WebSocket.OPEN || !tabId || !entry) return;
  try {
    ws.send(JSON.stringify({ type: "tabDiagnostic", tabId, entry }));
  } catch (_) {}
}

if (chrome.debugger && chrome.debugger.onEvent && typeof chrome.debugger.onEvent.addListener === "function") {
  chrome.debugger.onEvent.addListener((source, method, params) => {
    if (!source || !source.tabId || !params) return;
    const tabId = source.tabId;
    if (method === "Page.screencastFrame") {
      if (params.sessionId !== undefined) {
        chrome.debugger.sendCommand({ tabId }, "Page.screencastFrameAck", { sessionId: params.sessionId }).catch(() => {});
      }
      const now = Date.now();
      if (!params.data || now - lastScreencastPushAt < 120) return;
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      lastScreencastPushAt = now;
      const meta = params.metadata || {};
      try {
        ws.send(JSON.stringify({
          type: "screencastFrame",
          tabId,
          data: params.data,
          vw: Math.round(Number(meta.deviceWidth || 0)),
          vh: Math.round(Number(meta.deviceHeight || 0)),
          ts: now
        }));
      } catch (_) {}
      return;
    }
    if (method === "Runtime.exceptionThrown") {
      const det = params.exceptionDetails || {};
      const exc = det.exception || {};
      const msg = String(exc.description || exc.value || det.text || "Uncaught exception").trim();
      emitTabDiagnostic(tabId, { kind: "pageerror", message: msg.slice(0, 500), url: String(det.url || "") });
      return;
    }
    if (method === "Runtime.consoleAPICalled" && params.type === "error") {
      const parts = (params.args || []).map(a => String(a.value !== undefined ? a.value : (a.description || ""))).filter(Boolean);
      const msg = parts.join(" ").trim();
      if (msg) emitTabDiagnostic(tabId, { kind: "console.error", message: msg.slice(0, 500), url: "" });
      return;
    }
    if (method === "Log.entryAdded" && params.entry && params.entry.level === "error") {
      const ent = params.entry;
      const u = String(ent.url || "");
      if (u.endsWith("/favicon.ico") || ent.source === "network") return;
      const msg = String(ent.text || "").trim();
      if (msg) emitTabDiagnostic(tabId, { kind: "console.error", message: msg.slice(0, 500), url: u });
      return;
    }
    if (method === "Network.requestWillBeSent" && params.requestId && params.request) {
      const rType = String(params.type || "");
      if (rType === "Document" || rType === "Fetch" || rType === "XHR") {
        if (pendingNetRequests.size > 250) {
          const oldest = pendingNetRequests.keys().next().value;
          if (oldest !== undefined) pendingNetRequests.delete(oldest);
        }
        pendingNetRequests.set(`${tabId}:${params.requestId}`, {
          method: String(params.request.method || "GET"),
          url: String(params.request.url || ""),
          type: rType
        });
      }
      return;
    }
    if (method === "Network.responseReceived" && params.response) {
      const reqKey = `${tabId}:${params.requestId}`;
      const tracked = pendingNetRequests.get(reqKey);
      pendingNetRequests.delete(reqKey);
      const rType = String(params.type || (tracked && tracked.type) || "");
      const status = Number(params.response.status || 0);
      const u = String(params.response.url || (tracked && tracked.url) || "");
      if (status >= 400 && (rType === "Document" || rType === "Fetch" || rType === "XHR") && !u.endsWith("/favicon.ico")) {
        const verb = (tracked && tracked.method) || "GET";
        emitTabDiagnostic(tabId, { kind: "http", status, url: u, message: `${status} ${verb} ${u}`.slice(0, 500) });
      }
      return;
    }
    if (method === "Network.loadingFailed" && params.requestId) {
      const reqKey = `${tabId}:${params.requestId}`;
      const tracked = pendingNetRequests.get(reqKey);
      pendingNetRequests.delete(reqKey);
      const errText = String(params.errorText || "failed");
      if (tracked && !params.canceled && errText !== "net::ERR_ABORTED" && !tracked.url.endsWith("/favicon.ico")) {
        emitTabDiagnostic(tabId, {
          kind: "network",
          url: tracked.url,
          message: `${tracked.method} ${tracked.url} (${errText})`.slice(0, 500)
        });
      }
    }
  });
}

function stopLiveScreencast(tabId = undefined) {
  if (screencastIdleTimer) {
    clearTimeout(screencastIdleTimer);
    screencastIdleTimer = null;
  }
  const targetId = tabId !== undefined ? tabId : activeScreencastTabId;
  if (targetId && attachedDebuggers.has(targetId)) {
    chrome.debugger.sendCommand({ tabId: targetId }, "Page.stopScreencast", {}).catch(() => {});
  }
  if (tabId === undefined || activeScreencastTabId === tabId) {
    activeScreencastTabId = null;
  }
}

async function startLiveScreencast(tabId) {
  if (!tabId) return;
  if (screencastIdleTimer) {
    clearTimeout(screencastIdleTimer);
  }
  screencastIdleTimer = setTimeout(() => {
    screencastIdleTimer = null;
    stopLiveScreencast();
  }, 12000);
  if (activeScreencastTabId && activeScreencastTabId !== tabId) {
    if (attachedDebuggers.has(activeScreencastTabId)) {
      chrome.debugger.sendCommand({ tabId: activeScreencastTabId }, "Page.stopScreencast", {}).catch(() => {});
    }
    activeScreencastTabId = null;
  }
  if (activeScreencastTabId === tabId) return;
  try {
    await ensureDebuggerAndFocusEmulation(tabId);
    activeScreencastTabId = tabId;
    await chrome.debugger.sendCommand({ tabId }, "Page.startScreencast", {
      format: "jpeg",
      quality: 66,
      maxWidth: 1440,
      maxHeight: 900,
      everyNthFrame: 1
    });
  } catch (_) {
    activeScreencastTabId = null;
  }
}

async function captureViewportPreview(tabId, maxWidth = 1440) {
  if (!tabId) return null;
  const work = (async () => {
    await ensureDebuggerAndFocusEmulation(tabId);
    let pageX = 0;
    let pageY = 0;
    let vw = 1280;
    let vh = 800;
    try {
      const metrics = await sendDebuggerCommand(tabId, "Page.getLayoutMetrics", {});
      const vp = (metrics && (metrics.cssVisualViewport || metrics.visualViewport)) || null;
      if (vp && Number(vp.clientWidth) > 80 && Number(vp.clientHeight) > 60) {
        pageX = Math.max(0, Number(vp.pageX || 0));
        pageY = Math.max(0, Number(vp.pageY || 0));
        vw = Math.round(Number(vp.clientWidth));
        vh = Math.round(Number(vp.clientHeight));
      }
    } catch (_) {}
    const scale = Math.min(1, Math.max(0.25, maxWidth / Math.max(320, vw)));
    const shot = await sendDebuggerCommand(tabId, "Page.captureScreenshot", {
      format: "jpeg",
      quality: 68,
      optimizeForSpeed: true,
      clip: {
        x: pageX,
        y: pageY,
        width: vw,
        height: vh,
        scale
      }
    });
    if (shot && typeof shot.data === "string" && shot.data.length > 64) {
      lastScreencastPushAt = Date.now();
      return { jpeg: shot.data, vw, vh };
    }
    return null;
  })();
  const timeout = new Promise((resolve) => {
    const t = setTimeout(() => resolve(null), 650);
    if (t && typeof t.unref === "function") t.unref();
  });
  try {
    return await Promise.race([work, timeout]);
  } catch (_) {
    return null;
  }
}

function clearPendingPreview() {
  if (pendingPreviewTimer) {
    clearTimeout(pendingPreviewTimer);
    pendingPreviewTimer = null;
  }
}

function scheduleAsyncPreviewPush(tabId, delayMs = 30) {
  if (!tabId) return;
  clearPendingPreview();
  pendingPreviewTimer = setTimeout(async () => {
    pendingPreviewTimer = null;
    const prev = await captureViewportPreview(tabId);
    if (prev && prev.jpeg && ws && ws.readyState === WebSocket.OPEN) {
      try {
        ws.send(JSON.stringify({
          type: "screencastFrame",
          tabId,
          data: prev.jpeg,
          vw: prev.vw,
          vh: prev.vh,
          ts: Date.now()
        }));
      } catch (_) {}
    }
  }, delayMs);
  if (pendingPreviewTimer && typeof pendingPreviewTimer.unref === "function") {
    pendingPreviewTimer.unref();
  }
}

const UPLOAD_FILE_URL_ERR = 'upload failed: enable "Allow access to file URLs" for the fmu extension in chrome://extensions';

function uploadFileUrlError(inlineSkipped) {
  return new Error(inlineSkipped ? `${UPLOAD_FILE_URL_ERR}, or fix the inline fallback: ${inlineSkipped}` : UPLOAD_FILE_URL_ERR);
}

function fnv1a32(str) {
  let h = 0x811c9dc5;
  const s = String(str || "");
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h.toString(16).padStart(8, "0");
}

function normalizeFieldValueForCheck(str) {
  return String(str || "").replace(/[\s\-/]+/g, "");
}

const CDP_SPECIAL_KEYS = {
  backspace: ["Backspace", "Backspace", 8, null],
  tab: ["Tab", "Tab", 9, null],
  enter: ["Enter", "Enter", 13, "\r"],
  return: ["Enter", "Enter", 13, "\r"],
  escape: ["Escape", "Escape", 27, null],
  esc: ["Escape", "Escape", 27, null],
  space: [" ", "Space", 32, " "],
  end: ["End", "End", 35, null],
  home: ["Home", "Home", 36, null],
  arrowleft: ["ArrowLeft", "ArrowLeft", 37, null],
  left: ["ArrowLeft", "ArrowLeft", 37, null],
  arrowup: ["ArrowUp", "ArrowUp", 38, null],
  up: ["ArrowUp", "ArrowUp", 38, null],
  arrowright: ["ArrowRight", "ArrowRight", 39, null],
  right: ["ArrowRight", "ArrowRight", 39, null],
  arrowdown: ["ArrowDown", "ArrowDown", 40, null],
  down: ["ArrowDown", "ArrowDown", 40, null],
  delete: ["Delete", "Delete", 46, null],
  del: ["Delete", "Delete", 46, null]
};

const CDP_CHAR_KEYS = {
  " ": ["Space", 32, false],
  "-": ["Minus", 189, false],
  "_": ["Minus", 189, true],
  "=": ["Equal", 187, false],
  "+": ["Equal", 187, true],
  "[": ["BracketLeft", 219, false],
  "{": ["BracketLeft", 219, true],
  "]": ["BracketRight", 221, false],
  "}": ["BracketRight", 221, true],
  "\\": ["Backslash", 220, false],
  "|": ["Backslash", 220, true],
  ";": ["Semicolon", 186, false],
  ":": ["Semicolon", 186, true],
  "'": ["Quote", 222, false],
  '"': ["Quote", 222, true],
  ",": ["Comma", 188, false],
  "<": ["Comma", 188, true],
  ".": ["Period", 190, false],
  ">": ["Period", 190, true],
  "/": ["Slash", 191, false],
  "?": ["Slash", 191, true],
  "`": ["Backquote", 192, false],
  "~": ["Backquote", 192, true],
  "!": ["Digit1", 49, true],
  "@": ["Digit2", 50, true],
  "#": ["Digit3", 51, true],
  "$": ["Digit4", 52, true],
  "%": ["Digit5", 53, true],
  "^": ["Digit6", 54, true],
  "&": ["Digit7", 55, true],
  "*": ["Digit8", 56, true],
  "(": ["Digit9", 57, true],
  ")": ["Digit0", 48, true]
};

function isMacHost() {
  if (typeof navigator !== "undefined") {
    const plat = `${navigator.platform || ""} ${navigator.userAgent || ""}`;
    if (plat.trim()) return /Mac|Darwin/i.test(plat);
  }
  return typeof process !== "undefined" && process.platform === "darwin";
}

function parseCdpKeySpec(rawKey) {
  const keyStr = String(rawKey || "Enter");
  let modifiers = 0;
  let main = keyStr;
  if (keyStr.length > 1 && keyStr.includes("+")) {
    const parts = keyStr.split("+").map(p => p.trim()).filter(Boolean);
    if (keyStr.endsWith("+") && parts.length > 0) parts.push("+");
    main = parts.length > 0 ? parts[parts.length - 1] : keyStr;
    for (let i = 0; i < parts.length - 1; i++) {
      const m = parts[i].toLowerCase();
      if (m === "alt" || m === "opt" || m === "option") modifiers |= 1;
      else if (m === "ctrl" || m === "control") modifiers |= 2;
      else if (m === "meta" || m === "cmd" || m === "command" || m === "super") modifiers |= 4;
      else if (m === "shift") modifiers |= 8;
      else if (m === "cmdorctrl") modifiers |= isMacHost() ? 4 : 2;
    }
  }
  const low = main.toLowerCase();
  let commands = [];
  if (Object.prototype.hasOwnProperty.call(CDP_SPECIAL_KEYS, low)) {
    const [keyVal, code, vk, txt] = CDP_SPECIAL_KEYS[low];
    if (modifiers === 0 && low === "backspace") commands = ["deleteBackward"];
    else if (modifiers === 0 && (low === "delete" || low === "del")) commands = ["deleteForward"];
    const noCmd = (modifiers & 7) === 0;
    return {
      key: keyVal,
      code,
      vk,
      modifiers,
      text: noCmd ? txt : null,
      unmodifiedText: noCmd ? txt : null,
      commands
    };
  }
  if (main.length === 1) {
    const ch = main;
    const chCode = ch.charCodeAt(0);
    let code = "";
    let vk = 0;
    if (ch >= "0" && ch <= "9") {
      code = `Digit${ch}`;
      vk = chCode;
    } else if (low >= "a" && low <= "z") {
      code = `Key${ch.toUpperCase()}`;
      vk = ch.toUpperCase().charCodeAt(0);
      if (ch !== low) modifiers |= 8;
      if ((modifiers & 6) !== 0 && (modifiers & 9) === 0 && low === "a") {
        commands = ["selectAll"];
      }
    } else if (Object.prototype.hasOwnProperty.call(CDP_CHAR_KEYS, ch)) {
      const [cName, cVk, needShift] = CDP_CHAR_KEYS[ch];
      code = cName;
      vk = cVk;
      if (needShift) modifiers |= 8;
    } else {
      vk = chCode < 128 ? ch.toUpperCase().charCodeAt(0) : 0;
    }
    const noCmd = (modifiers & 7) === 0;
    const txt = (noCmd && chCode >= 32) ? ch : null;
    const unmod = (noCmd && chCode >= 32) ? ((ch >= "A" && ch <= "Z") ? ch.toLowerCase() : ch) : null;
    return { key: ch, code, vk, modifiers, text: txt, unmodifiedText: unmod, commands };
  }
  return { key: main, code: main, vk: 0, modifiers, text: null, unmodifiedText: null, commands };
}

async function dispatchCdpKey(tabId, rawKey) {
  const ks = parseCdpKeySpec(rawKey);
  const downParams = {
    type: "keyDown",
    key: ks.key,
    code: ks.code,
    windowsVirtualKeyCode: ks.vk,
    nativeVirtualKeyCode: ks.vk,
    modifiers: ks.modifiers
  };
  if (ks.text !== null) {
    downParams.text = ks.text;
    downParams.unmodifiedText = ks.unmodifiedText;
  }
  if (ks.commands && ks.commands.length > 0) {
    downParams.commands = ks.commands;
  }
  await sendDebuggerCommand(tabId, "Input.dispatchKeyEvent", downParams);
  await sendDebuggerCommand(tabId, "Input.dispatchKeyEvent", {
    type: "keyUp",
    key: ks.key,
    code: ks.code,
    windowsVirtualKeyCode: ks.vk,
    nativeVirtualKeyCode: ks.vk,
    modifiers: ks.modifiers
  });
}

async function dispatchCdpTextKeys(tabId, text) {
  const s = String(text || "");
  for (let i = 0; i < s.length; i++) {
    const ch = s[i];
    const cp = ch.charCodeAt(0);
    if (ch === "\n" || ch === "\r") {
      await dispatchCdpKey(tabId, "Enter");
    } else if (cp >= 32 && cp <= 126) {
      const ks = parseCdpKeySpec(ch);
      await sendDebuggerCommand(tabId, "Input.dispatchKeyEvent", {
        type: "keyDown",
        key: ch,
        code: ks.code,
        windowsVirtualKeyCode: ks.vk,
        nativeVirtualKeyCode: ks.vk,
        modifiers: ks.modifiers,
        text: ch,
        unmodifiedText: (ch >= "A" && ch <= "Z") ? ch.toLowerCase() : ch
      });
      await sendDebuggerCommand(tabId, "Input.dispatchKeyEvent", {
        type: "keyUp",
        key: ch,
        code: ks.code,
        windowsVirtualKeyCode: ks.vk,
        nativeVirtualKeyCode: ks.vk,
        modifiers: ks.modifiers
      });
    } else {
      await sendDebuggerCommand(tabId, "Input.insertText", { text: ch });
    }
    await new Promise(r => setTimeout(r, 5));
  }
}

async function probeAndPrepIframeInTab(tabId, opts) {
  if (!chrome.scripting || typeof chrome.scripting.executeScript !== "function") {
    return { supported: false, matched: false };
  }
  const value = String((opts && opts.value) || "");
  const expNorm = normalizeFieldValueForCheck(value);
  const explicitFrameId = (opts && opts.frameId !== undefined && opts.frameId !== null)
    ? Number(opts.frameId)
    : null;
  const wantFrameIndex = (opts && typeof opts.frameIndex === "number" && opts.frameIndex >= 0)
    ? opts.frameIndex
    : -1;
  const probeArg = {
    action: String((opts && opts.action) || "verify"),
    frameSrc: String((opts && opts.frameSrc) || "").trim(),
    frameName: String((opts && opts.frameName) || "").trim(),
    frameIndex: wantFrameIndex,
    expectedHash: fnv1a32(value),
    expectedLen: value.length,
    expectedNormHash: fnv1a32(expNorm),
    expectedNormLen: expNorm.length
  };
  const target = (explicitFrameId !== null && !Number.isNaN(explicitFrameId))
    ? { tabId, frameIds: [explicitFrameId] }
    : { tabId, allFrames: true };
  let frameResults = [];
  try {
    frameResults = await chrome.scripting.executeScript({
      target,
      args: [probeArg],
      func: (arg) => {
        if (window === window.top) return null;
        const curHref = String(location.href || "");
        let score = 0;
        if (arg.frameSrc) {
          if (curHref === arg.frameSrc) {
            score = 100 + (arg.frameName && window.name === arg.frameName ? 10 : 0);
          } else {
            try {
              const u1 = new URL(curHref);
              const u2 = new URL(arg.frameSrc, curHref);
              const p1 = u1.pathname.replace(/\/+$/, "") || "/";
              const p2 = u2.pathname.replace(/\/+$/, "") || "/";
              if (u1.origin === u2.origin && p1 === p2) {
                score = 80 + (arg.frameName && window.name === arg.frameName ? 10 : 0);
              } else {
                return null;
              }
            } catch (_) {
              return null;
            }
          }
          if (arg.frameName && window.name && window.name !== arg.frameName) {
            score -= 20;
          }
        } else {
          score = (arg.frameName && window.name === arg.frameName) ? 60 : 10;
        }
        let frameIndex = -1;
        try {
          if (window.parent === window.top) {
            for (let k = 0; k < window.top.length; k++) {
              if (window.top[k] === window) { frameIndex = k; break; }
            }
          }
        } catch (_) {}
        const isEditable = (el) => {
          if (!el || el.disabled) return false;
          const tag = (el.tagName || "").toLowerCase();
          if (tag === "textarea") return true;
          if (tag === "input") {
            const tp = String(el.type || "text").toLowerCase();
            return !["hidden", "checkbox", "radio", "button", "submit", "reset", "file", "image"].includes(tp);
          }
          const ce = el.getAttribute && el.getAttribute("contenteditable");
          return Boolean(el.isContentEditable || ce === "true" || ce === "" || ce === "plaintext-only");
        };
        const isVisibleEditable = (el) => {
          if (!isEditable(el)) return false;
          try {
            const r = el.getBoundingClientRect();
            const st = window.getComputedStyle(el);
            return r.width > 0 && r.height > 0 && st.display !== "none" && st.visibility !== "hidden";
          } catch (_) {
            return true;
          }
        };
        const findFirstEditableDeep = (root) => {
          const sel = "input:not([type='hidden']):not([type='checkbox']):not([type='radio']):not([type='button']):not([type='submit']):not([type='reset']):not([type='file']):not([type='image']), textarea, [contenteditable='true'], [contenteditable=''], [contenteditable='plaintext-only']";
          const list = root.querySelectorAll ? Array.from(root.querySelectorAll(sel)) : [];
          let fallback = null;
          for (const cand of list) {
            if (isVisibleEditable(cand)) return cand;
            if (!fallback && isEditable(cand)) fallback = cand;
          }
          const all = root.querySelectorAll ? root.querySelectorAll("*") : [];
          for (let i = 0; i < all.length; i++) {
            if (all[i].shadowRoot) {
              const inner = findFirstEditableDeep(all[i].shadowRoot);
              if (inner) return inner;
            }
          }
          return fallback;
        };
        let a = document.activeElement;
        while (a && a.shadowRoot && a.shadowRoot.activeElement) a = a.shadowRoot.activeElement;
        const hasFocus = Boolean(document.hasFocus && document.hasFocus());
        const activeIsEditable = isEditable(a);
        const targetEl = activeIsEditable ? a : findFirstEditableDeep(document);
        if (!targetEl) {
          return { supported: true, matched: false, score, frameIndex, hasFocus, activeIsEditable };
        }
        const readVal = () => (
          "value" in targetEl && targetEl.value !== undefined && typeof targetEl.value === "string"
            ? targetEl.value
            : String(targetEl.innerText || targetEl.textContent || "")
        );
        if (arg.action === "probe") {
          return {
            supported: true,
            matched: true,
            score,
            frameIndex,
            hasFocus,
            activeIsEditable,
            curLen: readVal().length
          };
        }
        if (arg.action === "focus_and_select") {
          try { targetEl.focus({ preventScroll: true }); } catch (_) { try { targetEl.focus(); } catch (_) {} }
          try {
            if (typeof targetEl.select === "function") {
              targetEl.select();
            } else if (window.getSelection && document.createRange) {
              const range = document.createRange();
              range.selectNodeContents(targetEl);
              const selObj = window.getSelection();
              if (selObj) { selObj.removeAllRanges(); selObj.addRange(range); }
            }
          } catch (_) {}
          return {
            supported: true,
            matched: true,
            score,
            frameIndex,
            hasFocus: Boolean(document.hasFocus && document.hasFocus()),
            activeIsEditable: isEditable(targetEl),
            curLen: readVal().length
          };
        }
        if (arg.action === "force_clear") {
          try { targetEl.focus({ preventScroll: true }); } catch (_) {}
          const tag = (targetEl.tagName || "").toLowerCase();
          if (tag === "input" || tag === "textarea") {
            const proto = tag === "textarea" ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
            const desc = proto ? Object.getOwnPropertyDescriptor(proto, "value") : null;
            try {
              if (desc && desc.set) desc.set.call(targetEl, "");
              else targetEl.value = "";
            } catch (_) { targetEl.value = ""; }
          } else {
            targetEl.textContent = "";
          }
          targetEl.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "deleteContentBackward", data: null }));
          targetEl.dispatchEvent(new Event("change", { bubbles: true }));
          return {
            supported: true,
            matched: true,
            score,
            frameIndex,
            hasFocus: Boolean(document.hasFocus && document.hasFocus()),
            activeIsEditable: isEditable(targetEl),
            curLen: readVal().length
          };
        }
        const actual = readVal();
        const normActual = actual.replace(/[\s\-/]+/g, "");
        const fnv = (s) => {
          let h = 0x811c9dc5;
          for (let i = 0; i < s.length; i++) {
            h ^= s.charCodeAt(i);
            h = Math.imul(h, 0x01000193) >>> 0;
          }
          return h.toString(16).padStart(8, "0");
        };
        const exactMatch = fnv(actual) === arg.expectedHash && actual.length === arg.expectedLen;
        const normMatch = fnv(normActual) === arg.expectedNormHash && normActual.length === arg.expectedNormLen;
        const bulletMasked = normActual.length > 0 && /^[•*]+$/.test(normActual) && arg.expectedNormLen >= 8 && normActual.length === arg.expectedNormLen;
        return {
          supported: true,
          matched: true,
          score,
          frameIndex,
          hasFocus,
          activeIsEditable,
          curLen: actual.length,
          actualNormLen: normActual.length,
          verified: Boolean(exactMatch || normMatch || bulletMasked)
        };
      }
    });
  } catch (_) {
    return { supported: false, matched: false };
  }
  const rankOf = (r) => [
    (wantFrameIndex >= 0 && r.frameIndex === wantFrameIndex) ? 1 : 0,
    (r.hasFocus && r.activeIsEditable) ? 1 : 0,
    r.hasFocus ? 1 : 0,
    Number(r.score || 0)
  ];
  const isBetterRank = (a, b) => {
    for (let i = 0; i < a.length; i++) {
      if (a[i] !== b[i]) return a[i] > b[i];
    }
    return false;
  };
  let best = null;
  let bestRank = [-1, -1, -1, -1];
  for (const fr of frameResults || []) {
    const r = fr && fr.result;
    if (!r || !r.matched) continue;
    const candRank = rankOf(r);
    if (!best || isBetterRank(candRank, bestRank)) {
      best = { ...r, frameId: fr.frameId };
      bestRank = candRank;
    }
  }
  return best || { supported: true, matched: false };
}

async function prepCdpIframeInTab(tabId, spec) {
  if (!spec || (!spec.uid && !spec.frameSrc && !spec.frameName && !spec.frameTitle && spec.frameIndex === undefined)) {
    return spec;
  }
  const uid = String(spec.uid || spec.label || "iframe");
  const querySpec = {
    uid: spec.uid || null,
    frameSrc: spec.frameSrc || "",
    frameName: spec.frameName || "",
    frameTitle: spec.frameTitle || "",
    frameIndex: typeof spec.frameIndex === "number" ? spec.frameIndex : -1
  };
  let prep = null;
  try {
    prep = await evalInTab(
      tabId,
      `(window.__fmu && typeof window.__fmu.prepIframeTarget === "function") ? window.__fmu.prepIframeTarget(${JSON.stringify(querySpec)}) : null`,
      false
    );
  } catch (_) {
    prep = null;
  }
  if (prep && typeof prep === "object") {
    if (prep.inViewport === false) {
      throw new Error(`cross-origin iframe ${uid} is outside visible viewport`);
    }
    const nextSpec = { ...spec };
    if (typeof prep.x === "number" && Number.isFinite(prep.x) && typeof prep.y === "number" && Number.isFinite(prep.y)) {
      nextSpec.x = prep.x;
      nextSpec.y = prep.y;
    }
    if (typeof prep.frameIndex === "number" && prep.frameIndex >= 0) {
      nextSpec.frameIndex = prep.frameIndex;
    }
    return nextSpec;
  }
  return spec;
}

async function clearCdpFocusedInput(tabId, spec) {
  await dispatchCdpKey(tabId, isMacHost() ? "Meta+a" : "Control+a");
  await dispatchCdpKey(tabId, "Backspace");
  await dispatchCdpKey(tabId, "Delete");
  await new Promise(r => setTimeout(r, 20));
  const st = await probeAndPrepIframeInTab(tabId, { ...spec, action: "verify", value: "" });
  if (st.matched && Number(st.curLen || 0) > 0) {
    await probeAndPrepIframeInTab(tabId, { ...spec, action: "focus_and_select", value: "" });
    await dispatchCdpKey(tabId, "Backspace");
    await new Promise(r => setTimeout(r, 15));
    const st2 = await probeAndPrepIframeInTab(tabId, { ...spec, action: "verify", value: "" });
    if (st2.matched && Number(st2.curLen || 0) > 0) {
      await probeAndPrepIframeInTab(tabId, { ...spec, action: "force_clear", value: "" });
    }
  }
}

async function dispatchCdpInput(tabId, spec) {
  const kind = String((spec && spec.kind) || "fill");
  if (kind === "key") {
    await dispatchCdpKey(tabId, String((spec && spec.key) || "Enter"));
    return;
  }
  if (kind === "type") {
    await dispatchCdpTextKeys(tabId, String((spec && spec.value) || ""));
    return;
  }
  const preppedSpec = await prepCdpIframeInTab(tabId, spec);
  const x = Number(preppedSpec.x);
  const y = Number(preppedSpec.y);
  const value = String(preppedSpec.value || "");
  const clear = preppedSpec.clear !== false;
  const uid = String(preppedSpec.uid || preppedSpec.label || "iframe");
  await sendDebuggerCommand(tabId, "Input.dispatchMouseEvent", { type: "mouseMoved", x, y, button: "none" });
  await sendDebuggerCommand(tabId, "Input.dispatchMouseEvent", { type: "mousePressed", x, y, button: "left", buttons: 1, clickCount: 1 });
  await sendDebuggerCommand(tabId, "Input.dispatchMouseEvent", { type: "mouseReleased", x, y, button: "left", buttons: 0, clickCount: 1 });
  await new Promise(r => setTimeout(r, 30));
  const probeRes = await probeAndPrepIframeInTab(tabId, { ...preppedSpec, action: "probe", value: "" });
  const pinnedSpec = (probeRes && probeRes.matched && probeRes.frameId !== undefined && probeRes.frameId !== null)
    ? { ...preppedSpec, frameId: probeRes.frameId }
    : preppedSpec;
  await probeAndPrepIframeInTab(tabId, { ...pinnedSpec, action: "focus_and_select", value: "" });
  if (clear) {
    await clearCdpFocusedInput(tabId, pinnedSpec);
  }
  if (value) {
    await sendDebuggerCommand(tabId, "Input.insertText", { text: value });
    await new Promise(r => setTimeout(r, 25));
  }
  let verifyRes = await probeAndPrepIframeInTab(tabId, { ...pinnedSpec, action: "verify", value });
  if (verifyRes.matched && !verifyRes.verified) {
    await probeAndPrepIframeInTab(tabId, { ...pinnedSpec, action: "focus_and_select", value: "" });
    await clearCdpFocusedInput(tabId, pinnedSpec);
    if (value) {
      await dispatchCdpTextKeys(tabId, value);
      await new Promise(r => setTimeout(r, 25));
    }
    verifyRes = await probeAndPrepIframeInTab(tabId, { ...pinnedSpec, action: "verify", value });
    if (verifyRes.matched && !verifyRes.verified) {
      const expLen = normalizeFieldValueForCheck(value).length;
      const actLen = Number(verifyRes.actualNormLen || 0);
      throw new Error(`cross-origin iframe verification failed for ${uid} (expected_len=${expLen}, actual_len=${actLen})`);
    }
  }
}

const NON_EXPR_STMT_RE = /^(?:const|let|var|if|for|while|do|switch|try|throw|return|break|continue|function|class|debugger)\b/;

function isNavigationError(err) {
  const msg = String((err && err.message) || err || "").toLowerCase();
  if (
    msg.includes("inspected target navigated or closed")
    || msg.includes("inspected target navigated")
    || msg.includes("execution context was destroyed")
    || msg.includes("most likely because of a navigation")
    || msg.includes("cannot find context with specified id")
    || msg.includes("frame with the given id was not found")
    || msg.includes("frame was detached")
    || msg.includes("navigating frame was detached")
  ) {
    return true;
  }
  return msg.includes("-32000") && msg.includes("navigat");
}

async function waitForNavigationSettlement(tabId, prevUrl = "", timeoutMs = 10000) {
  if (!chrome.tabs || typeof chrome.tabs.get !== "function") {
    await new Promise(r => setTimeout(r, 80));
    try { await ensureDebuggerAndFocusEmulation(tabId); } catch (_) {}
    return { alive: true, url: "", title: "" };
  }
  const commitDeadline = Date.now() + 180;
  while (Date.now() < commitDeadline) {
    let t = null;
    try {
      t = await chrome.tabs.get(tabId);
    } catch (_) {
      return { alive: false, url: "", title: "" };
    }
    if (!t) return { alive: false, url: "", title: "" };
    const curUrl = String(t.url || "");
    const pendingUrl = String(t.pendingUrl || "");
    if (t.status === "loading" || pendingUrl || (prevUrl && curUrl && curUrl !== prevUrl)) {
      break;
    }
    await new Promise(r => setTimeout(r, 20));
  }
  await waitForTabLoad(tabId, timeoutMs, "", prevUrl);
  let finalTab = null;
  try {
    finalTab = await chrome.tabs.get(tabId);
  } catch (_) {
    return { alive: false, url: "", title: "" };
  }
  if (!finalTab) return { alive: false, url: "", title: "" };
  attachedDebuggers.delete(tabId);
  try { await ensureDebuggerAndFocusEmulation(tabId); } catch (_) {}
  return {
    alive: true,
    url: String(finalTab.url || finalTab.pendingUrl || ""),
    title: String(finalTab.title || "")
  };
}

function scanJsTopLevel(rawCode) {
  const src = String(rawCode || "").trim();
  const n = src.length;
  let inStr = null;
  let inLineComment = false;
  let inBlockComment = false;
  let escape = false;
  let depth = 0;
  let hasTopReturn = false;
  const semiPositions = [];

  for (let i = 0; i < n; i++) {
    const ch = src[i];
    const nxt = i + 1 < n ? src[i + 1] : "";
    if (inLineComment) {
      if (ch === "\n") inLineComment = false;
      continue;
    }
    if (inBlockComment) {
      if (ch === "*" && nxt === "/") {
        inBlockComment = false;
        i++;
      }
      continue;
    }
    if (escape) {
      escape = false;
      continue;
    }
    if (inStr !== null) {
      if (ch === "\\") escape = true;
      else if (ch === inStr) inStr = null;
      continue;
    }
    if (ch === "/" && nxt === "/") {
      inLineComment = true;
      i++;
      continue;
    }
    if (ch === "/" && nxt === "*") {
      inBlockComment = true;
      i++;
      continue;
    }
    if (ch === "'" || ch === '"' || ch === "`") {
      inStr = ch;
      continue;
    }
    if (ch === "(" || ch === "[" || ch === "{") {
      depth++;
      continue;
    }
    if (ch === ")" || ch === "]" || ch === "}") {
      depth = Math.max(0, depth - 1);
      continue;
    }
    if (depth === 0) {
      if (ch === ";") {
        semiPositions.push(i);
      } else if (
        ch === "r"
        && src.slice(i, i + 6) === "return"
        && (i === 0 || !/[a-zA-Z0-9_$.]/.test(src[i - 1]))
        && (i + 6 >= n || !/[a-zA-Z0-9_$]/.test(src[i + 6]))
      ) {
        hasTopReturn = true;
      }
    }
  }

  let trimmed = src;
  while (trimmed.endsWith(";")) {
    trimmed = trimmed.slice(0, -1).trimEnd();
  }
  let lastSemi = -1;
  for (let i = 0; i < semiPositions.length; i++) {
    const pos = semiPositions[i];
    if (pos < trimmed.length && trimmed.slice(pos + 1).trim().length > 0) {
      lastSemi = pos;
    }
  }
  const tailStmt = lastSemi >= 0 ? trimmed.slice(lastSemi + 1).trim() : trimmed;
  let tailHasAssign = false;
  if (tailStmt) {
    let tStr = null;
    let tEsc = false;
    let tDepth = 0;
    for (let i = 0; i < tailStmt.length; i++) {
      const ch = tailStmt[i];
      if (tEsc) { tEsc = false; continue; }
      if (tStr !== null) {
        if (ch === "\\") tEsc = true;
        else if (ch === tStr) tStr = null;
        continue;
      }
      if (ch === "'" || ch === '"' || ch === "`") { tStr = ch; continue; }
      if (ch === "(" || ch === "[" || ch === "{") tDepth++;
      else if (ch === ")" || ch === "]" || ch === "}") tDepth = Math.max(0, tDepth - 1);
      else if (ch === "=" && tDepth === 0) {
        const prev = i > 0 ? tailStmt[i - 1] : "";
        const next = i + 1 < tailStmt.length ? tailStmt[i + 1] : "";
        if (prev !== "=" && prev !== "!" && prev !== "<" && prev !== ">" && next !== "=" && next !== ">") {
          tailHasAssign = true;
          break;
        }
      }
    }
  }
  return { code: String(rawCode || ""), trimmed, hasTopReturn, lastSemi, tailHasAssign };
}

function buildEvalCandidates(rawCode) {
  const info = scanJsTopLevel(rawCode);
  if (!info.trimmed || info.hasTopReturn) return [info.code];
  const out = [`return (\n${info.trimmed}\n)`];
  if (info.lastSemi >= 0) {
    const prefix = info.trimmed.slice(0, info.lastSemi + 1);
    const lastStmt = info.trimmed.slice(info.lastSemi + 1).trim();
    if (lastStmt && !NON_EXPR_STMT_RE.test(lastStmt) && !info.tailHasAssign) {
      out.push(`${prefix}\nreturn (\n${lastStmt}\n);`);
    }
  }
  out.push(info.code);
  return out;
}

function withTimeout(promise, ms, message) {
  return new Promise((resolve, reject) => {
    let settled = false;
    const timer = setTimeout(() => {
      if (!settled) {
        settled = true;
        reject(new Error(message));
      }
    }, ms);
    if (timer && typeof timer.unref === "function") timer.unref();
    Promise.resolve(promise).then(
      (val) => {
        if (!settled) {
          settled = true;
          clearTimeout(timer);
          resolve(val);
        }
      },
      (err) => {
        if (!settled) {
          settled = true;
          clearTimeout(timer);
          reject(err);
        }
      }
    );
  });
}

function isDiscardedTab(t) {
  return !t || t.discarded === true || t.status === "unloaded";
}

function formatExtensionInfo(ext) {
  return {
    id: String((ext && ext.id) || ""),
    name: String((ext && ext.name) || ""),
    enabled: Boolean(ext && ext.enabled),
    type: String((ext && ext.type) || ""),
    version: String((ext && ext.version) || "")
  };
}

async function getLiveTabOrThrow(tabId) {
  if (!chrome.tabs || typeof chrome.tabs.get !== "function") {
    return null;
  }
  let curTab = null;
  try {
    curTab = await chrome.tabs.get(tabId);
  } catch (_) {
    curTab = null;
  }
  if (!curTab) {
    attachedDebuggers.delete(tabId);
    debuggerLastUsedAt.delete(tabId);
    verifiedRendererTabs.delete(tabId);
    initializedRuntimeTabs.delete(tabId);
    throw new Error(`Tab ${tabId} was closed or does not exist; call cua.listTabs() to see active tabs`);
  }
  const curUrl = String(curTab.url || curTab.pendingUrl || "");
  if (isDiscardedTab(curTab)) {
    attachedDebuggers.delete(tabId);
    debuggerLastUsedAt.delete(tabId);
    verifiedRendererTabs.delete(tabId);
    initializedRuntimeTabs.delete(tabId);
    throw new Error(
      `Tab ${tabId} (${curUrl || "about:blank"}) is discarded/unloaded by Chrome; reload it via tab.goto(${JSON.stringify(curUrl || "about:blank")}) or open a new tab`
    );
  }
  return curTab;
}

async function ensureResponsiveTabDebugger(tabId, curTabUrl = "", timeoutMs = 3000) {
  const lastVerified = verifiedRendererTabs.get(tabId) || 0;
  if (attachedDebuggers.has(tabId) && (Date.now() - lastVerified) < 5000) return;
  try {
    await withTimeout(
      (async () => {
        await ensureDebuggerAndFocusEmulation(tabId);
        await evalInTab(tabId, "1", false);
      })(),
      timeoutMs,
      `Tab ${tabId} (${curTabUrl || "about:blank"}) is unresponsive (debugger/renderer timed out after ${timeoutMs}ms); reload or reopen the tab`
    );
    verifiedRendererTabs.set(tabId, Date.now());
  } catch (err) {
    if (isNavigationError(err)) return;
    attachedDebuggers.delete(tabId);
    debuggerLastUsedAt.delete(tabId);
    verifiedRendererTabs.delete(tabId);
    initializedRuntimeTabs.delete(tabId);
    throw err;
  }
}

async function evalInTab(tabId, expression, awaitPromise = true) {
  const res = await sendDebuggerCommand(tabId, "Runtime.evaluate", {
    expression,
    awaitPromise,
    returnByValue: true
  });
  verifiedRendererTabs.set(tabId, Date.now());
  if (res && res.exceptionDetails) {
    const exc = res.exceptionDetails.exception;
    const desc = (res.result && res.result.description) || (exc && exc.description) || res.exceptionDetails.text || "Runtime.evaluate error";
    throw new Error(desc);
  }
  return res && res.result ? res.result.value : undefined;
}

async function evalStepCodeOnTab(tabId, rawCode) {
  const candidates = buildEvalCandidates(rawCode);
  let lastErr = null;
  for (let i = 0; i < candidates.length; i++) {
    const body = candidates[i];
    const expr = `(async () => {\n  const __fmuRun = async () => {\n${body}\n  };\n  const __val = await __fmuRun();\n  if (__val === undefined) return { __fmuEvalBox: true, hasValue: false, text: "", raw: null };\n  if (typeof __val === "string") return { __fmuEvalBox: true, hasValue: true, text: __val, raw: __val };\n  if (__val === null || typeof __val === "number" || typeof __val === "boolean") return { __fmuEvalBox: true, hasValue: true, text: String(__val), raw: __val };\n  if (typeof __val === "bigint") return { __fmuEvalBox: true, hasValue: true, text: String(__val), raw: String(__val) };\n  try {\n    const __seen = new WeakSet();\n    const __s = JSON.stringify(__val, (_k, v) => {\n      if (typeof v === "bigint") return String(v);\n      if (v && typeof v === "object") { if (__seen.has(v)) return "[Circular]"; __seen.add(v); }\n      return v;\n    });\n    if (__s !== undefined) return { __fmuEvalBox: true, hasValue: true, text: __s, raw: JSON.parse(__s) };\n  } catch (_) {}\n  const __fallback = String(__val);\n  return { __fmuEvalBox: true, hasValue: true, text: __fallback, raw: __fallback };\n})()`;
    try {
      const val = await evalInTab(tabId, expr, true);
      if (val && typeof val === "object" && val.__fmuEvalBox === true) {
        return val;
      }
      if (val === undefined) {
        return { __fmuEvalBox: true, hasValue: false, text: "", raw: null };
      }
      const txt = typeof val === "string" ? val : JSON.stringify(val);
      return { __fmuEvalBox: true, hasValue: true, text: txt, raw: val };
    } catch (err) {
      lastErr = err;
      const msg = String((err && err.message) || err || "");
      if (i + 1 < candidates.length && msg.includes("SyntaxError")) {
        continue;
      }
      throw err;
    }
  }
  if (lastErr) throw lastErr;
  return { __fmuEvalBox: true, hasValue: false, text: "", raw: null };
}

async function injectInlineFilesOnTab(tabId, candidateSelectors, inlineFiles) {
  const expr = `((sels, items) => {
    let el = null;
    for (const sel of sels) {
      try {
        el = document.querySelector(sel);
        if (el) break;
      } catch (_) {}
    }
    if (!el) return { found: false, fileCount: 0 };
    const dt = new DataTransfer();
    for (const item of items) {
      const bin = atob(String(item.dataB64 || ""));
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      dt.items.add(new File([bytes], String(item.name || "upload.bin"), {
        type: String(item.type || "application/octet-stream"),
        lastModified: Date.now()
      }));
    }
    el.files = dt.files;
    const count = el.files ? el.files.length : 0;
    if (count > 0) {
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
    }
    return { found: true, fileCount: count };
  })(${JSON.stringify(candidateSelectors)}, ${JSON.stringify(inlineFiles)})`;
  return await evalInTab(tabId, expr, false);
}

async function setInputFilesOnTab(tabId, selector, files, inlineFiles = null, inlineSkipped = "") {
  const fileList = Array.isArray(files) ? files.map(String) : [String(files)];
  const rawSel = selector || "input[type='file']";
  const candidateSelectors = [];
  if (rawSel.startsWith("[")) {
    candidateSelectors.push(`input[type='file']${rawSel}`);
  }
  candidateSelectors.push(rawSel);
  if (rawSel !== "input[type='file']") {
    candidateSelectors.push("input[type='file']");
  }
  const hasInline = Array.isArray(inlineFiles) && inlineFiles.length === fileList.length && inlineFiles.length > 0;

  if (
    fileList.length > 0
    && chrome.extension
    && typeof chrome.extension.isAllowedFileSchemeAccess === "function"
  ) {
    let allowed = true;
    try {
      allowed = await chrome.extension.isAllowedFileSchemeAccess();
    } catch (_) {}
    if (allowed === false) {
      if (hasInline) {
        const inlineRes = await injectInlineFilesOnTab(tabId, candidateSelectors, inlineFiles);
        if (inlineRes && inlineRes.found && Number(inlineRes.fileCount || 0) > 0) {
          return { ok: true, files: fileList, via: "inlineDataTransfer" };
        }
        if (inlineRes && !inlineRes.found) {
          throw new Error(`No <input type="file"> found matching ${rawSel}`);
        }
      }
      throw uploadFileUrlError(inlineSkipped);
    }
  }
  const doc = await sendDebuggerCommand(tabId, "DOM.getDocument", { depth: 0 });
  const rootId = doc && doc.root ? doc.root.nodeId : 1;
  let nodeId = 0;
  let matchedSel = "input[type='file']";
  for (const cand of candidateSelectors) {
    try {
      const q = await sendDebuggerCommand(tabId, "DOM.querySelector", {
        nodeId: rootId,
        selector: cand
      });
      if (q && q.nodeId) {
        nodeId = q.nodeId;
        matchedSel = cand;
        break;
      }
    } catch (_) {}
  }
  if (!nodeId) {
    throw new Error(`No <input type="file"> found matching ${rawSel}`);
  }
  await sendDebuggerCommand(tabId, "DOM.setFileInputFiles", {
    nodeId,
    files: fileList
  });
  const dispatchExpr = `(() => {
    const el = document.querySelector(${JSON.stringify(matchedSel)}) || document.querySelector("input[type='file']");
    if (el) {
      const count = el.files ? el.files.length : 0;
      if (count > 0 || ${fileList.length === 0 ? "true" : "false"}) {
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
      }
      return { found: true, fileCount: count };
    }
    return { found: false, fileCount: 0 };
  })()`;
  const checkRes = await evalInTab(tabId, dispatchExpr, false);
  if (
    fileList.length > 0
    && checkRes !== true
    && (!checkRes || typeof checkRes !== "object" || Number(checkRes.fileCount || 0) === 0)
  ) {
    if (hasInline) {
      const inlineRes = await injectInlineFilesOnTab(tabId, [matchedSel, ...candidateSelectors], inlineFiles);
      if (inlineRes && inlineRes.found && Number(inlineRes.fileCount || 0) > 0) {
        return { ok: true, files: fileList, via: "inlineDataTransfer" };
      }
    }
    throw uploadFileUrlError(inlineSkipped);
  }
  return { ok: true, files: fileList };
}

async function handleRpc(msg) {
  const { id, method, params = {} } = msg;
  try {
    if (method === "ping") {
      return { id, result: { pong: true, attachedTabs: Array.from(attachedDebuggers) } };
    }
    if (method === "reloadExtension") {
      setTimeout(() => {
        if (chrome.runtime && typeof chrome.runtime.reload === "function") {
          chrome.runtime.reload();
        }
      }, 25);
      return { id, result: { reloading: true } };
    }
    if (method === "createBrowserTab") {
      clearPendingPreview();
      const {
        url = "about:blank",
        sessionName = "Agent",
        sessionId = "default",
        color = "blue",
        window: windowFilter,
        windowId: explicitWindowId,
        chromeTabId: explicitChromeTabId,
        reuseExisting = false,
        reattach = false,
        navigateIfReused = false,
        timeoutMs = 10000,
        owner = "",
        agent = ""
      } = params;

      const foreign = await foreignTabIds(owner);
      const allTabs = ((chrome.tabs && typeof chrome.tabs.query === "function")
        ? (await chrome.tabs.query({})) || []
        : []).filter(t => !foreign.has(t.id));
      const wf = String(windowFilter || "").trim().toLowerCase();
      let filterMatchedWindowId = undefined;

      if (explicitChromeTabId !== undefined && explicitChromeTabId !== null) {
        const targetCid = Number(explicitChromeTabId);
        let directTab = allTabs.find(t => t.id === targetCid) || null;
        if (!directTab && chrome.tabs && typeof chrome.tabs.get === "function") {
          try { directTab = await chrome.tabs.get(targetCid); } catch (_) {}
        }
        if (!directTab || directTab.id === undefined) {
          throw new Error(`No Chrome tab found with tabId=${targetCid}`);
        }
        const prevGroupId = directTab.groupId;
        const groupId = params.assignGroup === false
          ? directTab.groupId
          : await ensureAgentGroup(directTab.id, sessionName, color, directTab.windowId);
        await bindSessionTab(sessionId, directTab.id, owner, prevGroupId, agent);
        if (params.focusEmulation !== false) {
          try { await ensureDebuggerAndFocusEmulation(directTab.id); } catch (_) {}
        }
        if (params.skipPreview !== true) {
          scheduleAsyncPreviewPush(directTab.id);
        }
        return {
          id,
          result: {
            tabId: directTab.id,
            windowId: directTab.windowId,
            groupId,
            sessionName,
            reused: true,
            url: directTab.url || directTab.pendingUrl || "about:blank",
            title: directTab.title || ""
          }
        };
      }

      if (reattach && (!url || url === "about:blank")) {
        let reattachedTab = null;
        if (sessionTabs.has(sessionId)) {
          const savedId = sessionTabs.get(sessionId);
          const cand = allTabs.find(t => t.id === savedId) || null;
          if (cand && !isDiscardedTab(cand)) {
            reattachedTab = cand;
          } else if (!cand && chrome.tabs && typeof chrome.tabs.get === "function") {
            try {
              const fetched = await chrome.tabs.get(savedId);
              if (fetched && !isDiscardedTab(fetched)) reattachedTab = fetched;
            } catch (_) {}
          }
        }
        if (!reattachedTab || String(reattachedTab.url || "") === "about:blank") {
          const { groupId: existingGid } = await findGroupWindowId(sessionName, explicitWindowId);
          if (existingGid !== undefined) {
            const groupTabs = allTabs.filter(t => t.groupId === existingGid && !isDiscardedTab(t));
            const nonBlank = groupTabs.filter(t => {
              const u = String(t.url || t.pendingUrl || "");
              return u && u !== "about:blank" && !u.startsWith("chrome://newtab");
            });
            if (nonBlank.length > 0) {
              reattachedTab = nonBlank[nonBlank.length - 1];
            } else if (!reattachedTab && groupTabs.length > 0) {
              reattachedTab = groupTabs[groupTabs.length - 1];
            }
          }
        }
        if (reattachedTab && reattachedTab.id !== undefined) {
          const prevGroupId = reattachedTab.groupId;
          const groupId = await ensureAgentGroup(reattachedTab.id, sessionName, color, reattachedTab.windowId);
          await bindSessionTab(sessionId, reattachedTab.id, owner, prevGroupId, agent);
          if (params.focusEmulation !== false) {
            try { await ensureDebuggerAndFocusEmulation(reattachedTab.id); } catch (_) {}
          }
          if (params.skipPreview !== true) {
            scheduleAsyncPreviewPush(reattachedTab.id);
          }
          return {
            id,
            result: {
              tabId: reattachedTab.id,
              windowId: reattachedTab.windowId,
              groupId,
              sessionName,
              reused: true,
              reattached: true,
              url: reattachedTab.url || reattachedTab.pendingUrl || "about:blank",
              title: reattachedTab.title || ""
            }
          };
        }
      }

      if (wf) {
        const wfTabs = allTabs.filter(t => {
          if (explicitWindowId !== undefined && t.windowId !== explicitWindowId) return false;
          const tUrl = String(t.url || t.pendingUrl || "").toLowerCase();
          const tTitle = String(t.title || "").toLowerCase();
          return tTitle.includes(wf) || tUrl.includes(wf);
        });
        if (wfTabs.length > 0) {
          filterMatchedWindowId = wfTabs[0].windowId;
          if (reuseExisting) {
            const liveWfTabs = wfTabs.filter(t => !isDiscardedTab(t));
            const urlStr = String(url || "").trim();
            const urlLower = urlStr.toLowerCase();
            const exactUrlMatch = (urlStr && urlStr !== "about:blank")
              ? liveWfTabs.find(t => {
                  const tu = String(t.url || t.pendingUrl || "");
                  return tu.startsWith(urlStr) || tu.toLowerCase().includes(urlLower);
                })
              : null;
            const match = exactUrlMatch || liveWfTabs[0];
            if (match && match.id !== undefined) {
              const prevGroupId = match.groupId;
              const groupId = await ensureAgentGroup(match.id, sessionName, color, match.windowId);
              await bindSessionTab(sessionId, match.id, owner, prevGroupId, agent);
              const curMatchUrl = String(match.url || match.pendingUrl || "");
              const alreadyMatchesUrl = !urlStr
                || urlStr === "about:blank"
                || !hasUrlScheme(urlStr)
                || curMatchUrl === urlStr
                || curMatchUrl.startsWith(urlStr)
                || curMatchUrl.toLowerCase().includes(urlLower)
                || (getHostname(curMatchUrl) && getHostname(curMatchUrl) === getHostname(urlStr) && !navigateIfReused);
              let finalReusedUrl = curMatchUrl || urlStr;
              let finalReusedTitle = match.title || "";
              if (navigateIfReused || (!alreadyMatchesUrl && hasUrlScheme(urlStr))) {
                const targetNavUrl = normalizeNavUrl(urlStr);
                initializedRuntimeTabs.delete(match.id);
                await chrome.tabs.update(match.id, { url: targetNavUrl, active: false });
                await waitForTabLoad(match.id, timeoutMs, targetNavUrl);
                finalReusedUrl = targetNavUrl;
                if (chrome.tabs && typeof chrome.tabs.get === "function") {
                  try {
                    const rt = await chrome.tabs.get(match.id);
                    if (rt) {
                      finalReusedUrl = rt.url || rt.pendingUrl || finalReusedUrl;
                      finalReusedTitle = rt.title || finalReusedTitle;
                    }
                  } catch (_) {}
                }
              }
              if (params.focusEmulation !== false) {
                try { await ensureDebuggerAndFocusEmulation(match.id); } catch (_) {}
              }
              if (params.skipPreview !== true) {
                scheduleAsyncPreviewPush(match.id);
              }
              return {
                id,
                result: {
                  tabId: match.id,
                  windowId: match.windowId,
                  groupId,
                  sessionName,
                  reused: true,
                  url: finalReusedUrl,
                  title: finalReusedTitle
                }
              };
            }
          }
        }
      } else if (reuseExisting && url && url !== "about:blank") {
        const urlLower = String(url).toLowerCase();
        const wantHost = getHostname(url);
        const match = allTabs.find(t => {
          if (isDiscardedTab(t)) return false;
          if (explicitWindowId !== undefined && t.windowId !== explicitWindowId) return false;
          const tUrl = String(t.url || t.pendingUrl || "");
          if (tUrl.startsWith(url) || tUrl.toLowerCase().includes(urlLower)) return true;
          if (!hasUrlScheme(url) && wantHost && getHostname(tUrl) === wantHost) return true;
          return false;
        });
        if (match && match.id !== undefined) {
          const prevGroupId = match.groupId;
          const groupId = await ensureAgentGroup(match.id, sessionName, color, match.windowId);
          await bindSessionTab(sessionId, match.id, owner, prevGroupId, agent);
          if (params.focusEmulation !== false) {
            try { await ensureDebuggerAndFocusEmulation(match.id); } catch (_) {}
          }
          if (params.skipPreview !== true) {
            scheduleAsyncPreviewPush(match.id);
          }
          return {
            id,
            result: {
              tabId: match.id,
              windowId: match.windowId,
              groupId,
              sessionName,
              reused: true,
              url: match.url || match.pendingUrl || normalizeNavUrl(url),
              title: match.title || ""
            }
          };
        }
      } else if (reuseExisting && (!url || url === "about:blank")) {
        const { groupId: existingGid } = await findGroupWindowId(sessionName, explicitWindowId);
        if (existingGid !== undefined) {
          const groupTabs = allTabs.filter(t => t.groupId === existingGid && !isDiscardedTab(t));
          const nonBlank = groupTabs.filter(t => {
            const u = String(t.url || t.pendingUrl || "");
            return u && u !== "about:blank" && !u.startsWith("chrome://newtab");
          });
          const match = nonBlank.length > 0
            ? nonBlank[nonBlank.length - 1]
            : (groupTabs.length > 0 ? groupTabs[groupTabs.length - 1] : null);
          if (match && match.id !== undefined) {
            const prevGroupId = match.groupId;
            const groupId = await ensureAgentGroup(match.id, sessionName, color, match.windowId);
            await bindSessionTab(sessionId, match.id, owner, prevGroupId, agent);
            if (params.focusEmulation !== false) {
              try { await ensureDebuggerAndFocusEmulation(match.id); } catch (_) {}
            }
            if (params.skipPreview !== true) {
              scheduleAsyncPreviewPush(match.id);
            }
            return {
              id,
              result: {
                tabId: match.id,
                windowId: match.windowId,
                groupId,
                sessionName,
                reused: true,
                url: match.url || match.pendingUrl || "about:blank",
                title: match.title || ""
              }
            };
          }
        }
      }

      let targetWindowId = explicitWindowId !== undefined ? explicitWindowId : filterMatchedWindowId;
      if (targetWindowId === undefined) {
        const { windowId: groupWinId } = await findGroupWindowId(sessionName);
        if (groupWinId !== undefined) {
          targetWindowId = groupWinId;
        }
      }
      if (targetWindowId === undefined && url && url !== "about:blank") {
        const wantHost = getHostname(url);
        if (wantHost) {
          const sameHostTab = allTabs.find(t => getHostname(t.url || t.pendingUrl) === wantHost);
          if (sameHostTab && sameHostTab.windowId !== undefined) {
            targetWindowId = sameHostTab.windowId;
          }
        }
      }
      if (targetWindowId === undefined && chrome.windows && typeof chrome.windows.getAll === "function") {
        try {
          const wins = await chrome.windows.getAll({ windowTypes: ["normal"] });
          if (Array.isArray(wins) && wins.length > 0) {
            const bgWin = wins.find(w => !w.focused) || wins[0];
            if (bgWin && bgWin.id !== undefined) {
              targetWindowId = bgWin.id;
            }
          }
        } catch (_) {}
      }

      const navUrl = (url && url !== "about:blank" && !/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(String(url)))
        ? `https://${url}`
        : url;
      const createOpts = { url: navUrl, active: false };
      if (targetWindowId !== undefined) {
        createOpts.windowId = targetWindowId;
      }
      let tab;
      try {
        tab = await chrome.tabs.create(createOpts);
      } catch (err) {
        if (String(err && err.message || err).includes("No current window") && chrome.windows && typeof chrome.windows.create === "function") {
          const win = await chrome.windows.create({ url: navUrl, focused: false });
          tab = (win && Array.isArray(win.tabs) && win.tabs[0])
            ? win.tabs[0]
            : await chrome.tabs.create({ url: navUrl, active: false, windowId: win.id });
        } else {
          throw err;
        }
      }
      await claimTab(tab.id, owner, true, agent);
      const effectiveWinId = tab.windowId !== undefined ? tab.windowId : targetWindowId;
      const groupId = await ensureAgentGroup(tab.id, sessionName, color, effectiveWinId);
      await bindSessionTab(sessionId, tab.id, owner, undefined, agent);
      if (params.focusEmulation !== false && params.skipPreview !== true) {
        startLiveScreencast(tab.id);
      }
      if (navUrl && navUrl !== "about:blank") {
        await waitForTabLoad(tab.id, timeoutMs, navUrl);
      }
      if (params.focusEmulation !== false) {
        try { await ensureDebuggerAndFocusEmulation(tab.id); } catch (_) {}
      }
      let finalUrl = tab.pendingUrl || tab.url || url;
      let finalTitle = tab.title || "";
      if (chrome.tabs && typeof chrome.tabs.get === "function") {
        try {
          const refreshedTab = await chrome.tabs.get(tab.id);
          if (refreshedTab) {
            finalUrl = refreshedTab.url || refreshedTab.pendingUrl || finalUrl;
            finalTitle = refreshedTab.title || finalTitle;
          }
        } catch (_) {}
      }
      stopLiveScreencast(tab.id);
      if (params.skipPreview !== true) {
        scheduleAsyncPreviewPush(tab.id);
      }
      return {
        id,
        result: {
          tabId: tab.id,
          windowId: effectiveWinId,
          groupId,
          sessionName,
          url: finalUrl,
          title: finalTitle
        }
      };
    }
    if (method === "navigate" || method === "goto") {
      clearPendingPreview();
      const { tabId, url, timeoutMs = 10000, skipPreview = false } = params;
      const navUrl = normalizeNavUrl(url);
      initializedRuntimeTabs.delete(tabId);
      if (!skipPreview) {
        startLiveScreencast(tabId);
      }
      await chrome.tabs.update(tabId, { url: navUrl, active: false });
      if (navUrl && navUrl !== "about:blank") {
        await waitForTabLoad(tabId, timeoutMs, navUrl);
      }
      await ensureDebuggerAndFocusEmulation(tabId);
      let finalUrl = navUrl;
      let tabTitle = "";
      if (chrome.tabs && typeof chrome.tabs.get === "function") {
        try {
          const t = await chrome.tabs.get(tabId);
          if (t) {
            if (t.url || t.pendingUrl) finalUrl = t.url || t.pendingUrl;
            if (t.title) tabTitle = t.title;
          }
        } catch (_) {}
      }
      stopLiveScreencast(tabId);
      if (!skipPreview) {
        scheduleAsyncPreviewPush(tabId);
      }
      return { id, result: { tabId, url: finalUrl, title: tabTitle } };
    }
    if (method === "listTabs") {
      const {
        query: rawQuery = "",
        urlPattern = "",
        window: windowFilter = "",
        windowId: explicitWindowId,
        sessionName = "",
        includeInternal = false
      } = params;
      const allTabs = (chrome.tabs && typeof chrome.tabs.query === "function")
        ? (await chrome.tabs.query({})) || []
        : [];
      const groupTitleById = new Map();
      if (chrome.tabGroups && typeof chrome.tabGroups.query === "function") {
        try {
          const groups = (await chrome.tabGroups.query({})) || [];
          for (const g of groups) {
            if (g && g.id !== undefined) groupTitleById.set(g.id, String(g.title || ""));
          }
        } catch (_) {}
      }
      const q = String(rawQuery || urlPattern || windowFilter || "").trim().toLowerCase();
      const sn = String(sessionName || "").trim();
      const outTabs = [];
      for (const t of allTabs) {
        if (!t || t.id === undefined) continue;
        if (explicitWindowId !== undefined && t.windowId !== explicitWindowId) continue;
        const u = String(t.url || t.pendingUrl || "");
        const title = String(t.title || "");
        if (!includeInternal && (u.startsWith("chrome://") || u.startsWith("devtools://") || u.startsWith("chrome-extension://"))) {
          continue;
        }
        const gTitle = (t.groupId !== undefined && t.groupId >= 0) ? (groupTitleById.get(t.groupId) || "") : "";
        if (sn && gTitle !== sn) continue;
        if (q && !u.toLowerCase().includes(q) && !title.toLowerCase().includes(q) && !gTitle.toLowerCase().includes(q) && String(t.id) !== q) {
          continue;
        }
        outTabs.push({
          tabId: t.id,
          windowId: t.windowId,
          groupId: t.groupId !== undefined ? t.groupId : -1,
          groupTitle: gTitle,
          active: Boolean(t.active),
          discarded: Boolean(t.discarded),
          status: String(t.status || ""),
          url: u,
          title
        });
      }
      return { id, result: { tabs: outTabs } };
    }
    if (method === "getTab") {
      const {
        sessionId = "default",
        urlPattern,
        query: queryFilter,
        window: windowFilter,
        windowId: explicitWindowId,
        sessionName,
        tabId,
        chromeTabId,
        allowMissing = false,
        owner = "",
        agent = ""
      } = params;
      const foreign = await foreignTabIds(owner);
      const allTabs = ((chrome.tabs && typeof chrome.tabs.query === "function")
        ? (await chrome.tabs.query({})) || []
        : []).filter(t => !foreign.has(t.id));
      const explicitId = chromeTabId !== undefined ? Number(chromeTabId) : (tabId !== undefined ? Number(tabId) : undefined);
      let match = null;
      if (explicitId !== undefined && !Number.isNaN(explicitId)) {
        match = allTabs.find(t => t.id === explicitId) || null;
        if (!match && chrome.tabs && typeof chrome.tabs.get === "function") {
          try { match = await chrome.tabs.get(explicitId); } catch (_) {}
        }
      }
      const pat = String(urlPattern || queryFilter || windowFilter || "").trim().toLowerCase();
      if (!match && !pat && explicitWindowId === undefined && !sessionName && sessionTabs.has(sessionId)) {
        const savedId = sessionTabs.get(sessionId);
        const cand = allTabs.find(t => t.id === savedId) || null;
        if (cand && String(cand.url || cand.pendingUrl || "") !== "about:blank") {
          match = cand;
        }
      }
      if (!match && sessionName) {
        const { groupId: gid } = await findGroupWindowId(sessionName, explicitWindowId);
        if (gid !== undefined) {
          const gTabs = allTabs.filter(t => t.groupId === gid && (explicitWindowId === undefined || t.windowId === explicitWindowId));
          const filtered = pat
            ? gTabs.filter(t => String(t.url || t.pendingUrl || "").toLowerCase().includes(pat) || String(t.title || "").toLowerCase().includes(pat))
            : gTabs.filter(t => {
                const u = String(t.url || t.pendingUrl || "");
                return u && u !== "about:blank" && !u.startsWith("chrome://newtab");
              });
          if (filtered.length > 0) {
            const liveFiltered = filtered.filter(t => !isDiscardedTab(t));
            const pool = liveFiltered.length > 0 ? liveFiltered : filtered;
            match = pool[pool.length - 1];
          }
        }
      }
      if (!match) {
        const candidates = allTabs.filter(t => {
          if (!t || t.id === undefined) return false;
          if (explicitWindowId !== undefined && t.windowId !== explicitWindowId) return false;
          const u = String(t.url || t.pendingUrl || "");
          if (u.startsWith("chrome://") || u.startsWith("devtools://") || u.startsWith("chrome-extension://") || u.startsWith("chrome-error://")) {
            return false;
          }
          if (!pat) return u !== "" && u !== "about:blank";
          return u.toLowerCase().includes(pat) || String(t.title || "").toLowerCase().includes(pat);
        });
        if (candidates.length > 0) {
          const liveCandidates = candidates.filter(t => !isDiscardedTab(t));
          const pool = liveCandidates.length > 0 ? liveCandidates : candidates;
          match = pool.find(t => t.active) || pool[pool.length - 1];
        }
      }
      if (!match) {
        if (allowMissing) return { id, result: { found: false } };
        throw new Error(`No matching Chrome tab found for '${urlPattern || queryFilter || windowFilter || sessionName || sessionId}'`);
      }
      await bindSessionTab(sessionId, match.id, owner, match.groupId, agent);
      if (params.assignGroup === true && sessionName) {
        try { await ensureAgentGroup(match.id, sessionName, params.color || "blue", match.windowId); } catch (_) {}
      }
      const isDiscardedMatch = isDiscardedTab(match);
      if (params.focusEmulation !== false && !isDiscardedMatch) {
        try {
          await withTimeout(
            ensureDebuggerAndFocusEmulation(match.id),
            3000,
            `Tab ${match.id} debugger attach timed out`
          );
        } catch (_) {}
      }
      return {
        id,
        result: {
          found: true,
          reused: true,
          tabId: match.id,
          windowId: match.windowId,
          groupId: match.groupId,
          discarded: isDiscardedMatch,
          url: match.url || match.pendingUrl || "",
          title: match.title || ""
        }
      };
    }
    if (method === "getTabInfo") {
      const tid = Number(params && (params.tabId !== undefined ? params.tabId : params.chromeTabId));
      if (!tid || Number.isNaN(tid)) {
        throw new Error("getTabInfo requires numeric tabId");
      }
      if (!chrome.tabs || typeof chrome.tabs.get !== "function") {
        return { id, result: { found: false } };
      }
      try {
        const t = await chrome.tabs.get(tid);
        if (!t) return { id, result: { found: false } };
        const committedUrl = String(t.url || "");
        const liveUrl = (committedUrl && committedUrl !== "about:blank")
          ? committedUrl
          : String(t.pendingUrl || committedUrl || "");
        return {
          id,
          result: {
            found: true,
            tabId: t.id,
            windowId: t.windowId,
            groupId: t.groupId,
            status: String(t.status || ""),
            discarded: isDiscardedTab(t),
            url: liveUrl,
            title: String(t.title || "")
          }
        };
      } catch (_) {
        return { id, result: { found: false } };
      }
    }
    if (method === "setInputFiles") {
      const { tabId, selector = "input[type='file']", files = [], inlineFiles = null, inlineSkipped = "" } = params;
      const res = await setInputFilesOnTab(tabId, selector, files, inlineFiles, inlineSkipped);
      return { id, result: res };
    }
    if (method === "evaluate") {
      const { tabId, expression, arg, runtimeInit, awaitPromise = true } = params;
      const curTab = await getLiveTabOrThrow(tabId);
      const curTabUrl = curTab ? String(curTab.url || curTab.pendingUrl || "") : "";
      await ensureResponsiveTabDebugger(tabId, curTabUrl, 3000);
      const wrappedExpr = runtimeInit
        ? expression
        : `((__fn, __arg) => (typeof __fn === "function" ? __fn(__arg) : __fn))((${expression}), ${JSON.stringify(arg === undefined ? null : arg)})`;
      let val;
      try {
        val = await evalInTab(tabId, wrappedExpr, awaitPromise);
      } catch (evalErr) {
        if (!isNavigationError(evalErr)) throw evalErr;
        const navSt = await waitForNavigationSettlement(tabId, "", 10000);
        if (!navSt.alive) throw evalErr;
        val = await evalInTab(tabId, wrappedExpr, awaitPromise);
      }
      return { id, result: val };
    }
    if (method === "executeBatch") {
      clearPendingPreview();
      const { tabId, runtimeJs, runtimeVersion, batchReq, ...restParams } = params;
      const reqPayload = batchReq || restParams || {};
      if (runtimeJs) {
        cachedRuntimeJs = runtimeJs;
      }
      if (runtimeVersion) {
        cachedRuntimeVersion = String(runtimeVersion);
      }
      const effectiveRuntimeJs = runtimeJs || cachedRuntimeJs;
      const effectiveRuntimeVer = String(runtimeVersion || cachedRuntimeVersion || "init");
      stopLiveScreencast(tabId);

      let navWaitMs = 0;
      let curTabUrl = "";
      const curTab = await getLiveTabOrThrow(tabId);
      if (curTab) {
        curTabUrl = String(curTab.url || curTab.pendingUrl || "");
        if (curTab.status === "loading" || curTab.pendingUrl) {
          initializedRuntimeTabs.delete(tabId);
          const tNav0 = Date.now();
          await waitForTabLoad(tabId, 6000);
          navWaitMs += (Date.now() - tNav0);
          const postLoadTab = await getLiveTabOrThrow(tabId);
          if (postLoadTab) {
            curTabUrl = String(postLoadTab.url || postLoadTab.pendingUrl || curTabUrl);
          }
        }
      }
      await ensureResponsiveTabDebugger(tabId, curTabUrl, 3000);

      const injectAndRunBatch = async (payloadObj, forceReinit = false) => {
        const needsInit = forceReinit || initializedRuntimeTabs.get(tabId) !== effectiveRuntimeVer;
        if (needsInit && effectiveRuntimeJs) {
          try {
            await withTimeout(
              evalInTab(tabId, effectiveRuntimeJs, false),
              4000,
              `Tab ${tabId} (${curTabUrl || "about:blank"}) timed out during runtime injection after 4000ms; reload or reopen the tab`
            );
            initializedRuntimeTabs.set(tabId, effectiveRuntimeVer);
          } catch (rtErr) {
            if (!isNavigationError(rtErr)) throw rtErr;
            const tNav0 = Date.now();
            const navSt = await waitForNavigationSettlement(tabId, "", 8000);
            navWaitMs += (Date.now() - tNav0);
            if (!navSt.alive) throw rtErr;
            await withTimeout(
              evalInTab(tabId, effectiveRuntimeJs, false),
              4000,
              `Tab ${tabId} (${curTabUrl || "about:blank"}) timed out during runtime injection after 4000ms; reload or reopen the tab`
            );
            initializedRuntimeTabs.set(tabId, effectiveRuntimeVer);
          }
        }
        const batchExpr = `window.__fmu.executeBatch(${JSON.stringify(payloadObj || {})})`;
        try {
          return await evalInTab(tabId, batchExpr, true);
        } catch (runErr) {
          const msg = String((runErr && runErr.message) || runErr || "");
          if (
            effectiveRuntimeJs
            && (msg.includes("window.__fmu") || (msg.includes("executeBatch") && (msg.includes("undefined") || msg.includes("null"))))
          ) {
            initializedRuntimeTabs.delete(tabId);
            await evalInTab(tabId, effectiveRuntimeJs, false);
            initializedRuntimeTabs.set(tabId, effectiveRuntimeVer);
            return await evalInTab(tabId, batchExpr, true);
          }
          throw runErr;
        }
      };

      const isPostNavSafeOp = (op) => (
        op === "wait" || op === "sleep" || op === "delay" || op === "settle"
        || op === "waitfor" || op === "wait_for" || op === "waituntil"
        || op === "eval" || op === "evaluate"
        || op === "text" || op === "readtext" || op === "readmain"
        || op === "snapshot" || op === "diff"
      );

      const origSteps = Array.isArray(reqPayload.steps) ? reqPayload.steps : [];
      const tabHost = getHostname(curTabUrl);
      if (tabHost) await loadStrictCspHosts();
      const isPureEvalOnKnownCspHost = (
        tabHost
        && strictCspHosts.has(tabHost)
        && origSteps.length > 0
        && origSteps.every(stp => {
          const op = String((stp && (stp.op || stp.action)) || "").trim().toLowerCase();
          return op === "eval" || op === "evaluate";
        })
      );

      let batchRes;
      if (isPureEvalOnKnownCspHost) {
        const preEvals = [];
        const preRaws = [];
        const preErrors = [];
        for (let idx = 0; idx < origSteps.length; idx++) {
          const stp = origSteps[idx] || {};
          const code = String(stp.code ?? stp.value ?? stp.text ?? "");
          try {
            const evalBox = await evalStepCodeOnTab(tabId, code);
            if (evalBox && evalBox.hasValue) preEvals.push(evalBox.text);
            if (reqPayload && reqPayload.returnRawEval) {
              preRaws.push(evalBox && evalBox.hasValue ? evalBox.raw : null);
            }
          } catch (evalErr) {
            preErrors.push(`step ${idx + 1} (eval): ${(evalErr && evalErr.message) || evalErr}`);
          }
        }
        if (reqPayload && reqPayload.skipSnapshot) {
          batchRes = {
            url: curTabUrl,
            title: "",
            errors: preErrors,
            evalOutputs: preEvals,
            rawEvalValues: preRaws,
            escalations: [{ reason: "eval_csp_cached", handled: true }],
            canvases: [],
            timings: { execMs: 0, settleMs: 0, snapMs: 0, totalInPageMs: 0 },
            lines: []
          };
        } else {
          batchRes = await injectAndRunBatch({ ...reqPayload, steps: [], hadPreSteps: true });
          if (batchRes && typeof batchRes === "object") {
            batchRes.errors = preErrors.concat(Array.isArray(batchRes.errors) ? batchRes.errors : []);
            batchRes.evalOutputs = preEvals.concat(Array.isArray(batchRes.evalOutputs) ? batchRes.evalOutputs : []);
            batchRes.rawEvalValues = preRaws.concat(Array.isArray(batchRes.rawEvalValues) ? batchRes.rawEvalValues : []);
            batchRes.escalations = [{ reason: "eval_csp_cached", handled: true }].concat(
              Array.isArray(batchRes.escalations) ? batchRes.escalations : []
            );
          }
        }
      } else {
        const canCdpClick = Boolean(chrome.debugger && typeof chrome.debugger.sendCommand === "function");
        let curSteps = origSteps;
        let stepOffset = Number(reqPayload.stepOffset || 0);
        let hadPreSteps = Boolean(reqPayload.hadPreSteps);
        let curDiff = reqPayload.diff;
        let forceReinit = false;
        let didNavigate = false;
        const accErrors = [];
        const accEvals = [];
        const accRawVals = [];
        const accEscalations = [];
        let accExecMs = 0;
        let lastCursor = null;
        const maxSlices = origSteps.length + 3;

        for (let sliceIdx = 0; sliceIdx < maxSlices; sliceIdx++) {
          const sliceReq = {
            ...reqPayload,
            steps: curSteps,
            stepOffset,
            hadPreSteps,
            cdpClick: canCdpClick
          };
          if (curDiff !== undefined) sliceReq.diff = curDiff;

          try {
            batchRes = await injectAndRunBatch(sliceReq, forceReinit);
            forceReinit = false;
          } catch (batchErr) {
            if (!isNavigationError(batchErr)) throw batchErr;
            initializedRuntimeTabs.delete(tabId);
            const tNav0 = Date.now();
            const navSt = await waitForNavigationSettlement(tabId, curTabUrl, 8000);
            navWaitMs += (Date.now() - tNav0);
            if (!navSt.alive) throw batchErr;
            curTabUrl = navSt.url || curTabUrl;
            didNavigate = true;
            forceReinit = true;
            const navIdx = curSteps.findIndex(stp => {
              const op = String((stp && (stp.op || stp.action)) || "").trim().toLowerCase();
              return op === "click" || op === "press" || op === "key" || op === "eval" || op === "evaluate" || op === "webmcp" || op === "calltool";
            });
            if (navIdx >= 0) {
              stepOffset += navIdx + 1;
              curSteps = curSteps.slice(navIdx + 1).filter(stp => {
                const op = String((stp && (stp.op || stp.action)) || "").trim().toLowerCase();
                return isPostNavSafeOp(op);
              });
            } else {
              stepOffset += curSteps.length;
              curSteps = [];
            }
            hadPreSteps = true;
            if (!reqPayload.skipSnapshot) curDiff = false;
            if (reqPayload.skipSnapshot && curSteps.length === 0) {
              batchRes = {
                didNavigate: true,
                url: navSt.url || "",
                title: navSt.title || "",
                errors: accErrors,
                evalOutputs: accEvals,
                rawEvalValues: accRawVals,
                escalations: accEscalations,
                cursor: lastCursor,
                canvases: [],
                timings: { execMs: Math.round(accExecMs * 100) / 100 },
                lines: []
              };
              break;
            }
            continue;
          }

          if (batchRes && typeof batchRes === "object") {
            if (Array.isArray(batchRes.errors)) accErrors.push(...batchRes.errors);
            if (Array.isArray(batchRes.evalOutputs)) accEvals.push(...batchRes.evalOutputs);
            if (Array.isArray(batchRes.rawEvalValues)) accRawVals.push(...batchRes.rawEvalValues);
            if (Array.isArray(batchRes.escalations)) accEscalations.push(...batchRes.escalations);
            if (batchRes.timings && typeof batchRes.timings.execMs === "number") {
              accExecMs += batchRes.timings.execMs;
            }
            if (batchRes.cursor) lastCursor = batchRes.cursor;
          }

          const cdpPt = batchRes && batchRes.cdpClick;
          const cdpInp = batchRes && batchRes.cdpInput;
          if ((cdpPt || cdpInp) && canCdpClick) {
            const clickUrl = String(batchRes.url || curTabUrl || "");
            const nextIdx = typeof batchRes.nextStepIndex === "number" ? batchRes.nextStepIndex : curSteps.length;
            stepOffset += nextIdx;
            curSteps = nextIdx < curSteps.length ? curSteps.slice(nextIdx) : [];
            hadPreSteps = true;
            const actionLabel = cdpPt ? "click" : String((cdpInp && cdpInp.kind) || "input");
            try {
              if (cdpPt) {
                await sendDebuggerCommand(tabId, "Input.dispatchMouseEvent", { type: "mouseMoved", x: cdpPt.x, y: cdpPt.y, button: "none" });
                await sendDebuggerCommand(tabId, "Input.dispatchMouseEvent", { type: "mousePressed", x: cdpPt.x, y: cdpPt.y, button: "left", buttons: 1, clickCount: 1 });
                await sendDebuggerCommand(tabId, "Input.dispatchMouseEvent", { type: "mouseReleased", x: cdpPt.x, y: cdpPt.y, button: "left", buttons: 0, clickCount: 1 });
              } else if (cdpInp) {
                await dispatchCdpInput(tabId, cdpInp);
              }
            } catch (clickErr) {
              if (!isNavigationError(clickErr)) {
                accErrors.push(`step ${stepOffset} (${actionLabel}): ${(clickErr && clickErr.message) || clickErr}`);
              }
            }
            await new Promise(r => setTimeout(r, 15));
            if (chrome.tabs && typeof chrome.tabs.get === "function") {
              try {
                const postTab = await chrome.tabs.get(tabId);
                if (postTab) {
                  const postUrl = String(postTab.url || postTab.pendingUrl || "");
                  let samePath = false;
                  if (clickUrl && postUrl) {
                    try {
                      const u1 = new URL(clickUrl);
                      const u2 = new URL(postUrl);
                      samePath = (u1.origin === u2.origin && u1.pathname === u2.pathname);
                    } catch (_) {}
                  }
                  if (postTab.status === "loading" || postTab.pendingUrl || (clickUrl && postUrl && postUrl !== clickUrl && !samePath)) {
                    initializedRuntimeTabs.delete(tabId);
                    const tNav0 = Date.now();
                    const navSt = await waitForNavigationSettlement(tabId, clickUrl, 8000);
                    navWaitMs += (Date.now() - tNav0);
                    curTabUrl = navSt.url || postUrl || curTabUrl;
                    didNavigate = true;
                    forceReinit = true;
                    if (!reqPayload.skipSnapshot) curDiff = false;
                  } else if (postUrl) {
                    curTabUrl = postUrl;
                  }
                }
              } catch (_) {}
            }
            if (reqPayload.skipSnapshot && curSteps.length === 0) {
              batchRes = {
                didNavigate,
                url: curTabUrl || clickUrl,
                title: String(batchRes.title || ""),
                errors: accErrors,
                evalOutputs: accEvals,
                rawEvalValues: accRawVals,
                escalations: accEscalations,
                cursor: lastCursor,
                canvases: [],
                timings: { execMs: Math.round(accExecMs * 100) / 100 },
                lines: []
              };
              break;
            }
            continue;
          }

          if (batchRes && batchRes.navigated) {
            const navFrom = String(batchRes.url || curTabUrl || "");
            const nextIdx = typeof batchRes.nextStepIndex === "number" ? batchRes.nextStepIndex : curSteps.length;
            stepOffset += nextIdx;
            curSteps = nextIdx < curSteps.length ? curSteps.slice(nextIdx) : [];
            hadPreSteps = true;
            initializedRuntimeTabs.delete(tabId);
            const tNav0 = Date.now();
            const navSt = await waitForNavigationSettlement(tabId, navFrom, 8000);
            navWaitMs += (Date.now() - tNav0);
            curTabUrl = navSt.url || navFrom || curTabUrl;
            didNavigate = true;
            forceReinit = true;
            if (!reqPayload.skipSnapshot) curDiff = false;
            if (reqPayload.skipSnapshot && curSteps.length === 0) {
              batchRes = {
                didNavigate: true,
                url: curTabUrl,
                title: navSt.title || batchRes.title || "",
                errors: accErrors,
                evalOutputs: accEvals,
                rawEvalValues: accRawVals,
                escalations: accEscalations,
                cursor: lastCursor,
                canvases: [],
                timings: { ...(batchRes.timings || {}), execMs: Math.round(accExecMs * 100) / 100 },
                lines: []
              };
              break;
            }
            continue;
          }

          if (batchRes && typeof batchRes === "object") {
            if (didNavigate) batchRes.didNavigate = true;
            batchRes.errors = accErrors;
            batchRes.evalOutputs = accEvals;
            batchRes.rawEvalValues = accRawVals;
            batchRes.escalations = accEscalations;
            if (lastCursor && !batchRes.cursor) batchRes.cursor = lastCursor;
            if (batchRes.timings && typeof batchRes.timings === "object") {
              batchRes.timings.execMs = Math.round(accExecMs * 100) / 100;
            }
          }
          break;
        }
      }

      if (batchRes && Array.isArray(batchRes.escalations) && batchRes.escalations.length > 0) {
        const steps = (reqPayload && reqPayload.steps) || [];
        if (!Array.isArray(batchRes.evalOutputs)) batchRes.evalOutputs = [];
        if (!Array.isArray(batchRes.rawEvalValues)) batchRes.rawEvalValues = [];
        if (!Array.isArray(batchRes.errors)) batchRes.errors = [];
        let didMutate = false;
        for (const esc of batchRes.escalations) {
          if (esc.reason === "eval_csp" && !esc.handled) {
            esc.handled = true;
            didMutate = true;
            const escHost = getHostname((batchRes && batchRes.url) || curTabUrl);
            if (escHost) await rememberStrictCspHost(escHost);
            try {
              const evalBox = await evalStepCodeOnTab(tabId, esc.code);
              if (evalBox && evalBox.hasValue) {
                batchRes.evalOutputs.push(evalBox.text);
              }
              if (reqPayload && reqPayload.returnRawEval) {
                batchRes.rawEvalValues.push(evalBox && evalBox.hasValue ? evalBox.raw : null);
              }
            } catch (evalErr) {
              batchRes.errors.push(`step ${esc.step || 1} (eval): ${(evalErr && evalErr.message) || evalErr}`);
            }
          } else if (esc.reason === "file_input" && !esc.handled && !reqPayload.skipSnapshot) {
            const stepObj = steps[(esc.step || 1) - 1] || {};
            const files = stepObj.files || stepObj.value;
            if (files) {
              const sel = esc.uid ? `[data-fmu-upload-uid="${esc.uid}"]` : "input[type='file']";
              esc.handled = true;
              didMutate = true;
              try {
                await setInputFilesOnTab(tabId, sel, files, stepObj.inlineFiles || null, stepObj.inlineSkipped || "");
              } catch (upErr) {
                batchRes.errors.push(String((upErr && upErr.message) || upErr));
              }
            }
          }
        }
        if (didMutate && !reqPayload.skipSnapshot) {
          try {
            const refreshOpts = {
              agent: (reqPayload && reqPayload.agent) || "default",
              query: (reqPayload && reqPayload.query) || "",
              fullTable: Boolean(reqPayload && reqPayload.fullTable),
              useDiff: Boolean(batchRes.useDiff)
            };
            const refreshed = await evalInTab(
              tabId,
              `window.__fmu && window.__fmu.refreshAfterOcr ? window.__fmu.refreshAfterOcr(${JSON.stringify(refreshOpts)}) : null`,
              false
            );
            if (refreshed && typeof refreshed === "object") {
              if (refreshed.crc !== undefined) batchRes.crc = refreshed.crc;
              if (refreshed.seq !== undefined) batchRes.seq = refreshed.seq;
              if (refreshed.count !== undefined) batchRes.count = refreshed.count;
              if (Array.isArray(refreshed.lines)) batchRes.lines = refreshed.lines;
            }
          } catch (_) {}
        }
      }
      if (batchRes && typeof batchRes === "object" && navWaitMs > 0) {
        batchRes.navWaitMs = Math.round(navWaitMs * 100) / 100;
      }
      if (batchRes && typeof batchRes === "object" && (!reqPayload || reqPayload.skipPreview !== true)) {
        scheduleAsyncPreviewPush(tabId);
      }
      return { id, result: batchRes };
    }
    if (method === "capturePreview") {
      const { tabId, maxWidth = 1440 } = params;
      const preview = await captureViewportPreview(tabId, maxWidth);
      return { id, result: preview };
    }
    if (method === "stopScreencast") {
      stopLiveScreencast(params.tabId);
      return { id, result: { stopped: true } };
    }
    if (method === "captureClip") {
      const { tabId, clip } = params;
      const cdpParams = { format: "png" };
      if (clip) cdpParams.clip = { ...clip, scale: clip.scale || 1 };
      const shot = await sendDebuggerCommand(tabId, "Page.captureScreenshot", cdpParams);
      return { id, result: shot };
    }
    if (method === "closeTab") {
      clearPendingPreview();
      const { tabId, sessionId, force = false } = params;
      const targetId = tabId || (sessionId ? sessionTabs.get(sessionId) : undefined);
      if (!targetId) {
        if (sessionId) sessionTabs.delete(sessionId);
        return { id, result: { closed: false } };
      }
      await loadAgentTabClaims();
      const claim = agentTabClaims.get(targetId);
      let tabObj = null;
      if (chrome.tabs && typeof chrome.tabs.get === "function") {
        try { tabObj = await chrome.tabs.get(targetId); } catch (_) {}
      }
      let canClose = Boolean(force) || Boolean(claim && claim.opened);
      if (!canClose && !claim && tabObj) {
        canClose = await isAgentGroupId(tabObj.groupId);
      }
      stopLiveScreencast(targetId);
      if (!canClose) {
        let ungrouped = false;
        if (tabObj && (await isAgentGroupId(tabObj.groupId)) && chrome.tabs && typeof chrome.tabs.ungroup === "function") {
          try {
            await chrome.tabs.ungroup([targetId]);
            ungrouped = true;
          } catch (_) {}
        }
        if (attachedDebuggers.has(targetId) && chrome.debugger && typeof chrome.debugger.detach === "function") {
          try { await chrome.debugger.detach({ tabId: targetId }); } catch (_) {}
        }
        attachedDebuggers.delete(targetId);
        debuggerLastUsedAt.delete(targetId);
        verifiedRendererTabs.delete(targetId);
        initializedRuntimeTabs.delete(targetId);
        await releaseTabClaims([targetId]);
        if (sessionId) sessionTabs.delete(sessionId);
        for (const [sid, tid] of sessionTabs) {
          if (tid === targetId) sessionTabs.delete(sid);
        }
        return { id, result: { closed: false, keptUserTab: true, ungrouped } };
      }
      try { await chrome.tabs.remove(targetId); } catch (_) {}
      attachedDebuggers.delete(targetId);
      debuggerLastUsedAt.delete(targetId);
      verifiedRendererTabs.delete(targetId);
      initializedRuntimeTabs.delete(targetId);
      await releaseTabClaims([targetId]);
      if (sessionId) sessionTabs.delete(sessionId);
      for (const [sid, tid] of sessionTabs) {
        if (tid === targetId) sessionTabs.delete(sid);
      }
      return { id, result: { closed: true } };
    }
    if (method === "closeAgentTabs") {
      clearPendingPreview();
      const { owner = "", groupTitle = "", agent = "", keepTabIds = [], tabIds: explicitTabIds = null } = params;
      await loadAgentTabClaims();
      const keepSet = new Set(
        (Array.isArray(keepTabIds) ? keepTabIds : [])
          .map(v => Number(v))
          .filter(v => !Number.isNaN(v))
      );
      const explicitSet = Array.isArray(explicitTabIds)
        ? new Set(explicitTabIds.map(v => Number(v)).filter(v => !Number.isNaN(v)))
        : null;
      const matchesAgent = (claimAgent) => Boolean(
        agent && claimAgent && (claimAgent === agent || claimAgent.startsWith(`${agent}.`))
      );
      const targetIds = new Set(
        [...agentTabClaims]
          .filter(([, c]) => {
            if (!c.opened) return false;
            if (agent) {
              if (!matchesAgent(c.agent)) return false;
              return !owner || !c.owner || c.owner === owner;
            }
            return !owner || c.owner === owner;
          })
          .map(([tabId]) => tabId)
      );
      const ungroupIds = [];
      let liveTabIds = null;
      if (chrome.tabs && typeof chrome.tabs.query === "function") {
        try {
          const allTabs = (await chrome.tabs.query({})) || [];
          liveTabIds = new Set(allTabs.filter(t => t && t.id !== undefined).map(t => t.id));
          if (agent) {
            for (const t of allTabs) {
              if (!t || t.id === undefined) continue;
              const claim = agentTabClaims.get(t.id);
              if (claim && !claim.opened && matchesAgent(claim.agent) && (!owner || !claim.owner || claim.owner === owner)) {
                if (await isAgentGroupId(t.groupId)) ungroupIds.push(t.id);
              }
            }
          } else if (chrome.tabGroups && typeof chrome.tabGroups.query === "function") {
            const allGroups = (await chrome.tabGroups.query({})) || [];
            const allowSubgroups = Boolean(groupTitle && groupTitle.includes(" · "));
            const matchingGroupIds = new Set();
            for (const g of allGroups) {
              const title = String((g && g.title) || "").trim();
              if (!title) continue;
              const matches = owner
                ? Boolean(groupTitle && (title === groupTitle || (allowSubgroups && title.startsWith(`${groupTitle} · `))))
                : AGENT_GROUP_TITLE_RE.test(title);
              if (matches) matchingGroupIds.add(g.id);
            }
            if (matchingGroupIds.size > 0) {
              for (const t of allTabs) {
                if (!t || t.id === undefined || !matchingGroupIds.has(t.groupId)) continue;
                const claim = agentTabClaims.get(t.id);
                if (!claim) {
                  targetIds.add(t.id);
                } else if (!claim.opened) {
                  if (!owner && !claim.owner) {
                    targetIds.add(t.id);
                  } else if (!owner || claim.owner === owner) {
                    ungroupIds.push(t.id);
                  }
                }
              }
            }
          }
        } catch (_) {}
      }
      if (explicitSet !== null) {
        for (const tid of Array.from(targetIds)) {
          if (!explicitSet.has(tid)) targetIds.delete(tid);
        }
      }
      let keptCount = 0;
      for (const tid of keepSet) {
        if (targetIds.delete(tid)) keptCount += 1;
      }
      const filteredUngroupIds = ungroupIds.filter(tid => !keepSet.has(tid) && (explicitSet === null || explicitSet.has(tid)));
      const tabIds = [...targetIds];
      for (const tid of tabIds) {
        stopLiveScreencast(tid);
        attachedDebuggers.delete(tid);
        debuggerLastUsedAt.delete(tid);
        verifiedRendererTabs.delete(tid);
        initializedRuntimeTabs.delete(tid);
      }
      const removals = await Promise.allSettled(tabIds.map(tid => chrome.tabs.remove(tid)));
      let ungroupedCount = 0;
      if (filteredUngroupIds.length > 0 && chrome.tabs && typeof chrome.tabs.ungroup === "function") {
        try {
          await chrome.tabs.ungroup(filteredUngroupIds);
          ungroupedCount = filteredUngroupIds.length;
        } catch (_) {}
      }
      const pruneIds = [...tabIds];
      if (liveTabIds) {
        for (const tid of agentTabClaims.keys()) {
          if (!liveTabIds.has(tid)) pruneIds.push(tid);
        }
      }
      await releaseTabClaims(pruneIds);
      for (const [sid, tid] of sessionTabs) {
        if (targetIds.has(tid)) sessionTabs.delete(sid);
      }
      if (!owner && !agent && keepSet.size === 0 && explicitSet === null) sessionGroups.clear();
      return {
        id,
        result: {
          tabIds,
          closedCount: removals.filter(r => r.status === "fulfilled").length,
          keptCount,
          ungroupedCount
        }
      };
    }
    if (method === "retireOwner") {
      const { owner, titles = [] } = params;
      await loadAgentTabClaims();
      const owned = [...agentTabClaims].filter(([, c]) => c.owner === owner);
      const blankIds = [];
      const deadIds = [];
      const ungroupIds = [];
      for (const [tid, claim] of owned) {
        try {
          const t = await chrome.tabs.get(tid);
          if (claim.opened) {
            const u = String(t.url || t.pendingUrl || "");
            if (!u || u === "about:blank" || u.startsWith("chrome://newtab")) blankIds.push(tid);
          } else if (await isAgentGroupId(t.groupId)) {
            ungroupIds.push(tid);
          }
        } catch (_) {
          deadIds.push(tid);
        }
      }
      await Promise.allSettled(blankIds.map(tid => chrome.tabs.remove(tid)));
      if (ungroupIds.length > 0 && chrome.tabs && typeof chrome.tabs.ungroup === "function") {
        try {
          await chrome.tabs.ungroup(ungroupIds);
        } catch (_) {}
      }
      const dropIds = new Set([...blankIds, ...deadIds]);
      let changed = false;
      for (const [tid, claim] of owned) {
        if (dropIds.has(tid) || !claim.opened) {
          changed = agentTabClaims.delete(tid) || changed;
        } else {
          agentTabClaims.set(tid, { owner: "", opened: true, agent: claim.agent || "" });
          changed = true;
        }
      }
      if (changed) await saveAgentTabClaims();
      let collapsed = 0;
      for (const title of titles) {
        for (const g of await chrome.tabGroups.query({ title })) {
          try {
            await chrome.tabGroups.update(g.id, { collapsed: true });
            collapsed += 1;
          } catch (_) {}
        }
        sessionGroups.delete(title);
      }
      return { id, result: { closedCount: blankIds.length, collapsed } };
    }
    if (method === "closeTabGroup") {
      const { sessionName = "Agent" } = params;
      const groups = await chrome.tabGroups.query({ title: sessionName });
      let closedCount = 0;
      for (const g of groups) {
        const tabs = await chrome.tabs.query({ groupId: g.id });
        const ids = tabs.map(t => t.id).filter(Boolean);
        if (ids.length) {
          await chrome.tabs.remove(ids);
          closedCount += ids.length;
        }
      }
      sessionGroups.delete(sessionName);
      return { id, result: { closedCount } };
    }
    if (method === "listExtensions") {
      if (!chrome.management || typeof chrome.management.getAll !== "function") {
        throw new Error('chrome.management API unavailable; reload the MV3 extension to grant the "management" permission');
      }
      const items = (await chrome.management.getAll()) || [];
      const selfId = (chrome.runtime && chrome.runtime.id) ? String(chrome.runtime.id) : "";
      return {
        id,
        result: {
          extensions: items.filter(Boolean).map(formatExtensionInfo),
          selfId
        }
      };
    }
    if (method === "setExtensionEnabled") {
      if (!chrome.management || typeof chrome.management.setEnabled !== "function") {
        throw new Error('chrome.management API unavailable; reload the MV3 extension to grant the "management" permission');
      }
      const targetId = String(params.id || params.extensionId || "").trim();
      if (!targetId) {
        throw new Error("setExtensionEnabled requires extension 'id'");
      }
      if (typeof params.enabled !== "boolean") {
        throw new Error("setExtensionEnabled requires boolean 'enabled'");
      }
      const selfId = (chrome.runtime && chrome.runtime.id) ? String(chrome.runtime.id) : "";
      if (!params.enabled && selfId && targetId === selfId) {
        throw new Error(`Refusing to disable the active Faster Mac Use bridge extension (${selfId})`);
      }
      await chrome.management.setEnabled(targetId, params.enabled);
      let updated = null;
      if (typeof chrome.management.get === "function") {
        try {
          updated = await chrome.management.get(targetId);
        } catch (_) {}
      }
      if (!updated && typeof chrome.management.getAll === "function") {
        try {
          const all = (await chrome.management.getAll()) || [];
          updated = all.find(e => e && e.id === targetId) || null;
        } catch (_) {}
      }
      return {
        id,
        result: updated
          ? formatExtensionInfo(updated)
          : { id: targetId, name: String(params.name || ""), enabled: params.enabled, type: "", version: "" }
      };
    }
    if (method === "cdp") {
      const { tabId } = params;
      const cdpMethod = params.cdpMethod || params.method;
      const cdpParams = params.cdpParams || params.commandParams || {};
      const res = await sendDebuggerCommand(tabId, cdpMethod, cdpParams);
      return { id, result: res };
    }
    throw new Error(`Unknown extension RPC method: ${method}`);
  } catch (err) {
    return { id, error: String(err.message || err) };
  }
}

function connectRelay() {
  if (isConnecting || (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING))) {
    return;
  }
  isConnecting = true;
  try {
    ws = new WebSocket(relayUrl);
    ws.onopen = () => {
      isConnecting = false;
      try {
        ws.send(JSON.stringify({ type: "hello", client: "fmu-mv3-extension", version: "0.5.6" }));
      } catch (_) {}
    };
    ws.onmessage = async (ev) => {
      const msg = JSON.parse(ev.data);
      if (!msg || !msg.method) return;
      const resp = await handleRpc(msg);
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(resp));
      }
    };
    ws.onclose = () => {
      isConnecting = false;
      ws = null;
      scheduleReconnect(500);
    };
    ws.onerror = () => {
      isConnecting = false;
      ws = null;
      scheduleReconnect(500);
    };
  } catch (_) {
    isConnecting = false;
    ws = null;
    scheduleReconnect(800);
  }
}

connectRelay();
