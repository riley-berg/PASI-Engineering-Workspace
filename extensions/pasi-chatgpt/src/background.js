importScripts("api_contract.js", "userscript_contract.js", "userscript_runtime.js", "userscript_backup.js", "userscript_dnr.js", "userscript_install_queue.js", "userscript_vcs.js", "userscript_compiler.js", "userscript_cloud.js", "background-userscripts.js", "background-api.js", "timeout-config.js", "cdp-network-controller.js");

const BRIDGE = 'http://127.0.0.1:8765';
const ALARM = 'pasi-watchdog';
let STALE_MS = 45 * 1000;
let operationDispatchTail = Promise.resolve();
let cachedBridgeToken = null;
let bridgeTokenPromise = null;
const cdpOperationTimings = new Map();

async function cdpNetworkObservation(event) {
  const terminal = ['COMPLETED', 'INTERRUPTED', 'FAILED'].includes(event?.eventType);
  const kind = event?.eventType === 'COMPLETED'
    ? 'chatgpt_network_response'
    : 'chatgpt_network_lifecycle';

  const operationId = typeof event?.operationId === 'string' ? event.operationId : '';
  const controllerId = typeof event?.controllerId === 'string' ? event.controllerId : '';
  const timing = operationId ? (cdpOperationTimings.get(operationId) || {}) : {};
  if (event?.eventType === 'STARTED') {
    timing.generation_start_ms = Number(event.timestamp || Date.now());
  }
  if (terminal) {
    timing.completed_at_ms = Number(event.timestamp || Date.now());
  }

  const observation = {
    schema_version: 'pasi-network-cdp-v1',
    captured_at: new Date().toISOString(),
    data: {
      kind,
      network_source: 'cdp_fetch',
      active_operation_id: operationId || null,
      controller_id: controllerId || null,
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
      timing,
      stream_complete: event?.streamComplete === true,
      network_terminal: terminal
    }
  };

  const observed = await bridgeFetch('/browser/observation', 'POST', {observation}, 10000);

  if (!terminal || !operationId || !controllerId) return;

  if (event?.eventType === 'COMPLETED') {
    let chatUrl = '';
    try {
      const tab = await chrome.tabs.get(event.tabId);
      chatUrl = String(tab?.url || '');
    } catch (_) {}

    const completionPayload = {
      operation_id: operationId,
      controller_id: controllerId,
      chat_url: chatUrl,
      response_text: typeof event.responseText === 'string' ? event.responseText : '',
      response_text_available: typeof event.responseText === 'string' && Boolean(event.responseText.trim()),
      ack_only: true,
      // A prompt response must be fully consumed by the runner before the
      // next task is allowed to start. The bridge therefore completes only;
      // the worker then long-polls for the next operation to be queued after
      // response processing has finished.
      claim_next: false
    };
    if (Object.keys(timing).length) completionPayload.timing = timing;

    let completion = null;
    for (let attempt = 0; attempt < 3; attempt += 1) {
      completion = await bridgeFetch('/chat/finished', 'POST', completionPayload, 10000);
      if (completion.ok) break;
      await new Promise((resolve) => setTimeout(resolve, 150));
    }

    if (completion?.ok) {
      cdpOperationTimings.delete(operationId);
      await cdpNetworkController?.unbindOperation?.(
        event.tabId,
        operationId,
        controllerId
      );
      void waitForNextOperationForController(event.tabId, controllerId);
      return;
    }

    if (!observed?.ok) {
      await bridgeFetch('/chat/failed', 'POST', {
        operation_id: operationId,
        controller_id: controllerId,
        failure_source: 'network',
        error: 'PASI_CDP: durable completion acknowledgement failed',
      }, 10000);
    }
    return;
  }

  await bridgeFetch('/chat/failed', 'POST', {
    operation_id: operationId,
    controller_id: controllerId,
    failure_source: 'network',
    error: 'PASI_CDP: ' + String(event.reason || event.classification || 'NETWORK_FAILURE'),
    recovery_context: {
      network_request_id: String(event.requestId || '').slice(0, 200),
      network_classification: String(event.classification || '').slice(0, 120),
      network_reason: String(event.reason || '').slice(0, 200)
    }
  }, 10000);
  cdpOperationTimings.delete(operationId);
  await cdpNetworkController?.unbindOperation?.(
    event.tabId,
    operationId,
    controllerId
  );

  // Explicit provider/context terminal states must not be immediately
  // redispatched. Context exhaustion is surfaced to the runner, which
  // creates the fresh conversation before submitting the task again.
  const autoRetry = !new Set(['context_exhaustion', 'usage_limit', 'auth_failure'])
    .has(String(event.classification || ''));
  if (autoRetry) {
    void dispatchNextOperationForController(event.tabId, controllerId);
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

function serializeOperationDispatch(task) {
  const next = operationDispatchTail.then(task, task);
  operationDispatchTail = next.catch(() => undefined);
  return next;
}

async function waitForNextOperationForController(tabId, controllerId, waitMs = 60000) {
  if (typeof tabId !== 'number' || !controllerId) return false;
  return serializeOperationDispatch(async () => {
    const boundedWaitMs = Math.max(1000, Math.min(60000, Number(waitMs) || 60000));
    while (true) {
      if (cdpNetworkController?.isIdle && !cdpNetworkController.isIdle(tabId)) return false;
      const payload = await bridgeJson(
        '/next-operation?controller_id=' + encodeURIComponent(controllerId) + '&wait_ms=' + String(Math.round(boundedWaitMs))
      );
      const operation = payload?.operation;
      if (operation?.operation_id) {
        return dispatchOperationForController(tabId, controllerId, operation);
      }
      // A prompt can take longer than one bridge long-poll to finish being
      // parsed, patched, tested, committed, and acknowledged. Continue waiting
      // instead of silently dropping the handoff after the first 60s window.
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
  });
}

async function waitForTabNavigation(tabId, timeoutMs = 20000) {
  return new Promise((resolve, reject) => {
    let settled = false;
    const cleanup = () => {
      if (chrome.tabs?.onUpdated?.removeListener) {
        chrome.tabs.onUpdated.removeListener(listener);
      }
      clearTimeout(timer);
    };
    const finish = (value, error = null) => {
      if (settled) return;
      settled = true;
      cleanup();
      if (error) reject(error);
      else resolve(value);
    };
    const timer = setTimeout(() => finish(null, new Error('PASI_NATIVE: new chat navigation timed out')), timeoutMs);
    const listener = (updatedTabId, changeInfo, tab) => {
      if (updatedTabId !== tabId || changeInfo?.status !== 'complete') return;
      finish(String(tab?.url || ''));
    };
    chrome.tabs.onUpdated.addListener(listener);
  });
}

async function executePromptOperation(tabId, controllerId, operation) {
  if (!operation?.operation_id) return false;
  try {
    const binding = await cdpNetworkController.bindOperation({
      tabId,
      operationId: String(operation.operation_id),
      controllerId: String(controllerId),
      prompt: String(operation.prompt || ''),
      completionMarkers: Array.isArray(operation.completion_markers) ? operation.completion_markers : [],
      chatUrl: typeof operation.chat_url === 'string' ? operation.chat_url : null
    });
    if (binding?.bound !== true) {
      throw new Error('PASI_NATIVE: CDP operation bind failed');
    }

    const submission = await cdpNetworkController.submitOperation(
      tabId,
      String(operation.operation_id),
      String(controllerId)
    );
    const timing = {
      injected_at_ms: Number(submission.submittedAtMs || Date.now()),
      ack_at_ms: Number(submission.enterDispatchedAtMs || Date.now())
    };
    if (Number.isFinite(Number(operation.__pasi_completion_ack_at_ms))) {
      timing.completion_to_prompt_injected_ms = Math.max(
        0,
        timing.injected_at_ms - Number(operation.__pasi_completion_ack_at_ms)
      );
    }
    if (Number.isFinite(Number(operation.predecessor_completed_at_ms ?? operation.__pasi_response_completed_at_ms))) {
      timing.response_completed_to_prompt_injected_ms = Math.max(
        0,
        timing.injected_at_ms - Number(operation.predecessor_completed_at_ms ?? operation.__pasi_response_completed_at_ms)
      );
    }
    cdpOperationTimings.set(String(operation.operation_id), timing);
    return true;
  } catch (error) {
    await bridgeFetch('/chat/failed', 'POST', {
      operation_id: String(operation.operation_id),
      controller_id: String(controllerId),
      failure_source: 'controller',
      error: 'PASI_NATIVE: prompt dispatch failed: ' + String(error?.message || error).slice(0, 500)
    }, 10000);
    await cdpNetworkController?.unbindOperation?.(
      tabId,
      String(operation.operation_id),
      String(controllerId)
    );
    void dispatchNextOperationForController(tabId, controllerId);
    return false;
  }
}

async function executeAttachGithubOperation(tabId, controllerId, operation) {
  if (!operation?.operation_id) return false;
  try {
    const repository = String(operation.prompt || '').trim();
    if (!/^[^/\s]+\/[^/\s]+$/.test(repository)) {
      throw new Error('PASI_NATIVE: GitHub repository must be in owner/name form');
    }
    const result = await cdpNetworkController?.ensureGithubRepository?.(tabId, repository);
    if (result?.attached !== true || result.repository !== repository) {
      throw new Error('PASI_NATIVE: GitHub repository attachment could not be verified');
    }
    const completion = await bridgeFetch('/chat/finished', 'POST', {
      operation_id: String(operation.operation_id),
      controller_id: String(controllerId),
      chat_url: String((await chrome.tabs.get(tabId))?.url || ''),
      response_text: '',
      response_text_available: false,
      ack_only: true,
      claim_next: true
    }, 10000);
    if (!completion.ok) throw new Error('PASI_NATIVE: GitHub attachment completion rejected');
    try {
      const payload = JSON.parse(completion.text);
      const next = payload?.next_operation;
      if (next?.operation_id) {
        void serializeOperationDispatch(() => dispatchOperationForController(tabId, controllerId, next));
      }
    } catch (_) {}
    return true;
  } catch (error) {
    await bridgeFetch('/chat/failed', 'POST', {
      operation_id: String(operation.operation_id),
      controller_id: String(controllerId),
      failure_source: 'controller',
      error: 'PASI_NATIVE: GitHub attachment failed: ' + String(error?.message || error).slice(0, 500)
    }, 10000);
    return false;
  }
}
async function executeNewChatOperation(tabId, controllerId, operation) {
  if (!operation?.operation_id) return false;
  try {
    const tab = await chrome.tabs.get(tabId);
    const previousUrl = String(tab?.url || '');

    const reasoning = await cdpNetworkController?.ensureReasoningMode?.(tabId, 'thinking');
    if (reasoning?.enabled !== true) {
      throw new Error('PASI_NATIVE: Thinking state could not be verified before new-chat navigation');
    }

    const navigation = waitForTabNavigation(tabId);
    await chrome.tabs.update(tabId, {url: 'https://chatgpt.com/'});
    const finalUrl = await navigation;
    if (!finalUrl || !/^https:\/\/(?:www\.)?chatgpt\\.com(?::\d+)?\//.test(finalUrl)) {
      throw new Error('PASI_NATIVE: new chat navigation ended outside ChatGPT');
    }
    if (
      /^https:\/\/(?:www\.)?chatgpt\\.com(?::\d+)?\/c\//.test(previousUrl) &&
      finalUrl === previousUrl
    ) {
      throw new Error('PASI_NATIVE: new chat navigation did not change conversation identity');
    }

    const completion = await bridgeFetch('/chat/finished', 'POST', {
      operation_id: String(operation.operation_id),
      controller_id: String(controllerId),
      chat_url: finalUrl,
      response_text: '',
      response_text_available: false,
      ack_only: true,
      claim_next: true
    }, 10000);
    if (!completion.ok) {
      throw new Error(
        'PASI_NATIVE: new chat completion failed: HTTP ' + String(completion.status || 0)
      );
    }
    try {
      const payload = JSON.parse(completion.text);
      const next = payload?.next_operation;
      if (next?.operation_id) {
        void serializeOperationDispatch(() =>
          dispatchOperationForController(tabId, controllerId, next)
        );
      }
    } catch (_) {}
    return true;
  } catch (error) {
    const failure = String(error?.message || error).slice(0, 500);
    await bridgeFetch('/chat/failed', 'POST', {
      operation_id: String(operation.operation_id),
      controller_id: String(controllerId),
      failure_source: 'controller',
      error: failure || 'PASI_NATIVE: new chat navigation failed'
    }, 10000);
    return false;
  }
}

async function dispatchOperationForController(tabId, controllerId, operation) {
  if (typeof tabId !== 'number' || !controllerId || !operation?.operation_id) return false;

  let tab;
  try {
    tab = await chrome.tabs.get(tabId);
  } catch (_) {
    return false;
  }
  const url = String(tab?.url || '');
  if (!/^https:\/\/(?:www\.)?chatgpt\.com(?::\d+)?\//.test(url)) return false;

  if (operation.operation_type === 'new_chat') {
    return executeNewChatOperation(tabId, controllerId, operation);
  }

  if (operation.operation_type === 'prompt') {
    return executePromptOperation(tabId, controllerId, operation);
  }

  if (operation.operation_type === 'attach_github') {
    return executeAttachGithubOperation(tabId, controllerId, operation);
  }

  if (operation.operation_type === 'select_reasoning') {
    try {
      if (String(operation.prompt || '').trim() !== 'thinking') {
        throw new Error('PASI_NATIVE: unsupported reasoning mode');
      }
      const reasoning = await cdpNetworkController?.ensureReasoningMode?.(tabId, 'thinking');
      if (reasoning?.enabled !== true) {
        throw new Error('PASI_NATIVE: Thinking state could not be verified');
      }

      const completion = await bridgeFetch('/chat/finished', 'POST', {
        operation_id: String(operation.operation_id),
        controller_id: String(controllerId),
        chat_url: url,
        response_text: '',
        response_text_available: false,
        ack_only: true,
        claim_next: true
      }, 10000);
      if (!completion.ok) {
        throw new Error('PASI_NATIVE: reasoning completion rejected');
      }

      try {
        const completedPayload = JSON.parse(completion.text);
        const next = completedPayload?.next_operation;
        if (next?.operation_id) {
          void serializeOperationDispatch(() =>
            dispatchOperationForController(tabId, controllerId, next)
          );
        }
      } catch (_) {}
      return true;
    } catch (error) {
      await bridgeFetch('/chat/failed', 'POST', {
        operation_id: String(operation.operation_id),
        controller_id: String(controllerId),
        failure_source: 'controller',
        error: 'PASI_NATIVE: reasoning selection failed: ' + String(error?.message || error).slice(0, 500)
      }, 10000);
      return false;
    }
  }

  await bridgeFetch('/chat/failed', 'POST', {
    operation_id: operation.operation_id,
    controller_id: controllerId,
    failure_source: 'controller',
    error: 'PASI_NATIVE: unsupported operation type: ' + String(operation.operation_type || '')
  }, 10000);
  return false;
}


async function dispatchNextOperationForController(tabId, controllerId) {
  if (typeof tabId !== 'number' || !controllerId) return false;
  return serializeOperationDispatch(async () => {
    if (cdpNetworkController?.isIdle && !cdpNetworkController.isIdle(tabId)) return false;
    const payload = await bridgeJson(
      '/next-operation?controller_id=' + encodeURIComponent(controllerId)
    );
    const operation = payload?.operation;
    if (!operation || !operation.operation_id) return false;
    return dispatchOperationForController(tabId, controllerId, operation);
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
function isChatGPTUrl(url) {
  return /^https:\/\/(?:www\.)?chatgpt\.com(?::\d+)?\//.test(String(url || ''));
}

function controllerIdForTab(tabId) {
  return 'cdp-tab:' + String(tabId);
}

async function reportWorkerHealth(tab) {
  const tabId = tab?.id;
  if (typeof tabId !== 'number' || !isChatGPTUrl(tab?.url)) return;
  const binding = cdpNetworkController?.currentBinding?.(tabId);
  await bridgeFetch('/browser/observation', 'POST', {
    observation: {
      schema_version: 'pasi-native-chromium-v2',
      captured_at: new Date().toISOString(),
      data: {
        kind: 'chatgpt_health',
        controller_version: 'cdp-worker-v1',
        chat_url: String(tab.url || ''),
        active_operation_id: binding?.operationId || null,
        page_visible: tab.active === true,
        native_controller: true,
        network_authority: true
      }
    }
  }, 5000);
}

async function attachAndDispatchTab(tab) {
  const tabId = tab?.id;
  if (typeof tabId !== 'number' || !isChatGPTUrl(tab?.url)) return false;
  if (!cdpNetworkController?.attachTab) return false;
  try {
    await cdpNetworkController.attachTab(tabId);
    await reportWorkerHealth(tab);
    return await dispatchNextOperationForController(tabId, controllerIdForTab(tabId));
  } catch (_) {
    return false;
  }
}

async function attachExistingChatTabs() {
  const tabs = await chrome.tabs.query({
    url: ['https://chatgpt.com/*', 'https://www.chatgpt.com/*']
  });
  const active = tabs.find((tab) => tab.active && typeof tab.id === 'number');
  if (active) {
    await attachAndDispatchTab(active);
    return;
  }
  for (const tab of tabs) {
    if (await attachAndDispatchTab(tab)) return;
  }
}

async function inspect() {
  const tabs = await chrome.tabs.query({
    active: true,
    lastFocusedWindow: true,
    url: ['https://chatgpt.com/*', 'https://www.chatgpt.com/*']
  });
  if (tabs[0]) {
    await attachAndDispatchTab(tabs[0]);
    return;
  }
  await attachExistingChatTabs();
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
  void attachExistingChatTabs();
});

chrome.runtime.onStartup.addListener(() => {
  void ensureWatchdogAlarm();
  void attachExistingChatTabs();
});

void ensureWatchdogAlarm();
void attachExistingChatTabs();

if (chrome.tabs?.onRemoved) {
  chrome.tabs.onRemoved.addListener((tabId) => {
    void cdpNetworkController?.detachTab?.(tabId);
  });
}
if (chrome.tabs?.onActivated) {
  chrome.tabs.onActivated.addListener((activeInfo) => {
    void chrome.tabs.get(activeInfo.tabId)
      .then((tab) => attachAndDispatchTab(tab))
      .catch(() => undefined);
  });
}
if (chrome.tabs?.onUpdated) {
  chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
    if (changeInfo?.status === 'complete' && isChatGPTUrl(tab?.url)) {
      void attachAndDispatchTab(tab);
    }
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
