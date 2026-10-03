(() => {
  'use strict';

  if (globalThis.PASI_CDP_NETWORK) return;

  const GENERATION_ENDPOINTS = Object.freeze([
    '/backend-api/conversation',
    '/backend-api/f/conversation'
  ]);
  const MAX_RESPONSE_TEXT_CHARS = 120_000;
  const MAX_REPLAY_BODY_BYTES = 8 * 1024 * 1024;
  const DEFAULT_STALL_MS = 8_000;
  const MAX_BROWSER_TEST_ITEMS = 100;
  const MAX_BROWSER_TEST_TEXT_CHARS = 4_000;
  const MAX_BROWSER_TEST_SCREENSHOT_BYTES = 8 * 1024 * 1024;

  function requestUrlIsGeneration(url) {
    try {
      const pathname = new URL(String(url || ''), 'https://chatgpt.com').pathname.replace(/\/$/, '');
      return GENERATION_ENDPOINTS.includes(pathname);
    } catch (_) {
      return false;
    }
  }

  function normalizePrompt(value) {
    return String(value || '').replace(/\s+/g, ' ').trim();
  }

  function extractAssistantResponseText(payload) {
    const message = payload?.message || payload?.v?.message;
    const role = String(message?.author?.role || '').toLowerCase();
    if (role && role !== 'assistant') return null;

    const parts = message?.content?.parts;
    if (Array.isArray(parts)) {
      const text = parts.map((part) => {
        if (typeof part === 'string') return part;
        if (part && typeof part === 'object' && typeof part.text === 'string') return part.text;
        return '';
      }).filter(Boolean).join('');
      if (text) {
        return {
          mode: 'snapshot',
          text: text.slice(0, MAX_RESPONSE_TEXT_CHARS),
          messageId: typeof message?.id === 'string' ? message.id : null
        };
      }
    }

    const contentText =
      typeof message?.content?.text === 'string' ? message.content.text :
      typeof message?.content === 'string' ? message.content :
      null;
    if (contentText) {
      return {
        mode: 'snapshot',
        text: contentText.slice(0, MAX_RESPONSE_TEXT_CHARS),
        messageId: typeof message?.id === 'string' ? message.id : null
      };
    }

    if (typeof payload?.delta === 'string' && payload.delta) {
      return {mode: 'delta', text: payload.delta, messageId: null};
    }
    if (typeof payload?.text === 'string' && payload.text) {
      return {mode: 'delta', text: payload.text, messageId: null};
    }
    if (typeof payload?.p === 'string' && typeof payload?.v === 'string') {
      if (!/\/message\/content\/parts(?:\/0)?$/.test(payload.p)) return null;
      return {
        mode: String(payload.o || '').toLowerCase() === 'replace' ? 'snapshot' : 'delta',
        text: payload.v.slice(0, MAX_RESPONSE_TEXT_CHARS),
        messageId: null
      };
    }
    if (typeof payload?.v === 'string' && !payload?.p) {
      return {mode: 'delta', text: payload.v.slice(0, MAX_RESPONSE_TEXT_CHARS), messageId: null};
    }
    if (String(payload?.o || '').toLowerCase() === 'patch' && Array.isArray(payload?.v)) {
      const items = payload.v.map(extractAssistantResponseText).filter(Boolean);
      if (items.length) {
        return {
          mode: items.some((item) => item.mode === 'snapshot') ? 'snapshot' : 'delta',
          text: items.map((item) => item.text).join('').slice(0, MAX_RESPONSE_TEXT_CHARS),
          messageId: items.find((item) => item.messageId)?.messageId || null
        };
      }
    }
    return null;
  }

  function authoritativeStreamComplete(state) {
    return Boolean(
      state &&
      state.doneMarkerSeen === true &&
      state.assistantCompletionVerified === true
    );
  }

  function extractAssistantCompletionState(payload) {
    const type = String(payload?.type || '').toLowerCase();
    if (
      type === 'message_stream_complete' ||
      payload?.message_stream_complete === true
    ) {
      return {
        finished: true,
        status: 'message_stream_complete',
        source: 'message_stream_complete'
      };
    }

    const path = String(payload?.p || '');
    if (/\/message\/status$/.test(path) && typeof payload?.v === 'string') {
      const status = payload.v.trim().toLowerCase();
      if (status === 'finished_successfully' || status === 'finished' || status === 'completed') {
        return {finished: true, status, source: 'message_status_patch'};
      }
    }

    const message = payload?.message || payload?.v?.message;
    const status = String(message?.status || '').trim().toLowerCase();
    if (status === 'finished_successfully' || status === 'finished' || status === 'completed') {
      return {finished: true, status, source: 'message_status'};
    }

    return {
      finished: false,
      status: status || null,
      source: null
    };
  }

  function classifyHttpStatus(status) {
    const value = Number(status);
    if (value === 401 || value === 403) {
      return {eventType: 'FAILED', reason: 'AUTHENTICATION_EXPIRED', classification: 'auth_failure'};
    }
    if (value === 429) {
      return {eventType: 'INTERRUPTED', reason: 'USAGE_LIMIT_REACHED', classification: 'usage_limit'};
    }
    if (value >= 400) {
      return {eventType: 'FAILED', reason: 'HTTP_ERROR_STATUS', classification: 'provider_failure'};
    }
    return null;
  }

  function classifyPayload(payload) {
    const error = payload?.error && typeof payload.error === 'object' ? payload.error : null;
    const code = String(error?.code || '').toLowerCase();
    const message = String(error?.message || payload?.message || '').toLowerCase();
    const status = String(payload?.status || '').toLowerCase();
    if (
      code === 'context_length_exceeded' ||
      message.includes('context length') ||
      message.includes('max token') ||
      message.includes('maximum context')
    ) {
      return {eventType: 'INTERRUPTED', reason: 'CONTEXT_EXHAUSTED', classification: 'context_exhaustion'};
    }
    if (
      code === 'rate_limit_exceeded' ||
      status === 'exhausted' ||
      message.includes('rate limit') ||
      message.includes('usage limit')
    ) {
      return {eventType: 'INTERRUPTED', reason: 'USAGE_LIMIT_REACHED', classification: 'usage_limit'};
    }
    return null;
  }

  function parseSseText(text, state) {
    let buffer = String(text || '');
    let newlineIndex;
    while ((newlineIndex = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, newlineIndex).replace(/\r$/, '').trim();
      buffer = buffer.slice(newlineIndex + 1);
      if (!line.startsWith('data:')) continue;
      const raw = line.slice(5).trim();
      if (!raw) continue;
      if (raw === '[DONE]') {
        state.doneMarkerSeen = true;
        continue;
      }
      let parsed;
      try {
        parsed = JSON.parse(raw);
      } catch (_) {
        continue;
      }
      const completion = extractAssistantCompletionState(parsed);
      if (completion.finished) {
        state.assistantCompletionVerified = true;
        state.assistantCompletionStatus = completion.status;
        state.assistantCompletionSource = completion.source;
      }

      const extracted = extractAssistantResponseText(parsed);
      if (extracted?.text) {
        if (
          extracted.mode === 'snapshot' &&
          extracted.messageId &&
          state.assistantMessageId &&
          extracted.messageId !== state.assistantMessageId
        ) {
          state.responseText = extracted.text;
        } else if (extracted.mode === 'snapshot') {
          state.responseText = extracted.text;
        } else {
          state.responseText = (state.responseText + extracted.text).slice(0, MAX_RESPONSE_TEXT_CHARS);
        }
        if (extracted.messageId) state.assistantMessageId = extracted.messageId;
      }
      const classification = classifyPayload(parsed);
      if (classification && !state.terminal) state.terminal = classification;
    }
    return buffer;
  }

  function decodeBase64(value) {
    const binary = atob(String(value || ''));
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
    return bytes;
  }

  function encodeBase64(bytes) {
    let binary = '';
    for (let index = 0; index < bytes.length; index += 0x8000) {
      binary += String.fromCharCode(...bytes.subarray(index, index + 0x8000));
    }
    return btoa(binary);
  }

  function concatBytes(chunks, total) {
    const output = new Uint8Array(total);
    let offset = 0;
    for (const chunk of chunks) {
      output.set(chunk, offset);
      offset += chunk.length;
    }
    return output;
  }

  function recursivelyContainsPrompt(value, expectedPrompt) {
    const needle = normalizePrompt(expectedPrompt);
    if (!needle) return false;
    if (typeof value === 'string') return normalizePrompt(value) === needle;
    if (Array.isArray(value)) return value.some((item) => recursivelyContainsPrompt(item, needle));
    if (value && typeof value === 'object') return Object.values(value).some((item) => recursivelyContainsPrompt(item, needle));
    return false;
  }

  function requestContainsPrompt(postData, expectedPrompt) {
    if (!expectedPrompt || !postData) return false;
    let parsed;
    try {
      parsed = JSON.parse(postData);
    } catch (_) {
      return normalizePrompt(postData).includes(normalizePrompt(expectedPrompt));
    }
    return recursivelyContainsPrompt(parsed, expectedPrompt);
  }

  function completionMarkersSatisfied(responseText, markers) {
    if (!String(responseText || '').trim()) return false;
    const configured = Array.isArray(markers)
      ? markers.filter((marker) => typeof marker === 'string' && marker.trim()).map((marker) => marker.trim())
      : [];
    if (!configured.length) return true;
    const lines = [];
    let inFence = false;
    for (const rawLine of String(responseText).split(/\r?\n/)) {
      const line = rawLine.trim();
      if (/^(?:```|~~~)/.test(line)) {
        inFence = !inFence;
        continue;
      }
      if (inFence || line.startsWith('>')) continue;
      lines.push(line);
    }
    return configured.some((marker) => lines.some((line) => line === marker || line.startsWith(marker + ':')));
  }

  function createController(options = {}) {
    const debuggerApi = options.debuggerApi || globalThis.chrome?.debugger;
    const onEvent = typeof options.onEvent === 'function' ? options.onEvent : () => {};
    const now = typeof options.now === 'function' ? options.now : Date.now;
    const stallMs = Number(options.stallMs) > 0 ? Number(options.stallMs) : DEFAULT_STALL_MS;
    const tabs = new Map();
    const requests = new Map();
    const consoleErrors = new Map();
    const networkEvents = new Map();
    const networkRequests = new Map();
    let installed = false;

    function pushBounded(map, tabId, value, limit = MAX_BROWSER_TEST_ITEMS) {
      const list = map.get(tabId) || [];
      list.push(value);
      if (list.length > limit) list.splice(0, list.length - limit);
      map.set(tabId, list);
      return value;
    }

    function sanitizeNetworkUrl(url) {
      try {
        const parsed = new URL(String(url || ''), 'https://chatgpt.com');
        return parsed.origin + parsed.pathname;
      } catch (_) {
        return String(url || '').slice(0, 2000);
      }
    }

    function safeConsoleArgument(argument) {
      if (argument == null) return null;
      if (Object.prototype.hasOwnProperty.call(argument, 'value')) {
        const value = argument.value;
        return typeof value === 'string'
          ? value.slice(0, MAX_BROWSER_TEST_TEXT_CHARS)
          : typeof value === 'number' || typeof value === 'boolean'
          ? value
          : argument.type || null;
      }
      return String(argument.description || argument.unserializableValue || argument.type || '').slice(0, MAX_BROWSER_TEST_TEXT_CHARS);
    }

    function recordConsoleEvent(tabId, method, params = {}) {
      if (!Number.isInteger(tabId)) return;
      if (method === 'Runtime.consoleAPICalled') {
        const type = String(params.type || '').toLowerCase();
        if (type !== 'error' && type !== 'assert') return;
        pushBounded(consoleErrors, tabId, {
          kind: 'console_error',
          type,
          timestamp: Number(params.timestamp || 0) || null,
          text: (Array.isArray(params.args) ? params.args : [])
            .slice(0, 8)
            .map(safeConsoleArgument)
            .filter((value) => value != null),
          stack_trace: params.stackTrace?.callFrames?.slice(0, 8).map((frame) => ({
            function_name: String(frame.functionName || '').slice(0, 200),
            url: sanitizeNetworkUrl(frame.url),
            line_number: Number(frame.lineNumber || 0),
            column_number: Number(frame.columnNumber || 0),
          })) || [],
        });
        return;
      }
      if (method === 'Runtime.exceptionThrown') {
        const details = params.exceptionDetails || {};
        const exception = details.exception || {};
        pushBounded(consoleErrors, tabId, {
          kind: 'uncaught_exception',
          timestamp: Number(details.timestamp || 0) || null,
          text: String(details.text || exception.description || exception.value || 'Uncaught exception')
            .slice(0, MAX_BROWSER_TEST_TEXT_CHARS),
          url: sanitizeNetworkUrl(details.url || ''),
          line_number: Number(details.lineNumber || 0),
          column_number: Number(details.columnNumber || 0),
          stack_trace: details.stackTrace?.callFrames?.slice(0, 8).map((frame) => ({
            function_name: String(frame.functionName || '').slice(0, 200),
            url: sanitizeNetworkUrl(frame.url),
            line_number: Number(frame.lineNumber || 0),
            column_number: Number(frame.columnNumber || 0),
          })) || [],
        });
      }
    }

    function recordNetworkEvent(tabId, method, params = {}) {
      if (!Number.isInteger(tabId)) return;
      if (method === 'Network.requestWillBeSent') {
        const requestId = String(params.requestId || '');
        if (!requestId) return;
        networkRequests.set(tabId + ':' + requestId, {
          url: sanitizeNetworkUrl(params.request?.url),
          method: String(params.request?.method || 'GET'),
          resource_type: String(params.type || ''),
          started_at: Number(params.timestamp || 0) || null,
        });
        pushBounded(networkEvents, tabId, {
          kind: 'request',
          request_id: requestId,
          method: String(params.request?.method || 'GET'),
          url: sanitizeNetworkUrl(params.request?.url),
          resource_type: String(params.type || ''),
          timestamp: Number(params.timestamp || 0) || null,
        });
        return;
      }
      if (method === 'Network.responseReceived') {
        const requestId = String(params.requestId || '');
        if (!requestId) return;
        const prior = networkRequests.get(tabId + ':' + requestId) || {};
        networkRequests.set(tabId + ':' + requestId, {...prior, response_url: sanitizeNetworkUrl(params.response?.url)});
        pushBounded(networkEvents, tabId, {
          kind: 'response',
          request_id: requestId,
          method: prior.method || null,
          url: sanitizeNetworkUrl(params.response?.url || prior.url),
          status: Number(params.response?.status || 0) || null,
          mime_type: String(params.response?.mimeType || ''),
          resource_type: prior.resource_type || String(params.type || ''),
          timestamp: Number(params.timestamp || 0) || null,
        });
        return;
      }
      if (method === 'Network.loadingFinished') {
        const requestId = String(params.requestId || '');
        if (!requestId) return;
        const prior = networkRequests.get(tabId + ':' + requestId) || {};
        pushBounded(networkEvents, tabId, {
          kind: 'finished',
          request_id: requestId,
          method: prior.method || null,
          url: prior.response_url || prior.url || null,
          resource_type: prior.resource_type || null,
          timestamp: Number(params.timestamp || 0) || null,
          encoded_data_length: Number(params.encodedDataLength || 0) || 0,
        });
        networkRequests.delete(tabId + ':' + requestId);
        return;
      }
      if (method === 'Network.loadingFailed') {
        const requestId = String(params.requestId || '');
        const prior = networkRequests.get(tabId + ':' + requestId) || {};
        pushBounded(networkEvents, tabId, {
          kind: 'failed',
          request_id: requestId,
          method: prior.method || null,
          url: prior.response_url || prior.url || null,
          resource_type: prior.resource_type || null,
          timestamp: Number(params.timestamp || 0) || null,
          error_text: String(params.errorText || 'Network request failed').slice(0, 500),
          canceled: params.canceled === true,
        });
        networkRequests.delete(tabId + ':' + requestId);
      }
    }

    function sendCommand(tabId, method, params = {}) {
      return new Promise((resolve, reject) => {
        if (!debuggerApi?.sendCommand) {
          reject(new Error('chrome.debugger.sendCommand is unavailable'));
          return;
        }
        let settled = false;
        try {
          debuggerApi.sendCommand({tabId}, method, params, (result) => {
            const runtimeError = globalThis.chrome?.runtime?.lastError;
            if (settled) return;
            settled = true;
            if (runtimeError) {
              reject(new Error(String(runtimeError.message || runtimeError)));
              return;
            }
            resolve(result || {});
          });
        } catch (error) {
          if (!settled) {
            settled = true;
            reject(error);
          }
        }
      });
    }

    async function attachTab(tabId) {
      if (!Number.isInteger(tabId)) throw new Error('tabId is required');
      let state = tabs.get(tabId);
      if (state?.enabled) return state;
      if (!state) {
        state = {tabId, enabled: false, attaching: null, binding: null};
        tabs.set(tabId, state);
      }
      if (state.attaching) return state.attaching;
      state.attaching = (async () => {
        try {
          if (typeof debuggerApi?.attach === 'function') {
            try {
              await new Promise((resolve, reject) => {
                debuggerApi.attach({tabId}, '1.3', () => {
                  const runtimeError = globalThis.chrome?.runtime?.lastError;
                  if (runtimeError) reject(new Error(String(runtimeError.message || runtimeError)));
                  else resolve();
                });
              });
            } catch (error) {
              if (!/already attached|another debugger/i.test(String(error?.message || error))) throw error;
            }
          }
          await sendCommand(tabId, 'Accessibility.enable');
          await sendCommand(tabId, 'Runtime.enable');
          await sendCommand(tabId, 'Network.enable');
          await sendCommand(tabId, 'Page.enable');
          await sendCommand(tabId, 'Fetch.enable', {
            patterns: GENERATION_ENDPOINTS.flatMap((urlPattern) => ([
              {urlPattern: '*' + urlPattern, requestStage: 'Request'},
              {urlPattern: '*' + urlPattern, requestStage: 'Response'}
            ])),
            handleAuthRequests: false
          });
          state.enabled = true;
          installed = true;
          return state;
        } finally {
          state.attaching = null;
        }
      })();
      return state.attaching;
    }

    async function detachTab(tabId) {
      const state = tabs.get(tabId);
      if (!state) return false;
      state.binding = null;
      for (const [requestId, request] of requests.entries()) {
        if (request.tabId === tabId) requests.delete(requestId);
      }
      for (const [requestId] of networkRequests.entries()) {
        if (requestId.startsWith(String(tabId) + ':')) networkRequests.delete(requestId);
      }
      consoleErrors.delete(tabId);
      networkEvents.delete(tabId);
      try {
        if (state.enabled) await sendCommand(tabId, 'Fetch.disable');
      } catch (_) {}
      try {
        await sendCommand(tabId, 'Accessibility.disable');
      } catch (_) {}
      try {
        await sendCommand(tabId, 'Runtime.disable');
      } catch (_) {}
      try {
        await sendCommand(tabId, 'Network.disable');
      } catch (_) {}
      try {
        await sendCommand(tabId, 'Page.disable');
      } catch (_) {}
      try {
        if (typeof debuggerApi?.detach === 'function') {
          await new Promise((resolve) => {
            try {
              debuggerApi.detach({tabId}, () => {
                // Consume Chrome's stale-tab cleanup error so ordinary tab
                // closure does not surface as an unchecked extension error.
                void globalThis.chrome?.runtime?.lastError;
                resolve();
              });
            } catch (_) {
              resolve();
            }
          });
        }
      } catch (_) {}
      tabs.delete(tabId);
      return true;
    }

    function currentBinding(tabId) {
      return tabs.get(tabId)?.binding || null;
    }

    function isIdle(tabId) {
      const state = tabs.get(tabId);
      for (const request of requests.values()) {
        if (request.tabId === tabId) return false;
      }
      return !state?.binding;
    }

    function axValue(value) {
      if (value == null) return '';
      if (typeof value === 'object' && Object.prototype.hasOwnProperty.call(value, 'value')) {
        return String(value.value ?? '');
      }
      return String(value);
    }

    function axProperty(node, name) {
      const properties = Array.isArray(node?.properties) ? node.properties : [];
      const property = properties.find((entry) => axValue(entry?.name).toLowerCase() === String(name).toLowerCase());
      return property ? property.value : null;
    }

    function axRole(node) {
      return axValue(node?.role).toLowerCase();
    }

    function axName(node) {
      return axValue(node?.name);
    }

    function axBooleanProperty(node, name) {
      const value = axProperty(node, name);
      if (typeof value === 'boolean') return value;
      const normalized = axValue(value).toLowerCase();
      if (normalized === 'true') return true;
      if (normalized === 'false') return false;
      return null;
    }

    function findComposerAXNode(nodes) {
      const candidates = Array.isArray(nodes) ? nodes : [];
      let best = null;
      let bestScore = -1;
      const editableRoles = new Set(['textbox', 'searchbox', 'combobox']);

      for (const node of candidates) {
        if (!node || node.ignored === true) continue;

        const role = axRole(node);
        const editable = axBooleanProperty(node, 'editable');
        const multiline = axBooleanProperty(node, 'multiline');
        const readonly = axBooleanProperty(node, 'readonly');
        if (readonly === true || editable === false) continue;

        // Chromium normally exposes ChatGPT's contenteditable composer as a
        // textbox, but newer layouts can expose the same control as a
        // searchbox/combobox or as a generic editable AX node. Editability
        // plus semantic context are stronger signals than the exact role.
        const name = axName(node).toLowerCase();
        const semantic = /\b(?:message|prompt|chat|ask)\b/.test(name);
        const focused = axBooleanProperty(node, 'focused') === true;
        const supportedRole = editableRoles.has(role);
        const genericEditable = editable === true || multiline === true;
        const editableLike = supportedRole || genericEditable;
        if (!editableLike) continue;

        const score =
          (focused ? 1000 : 0) +
          (multiline === true ? 250 : 0) +
          (editable === true ? 200 : 0) +
          (supportedRole ? 100 : 0) +
          (semantic ? 50 : 0);

        if (score > bestScore) {
          bestScore = score;
          best = node;
        }
      }

      if (!best) throw new Error('CDP submit target unavailable: NO_ACCESSIBLE_COMPOSER');
      const backendNodeId = Number(best.backendDOMNodeId);
      if (!Number.isInteger(backendNodeId) || backendNodeId <= 0) {
        throw new Error('CDP submit target unavailable: ACCESSIBLE_COMPOSER_HAS_NO_BACKEND_NODE');
      }
      const selectedRole = axRole(best);
      const selectedName = axName(best);
      const selectedSemantic = /\b(?:message|prompt|chat|ask)\b/i.test(selectedName.toLowerCase());
      return {
        backendNodeId,
        kind: 'accessibility_textbox',
        role: selectedRole,
        name: selectedName,
        semantic: selectedSemantic,
        currentText: axValue(best.value),
        focused: axBooleanProperty(best, 'focused') === true
      };
    }

    async function focusEditableTarget(tabId) {
      const timeoutMs = 8000;
      const retryMs = 200;
      const startedAt = now();
      let lastError = null;

      while (now() - startedAt <= timeoutMs) {
        try {
          const result = await sendCommand(tabId, 'Accessibility.getFullAXTree');
          const target = findComposerAXNode(result?.nodes);
          if (!target.focused) {
            await sendCommand(tabId, 'DOM.focus', {backendNodeId: target.backendNodeId});
          }
          return target;
        } catch (error) {
          lastError = error;
          const message = String(error?.message || error);
          if (
            !message.includes('NO_ACCESSIBLE_COMPOSER') &&
            !message.includes('ACCESSIBLE_COMPOSER_HAS_NO_BACKEND_NODE')
          ) {
            throw error;
          }
          await new Promise((resolve) => setTimeout(resolve, retryMs));
        }
      }

      throw lastError || new Error('CDP submit target unavailable: NO_ACCESSIBLE_COMPOSER');
    }

    function axSelected(node) {
      for (const name of ['selected', 'checked', 'pressed', 'current']) {
        const value = axBooleanProperty(node, name);
        if (value !== null) return value;
      }
      return null;
    }

    function reasoningNameIsThinking(name) {
      return /\b(?:thinking|think|medium|high|extra high|pro(?: standard| extended)?)\b/i.test(String(name || '')) ||
        /\bextended\b/i.test(String(name || ''));
    }

    function reasoningNameIsNonThinking(name) {
      return /\b(?:instant|auto(?:matic)?)\b/i.test(String(name || ''));
    }

    function findDirectReasoningAXNode(nodes) {
      const candidates = Array.isArray(nodes) ? nodes : [];
      const allowedRoles = new Set(['button', 'radio', 'menuitem', 'menuitemradio', 'option']);
      let best = null;
      let bestScore = -1;
      for (const node of candidates) {
        if (!node || node.ignored === true) continue;
        const role = axRole(node);
        if (!allowedRoles.has(role)) continue;
        const name = axName(node);
        if (!reasoningNameIsThinking(name)) continue;
        const state = axSelected(node);
        const score =
          (state === true ? 1000 : 0) +
          (role === 'radio' || role === 'menuitemradio' ? 100 : 0) +
          (/\bthinking\b/i.test(name) ? 50 : 0);
        if (score > bestScore) {
          bestScore = score;
          best = node;
        }
      }
      return best;
    }

    function findReasoningMenuAXNode(nodes) {
      const candidates = Array.isArray(nodes) ? nodes : [];
      const allowedRoles = new Set(['radio', 'menuitemradio', 'option', 'menuitem', 'button']);
      let best = null;
      let bestScore = -1;
      for (const node of candidates) {
        if (!node || node.ignored === true) continue;
        const role = axRole(node);
        if (!allowedRoles.has(role)) continue;
        const name = axName(node);
        if (!reasoningNameIsThinking(name)) continue;
        const score =
          (axSelected(node) === true ? 1000 : 0) +
          (role === 'radio' || role === 'menuitemradio' ? 100 : 0) +
          (/\b(?:thinking|medium|high)\b/i.test(name) ? 25 : 0);
        if (score > bestScore) {
          bestScore = score;
          best = node;
        }
      }
      return best;
    }

    function findModelPickerAXNode(nodes) {
      const candidates = Array.isArray(nodes) ? nodes : [];
      const allowedRoles = new Set(['button', 'combobox']);
      let best = null;
      let bestScore = -1;
      for (const node of candidates) {
        if (!node || node.ignored === true) continue;
        const role = axRole(node);
        if (!allowedRoles.has(role)) continue;
        const name = axName(node);
        if (!name || reasoningNameIsNonThinking(name)) continue;
        const score =
          (/\b(?:model|intelligence)\b/i.test(name) ? 500 : 0) +
          (/\b(?:gpt|o[0-9]|codex)\b/i.test(name) ? 200 : 0) +
          (role === 'combobox' ? 100 : 0);
        if (score <= 0) continue;
        if (score > bestScore) {
          bestScore = score;
          best = node;
        }
      }
      return best;
    }

    async function readAXTree(tabId) {
      const result = await sendCommand(tabId, 'Accessibility.getFullAXTree');
      return Array.isArray(result?.nodes) ? result.nodes : [];
    }

    async function activateAXNode(tabId, node) {
      const backendNodeId = Number(node?.backendDOMNodeId);
      if (!Number.isInteger(backendNodeId) || backendNodeId <= 0) {
        throw new Error('CDP reasoning control has no backend node');
      }
      await sendCommand(tabId, 'DOM.focus', {backendNodeId});
      for (const type of ['keyDown', 'keyUp']) {
        await sendCommand(tabId, 'Input.dispatchKeyEvent', {
          type,
          key: 'Enter',
          code: 'Enter',
          windowsVirtualKeyCode: 13,
          nativeVirtualKeyCode: 13
        });
      }
    }

    async function ensureReasoningMode(tabId, mode = 'thinking') {
      if (String(mode || '').trim().toLowerCase() !== 'thinking') {
        throw new Error('CDP reasoning mode supports only thinking');
      }
      await attachTab(tabId);

      const deadline = now() + 8000;
      let lastError = null;
      let openedPicker = false;

      while (now() < deadline) {
        try {
          const nodes = await readAXTree(tabId);

          const direct = findDirectReasoningAXNode(nodes);
          if (direct) {
            const selected = axSelected(direct);
            if (selected === true) return {enabled: true, control: axName(direct)};
            if (selected === false) {
              await activateAXNode(tabId, direct);
              await new Promise((resolve) => setTimeout(resolve, 150));
              const verify = findDirectReasoningAXNode(await readAXTree(tabId));
              if (verify && axSelected(verify) === true) {
                return {enabled: true, control: axName(verify)};
              }
            }
          }

          const picker = findModelPickerAXNode(nodes);
          if (picker && !openedPicker) {
            await activateAXNode(tabId, picker);
            openedPicker = true;
            await new Promise((resolve) => setTimeout(resolve, 150));
            continue;
          }

          const menuOption = findReasoningMenuAXNode(nodes);
          if (menuOption) {
            const selected = axSelected(menuOption);
            if (selected === true) return {enabled: true, control: axName(menuOption)};
            if (selected === false || selected === null) {
              await activateAXNode(tabId, menuOption);
              await new Promise((resolve) => setTimeout(resolve, 150));
              const verify = findDirectReasoningAXNode(await readAXTree(tabId)) ||
                findReasoningMenuAXNode(await readAXTree(tabId));
              if (verify && axSelected(verify) === true) {
                return {enabled: true, control: axName(verify)};
              }
            }
          }

          lastError = new Error('NO_VERIFIED_THINKING_CONTROL');
        } catch (error) {
          lastError = error;
        }
        await new Promise((resolve) => setTimeout(resolve, 200));
      }

      throw lastError || new Error('NO_VERIFIED_THINKING_CONTROL');
    }

    function findAXNodeByPattern(nodes, pattern, roles) {
      const candidates = Array.isArray(nodes) ? nodes : [];
      let best = null;
      let bestScore = -1;
      for (const node of candidates) {
        if (!node || node.ignored === true) continue;
        const role = axRole(node);
        if (roles && !roles.has(role)) continue;
        const name = axName(node);
        if (!pattern.test(name)) continue;
        const score = role === 'button' ? 100 : 50;
        if (score > bestScore) { bestScore = score; best = node; }
      }
      return best;
    }

    function findAXNodeByText(nodes, text, roles) {
      const needle = String(text || '').trim().toLowerCase();
      if (!needle) return null;
      const candidates = Array.isArray(nodes) ? nodes : [];
      let best = null;
      let bestScore = -1;
      for (const node of candidates) {
        if (!node || node.ignored === true) continue;
        const role = axRole(node);
        if (roles && !roles.has(role)) continue;
        const name = axName(node).toLowerCase();
        if (!name.includes(needle)) continue;
        const score = role === 'button' ? 100 : 50;
        if (score > bestScore) { bestScore = score; best = node; }
      }
      return best;
    }

    function findEditableAXNode(nodes) {
      const candidates = Array.isArray(nodes) ? nodes : [];
      const roles = new Set(['textbox', 'searchbox', 'combobox', 'generic']);
      for (const node of candidates) {
        if (!node || node.ignored === true) continue;
        const role = axRole(node);
        if (!roles.has(role)) continue;
        const editable = axBooleanProperty(node, 'editable');
        const multiline = axBooleanProperty(node, 'multiline');
        if (editable === true || role === 'searchbox' || role === 'combobox' || multiline === true) return node;
      }
      return null;
    }

    async function activateAXNode(tabId, node) {
      const backendNodeId = Number(node?.backendDOMNodeId);
      if (!Number.isInteger(backendNodeId) || backendNodeId <= 0) throw new Error('CDP UI control has no backend node');
      await sendCommand(tabId, 'DOM.focus', {backendNodeId});
      for (const type of ['keyDown', 'keyUp']) {
        await sendCommand(tabId, 'Input.dispatchKeyEvent', {
          type, key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13
        });
      }
    }

    async function fillAXNode(tabId, node, value) {
      const backendNodeId = Number(node?.backendDOMNodeId);
      if (!Number.isInteger(backendNodeId) || backendNodeId <= 0) throw new Error('CDP editable control has no backend node');
      await sendCommand(tabId, 'DOM.focus', {backendNodeId});
      await sendCommand(tabId, 'Input.insertText', {text: String(value)});
    }

    async function ensureGithubRepository(tabId, repository) {
      const target = String(repository || '').trim();
      if (!/^[^/\s]+\/[^/\s]+$/.test(target)) throw new Error('PASI_NATIVE: GitHub repository must be in owner/name form');
      await attachTab(tabId);
      const deadline = now() + 12000;
      let stage = 'plus';
      let lastError = null;
      while (now() < deadline) {
        try {
          const nodes = await readAXTree(tabId);
          if (stage === 'plus') {
            const plus = findAXNodeByPattern(nodes, /add files and more|add files|attach/i, new Set(['button', 'menuitem']));
            if (plus) { await activateAXNode(tabId, plus); stage = 'github'; continue; }
          }
          if (stage === 'github') {
            const github = findAXNodeByPattern(nodes, /github/i, new Set(['button', 'menuitem', 'option', 'link']));
            if (github) { await activateAXNode(tabId, github); stage = 'repository'; continue; }
          }
          if (stage === 'repository') {
            const editable = findEditableAXNode(nodes);
            if (editable) { await fillAXNode(tabId, editable, target); stage = 'select'; continue; }
          }
          if (stage === 'select') {
            const result = findAXNodeByText(nodes, target, new Set(['button', 'option', 'menuitem', 'link']));
            if (result) { await activateAXNode(tabId, result); return {attached: true, repository: target}; }
          }
          lastError = new Error('WAITING_FOR_GITHUB_' + stage.toUpperCase());
        } catch (error) { lastError = error; }
        await new Promise((resolve) => setTimeout(resolve, 200));
      }
      throw lastError || new Error('PASI_NATIVE: GitHub repository attachment could not be verified');
    }
    async function waitForSubmissionAcknowledgement(tabId, operationId, prompt, timeoutMs = 3000) {
      const deadline = now() + timeoutMs;
      const expected = String(prompt || '').trim();
      while (now() < deadline) {
        const activeRequest = [...requests.values()].find((request) =>
          request.tabId === tabId &&
          request.operationId === String(operationId)
        );
        if (activeRequest) {
          return {acknowledged: true, source: 'network_request'};
        }

        try {
          const nodes = await readAXTree(tabId);
          const target = findComposerAXNode(nodes);
          const currentText = String(target.currentText || '').trim();
          if (!currentText || currentText !== expected) {
            return {acknowledged: true, source: 'composer_state'};
          }
        } catch (error) {
          const message = String(error?.message || error);
          if (
            !message.includes('NO_ACCESSIBLE_COMPOSER') &&
            !message.includes('ACCESSIBLE_COMPOSER_HAS_NO_BACKEND_NODE')
          ) {
            throw error;
          }
        }
        await new Promise((resolve) => setTimeout(resolve, 100));
      }
      throw new Error('PASI_NATIVE: prompt submission not acknowledged');
    }

    function findSendButtonAXNode(nodes) {
      const candidates = Array.isArray(nodes) ? nodes : [];
      const roles = new Set(['button']);
      let best = null;
      let bestScore = -1;
      for (const node of candidates) {
        if (!node || node.ignored === true) continue;
        const role = axRole(node);
        if (!roles.has(role)) continue;
        const disabled = axBooleanProperty(node, 'disabled');
        if (disabled === true) continue;
        const name = axName(node);
        if (!/^(?:send|send message|send prompt|submit)$/i.test(name.trim())) continue;
        const score = /^send(?: prompt| message)?$/i.test(name.trim()) ? 100 : 50;
        if (score > bestScore) {
          bestScore = score;
          best = node;
        }
      }
      return best;
    }

    async function clickAXNode(tabId, node) {
      const backendNodeId = Number(node?.backendDOMNodeId);
      if (!Number.isInteger(backendNodeId) || backendNodeId <= 0) {
        throw new Error('CDP UI control has no backend node');
      }
      const model = await sendCommand(tabId, 'DOM.getBoxModel', {backendNodeId});
      const quad = model?.model?.border;
      if (!Array.isArray(quad) || quad.length < 8) {
        throw new Error('CDP UI control has no usable box model');
      }
      const values = quad.slice(0, 8).map(Number);
      if (!values.every(Number.isFinite)) {
        throw new Error('CDP UI control box model is invalid');
      }
      const x = (values[0] + values[2] + values[4] + values[6]) / 4;
      const y = (values[1] + values[3] + values[5] + values[7]) / 4;
      await sendCommand(tabId, 'Input.dispatchMouseEvent', {
        type: 'mouseMoved',
        x,
        y,
        button: 'none'
      });
      await sendCommand(tabId, 'Input.dispatchMouseEvent', {
        type: 'mousePressed',
        x,
        y,
        button: 'left',
        buttons: 1,
        clickCount: 1
      });
      await sendCommand(tabId, 'Input.dispatchMouseEvent', {
        type: 'mouseReleased',
        x,
        y,
        button: 'left',
        buttons: 0,
        clickCount: 1
      });
      return {x, y};
    }

    async function submitOperation(tabId, operationId, controllerId) {
      const state = tabs.get(tabId);
      if (!state?.binding) throw new Error('CDP submit requires an active operation binding');
      if (state.binding.operationId !== String(operationId)) {
        throw new Error('CDP submit operation mismatch');
      }
      if (state.binding.controllerId !== String(controllerId || '')) {
        throw new Error('CDP submit controller ownership conflict');
      }
      const prompt = String(state.binding.prompt || '');
      if (!prompt.trim()) throw new Error('CDP submit requires a non-empty prompt');

      const submittedAt = now();
      const target = await focusEditableTarget(tabId);
      const currentText = String(target.currentText || '').trim();
      if (currentText && currentText !== prompt.trim()) {
        throw new Error(
          'CDP submit target contains unrelated draft text: ' +
          JSON.stringify({
            role: target.role,
            name: target.name,
            semantic: target.semantic === true,
            focused: target.focused === true,
            current_text_length: currentText.length
          })
        );
      }
      const insertedAt = now();
      if (!currentText) {
        await sendCommand(tabId, 'Input.insertText', {text: prompt});
        await new Promise((resolve) => setTimeout(resolve, 100));
      }

      const nodes = await readAXTree(tabId);
      const sendButton = findSendButtonAXNode(nodes);
      if (sendButton) {
        try {
          await clickAXNode(tabId, sendButton);
          const acknowledgement = await waitForSubmissionAcknowledgement(
            tabId,
            state.binding.operationId,
            prompt,
            1500,
          );
          return {
            submitted: true,
            acknowledged: true,
            acknowledgementSource: acknowledgement.source,
            operationId: state.binding.operationId,
            controllerId: state.binding.controllerId,
            submissionMethod: 'cdp_accessibility_send_button_mouse',
            targetKind: target.kind,
            submittedAtMs: submittedAt,
            insertedAtMs: insertedAt,
            sendControlActivatedAtMs: now()
          };
        } catch (error) {
          // The AX send control existed, but its pointer path did not produce
          // a submission. Fall through to native keyboard submission.
        }
      }

      await sendCommand(tabId, 'DOM.focus', {backendNodeId: target.backendNodeId});
      await sendCommand(tabId, 'Input.dispatchKeyEvent', {
        type: 'keyDown',
        key: 'Enter',
        code: 'Enter',
        windowsVirtualKeyCode: 13,
        nativeVirtualKeyCode: 13
      });
      await sendCommand(tabId, 'Input.dispatchKeyEvent', {
        type: 'keyUp',
        key: 'Enter',
        code: 'Enter',
        windowsVirtualKeyCode: 13,
        nativeVirtualKeyCode: 13
      });

      try {
        const acknowledgement = await waitForSubmissionAcknowledgement(
          tabId,
          state.binding.operationId,
          prompt,
          3000,
        );
        return {
          submitted: true,
          acknowledged: true,
          acknowledgementSource: acknowledgement.source,
          operationId: state.binding.operationId,
          controllerId: state.binding.controllerId,
          submissionMethod: 'cdp_input',
          targetKind: target.kind,
          submittedAtMs: submittedAt,
          insertedAtMs: insertedAt,
          enterDispatchedAtMs: now()
        };
      } catch (error) {
        const detail = sendButton
          ? 'accessible send control was present but did not produce an acknowledged submission'
          : 'no accessible send control was exposed';
        throw new Error(
          'PASI_NATIVE: prompt submission not acknowledged after CDP input; ' + detail
        );
      }
    }

    function bindOperation({tabId, operationId, controllerId, prompt, completionMarkers, chatUrl}) {
      return attachTab(tabId).then(() => {
        const state = tabs.get(tabId);
        if (!state) throw new Error('CDP tab attachment disappeared');
        const nextOperationId = String(operationId);
        if (state.binding && state.binding.operationId !== nextOperationId) {
          throw new Error('PASI_NATIVE: CDP operation bind blocked by active operation');
        }
        for (const request of requests.values()) {
          if (request.tabId === tabId && request.operationId !== nextOperationId) {
            throw new Error('PASI_NATIVE: CDP operation bind blocked by active generation request');
          }
        }
        state.binding = {
          operationId: nextOperationId,
          controllerId: String(controllerId || ''),
          prompt: typeof prompt === 'string' ? prompt : '',
          completionMarkers: Array.isArray(completionMarkers) ? completionMarkers.slice(0, 4) : [],
          chatUrl: typeof chatUrl === 'string' ? chatUrl : null,
          boundAt: now()
        };
        return {
          bound: true,
          tabId,
          operationId: state.binding.operationId,
          controllerId: state.binding.controllerId
        };
      });
    }

    async function unbindOperation(tabId, operationId, controllerId) {
      const state = tabs.get(tabId);
      if (!state?.binding) return {bound: false};
      if (operationId != null && state.binding.operationId !== String(operationId)) return {bound: false, reason: 'different_operation'};
      if (controllerId != null && state.binding.controllerId && state.binding.controllerId !== String(controllerId)) return {bound: false, reason: 'different_controller'};
      state.binding = null;
      return {bound: true};
    }

    async function interruptOperation(tabId, operationId, controllerId, reason = 'NETWORK_STREAM_DISCONNECTED') {
      const state = tabs.get(tabId);
      if (!state?.binding) return {interrupted: 0, reason: 'no_binding'};
      if (state.binding.operationId !== String(operationId)) return {interrupted: 0, reason: 'different_operation'};
      if (controllerId != null && state.binding.controllerId && state.binding.controllerId !== String(controllerId)) {
        return {interrupted: 0, reason: 'different_controller'};
      }

      let interrupted = 0;
      for (const [requestId, correlation] of requests.entries()) {
        if (
          correlation.tabId !== tabId ||
          correlation.operationId !== String(operationId) ||
          correlation.controllerId !== state.binding.controllerId
        ) continue;

        correlation.interrupted = true;
        interrupted += 1;
        emitLifecycle(correlation, {
          eventType: 'FAILED',
          reason: String(reason || 'NETWORK_STREAM_DISCONNECTED').slice(0, 200),
          classification: 'retryable_transport_failure',
          telemetry: {controlledInterrupt: true}
        });
        await sendCommand(tabId, 'Fetch.failRequest', {
          requestId,
          errorReason: 'Aborted'
        }).catch(() => undefined);
        requests.delete(requestId);
      }
      return {interrupted, reason: interrupted ? null : 'no_active_request'};
    }

    async function replayResponse(source, params, bodyBytes) {
      const headers = Array.isArray(params.responseHeaders)
        ? params.responseHeaders.map((header) => ({name: String(header.name), value: String(header.value)}))
        : [];
      return sendCommand(source.tabId, 'Fetch.fulfillRequest', {
        requestId: params.requestId,
        responseCode: Number(params.responseStatusCode || 200),
        responsePhrase: params.responseStatusText || undefined,
        responseHeaders: headers,
        body: encodeBase64(bodyBytes)
      });
    }

    async function readStream(source, handle, correlation, params) {
      const byteChunks = [];
      let totalBytes = 0;
      let lastChunkAt = now();
      let timer = null;
      let textBuffer = '';
      const decoder = new TextDecoder('utf-8');
      const streamState = {
        responseText: '',
        assistantMessageId: null,
        terminal: null,
        streamComplete: false,
        doneMarkerSeen: false,
        assistantCompletionVerified: false,
        assistantCompletionStatus: null,
        assistantCompletionSource: null
      };

      const capture = (bytes) => {
        totalBytes += bytes.length;
        if (totalBytes > MAX_REPLAY_BODY_BYTES) throw new Error('CDP response body exceeded bound');
        byteChunks.push(bytes);
        textBuffer += decoder.decode(bytes, {stream: true});
        textBuffer = parseSseText(textBuffer, streamState);
        lastChunkAt = now();
      };

      timer = setInterval(() => {
        if (now() - lastChunkAt >= stallMs && !correlation.stallReported) {
          correlation.stallReported = true;
          emitLifecycle(correlation, {
            eventType: 'STALL_DETECTED',
            reason: 'GENERATION_STALLED',
            classification: 'retryable_transport_signal',
            telemetry: {durationSinceLastChunk: now() - lastChunkAt}
          });
        }
      }, Math.max(250, Math.min(2000, Math.floor(stallMs / 4))));

      try {
        let eof = false;
        while (!eof) {
          const result = await sendCommand(source.tabId, 'IO.read', {handle});
          eof = result.eof === true;
          const chunk = result.base64Encoded === true
            ? decodeBase64(result.data || '')
            : new TextEncoder().encode(result.data || '');
          if (chunk.length) capture(chunk);
          if (!result.data && !eof) await new Promise((resolve) => setTimeout(resolve, 0));
        }
        textBuffer += decoder.decode();
        if (textBuffer) parseSseText(textBuffer + '\n', streamState);
        streamState.streamComplete = authoritativeStreamComplete(streamState);
        await sendCommand(source.tabId, 'IO.close', {handle}).catch(() => undefined);
        const bodyBytes = concatBytes(byteChunks, totalBytes);
        if (streamState.streamComplete || streamState.terminal) {
          await replayResponse(source, params, bodyBytes);
        }
        return streamState;
      } catch (error) {
        try { await sendCommand(source.tabId, 'IO.close', {handle}); } catch (_) {}
        throw error;
      } finally {
        if (timer) clearInterval(timer);
      }
    }

    async function readResponseBody(source, params, correlation) {
      try {
        const result = await sendCommand(source.tabId, 'Fetch.takeResponseBodyAsStream', {requestId: params.requestId});
        if (result?.stream) return await readStream(source, result.stream, correlation, params);
      } catch (streamError) {
        try {
          const fallback = await sendCommand(source.tabId, 'Fetch.getResponseBody', {requestId: params.requestId});
          const bytes = fallback?.base64Encoded
            ? decodeBase64(fallback.body || '')
            : new TextEncoder().encode(fallback?.body || '');
          if (bytes.length > MAX_REPLAY_BODY_BYTES) throw new Error('Fallback response body exceeded bound');
          const state = {
            responseText: '',
            assistantMessageId: null,
            terminal: null,
            streamComplete: false,
            doneMarkerSeen: false,
            assistantCompletionVerified: false,
            assistantCompletionStatus: null,
            assistantCompletionSource: null
          };
          parseSseText(new TextDecoder('utf-8').decode(bytes) + '\n', state);
          state.streamComplete = authoritativeStreamComplete(state);
          if (state.streamComplete || state.terminal) {
            await replayResponse(source, params, bytes);
          }
          return state;
        } catch (fallbackError) {
          const combined = new Error('CDP response capture failed: ' + String(fallbackError?.message || fallbackError));
          combined.cause = streamError;
          throw combined;
        }
      }
      throw new Error('CDP did not return a response stream');
    }

    function emitLifecycle(correlation, extra = {}) {
      const binding = correlation.binding;
      const event = {
        source: 'cdp_fetch',
        eventType: extra.eventType || 'LIFECYCLE',
        eventId: 'cdp-' + now() + '-' + Math.random().toString(16).slice(2),
        operationId: correlation.operationId,
        controllerId: correlation.controllerId,
        requestId: correlation.requestId,
        tabId: correlation.tabId,
        requestUrl: correlation.requestUrl,
        timestamp: now(),
        ...extra
      };
      onEvent(event);
      return event;
    }

    async function handlePaused(source, method, params) {
      const tabId = source?.tabId;
      if (Number.isInteger(tabId)) {
        recordConsoleEvent(tabId, method, params);
        recordNetworkEvent(tabId, method, params);
      }
      if (method !== 'Fetch.requestPaused') return;
      if (!Number.isInteger(tabId) || !params?.requestId) return;
      const request = params.request || {};
      const generation = request.method === 'POST' && requestUrlIsGeneration(request.url);
      if (!generation) {
        try { await sendCommand(tabId, 'Fetch.continueRequest', {requestId: params.requestId}); } catch (_) {}
        return;
      }

      const isResponse = Number.isFinite(params.responseStatusCode);
      if (!isResponse) {
        const binding = currentBinding(tabId);
        try { await sendCommand(tabId, 'Fetch.continueRequest', {requestId: params.requestId}); } catch (_) {}
        if (!binding) return;

        const expectedPromptMatched = requestContainsPrompt(request.postData, binding.prompt);
        if (binding.prompt && !expectedPromptMatched) return;

        const existingCorrelation = [...requests.values()].find((entry) =>
          entry.tabId === tabId &&
          entry.operationId === binding.operationId
        );
        if (existingCorrelation) {
          return;
        }

        const correlation = {
          tabId,
          operationId: binding.operationId,
          controllerId: binding.controllerId,
          requestId: params.requestId,
          requestUrl: request.url,
          boundAt: binding.boundAt,
          requestStartedAt: now(),
          promptMatched: expectedPromptMatched || !binding.prompt,
          binding,
          stallReported: false
        };
        requests.set(params.requestId, correlation);
        emitLifecycle(correlation, {
          eventType: 'STARTED',
          telemetry: {promptMatched: correlation.promptMatched, requestUri: request.url}
        });
        return;
      }

      const correlation = requests.get(params.requestId);
      try {
        if (!correlation) {
          await sendCommand(tabId, 'Fetch.continueRequest', {requestId: params.requestId});
          return;
        }

        const statusFailure = classifyHttpStatus(params.responseStatusCode);
        if (statusFailure) {
          emitLifecycle(correlation, {...statusFailure, telemetry: {httpStatus: params.responseStatusCode}});
          await sendCommand(tabId, 'Fetch.continueResponse', {requestId: params.requestId})
            .catch(() => sendCommand(tabId, 'Fetch.continueRequest', {requestId: params.requestId}));
          return;
        }

        const state = await readResponseBody(source, params, correlation);
        if (state.terminal) {
          emitLifecycle(correlation, {
            ...state.terminal,
            telemetry: {bodyObserved: true},
            responseText: state.responseText,
            assistantMessageId: state.assistantMessageId
          });
        } else if (!state.streamComplete) {
          emitLifecycle(correlation, {
            eventType: 'FAILED',
            reason: 'NETWORK_RESPONSE_INCOMPLETE',
            classification: 'retryable_transport_failure',
            telemetry: {bodyObserved: true, streamComplete: false},
            responseText: state.responseText,
            assistantMessageId: state.assistantMessageId,
            streamComplete: false
          });
          await sendCommand(tabId, 'Fetch.failRequest', {
            requestId: params.requestId,
            errorReason: 'ConnectionAborted'
          }).catch(() => undefined);
        } else if (completionMarkersSatisfied(state.responseText, correlation.binding.completionMarkers)) {
          emitLifecycle(correlation, {
            eventType: 'COMPLETED',
            reason: 'RESPONSE_STREAM_FINISHED',
            classification: 'success',
            telemetry: {
              bodyObserved: true,
              streamComplete: true,
              completionSignal: state.assistantCompletionStatus,
              completionSignalSource: state.assistantCompletionSource
            },
            responseText: state.responseText,
            assistantMessageId: state.assistantMessageId,
            streamComplete: true
          });
        } else {
          emitLifecycle(correlation, {
            eventType: 'FAILED',
            reason: 'RESPONSE_MARKER_NOT_FOUND',
            classification: 'response_correlation_failure',
            telemetry: {bodyObserved: true, streamComplete: true},
            responseText: state.responseText,
            assistantMessageId: state.assistantMessageId,
            streamComplete: true
          });
        }
      } catch (error) {
        if (!correlation.interrupted) {
          emitLifecycle(correlation, {
            eventType: 'FAILED',
            reason: 'NETWORK_RESPONSE_CAPTURE_FAILED',
            classification: 'retryable_transport_failure',
            telemetry: {errorMessage: String(error?.message || error).slice(0, 500)}
          });
          await sendCommand(tabId, 'Fetch.failRequest', {requestId: params.requestId, errorReason: 'Failed'}).catch(() => undefined);
        }
      } finally {
        requests.delete(params.requestId);
      }
    }

    async function browserTestScreenshot(tabId) {
      await attachTab(tabId);
      const metrics = await sendCommand(tabId, 'Page.getLayoutMetrics');
      const shot = await sendCommand(tabId, 'Page.captureScreenshot', {
        format: 'png',
        fromSurface: true,
        captureBeyondViewport: false,
      });
      const base64 = String(shot?.data || '');
      const byteLength = Math.floor((base64.length * 3) / 4);
      if (!base64 || byteLength > MAX_BROWSER_TEST_SCREENSHOT_BYTES) {
        throw new Error('browser screenshot is empty or exceeds the bounded size');
      }
      const viewport = metrics?.visualViewport || metrics?.layoutViewport || {};
      return {
        kind: 'screenshot',
        tab_id: tabId,
        mime_type: 'image/png',
        width: Number(viewport.clientWidth || viewport.width || 1),
        height: Number(viewport.clientHeight || viewport.height || 1),
        byte_length: byteLength,
        image_base64: base64,
        captured_at: new Date().toISOString(),
      };
    }

    function domSnapshotAttributes(flatAttributes) {
      const attributes = {};
      const values = Array.isArray(flatAttributes) ? flatAttributes : [];
      for (let index = 0; index + 1 < values.length; index += 2) {
        const name = String(values[index] || '');
        const value = String(values[index + 1] || '');
        if (!name) continue;
        attributes[name] = value.slice(0, 1000);
      }
      return attributes;
    }

    function domSnapshotNodeMatches(nodeName, attributes, selector) {
      const query = String(selector || 'body').trim();
      if (!query || query === '*') return true;
      const tagMatch = query.match(/^[a-zA-Z][\w-]*/);
      let remainder = query;
      if (tagMatch) {
        if (String(nodeName || '').toLowerCase() !== tagMatch[0].toLowerCase()) return false;
        remainder = remainder.slice(tagMatch[0].length);
      }
      const idMatch = remainder.match(/#([\w-]+)/);
      if (idMatch && attributes.id !== idMatch[1]) return false;
      const classMatches = [...remainder.matchAll(/\.([\w-]+)/g)].map((match) => match[1]);
      if (classMatches.length) {
        const classes = new Set(String(attributes.class || '').split(/\s+/).filter(Boolean));
        if (classMatches.some((value) => !classes.has(value))) return false;
      }
      const attributeMatches = [...remainder.matchAll(/\[([a-zA-Z_:][-a-zA-Z0-9_:.]*)(?:\s*=\s*["']([^"']*)["'])?\]/g)];
      for (const match of attributeMatches) {
        const name = String(match[1] || '');
        if (!Object.prototype.hasOwnProperty.call(attributes, name)) return false;
        if (match[2] != null && attributes[name] !== match[2]) return false;
      }
      return true;
    }

    function domSnapshotText(nodeIndex, nodes, childrenByParent, maxTextChars) {
      const fragments = [];
      const stack = [{index: nodeIndex, depth: 0}];
      const visited = new Set();
      while (stack.length && fragments.join('').length < maxTextChars) {
        const current = stack.pop();
        if (!current || current.depth > 8 || visited.has(current.index)) continue;
        visited.add(current.index);
        const node = nodes[current.index];
        if (!node) continue;
        if (Number(node.nodeType) === 3 || String(node.nodeName || '').toLowerCase() === '#text') {
          if (typeof node.nodeValue === 'string' && node.nodeValue) fragments.push(node.nodeValue);
          continue;
        }
        const children = childrenByParent.get(current.index) || [];
        for (let index = Math.min(children.length, 64) - 1; index >= 0; index -= 1) {
          stack.push({index: children[index], depth: current.depth + 1});
        }
      }
      return fragments.join(' ').replace(/\s+/g, ' ').trim().slice(0, maxTextChars);
    }

    async function browserTestDom(tabId, params = {}) {
      await attachTab(tabId);
      const selector = typeof params.selector === 'string' && params.selector.trim()
        ? params.selector.trim()
        : 'body';
      const maxElements = Math.min(Math.max(Number(params.max_elements) || 50, 1), 100);
      const maxTextChars = Math.min(Math.max(Number(params.max_text_chars) || 500, 50), MAX_BROWSER_TEST_TEXT_CHARS);
      if (selector.length > 500) throw new Error('DOM selector exceeds bound');
      const snapshot = await sendCommand(tabId, 'DOMSnapshot.captureSnapshot', {
        computedStyles: [],
        includePaintOrder: false,
        includeTextColorOpacities: false,
      });
      const document = Array.isArray(snapshot?.documents) ? snapshot.documents[0] : null;
      const nodes = Array.isArray(document?.nodes?.nodeName)
        ? document.nodes.nodeName.map((nodeName, index) => ({
            nodeName,
            nodeType: Array.isArray(document.nodes.nodeType) ? document.nodes.nodeType[index] : null,
            nodeValue: Array.isArray(document.nodes.nodeValue) ? document.nodes.nodeValue[index] : '',
            parentIndex: Array.isArray(document.nodes.parentIndex) ? document.nodes.parentIndex[index] : -1,
            attributes: domSnapshotAttributes(
              Array.isArray(document.nodes.attributes) && Array.isArray(document.nodes.attributes[index])
                ? document.nodes.attributes[index]
                : []
            ),
          }))
        : [];
      const childrenByParent = new Map();
      for (let index = 0; index < nodes.length; index += 1) {
        const parent = Number(nodes[index]?.parentIndex);
        if (!Number.isInteger(parent) || parent < 0) continue;
        const children = childrenByParent.get(parent) || [];
        children.push(index);
        childrenByParent.set(parent, children);
      }
      const matched = [];
      const scanLimit = Math.min(nodes.length, 50_000);
      for (let index = 0; index < scanLimit; index += 1) {
        const node = nodes[index];
        if (!node || Number(node.nodeType) !== 1) continue;
        if (!domSnapshotNodeMatches(node.nodeName, node.attributes, selector)) continue;
        matched.push(index);
        if (matched.length >= maxElements) break;
      }
      const elements = matched.map((index) => {
        const node = nodes[index];
        const layoutIndex = Array.isArray(document?.layout?.nodeIndex)
          ? document.layout.nodeIndex.indexOf(index)
          : -1;
        const bounds = layoutIndex >= 0 && Array.isArray(document?.layout?.bounds)
          ? document.layout.bounds[layoutIndex] || null
          : null;
        return {
          tag: String(node.nodeName || '').toLowerCase(),
          id: node.attributes.id || '',
          class_name: node.attributes.class || '',
          role: node.attributes.role || null,
          aria_label: node.attributes['aria-label'] || null,
          title: node.attributes.title || null,
          test_id: node.attributes['data-testid'] || null,
          type: node.attributes.type || null,
          name: node.attributes.name || null,
          disabled: node.attributes.disabled != null || node.attributes['aria-disabled'] === 'true',
          selected: node.attributes.selected != null || node.attributes['aria-selected'] === 'true',
          visible: bounds ? Number(bounds[2]) > 0 && Number(bounds[3]) > 0 : null,
          text: domSnapshotText(index, nodes, childrenByParent, maxTextChars),
          bounds: Array.isArray(bounds) && bounds.length >= 4
            ? {
                x: Number(bounds[0]),
                y: Number(bounds[1]),
                width: Number(bounds[2]),
                height: Number(bounds[3]),
              }
            : null,
        };
      });
      return {
        kind: 'dom',
        tab_id: tabId,
        url: String(document?.frame?.url || ''),
        title: String(document?.frame?.name || ''),
        ready_state: null,
        selector,
        matched_count: matched.length,
        truncated: nodes.length > scanLimit || matched.length >= maxElements,
        elements,
        source: 'cdp_dom_snapshot',
      };
    }

    async function browserTestElementPoint(tabId, selector) {
      const query = String(selector || '').trim();
      if (!query || query.length > 500) throw new Error('selector is required and must be bounded');
      const documentResult = await sendCommand(tabId, 'DOM.getDocument', {depth: -1, pierce: true});
      const rootNodeId = Number(documentResult?.root?.nodeId || 0);
      if (!rootNodeId) throw new Error('DOM root is unavailable');
      const match = await sendCommand(tabId, 'DOM.querySelector', {
        nodeId: rootNodeId,
        selector: query,
      });
      const nodeId = Number(match?.nodeId || 0);
      if (!nodeId) throw new Error('No element matched selector: ' + query);
      const box = await sendCommand(tabId, 'DOM.getBoxModel', {nodeId});
      const border = Array.isArray(box?.model?.border) ? box.model.border : [];
      if (border.length < 8) throw new Error('Matched element has no visible box: ' + query);
      const x = (Number(border[0]) + Number(border[2]) + Number(border[4]) + Number(border[6])) / 4;
      const y = (Number(border[1]) + Number(border[3]) + Number(border[5]) + Number(border[7])) / 4;
      return {nodeId, x, y};
    }

    async function browserTestClick(tabId, params = {}) {
      const target = await browserTestElementPoint(tabId, params.selector);
      await sendCommand(tabId, 'Input.dispatchMouseEvent', {
        type: 'mouseMoved',
        x: target.x,
        y: target.y,
        button: 'none',
      });
      await sendCommand(tabId, 'Input.dispatchMouseEvent', {
        type: 'mousePressed',
        x: target.x,
        y: target.y,
        button: 'left',
        clickCount: 1,
      });
      await sendCommand(tabId, 'Input.dispatchMouseEvent', {
        type: 'mouseReleased',
        x: target.x,
        y: target.y,
        button: 'left',
        clickCount: 1,
      });
      return {
        kind: 'interaction',
        action: 'click',
        tab_id: tabId,
        selector: String(params.selector || '').trim(),
        coordinates: {x: target.x, y: target.y},
        success: true,
      };
    }

    async function browserTestFill(tabId, params = {}) {
      const selector = String(params.selector || '').trim();
      const value = typeof params.value === 'string' ? params.value : '';
      if (!selector || selector.length > 500) throw new Error('selector is required and must be bounded');
      if (value.length > 20_000) throw new Error('fill value exceeds bound');
      const target = await browserTestElementPoint(tabId, selector);
      await sendCommand(tabId, 'DOM.focus', {backendNodeId: target.nodeId});
      await sendCommand(tabId, 'Input.dispatchKeyEvent', {
        type: 'keyDown',
        key: 'Control',
        code: 'ControlLeft',
        modifiers: 2,
      });
      await sendCommand(tabId, 'Input.dispatchKeyEvent', {
        type: 'keyDown',
        key: 'a',
        code: 'KeyA',
        modifiers: 2,
      });
      await sendCommand(tabId, 'Input.dispatchKeyEvent', {
        type: 'keyUp',
        key: 'a',
        code: 'KeyA',
        modifiers: 2,
      });
      await sendCommand(tabId, 'Input.dispatchKeyEvent', {
        type: 'keyUp',
        key: 'Control',
        code: 'ControlLeft',
        modifiers: 0,
      });
      await sendCommand(tabId, 'Input.insertText', {text: value});
      return {
        kind: 'interaction',
        action: 'fill',
        tab_id: tabId,
        selector,
        value_length: value.length,
        success: true,
      };
    }

    async function browserTestPressKey(tabId, params = {}) {
      const key = String(params.key || '').trim();
      const allowedNamed = new Set([
        'Enter', 'Escape', 'Tab', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight',
        'Home', 'End', 'PageUp', 'PageDown', 'Backspace', 'Delete', 'Space'
      ]);
      if (!key || key.length > 30 || (key.length !== 1 && !allowedNamed.has(key))) {
        throw new Error('key must be one printable character or a supported named key');
      }
      const modifiers = Math.max(0, Math.min(15, Number(params.modifiers) || 0));
      await sendCommand(tabId, 'Input.dispatchKeyEvent', {
        type: 'keyDown',
        key,
        code: key.length === 1 ? '' : key,
        modifiers,
      });
      await sendCommand(tabId, 'Input.dispatchKeyEvent', {
        type: 'keyUp',
        key,
        code: key.length === 1 ? '' : key,
        modifiers,
      });
      return {
        kind: 'interaction',
        action: 'press_key',
        tab_id: tabId,
        key,
        modifiers,
        success: true,
      };
    }

    async function browserTestScroll(tabId, params = {}) {
      const deltaX = Math.max(-5000, Math.min(5000, Number(params.delta_x) || 0));
      const deltaY = Math.max(-5000, Math.min(5000, Number(params.delta_y) || 0));
      const metrics = await sendCommand(tabId, 'Page.getLayoutMetrics');
      const viewport = metrics?.visualViewport || metrics?.layoutViewport || {};
      const x = Math.max(1, Number(viewport.clientWidth || viewport.width || 1) / 2);
      const y = Math.max(1, Number(viewport.clientHeight || viewport.height || 1) / 2);
      await sendCommand(tabId, 'Input.dispatchMouseEvent', {
        type: 'mouseWheel',
        x,
        y,
        deltaX,
        deltaY,
      });
      return {
        kind: 'interaction',
        action: 'scroll',
        tab_id: tabId,
        delta_x: deltaX,
        delta_y: deltaY,
        success: true,
      };
    }

    function browserTestConsoleErrors(tabId, params = {}) {
      const limit = Math.min(Math.max(Number(params.limit) || 50, 1), 100);
      const errors = (consoleErrors.get(tabId) || []).slice(-limit);
      return {
        kind: 'console',
        tab_id: tabId,
        errors,
        count: errors.length,
      };
    }

    function browserTestNetwork(tabId, params = {}) {
      const limit = Math.min(Math.max(Number(params.limit) || 50, 1), 100);
      const urlContains = typeof params.url_contains === 'string' ? params.url_contains : '';
      const resourceTypes = new Set(
        Array.isArray(params.resource_types) ? params.resource_types.map((value) => String(value)) : []
      );
      const all = networkEvents.get(tabId) || [];
      const filtered = all.filter((event) => {
        if (urlContains && !String(event.url || '').includes(urlContains)) return false;
        if (resourceTypes.size && !resourceTypes.has(String(event.resource_type || ''))) return false;
        return true;
      });
      return {
        kind: 'network',
        tab_id: tabId,
        events: filtered.slice(-limit),
        count: Math.min(filtered.length, limit),
        captured_since: all[0]?.timestamp || null,
      };
    }

    async function runBrowserTest(tabId, action, params = {}) {
      if (!Number.isInteger(tabId) || tabId < 1) throw new Error('tabId is required');
      await attachTab(tabId);
      const normalized = String(action || '').trim();
      if (normalized === 'screenshot') return browserTestScreenshot(tabId);
      if (normalized === 'dom') return browserTestDom(tabId, params);
      if (normalized === 'console_errors') return browserTestConsoleErrors(tabId, params);
      if (normalized === 'network') return browserTestNetwork(tabId, params);
      if (normalized === 'click') return browserTestClick(tabId, params);
      if (normalized === 'fill') return browserTestFill(tabId, params);
      if (normalized === 'press_key') return browserTestPressKey(tabId, params);
      if (normalized === 'scroll') return browserTestScroll(tabId, params);
      throw new Error('Unsupported browser-test action');
    }

    function install() {
      if (installed) return false;
      if (!debuggerApi?.onEvent?.addListener) throw new Error('chrome.debugger.onEvent is unavailable');
      debuggerApi.onEvent.addListener(handlePaused);
      if (debuggerApi.onDetach?.addListener) {
        debuggerApi.onDetach.addListener((source, reason) => {
          const tabId = source?.tabId;
          if (!Number.isInteger(tabId)) return;
          const state = tabs.get(tabId);
          if (!state?.binding) return;
          emitLifecycle({
            tabId,
            operationId: state.binding.operationId,
            controllerId: state.binding.controllerId,
            requestId: null,
            requestUrl: null,
            binding: state.binding
          }, {
            eventType: 'FAILED',
            reason: 'CDP_DEBUGGER_DETACHED',
            classification: 'retryable_transport_failure',
            telemetry: {detachReason: String(reason || '')}
          });
          tabs.delete(tabId);
          for (const [requestId, request] of requests.entries()) {
            if (request.tabId === tabId) requests.delete(requestId);
          }
          consoleErrors.delete(tabId);
          networkEvents.delete(tabId);
          for (const [requestId] of networkRequests.entries()) {
            if (requestId.startsWith(String(tabId) + ':')) networkRequests.delete(requestId);
          }
        });
      }
      installed = true;
      return true;
    }

    return {
      install,
      attachTab,
      detachTab,
      bindOperation,
      submitOperation,
      unbindOperation,
      interruptOperation,
      ensureReasoningMode,
      ensureGithubRepository,
      handlePaused,
      runBrowserTest,
      findComposerAXNode,
      currentBinding,
      isIdle,
      health() {
        return {
          status: 'HEALTHY',
          installed,
          attachedTabs: [...tabs.entries()].map(([tabId, state]) => ({
            tabId,
            enabled: state.enabled,
            operationId: state.binding?.operationId || null,
            controllerId: state.binding?.controllerId || null
          })),
          activeRequests: [...requests.values()].map((request) => ({
            tabId: request.tabId,
            requestId: request.requestId,
            operationId: request.operationId,
            controllerId: request.controllerId,
            assistantMessageId: request.assistantMessageId || null
          })),
          browserTesting: [...tabs.keys()].map((tabId) => ({
            tabId,
            consoleErrorCount: (consoleErrors.get(tabId) || []).length,
            networkEventCount: (networkEvents.get(tabId) || []).length
          }))
        };
      }
    };
  }

  const api = {
    createController,
    requestUrlIsGeneration,
    extractAssistantResponseText,
    extractAssistantCompletionState,
    authoritativeStreamComplete,
    parseSseText,
    classifyHttpStatus,
    classifyPayload,
    completionMarkersSatisfied,
    requestContainsPrompt
  };

  globalThis.PASI_CDP_NETWORK = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})();