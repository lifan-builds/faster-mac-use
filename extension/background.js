const DEFAULT_RELAY_URL = "ws://127.0.0.1:19222/ext";
let relayUrl = DEFAULT_RELAY_URL;
let ws = null;
let isConnecting = false;
let reconnectTimer = null;
const sessionGroups = new Map();
const sessionTabs = new Map();
const attachedDebuggers = new Set();
let activeScreencastTabId = null;
let screencastIdleTimer = null;
let lastScreencastPushAt = 0;
let pendingPreviewTimer = null;

function scheduleReconnect(delayMs = 500) {
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      connectRelay();
    }
  }, delayMs);
}

chrome.alarms.create("fmu-keepalive", { periodInMinutes: 0.25 });
chrome.alarms.onAlarm.addListener(() => {
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    connectRelay();
  }
});
if (chrome.runtime && chrome.runtime.onStartup) {
  chrome.runtime.onStartup.addListener(() => connectRelay());
}
if (chrome.runtime && chrome.runtime.onInstalled) {
  chrome.runtime.onInstalled.addListener(() => connectRelay());
}

function getHostname(rawUrl) {
  try {
    const u = new URL(String(rawUrl || ""));
    return (u.hostname || "").toLowerCase();
  } catch (_) {
    return "";
  }
}

async function findGroupWindowId(sessionName = "🤖 Agent", targetWindowId = undefined) {
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

async function ensureAgentGroup(tabId, sessionName = "🤖 Agent", color = "blue", targetWindowId = undefined) {
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

async function waitForTabLoad(tabId, timeoutMs = 10000, expectedUrl = "") {
  if (!chrome.tabs || typeof chrome.tabs.get !== "function") return;
  const start = Date.now();
  const deadline = start + Math.max(500, timeoutMs);
  const wantRealUrl = Boolean(expectedUrl && expectedUrl !== "about:blank");
  while (Date.now() < deadline) {
    try {
      const t = await chrome.tabs.get(tabId);
      if (!t) return;
      const curUrl = String(t.url || "");
      const pendingUrl = String(t.pendingUrl || "");
      if (t.status === "complete" && !pendingUrl) {
        if (!wantRealUrl || (curUrl && curUrl !== "about:blank") || (Date.now() - start > 450)) {
          return;
        }
      }
    } catch (_) {
      return;
    }
    await new Promise(r => setTimeout(r, 35));
  }
}

async function ensureDebuggerAndFocusEmulation(tabId) {
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
  try {
    await chrome.debugger.sendCommand({ tabId }, "Emulation.setFocusEmulationEnabled", { enabled: true });
  } catch (_) {}
  try {
    await chrome.debugger.sendCommand({ tabId }, "Page.setWebLifecycleState", { state: "active" });
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
      if (activeScreencastTabId === source.tabId) {
        activeScreencastTabId = null;
      }
    }
  });
}

if (chrome.debugger && chrome.debugger.onEvent && typeof chrome.debugger.onEvent.addListener === "function") {
  chrome.debugger.onEvent.addListener((source, method, params) => {
    if (method !== "Page.screencastFrame" || !source || !source.tabId || !params) return;
    const tabId = source.tabId;
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

async function evalInTab(tabId, expression, awaitPromise = true) {
  const res = await sendDebuggerCommand(tabId, "Runtime.evaluate", {
    expression,
    awaitPromise,
    returnByValue: true
  });
  if (res && res.exceptionDetails) {
    const desc = (res.result && res.result.description) || res.exceptionDetails.text || "Runtime.evaluate error";
    throw new Error(desc);
  }
  return res && res.result ? res.result.value : undefined;
}

async function evalStepCodeOnTab(tabId, rawCode) {
  const code = String(rawCode || "");
  const body = code.includes("return ") ? code : `return (${code}\n)`;
  try {
    return await evalInTab(tabId, `(async () => {\n${body}\n})()`, true);
  } catch (err) {
    const msg = String((err && err.message) || err || "");
    if (!code.includes("return ") && msg.includes("SyntaxError")) {
      return await evalInTab(tabId, `(async () => {\n${code}\n})()`, true);
    }
    throw err;
  }
}

async function setInputFilesOnTab(tabId, selector, files) {
  const fileList = Array.isArray(files) ? files.map(String) : [String(files)];
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
      throw new Error(UPLOAD_FILE_URL_ERR);
    }
  }
  const doc = await sendDebuggerCommand(tabId, "DOM.getDocument", { depth: 0 });
  const rootId = doc && doc.root ? doc.root.nodeId : 1;
  const rawSel = selector || "input[type='file']";
  const candidateSelectors = [];
  if (rawSel.startsWith("[")) {
    candidateSelectors.push(`input[type='file']${rawSel}`);
  }
  candidateSelectors.push(rawSel);
  if (rawSel !== "input[type='file']") {
    candidateSelectors.push("input[type='file']");
  }
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
    throw new Error(UPLOAD_FILE_URL_ERR);
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
        sessionName = "🤖 Agent",
        sessionId = "default",
        color = "blue",
        window: windowFilter,
        windowId: explicitWindowId,
        reuseExisting = false,
        reattach = false,
        timeoutMs = 10000
      } = params;

      const allTabs = (chrome.tabs && typeof chrome.tabs.query === "function")
        ? (await chrome.tabs.query({})) || []
        : [];
      const wf = String(windowFilter || "").trim().toLowerCase();
      let filterMatchedWindowId = undefined;

      if (reattach && (!url || url === "about:blank")) {
        let reattachedTab = null;
        if (sessionTabs.has(sessionId)) {
          const savedId = sessionTabs.get(sessionId);
          reattachedTab = allTabs.find(t => t.id === savedId) || null;
          if (!reattachedTab && chrome.tabs && typeof chrome.tabs.get === "function") {
            try { reattachedTab = await chrome.tabs.get(savedId); } catch (_) {}
          }
        }
        if (!reattachedTab || String(reattachedTab.url || "") === "about:blank") {
          const { groupId: existingGid } = await findGroupWindowId(sessionName, explicitWindowId);
          if (existingGid !== undefined) {
            const groupTabs = allTabs.filter(t => t.groupId === existingGid);
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
          const groupId = await ensureAgentGroup(reattachedTab.id, sessionName, color, reattachedTab.windowId);
          sessionTabs.set(sessionId, reattachedTab.id);
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
            const exactUrlMatch = (url && url !== "about:blank")
              ? wfTabs.find(t => String(t.url || t.pendingUrl || "").startsWith(url))
              : null;
            const match = exactUrlMatch || wfTabs[0];
            if (match && match.id !== undefined) {
              const groupId = await ensureAgentGroup(match.id, sessionName, color, match.windowId);
              sessionTabs.set(sessionId, match.id);
              if (url && url !== "about:blank" && match.url !== url) {
                await chrome.tabs.update(match.id, { url, active: false });
                await waitForTabLoad(match.id, timeoutMs, url);
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
                  url: url !== "about:blank" ? url : (match.url || match.pendingUrl || url),
                  title: match.title || ""
                }
              };
            }
          }
        }
      } else if (reuseExisting && url && url !== "about:blank") {
        const urlLower = String(url).toLowerCase();
        const match = allTabs.find(t => {
          if (explicitWindowId !== undefined && t.windowId !== explicitWindowId) return false;
          const tUrl = String(t.url || t.pendingUrl || "");
          return tUrl.startsWith(url) || tUrl.toLowerCase().includes(urlLower);
        });
        if (match && match.id !== undefined) {
          const groupId = await ensureAgentGroup(match.id, sessionName, color, match.windowId);
          sessionTabs.set(sessionId, match.id);
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
              url: match.url || match.pendingUrl || url,
              title: match.title || ""
            }
          };
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
      const effectiveWinId = tab.windowId !== undefined ? tab.windowId : targetWindowId;
      const groupId = await ensureAgentGroup(tab.id, sessionName, color, effectiveWinId);
      sessionTabs.set(sessionId, tab.id);
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
      if (!skipPreview) {
        startLiveScreencast(tabId);
      }
      await chrome.tabs.update(tabId, { url, active: false });
      if (url && url !== "about:blank") {
        await waitForTabLoad(tabId, timeoutMs, url);
      }
      await ensureDebuggerAndFocusEmulation(tabId);
      let tabTitle = "";
      if (chrome.tabs && typeof chrome.tabs.get === "function") {
        try {
          const t = await chrome.tabs.get(tabId);
          if (t && t.title) tabTitle = t.title;
        } catch (_) {}
      }
      stopLiveScreencast(tabId);
      if (!skipPreview) {
        scheduleAsyncPreviewPush(tabId);
      }
      return { id, result: { tabId, url, title: tabTitle } };
    }
    if (method === "getTab") {
      const { sessionId = "default", urlPattern, window: windowFilter, tabId } = params;
      if (tabId) return { id, result: { tabId } };
      if (!urlPattern && !windowFilter && sessionTabs.has(sessionId)) {
        return { id, result: { tabId: sessionTabs.get(sessionId) } };
      }
      const tabs = await chrome.tabs.query({});
      const pat = String(urlPattern || windowFilter || "").toLowerCase();
      const match = pat
        ? tabs.find(t => String(t.url || "").toLowerCase().includes(pat) || String(t.title || "").toLowerCase().includes(pat))
        : tabs[0];
      if (!match) throw new Error(`No matching Chrome tab found for '${urlPattern || windowFilter || sessionId}'`);
      sessionTabs.set(sessionId, match.id);
      return { id, result: { tabId: match.id, windowId: match.windowId, url: match.url, title: match.title } };
    }
    if (method === "setInputFiles") {
      const { tabId, selector = "input[type='file']", files = [] } = params;
      const res = await setInputFilesOnTab(tabId, selector, files);
      return { id, result: res };
    }
    if (method === "evaluate") {
      const { tabId, expression, arg, runtimeInit, awaitPromise = true } = params;
      const wrappedExpr = runtimeInit
        ? expression
        : `((__fn, __arg) => (typeof __fn === "function" ? __fn(__arg) : __fn))((${expression}), ${JSON.stringify(arg === undefined ? null : arg)})`;
      const val = await evalInTab(tabId, wrappedExpr, awaitPromise);
      return { id, result: val };
    }
    if (method === "executeBatch") {
      clearPendingPreview();
      const { tabId, runtimeJs, batchReq, ...restParams } = params;
      const reqPayload = batchReq || restParams;
      stopLiveScreencast(tabId);
      if (runtimeJs) {
        await evalInTab(tabId, runtimeJs, false);
      }
      const expr = `window.__fmu.executeBatch(${JSON.stringify(reqPayload || {})})`;
      let batchRes = await evalInTab(tabId, expr, true);
      if (batchRes && Array.isArray(batchRes.escalations) && batchRes.escalations.length > 0) {
        const steps = (reqPayload && reqPayload.steps) || [];
        if (!Array.isArray(batchRes.evalOutputs)) batchRes.evalOutputs = [];
        if (!Array.isArray(batchRes.errors)) batchRes.errors = [];
        let didMutate = false;
        for (const esc of batchRes.escalations) {
          if (esc.reason === "eval_csp" && !esc.handled) {
            esc.handled = true;
            didMutate = true;
            try {
              const val = await evalStepCodeOnTab(tabId, esc.code);
              if (val !== undefined) {
                batchRes.evalOutputs.push(typeof val === "string" ? val : JSON.stringify(val));
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
                await setInputFilesOnTab(tabId, sel, files);
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
      const { tabId, sessionId } = params;
      const targetId = tabId || (sessionId ? sessionTabs.get(sessionId) : undefined);
      if (targetId) {
        stopLiveScreencast(targetId);
        try { await chrome.tabs.remove(targetId); } catch (_) {}
        attachedDebuggers.delete(targetId);
      }
      if (sessionId) sessionTabs.delete(sessionId);
      return { id, result: { closed: Boolean(targetId) } };
    }
    if (method === "closeTabGroup") {
      const { sessionName = "🤖 Agent" } = params;
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
        ws.send(JSON.stringify({ type: "hello", client: "fmu-mv3-extension", version: "0.3.0" }));
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
