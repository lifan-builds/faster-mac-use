const DEFAULT_RELAY_URL = "ws://127.0.0.1:19222/ext";
let relayUrl = DEFAULT_RELAY_URL;
let ws = null;
const sessionGroups = new Map();
const sessionTabs = new Map();
const attachedDebuggers = new Set();

chrome.alarms.create("fmu-keepalive", { periodInMinutes: 0.25 });
chrome.alarms.onAlarm.addListener(() => {
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    connectRelay();
  }
});

async function ensureAgentGroup(tabId, sessionName = "🤖 Agent", color = "blue") {
  let groupId = sessionGroups.get(sessionName);
  if (groupId !== undefined) {
    try {
      await chrome.tabGroups.get(groupId);
      await chrome.tabs.group({ tabIds: [tabId], groupId });
      return groupId;
    } catch (_) {
      sessionGroups.delete(sessionName);
    }
  }
  const existing = await chrome.tabGroups.query({ title: sessionName });
  if (existing.length > 0) {
    groupId = existing[0].id;
    await chrome.tabs.group({ tabIds: [tabId], groupId });
  } else {
    groupId = await chrome.tabs.group({ tabIds: [tabId] });
    await chrome.tabGroups.update(groupId, {
      title: sessionName,
      color,
      collapsed: false
    });
  }
  sessionGroups.set(sessionName, groupId);
  return groupId;
}

async function ensureDebuggerAndFocusEmulation(tabId) {
  if (attachedDebuggers.has(tabId)) return;
  await chrome.debugger.attach({ tabId }, "1.3");
  attachedDebuggers.add(tabId);
  await chrome.debugger.sendCommand({ tabId }, "Emulation.setFocusEmulationEnabled", { enabled: true });
  try {
    await chrome.debugger.sendCommand({ tabId }, "Page.setWebLifecycleState", { state: "active" });
  } catch (_) {}
}

chrome.debugger.onDetach.addListener((source) => {
  if (source.tabId) attachedDebuggers.delete(source.tabId);
});

async function evalInTab(tabId, expression, awaitPromise = true) {
  await ensureDebuggerAndFocusEmulation(tabId);
  const res = await chrome.debugger.sendCommand({ tabId }, "Runtime.evaluate", {
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

async function handleRpc(msg) {
  const { id, method, params = {} } = msg;
  try {
    if (method === "ping") {
      return { id, result: { pong: true, attachedTabs: Array.from(attachedDebuggers) } };
    }
    if (method === "createBrowserTab") {
      const { url = "about:blank", sessionName = "🤖 Agent", sessionId = "default", color = "blue" } = params;
      const tab = await chrome.tabs.create({ url, active: false });
      const groupId = await ensureAgentGroup(tab.id, sessionName, color);
      sessionTabs.set(sessionId, tab.id);
      if (params.focusEmulation !== false) {
        try { await ensureDebuggerAndFocusEmulation(tab.id); } catch (_) {}
      }
      return { id, result: { tabId: tab.id, groupId, sessionName, url: tab.pendingUrl || tab.url } };
    }
    if (method === "navigate" || method === "goto") {
      const { tabId, url } = params;
      await chrome.tabs.update(tabId, { url, active: false });
      await ensureDebuggerAndFocusEmulation(tabId);
      return { id, result: { tabId, url } };
    }
    if (method === "getTab") {
      const { sessionId = "default", urlPattern, tabId } = params;
      if (tabId) return { id, result: { tabId } };
      if (sessionTabs.has(sessionId)) return { id, result: { tabId: sessionTabs.get(sessionId) } };
      const tabs = await chrome.tabs.query({});
      const match = urlPattern ? tabs.find(t => (t.url || "").includes(urlPattern)) : tabs[0];
      if (!match) throw new Error("No matching Chrome tab found");
      sessionTabs.set(sessionId, match.id);
      return { id, result: { tabId: match.id, url: match.url, title: match.title } };
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
      const { tabId, runtimeJs, batchReq } = params;
      if (runtimeJs) {
        await evalInTab(tabId, runtimeJs, false);
      }
      const reqPayload = batchReq || params;
      const expr = `window.__fmu.executeBatch(${JSON.stringify(reqPayload || {})})`;
      const batchRes = await evalInTab(tabId, expr, true);
      return { id, result: batchRes };
    }
    if (method === "captureClip") {
      const { tabId, clip } = params;
      await ensureDebuggerAndFocusEmulation(tabId);
      const cdpParams = { format: "png" };
      if (clip) cdpParams.clip = { ...clip, scale: clip.scale || 1 };
      const shot = await chrome.debugger.sendCommand({ tabId }, "Page.captureScreenshot", cdpParams);
      return { id, result: shot };
    }
    if (method === "closeTab") {
      const { tabId, sessionId } = params;
      const targetId = tabId || (sessionId ? sessionTabs.get(sessionId) : undefined);
      if (targetId) {
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
      const { tabId, cdpMethod, cdpParams = {} } = params;
      await ensureDebuggerAndFocusEmulation(tabId);
      const res = await chrome.debugger.sendCommand({ tabId }, cdpMethod, cdpParams);
      return { id, result: res };
    }
    throw new Error(`Unknown extension RPC method: ${method}`);
  } catch (err) {
    return { id, error: String(err.message || err) };
  }
}

function connectRelay() {
  try {
    ws = new WebSocket(relayUrl);
    ws.onopen = () => {
      try {
        ws.send(JSON.stringify({ type: "hello", client: "fmu-mv3-extension", version: "0.2.0" }));
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
    ws.onclose = () => { ws = null; };
    ws.onerror = () => { ws = null; };
  } catch (_) {
    ws = null;
  }
}

connectRelay();
