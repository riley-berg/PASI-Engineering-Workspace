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
      if (!raw || raw === '[DONE]') continue;
      let parsed;
      try {
        parsed = JSON.parse(raw);
      } catch (_) {
        continue;
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
    const lines = String(responseText).split(/\r?\n/).map((line) => line.trim());
    return configured.some((marker) => lines.some((line) => line === marker || line.startsWith(marker + ':')));
  }

  function createController(options = {}) {
    const debuggerApi = options.debuggerApi || globalThis.chrome?.debugger;
    const onEvent = typeof options.onEvent === 'function' ? options.onEvent : () => {};
    const now = typeof options.now === 'function' ? options.now : Date.now;
    const stallMs = Number(options.stallMs) > 0 ? Number(options.stallMs) : DEFAULT_STALL_MS;
    const tabs = new Map();
    const requests = new Map();
    let installed = false;

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
      try {
        if (state.enabled) await sendCommand(tabId, 'Fetch.disable');
      } catch (_) {}
      try {
        await sendCommand(tabId, 'Accessibility.disable');
      } catch (_) {}
      try {
        if (typeof debuggerApi?.detach === 'function') {
          await new Promise((resolve) => {
            try { debuggerApi.detach({tabId}, () => resolve()); } catch (_) { resolve(); }
          });
        }
      } catch (_) {}
      tabs.delete(tabId);
      return true;
    }

    function currentBinding(tabId) {
      return tabs.get(tabId)?.binding || null;
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
        const semantic = /\\b(?:message|prompt|chat|ask)\\b/.test(name);
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
      return {
        backendNodeId,
        kind: 'accessibility_textbox',
        name: axName(best),
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
        throw new Error('CDP submit target contains unrelated draft text');
      }
      const insertedAt = now();
      if (!currentText) {
        await sendCommand(tabId, 'Input.insertText', {text: prompt});
      }
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
      return {
        submitted: true,
        operationId: state.binding.operationId,
        controllerId: state.binding.controllerId,
        submissionMethod: 'cdp_input',
        targetKind: target.kind,
        submittedAtMs: submittedAt,
        insertedAtMs: insertedAt,
        enterDispatchedAtMs: now()
      };
    }

    function bindOperation({tabId, operationId, controllerId, prompt, completionMarkers, chatUrl}) {
      return attachTab(tabId).then(() => {
        const state = tabs.get(tabId);
        if (!state) throw new Error('CDP tab attachment disappeared');
        state.binding = {
          operationId: String(operationId),
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
      const streamState = {responseText: '', assistantMessageId: null, terminal: null};

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
        await sendCommand(source.tabId, 'IO.close', {handle}).catch(() => undefined);
        const bodyBytes = concatBytes(byteChunks, totalBytes);
        await replayResponse(source, params, bodyBytes);
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
          const state = {responseText: '', assistantMessageId: null, terminal: null};
          parseSseText(new TextDecoder('utf-8').decode(bytes) + '\n', state);
          await replayResponse(source, params, bytes);
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
      if (method !== 'Fetch.requestPaused') return;
      const tabId = source?.tabId;
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
        } else if (completionMarkersSatisfied(state.responseText, correlation.binding.completionMarkers)) {
          emitLifecycle(correlation, {
            eventType: 'COMPLETED',
            reason: 'RESPONSE_STREAM_FINISHED',
            classification: 'success',
            telemetry: {bodyObserved: true},
            responseText: state.responseText,
            assistantMessageId: state.assistantMessageId
          });
        } else {
          emitLifecycle(correlation, {
            eventType: 'FAILED',
            reason: 'RESPONSE_MARKER_NOT_FOUND',
            classification: 'response_correlation_failure',
            telemetry: {bodyObserved: true},
            responseText: state.responseText,
            assistantMessageId: state.assistantMessageId
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
      handlePaused,
      currentBinding,
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
          }))
        };
      }
    };
  }

  const api = {
    createController,
    requestUrlIsGeneration,
    extractAssistantResponseText,
    parseSseText,
    classifyHttpStatus,
    classifyPayload,
    completionMarkersSatisfied,
    requestContainsPrompt
  };

  globalThis.PASI_CDP_NETWORK = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})();