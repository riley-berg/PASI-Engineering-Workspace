importScripts("api_contract.js", "userscript_contract.js", "userscript_runtime.js", "userscript_backup.js", "userscript_dnr.js", "userscript_install_queue.js", "userscript_vcs.js", "userscript_compiler.js", "userscript_cloud.js", "background-userscripts.js", "background-api.js", "timeout-config.js", "cdp-network-controller.js");

const BRIDGE = 'http://127.0.0.1:8765';
const ALARM = 'pasi-watchdog';
const OPERATION_DISPATCH_ERROR = 'PASI_NATIVE: controller dispatch unavailable';
let STALE_MS = 45 * 1000;
const CONTROLLER_LEASE_KEY = 'pasi:controller-lease';
const CONTROLLER_LEASE_MS = 10 * 1000;
let controllerClaimTail = Promise.resolve();
let operationDispatchTail = Promise.resolve();
let cachedBridgeToken = null;
let bridgeTokenPromise = null;

function cdpNetworkObservation(event) {
  const terminal = ['COMPLETED', 'INTERRUPTED', 'FAILED'].includes(event?.eventType);
  const kind = event?.eventType === 'COMPLETED'
    ? 'chatgpt_network_response'
    : 'chatgpt_network_lifecycle';
  const observation = {
    schema_version: 'pasi-network-cdp-v1',
    captured_at: new Date().toISOString(),
    data: {
      kind,
      network_source: 'cdp_fetch',
      active_operation_id: event?.operationId || null,
      controller_id: event?.controllerId || null,
      request_id: event?.requestId || null,
      tab_id: Number.isInteger(event?.tabId) ? event.tabId : null,
      request_url: typeof event?.requestUrl === 'string' ? event.requestUrl : null,
      event_type: event?.eventType || null,
      reason: event?.reason || null,
      classification: event?.classification || null,
      response_text: typeof event?.responseText === 'string' ? event.responseText : '',
      response_text_available: typeof event?.responseText === 'string' && Boolean(event.responseText.trim()),
      assistant_message_id: event?.assistantMessageId || null,
      telemetry: event?.telemetry && typeof event.telemetry === 'object' ? event.telemetry : {},
      network_terminal: terminal
    }
  };

  void bridgeFetch('/browser/observation', 'POST', { observation }, 10000);

  if (
    terminal &&
    event?.eventType !== 'COMPLETED' &&
    typeof event?.operationId === 'string' &&
    event.operationId &&
    typeof event?.controllerId === 'string' &&
    event.controllerId
  ) {
    const error = 'PASI_CDP: ' + String(event.reason || event.classification || 'NETWORK_FAILURE');
    void bridgeFetch('/chat/failed', 'POST', {
      operation_id: event.operationId,
      controller_id: event.controllerId,
      failure_source: 'network',
      error,
      recovery_context: {
        network_request_id: String(event.requestId || '').slice(0, 200),
        network_classification: String(event.classification || '').slice(0, 120),
        network_reason: String(event.reason || '').slice(0, 200)
      }
    }, 10000);
  }
}

const cdpNetworkController = globalThis.PASI_CDP_NETWORK?.createController?.({
  debuggerApi: chrome.debugger,
  onEvent: cdpNetworkObservation
});
if (cdpNetworkController) {
  cdpNetworkController.install();
}


if (chrome.sidePanel?.setPanelBehavior) {
  chrome.sidePanel
    .setPanelBehavior({ openPanelOnActionClick: true })
    .catch((error) => console.warn('[PASI side panel]', error));
}

function serializeControllerClaim(task) {
  const next = controllerClaimTail.then(task, task);
  controllerClaimTail = next.catch(() => undefined);
  return next;
}

function serializeOperationDispatch(task) {
  const next = operationDispatchTail.then(task, task);
  operationDispatchTail = next.catch(() => undefined);
  return next;
}

async function dispatchNextOperationForController(tabId, controllerId) {
  if (typeof tabId !== 'number' || !controllerId) return false;
  return serializeOperationDispatch(async () => {
    let tab;
    try {
      tab = await chrome.tabs.get(tabId);
    } catch (_) {
      return false;
    }
    const url = String(tab?.url || '');
    if (!/^https:\/\/(?:www\.)?chatgpt\.com(?::\d+)?\//.test(url)) return false;

    const payload = await bridgeJson(
      '/next-operation?controller_id=' + encodeURIComponent(controllerId)
    );
    const operation = payload?.operation;
    if (!operation || !operation.operation_id) return false;

    try {
      const result = await chrome.tabs.sendMessage(tabId, {
        type: 'pasi-dispatch-operation',
        operation,
        controller_id: controllerId
      });
      if (result?.accepted === true) return true;
    } catch (_) {}

    await bridgeFetch('/chat/failed', 'POST', {
      operation_id: operation.operation_id,
      controller_id: controllerId,
      failure_source: 'controller',
      error: OPERATION_DISPATCH_ERROR
    }, 10000);
    return false;
  });
}

const BRIDGE_ROUTES = new Set([
  'GET /health',
  'GET /status',
  'GET /runner/capabilities',
  'GET /runner/state',
  'POST /runner/control',
  'GET /browser/observation',
  'GET /browser/health',
  'GET /browser/state',
  'GET /browser/response',
    'POST /browser/observation',
  'POST /queue',
  'POST /chat/claim',
  'POST /chat/heartbeat',
  'POST /chat/finished',
  'POST /chat/failed',
  'POST /chat/cancel',
  'GET /next-operation'
]);
const BRIDGE_OPERATION_RE = /^\/operation\?operation_id=[^&]{1,200}$/;
const BRIDGE_NEXT_OPERATION_RE = /^\/next-operation\?controller_id=[^&]{1,200}$/;

async function bridgeToken(forceRefresh = false) {
  if (!forceRefresh && cachedBridgeToken) return cachedBridgeToken;
  if (bridgeTokenPromise) return bridgeTokenPromise;

  bridgeTokenPromise = (async () => {
    try {
      const response = await fetch(chrome.runtime.getURL('.bridge-token'), { cache: 'no-store' });
      if (!response.ok) return '';
      const token = (await response.text()).trim();
      if (token) cachedBridgeToken = token;
      return token;
    } catch (_) {
      return '';
    } finally {
      bridgeTokenPromise = null;
    }
  })();
  return bridgeTokenPromise;
}

function allowedBridgeRequest(method, path) {
  const normalized = String(method || 'GET').toUpperCase();
  const value = String(path || '');
  if (normalized === 'GET' && (
    BRIDGE_OPERATION_RE.test(value) ||
    BRIDGE_NEXT_OPERATION_RE.test(value)
  )) return true;
  return BRIDGE_ROUTES.has(`${normalized} ${value}`);
}

async function bridgeFetch(path, method = 'GET', body = null, timeoutMs = 5000) {
  const normalizedMethod = String(method || 'GET').toUpperCase();
  if (!allowedBridgeRequest(normalizedMethod, path)) {
    return { ok: false, status: 400, text: '' };
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const request = (token) => fetch(`${BRIDGE}${path}`, {
      method: normalizedMethod,
      headers: {
        ...(body ? { 'Content-Type': 'application/json' } : {}),
        ...(token ? { 'Authorization': `Bearer ${token}` } : {})
      },
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
      credentials: 'omit',
      cache: 'no-store'
    });

    let token = await bridgeToken();
    let response = await request(token);
    if (response.status === 401) {
      cachedBridgeToken = null;
      token = await bridgeToken(true);
      if (token) response = await request(token);
    }
    return { ok: response.ok, status: response.status, text: await response.text() };
  } catch (error) {
    const message = String(error?.message || error).slice(0, 300);
    console.warn('[PASI worker bridge]', message);
    return { ok: false, status: 0, text: '', error: message };
  } finally {
    clearTimeout(timer);
  }
}

async function bridgeJson(path) {
  const response = await bridgeFetch(path);
  if (!response.ok) return null;
  try {
    return JSON.parse(response.text);
  } catch (_) {
    return null;
  }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type === 'pasi-controller-ready') {
    const senderUrl = String(sender?.url || '');
    const tabId = sender?.tab?.id;
    const controllerId = message?.controller_id == null ? '' : String(message.controller_id).trim();
    const ready = message?.ready !== false;
    if (
      typeof tabId !== 'number' ||
      !/^https:\/\/(?:www\.)?chatgpt\.com(?::\d+)?\//.test(senderUrl) ||
      !controllerId ||
      controllerId.length > 200 ||
      !ready
    ) {
      sendResponse({ ok: true, accepted: false, ready: false, controller_id: controllerId || null });
      return undefined;
    }
    sendResponse({
      ok: true,
      accepted: false,
      ready: true,
      controller_id: controllerId
    });
    void dispatchNextOperationForController(tabId, controllerId);
    return undefined;
  }

  if (message?.type === 'pasi-network-bind-operation') {
    const senderUrl = String(sender?.url || '');
    const tabId = sender?.tab?.id;
    const rawOperationId = message?.operation_id;
    const operationId = rawOperationId == null ? null : String(rawOperationId).trim();
    if (
      typeof tabId !== 'number' ||
      !/^https:\/\/(?:www\.)?chatgpt\.com(?::\d+)?\//.test(senderUrl) ||
      (operationId !== null && (!operationId || operationId.length > 200))
    ) {
      sendResponse({ ok: false, bound: false });
      return undefined;
    }

    const controllerId = message?.controller_id == null ? '' : String(message.controller_id).trim();
    if (!controllerId || controllerId.length > 200) {
      sendResponse({ ok: false, bound: false });
      return undefined;
    }

    if (operationId === null) {
      cdpNetworkController?.unbindOperation?.(tabId, null, controllerId)
        .then((result) => sendResponse({ ok: true, bound: result?.bound === true }))
        .catch(() => sendResponse({ ok: false, bound: false }));
      return true;
    }

    bridgeFetch('/operation?operation_id=' + encodeURIComponent(operationId))
      .then((response) => {
        if (!response.ok) throw new Error('operation lookup failed');
        let operation;
        try {
          operation = JSON.parse(response.text)?.operation;
        } catch (_) {
          throw new Error('operation lookup was invalid');
        }
        if (!operation || operation.operation_id !== operationId) throw new Error('operation not found');
        if (operation.controller_id !== controllerId) throw new Error('operation controller ownership conflict');
        return cdpNetworkController?.bindOperation?.({
          tabId,
          operationId,
          controllerId,
          prompt: '[PASI_OPERATION ' + operationId + ']\n' + String(operation.prompt || ''),
          completionMarkers: Array.isArray(operation.completion_markers) ? operation.completion_markers : [],
          chatUrl: typeof operation.chat_url === 'string' ? operation.chat_url : null
        });
      })
      .then((result) => {
        const bound = result?.bound === true;
        sendResponse({ ok: bound, bound });
      })
      .catch(() => {
        sendResponse({ ok: false, bound: false });
      });
    return true;
  }

  if (message?.type === 'pasi-cdp-submit-operation') {
    const senderUrl = String(sender?.url || '');
    const tabId = sender?.tab?.id;
    const operationId = message?.operation_id == null ? '' : String(message.operation_id).trim();
    const controllerId = message?.controller_id == null ? '' : String(message.controller_id).trim();
    if (
      typeof tabId !== 'number' ||
      !/^https:\/\/(?:www\.)?chatgpt\.com(?::\d+)?\//.test(senderUrl) ||
      !operationId ||
      operationId.length > 200 ||
      !controllerId ||
      controllerId.length > 200
    ) {
      sendResponse({ok: false, submitted: false});
      return undefined;
    }

    const binding = cdpNetworkController?.currentBinding?.(tabId);
    if (
      !binding ||
      binding.operationId !== operationId ||
      binding.controllerId !== controllerId
    ) {
      sendResponse({ok: false, submitted: false});
      return undefined;
    }

    cdpNetworkController.submitOperation(tabId, operationId, controllerId)
      .then((result) => sendResponse({ok: result?.submitted === true, ...result}))
      .catch((error) => sendResponse({
        ok: false,
        submitted: false,
        error: String(error?.message || error).slice(0, 500)
      }));
    return true;
  }

  if (message?.type === 'pasi-controller-claim') {
    const tabId = sender?.tab?.id;
    const rawControllerId = message?.controller_id;
    const controllerId = rawControllerId == null ? `legacy:${tabId}` : String(rawControllerId).trim();
    if (typeof tabId !== 'number' || !controllerId || controllerId.length > 200) {
      sendResponse({ ok: false, leader: false });
      return undefined;
    }
    serializeControllerClaim(async () => {
      const stored = await chrome.storage.local.get(CONTROLLER_LEASE_KEY);
      const current = stored?.[CONTROLLER_LEASE_KEY];
      const now = Date.now();
      const currentFresh = Boolean(
        current &&
        now - Number(current.renewedAt || 0) < CONTROLLER_LEASE_MS
      );
      const owned = Boolean(
        currentFresh &&
        current.tabId === tabId &&
        current.controllerId === controllerId
      );
      const available = !current || !currentFresh || current.tabId === tabId;
      if (!owned && !available) {
        sendResponse({ ok: true, leader: false, controller_id: controllerId });
        return;
      }
      await chrome.storage.local.set({
        [CONTROLLER_LEASE_KEY]: { tabId, controllerId, renewedAt: now }
      });
      sendResponse({ ok: true, leader: true, controller_id: controllerId });
    }).catch(() => sendResponse({ ok: false, leader: false }));
    return true;
  }

  if (message?.type === 'pasi-control-center-bridge-request') {
    const senderUrl = String(sender?.url || '');
    const extensionPrefix = `chrome-extension://${chrome.runtime.id}/`;
    if (!senderUrl.startsWith(extensionPrefix)) {
      sendResponse({ ok: false, status: 403, text: '' });
      return undefined;
    }

    const method = String(message.method || 'GET').toUpperCase();
    const path = String(message.path || '');
    const allowed = (
      (method === 'GET' && new Set(['/status', '/browser/observation', '/runner/capabilities', '/runner/state']).has(path))
      || (method === 'POST' && path === '/runner/control')
    );
    if (!allowed) {
      sendResponse({ ok: false, status: 403, text: '' });
      return undefined;
    }

    bridgeFetch(path, method, message.body ?? null, 5000).then(sendResponse);
    return true;
  }

  if (!message || message.type !== 'pasi-bridge-request') return undefined;
  const senderUrl = String(sender?.url || '');
  if (!/^https:\/\/(?:www\.)?chatgpt\.com(?::\d+)?\//.test(senderUrl)) {
    sendResponse({ ok: false, status: 403, text: '' });
    return undefined;
  }

  const method = String(message.method || 'GET').toUpperCase();
  const path = String(message.path || '');
  if (!allowedBridgeRequest(method, path)) {
    sendResponse({ ok: false, status: 403, text: '' });
    return undefined;
  }

  const body = message.body == null ? null : message.body;
  if (body !== null && (typeof body !== 'object' || Array.isArray(body))) {
    sendResponse({ ok: false, status: 400, text: '' });
    return undefined;
  }

  const requestedTimeout = Number(message.timeout);
  const timeoutMs = Number.isFinite(requestedTimeout)
    ? Math.min(Math.max(requestedTimeout, 250), 10000)
    : 10000;
  bridgeFetch(path, method, body, timeoutMs).then(sendResponse);
  return true;
});
function observationAge(observation) {
  const stamp = observation && observation.captured_at;
  if (typeof stamp !== 'string') return Infinity;
  const value = Date.parse(stamp);
  if (!Number.isFinite(value)) return Infinity;
  return Math.max(0, Date.now() - value);
}

function healthData(payload) {
  const observation = payload && payload.observation;
  if (!observation || typeof observation !== 'object') return null;
  const data = observation.data && typeof observation.data === 'object' ? observation.data : observation;
  return { observation, data };
}

function sameChatConversationUrl(candidate, target) {
  try {
    const left = new URL(String(candidate || ''));
    const right = new URL(String(target || ''));
    const allowedOrigins = new Set(['https://chatgpt.com', 'https://www.chatgpt.com']);
    return allowedOrigins.has(left.origin)
      && allowedOrigins.has(right.origin)
      && left.pathname === right.pathname
      && left.pathname.startsWith('/c/');
  } catch (_) {
    return false;
  }
}

async function injectExistingChatTabs() {
  if (!chrome.scripting?.executeScript) return;
  const tabs = await chrome.tabs.query({
    url: ['https://chatgpt.com/*', 'https://www.chatgpt.com/*']
  });
  for (const tab of tabs) {
    if (typeof tab.id !== 'number') continue;

    // A previously injected controller can answer this ping. Do not
    // re-execute the full support-script bundle on an already-live tab,
    // because the support scripts are intentionally global and are not
    // themselves controller lifecycle owners.
    try {
      await chrome.tabs.sendMessage(tab.id, { type: 'pasi-health-ping' });
      continue;
    } catch (_) {
      // No live controller listener is present; inject into the existing tab.
    }

    try {
      await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        files: [
          'src/timeout-config.js',
          'src/detectors.js',
          'src/recovery_progress.js',
          'src/legacy/dom-controller.js',
          'src/recovery.js'
        ]
      });
    } catch (_) {
      // Retry later without creating, navigating, or reloading a tab.
    }
  }
}
async function inspect() {
  await injectExistingChatTabs();

  const payload = await bridgeJson('/browser/observation');
  if (!payload) return;
  const health = healthData(payload);
  if (!health || health.data.auth_required === true) return;

  const targetChatUrl = typeof health.data.chat_url === 'string'
    ? health.data.chat_url
    : '';
  if (!targetChatUrl) return;

  const tabs = await chrome.tabs.query({
    url: ['https://chatgpt.com/*', 'https://www.chatgpt.com/*']
  });
  const matchingTab = tabs.find((tab) => sameChatConversationUrl(tab.url, targetChatUrl));
  if (matchingTab && typeof matchingTab.id === 'number') {
    try {
      await chrome.tabs.sendMessage(matchingTab.id, { type: 'pasi-controller-ready', source: 'watchdog' });
    } catch (_) {
      // Existing-tab injection will be retried on the next watchdog pass.
    }
  }
}

async function applyTimeoutPolicy() {
  try {
    const response = await fetch(chrome.runtime.getURL('timeout-policy.json'), { cache: 'no-store' });
    if (!response.ok) return;
    const policy = await response.json();
    const staleSeconds = Number(policy?.stale_seconds);
    if (Number.isFinite(staleSeconds) && staleSeconds > 0) {
      STALE_MS = staleSeconds * 1000;
    }
  } catch (_) {}
}

async function ensureWatchdogAlarm() {
  await applyTimeoutPolicy();
  try {
    const alarm = await chrome.alarms.get(ALARM);
    const period = Number(alarm?.periodInMinutes);
    if (!alarm || !Number.isFinite(period) || Math.abs(period - 0.5) > 0.001) {
      await chrome.alarms.create(ALARM, { periodInMinutes: 0.5 });
    }
  } catch (_) {}
}

chrome.runtime.onInstalled.addListener(() => {
  void ensureWatchdogAlarm();
  void injectExistingChatTabs();
});

chrome.runtime.onStartup.addListener(() => {
  void ensureWatchdogAlarm();
  void injectExistingChatTabs();
});

void ensureWatchdogAlarm();
void injectExistingChatTabs();

if (chrome.tabs?.onRemoved) {
  chrome.tabs.onRemoved.addListener((tabId) => {
    void cdpNetworkController?.detachTab?.(tabId);
  });
}

if (chrome.sidePanel?.setPanelBehavior) {
  chrome.sidePanel
    .setPanelBehavior({ openPanelOnActionClick: true })
    .catch((error) => console.warn('[PASI side panel]', error));
}


chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === ALARM) inspect();
});


/*
 * PASI userscript/API message plane. The native ChatGPT bridge listener above
 * remains the only listener allowed to talk to 127.0.0.1:8765.
 */
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (String(message?.type || "").startsWith("pasi.userscript.") && message?.type !== "pasi.userscript.rpc") {
    globalThis.PASIUserScriptManager.handle(message, sender)
      .then(sendResponse)
      .catch((error) => sendResponse({ok: false, error: String(error?.message || error)}));
    return true;
  }

  if (String(message?.type || "").startsWith("pasi.api.")) {
    globalThis.PASIBackgroundAPI.handle(message, sender)
      .then(sendResponse)
      .catch((error) => sendResponse({ok: false, error: String(error?.message || error)}));
    return true;
  }

  return undefined;
});

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== "pasi.http.stream") return;
  port.onMessage.addListener((message) => {
    if (message?.type !== globalThis.PASIExtensionAPIContract.MESSAGE_TYPES.HTTP_STREAM_START) return;
    void globalThis.PASIBackgroundAPI.startHttpStream(port, message);
  });
});

chrome.runtime.onInstalled.addListener((details) => {
  if (details.reason === "update" || details.reason === "install") {
    void globalThis.PASIUserScriptManager.restore();
  }
});
