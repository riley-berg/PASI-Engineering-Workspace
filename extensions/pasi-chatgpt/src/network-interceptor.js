(() => {
  'use strict';

  if (globalThis.__PASI_NETWORK_INTERCEPTOR__) return;

  const ENDPOINT_MARKERS = Object.freeze([
    '/backend-api/conversation',
    '/backend-api/f/conversation',
  ]);
  const ENDPOINT_MARKER = ENDPOINT_MARKERS[0];
  const DEFAULT_STALL_THRESHOLD_MS = 8000;
  const DEFAULT_FETCH_RECONCILE_INTERVAL_MS = 50;
  const MAX_RESPONSE_TEXT_CHARS = 120_000;
  const TERMINAL_EVENTS = new Set(['COMPLETED', 'INTERRUPTED', 'FAILED']);

  function requestDetails(input, init) {
    if (typeof input === 'string' || input instanceof URL) {
      return {
        url: String(input),
        method: String(init?.method || 'GET').toUpperCase(),
      };
    }
    if (input && typeof input === 'object') {
      return {
        url: String(input.url || ''),
        method: String(init?.method || input.method || 'GET').toUpperCase(),
      };
    }
    return {url: '', method: String(init?.method || 'GET').toUpperCase()};
  }

  function isGenerationRequest(input, init, endpointMarkers = ENDPOINT_MARKERS) {
    const request = requestDetails(input, init);
    if (request.method !== 'POST') return false;
    if (!request.url) return false;

    let pathname;
    try {
      pathname = new URL(request.url, 'https://chatgpt.com').pathname;
    } catch (_) {
      return false;
    }

    const normalizedPathname = pathname.replace(/\/$/, '');
    const markers = Array.isArray(endpointMarkers) ? endpointMarkers : [endpointMarkers];
    return markers.some(marker => normalizedPathname === String(marker).replace(/\/$/, ''));
  }

  function classifyHttpStatus(status) {
    const value = Number(status);
    if (value === 401 || value === 403) {
      return {
        eventType: 'FAILED',
        reason: 'AUTHENTICATION_EXPIRED',
        classification: 'auth_failure',
      };
    }
    if (value === 429) {
      return {
        eventType: 'INTERRUPTED',
        reason: 'USAGE_LIMIT_REACHED',
        classification: 'usage_limit',
      };
    }
    if (value >= 400) {
      return {
        eventType: 'FAILED',
        reason: 'HTTP_ERROR_STATUS',
        classification: 'provider_failure',
      };
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
      return {
        eventType: 'INTERRUPTED',
        reason: 'CONTEXT_EXHAUSTED',
        classification: 'context_exhaustion',
        telemetry: {providerErrorCode: error?.code || null},
      };
    }

    if (
      code === 'rate_limit_exceeded' ||
      status === 'exhausted' ||
      message.includes('rate limit') ||
      message.includes('usage limit')
    ) {
      return {
        eventType: 'INTERRUPTED',
        reason: 'USAGE_LIMIT_REACHED',
        classification: 'usage_limit',
        telemetry: {providerErrorCode: error?.code || null},
      };
    }

    return null;
  }

  function extractAssistantResponseText(payload) {
    const message = payload?.message || payload?.v?.message;
    const role = String(message?.author?.role || '').toLowerCase();
    if (role && role !== 'assistant') return null;

    const parts = message?.content?.parts;
    if (Array.isArray(parts)) {
      const textParts = parts
        .map(part => {
          if (typeof part === 'string') return part;
          if (part && typeof part === 'object' && typeof part.text === 'string') return part.text;
          return '';
        })
        .filter(Boolean);
      if (textParts.length) {
        return {
          mode: 'snapshot',
          text: textParts.join('').slice(0, MAX_RESPONSE_TEXT_CHARS),
        };
      }
    }

    const messageText =
      typeof message?.content?.text === 'string'
        ? message.content.text
        : typeof message?.content === 'string'
          ? message.content
          : null;
    if (messageText) {
      return {
        mode: 'snapshot',
        text: messageText.slice(0, MAX_RESPONSE_TEXT_CHARS),
      };
    }

    if (typeof payload?.delta === 'string' && payload.delta) {
      return {mode: 'delta', text: payload.delta};
    }
    if (typeof payload?.text === 'string' && payload.text) {
      return {mode: 'delta', text: payload.text};
    }

    if (
      typeof payload?.p === 'string' &&
      typeof payload?.v === 'string' &&
      /\/message\/content\/parts(?:\/0)?$/.test(payload.p)
    ) {
      return {mode: 'delta', text: payload.v};
    }

    return null;
  }

  function parseStreamLines(text, onData) {
    let buffer = String(text || '');
    let newlineIndex;
    while ((newlineIndex = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, newlineIndex).replace(/\r$/, '').trim();
      buffer = buffer.slice(newlineIndex + 1);
      if (!line.startsWith('data:')) continue;
      const raw = line.slice(5).trim();
      if (!raw) continue;
      onData(raw);
    }
    return buffer;
  }

  function createInterceptor(options = {}) {
    const target = options.target || globalThis;
    const endpointMarkers = Array.isArray(options.endpointMarkers)
      ? options.endpointMarkers.map(String)
      : options.endpointMarker
        ? [String(options.endpointMarker)]
        : [...ENDPOINT_MARKERS];
    const stallThresholdMs =
      Number.isFinite(options.stallThresholdMs) && options.stallThresholdMs > 0
        ? options.stallThresholdMs
        : DEFAULT_STALL_THRESHOLD_MS;
    const now = typeof options.now === 'function' ? options.now : Date.now;
    const setIntervalImpl = options.setIntervalImpl || ((fn, delay) => {
      const timer = setInterval(fn, delay);
      if (typeof timer?.unref === 'function') timer.unref();
      return timer;
    });
    const clearIntervalImpl = options.clearIntervalImpl || clearInterval;
    const randomId =
      typeof options.randomId === 'function'
        ? options.randomId
        : () => `REQ-${now()}-${Math.random().toString(16).slice(2)}`;
    const emitExternal = typeof options.emit === 'function' ? options.emit : () => {};
    const decoderFactory =
      typeof options.decoderFactory === 'function'
        ? options.decoderFactory
        : () => new TextDecoder('utf-8');

    const state = {
      installed: false,
      originalFetch: null,
      originalFetchDescriptor: null,
      fetchGetter: null,
      fetchSetter: null,
      fetchAccessorInstalled: false,
      fetchReconcileIntervalId: null,
      currentOperationId: null,
      pendingOperationClear: false,
      activeGeneration: null,
      requestCounter: 0,
      operationEventListener: null,
    };

    function emit(eventType, trackingState, payload = {}) {
      if (
        trackingState?.isTerminal &&
        TERMINAL_EVENTS.has(eventType)
      ) {
        return false;
      }
      if (trackingState && TERMINAL_EVENTS.has(eventType)) {
        trackingState.isTerminal = true;
      }

      emitExternal({
        eventType,
        eventId: randomId(),
        operationId: trackingState?.operationId ?? state.currentOperationId,
        requestId: trackingState?.requestId || null,
        timestamp: now(),
        ...payload,
        telemetry: payload.telemetry || {},
      });
      if (trackingState && TERMINAL_EVENTS.has(eventType) && state.activeGeneration === trackingState) {
        state.activeGeneration = null;
        if (state.pendingOperationClear) {
          state.currentOperationId = null;
          state.pendingOperationClear = false;
        }
      }
      return true;
    }

    function inspectPayload(raw, trackingState, responseState) {
      if (raw === '[DONE]') return;
      let parsed;
      try {
        parsed = JSON.parse(raw);
      } catch (_) {
        return;
      }

      const extracted = extractAssistantResponseText(parsed);
      if (extracted?.text) {
        if (extracted.mode === 'snapshot') {
          responseState.text = extracted.text;
        } else {
          responseState.text = (responseState.text + extracted.text).slice(0, MAX_RESPONSE_TEXT_CHARS);
        }
      }

      const classification = classifyPayload(parsed);
      if (classification) emit(classification.eventType, trackingState, classification);
    }

    async function observeResponse(response, trackingState) {
      if (!response?.body || typeof response.body.getReader !== 'function') {
        emit('COMPLETED', trackingState, {
          telemetry: {totalChunksProcessed: 0, bodyObserved: false},
        });
        return;
      }

      let intervalId = null;
      let lastChunkAt = now();
      let chunkCount = 0;
      let buffer = '';
      let stallReported = false;
      const responseState = {text: ''};

      try {
        const reader = response.body.getReader();
        const decoder = decoderFactory();

        const checkStall = () => {
          if (trackingState.isTerminal) {
            if (intervalId !== null) clearIntervalImpl(intervalId);
            return;
          }
          const elapsed = now() - lastChunkAt;
          if (elapsed >= stallThresholdMs && !stallReported) {
            stallReported = true;
            emit('STALL_DETECTED', trackingState, {
              reason: 'GENERATION_STALLED',
              classification: 'retryable_transport_signal',
              telemetry: {durationSinceLastChunk: elapsed},
            });
          }
        };

        intervalId = setIntervalImpl(
          checkStall,
          Math.max(250, Math.min(2000, Math.floor(stallThresholdMs / 4))),
        );

        while (true) {
          const result = await reader.read();

          if (result.done) {
            buffer += decoder.decode();
            parseStreamLines(buffer + '\n', raw => {
              if (!trackingState.isTerminal) inspectPayload(raw, trackingState, responseState);
            });
            if (!trackingState.isTerminal) {
              emit('COMPLETED', trackingState, {
                telemetry: {totalChunksProcessed: chunkCount},
                responseText: responseState.text.slice(0, MAX_RESPONSE_TEXT_CHARS),
              });
            }
            break;
          }

          lastChunkAt = now();
          chunkCount += 1;
          buffer += decoder.decode(result.value, {stream: true});
          buffer = parseStreamLines(buffer, raw => {
            if (!trackingState.isTerminal) inspectPayload(raw, trackingState, responseState);
          });
        }
      } catch (error) {
        if (!trackingState.isTerminal) {
          emit('INTERRUPTED', trackingState, {
            reason: 'NETWORK_STREAM_DISCONNECTED',
            classification: 'retryable_transport_failure',
            telemetry: {
              errorMessage: String(error?.message || error).slice(0, 300),
              totalChunksProcessed: chunkCount,
            },
          });
        }
      } finally {
        if (intervalId !== null) clearIntervalImpl(intervalId);
      }
    }

    async function interceptedFetch(...args) {
      if (!isGenerationRequest(args[0], args[1], endpointMarkers)) {
        return state.originalFetch.apply(this, args);
      }

      state.requestCounter += 1;
      const trackingState = {
        requestId: `REQ-${now()}-${state.requestCounter}`,
        operationId: state.currentOperationId,
        isTerminal: false,
        startTime: now(),
      };
      state.activeGeneration = trackingState;

      emit('STARTED', trackingState, {
        telemetry: {
          requestUri: requestDetails(args[0], args[1]).url,
        },
      });

      try {
        const response = await state.originalFetch.apply(this, args);
        const statusFailure = !response.ok
          ? classifyHttpStatus(response.status)
          : null;

        if (statusFailure) {
          emit(statusFailure.eventType, trackingState, {
            ...statusFailure,
            telemetry: {httpStatus: response.status},
          });
          return response;
        }

        let cloned;
        try {
          cloned = response.clone();
        } catch (error) {
          emit('FAILED', trackingState, {
            reason: 'RESPONSE_CLONE_FAILED',
            classification: 'retryable_transport_failure',
            telemetry: {
              errorMessage: String(error?.message || error).slice(0, 300),
            },
          });
          return response;
        }

        void observeResponse(cloned, trackingState);
        return response;
      } catch (error) {
        emit('FAILED', trackingState, {
          reason: 'TRANSPORT_ESTABLISHMENT_FAILED',
          classification: 'retryable_transport_failure',
          telemetry: {
            errorMessage: String(error?.message || error).slice(0, 300),
          },
        });
        throw error;
      }
    }

    function bindOperation(operationId) {
      const value = operationId == null || operationId === ''
        ? null
        : String(operationId);

      if (value === null) {
        if (state.activeGeneration && !state.activeGeneration.isTerminal) {
          state.pendingOperationClear = true;
          return state.currentOperationId;
        }
        state.currentOperationId = null;
        state.pendingOperationClear = false;
        return null;
      }

      state.currentOperationId = value;
      state.pendingOperationClear = false;
      return state.currentOperationId;
    }

    function health() {
      return {
        status: 'HEALTHY',
        timestamp: now(),
        installed: state.installed,
        trackingRequestId: state.activeGeneration?.requestId || null,
        currentOperationId: state.currentOperationId,
        fetchWrapped: state.installed && target.fetch === interceptedFetch,
        fetchAccessorInstalled: state.fetchAccessorInstalled,
        fetchFunctionName: typeof target.fetch === 'function' ? target.fetch.name : null,
      };
    }

    function installFetchAccessor() {
      if (!target || typeof target.fetch !== 'function') return false;

      const descriptor = Object.getOwnPropertyDescriptor(target, 'fetch');
      if (descriptor && !descriptor.configurable) return false;

      state.originalFetch = target.fetch;
      state.originalFetchDescriptor = descriptor || null;
      state.fetchGetter = () => interceptedFetch;
      state.fetchSetter = value => {
        state.originalFetch = value;
      };

      try {
        Object.defineProperty(target, 'fetch', {
          configurable: true,
          enumerable: descriptor ? descriptor.enumerable : true,
          get: state.fetchGetter,
          set: state.fetchSetter,
        });
        state.fetchAccessorInstalled = true;
        return true;
      } catch (_) {
        state.originalFetchDescriptor = null;
        state.fetchGetter = null;
        state.fetchSetter = null;
        return false;
      }
    }

    function reconcileFetch() {
      if (!state.installed || !target) return false;

      const descriptor = Object.getOwnPropertyDescriptor(target, 'fetch');
      if (
        state.fetchAccessorInstalled &&
        descriptor?.get === state.fetchGetter &&
        descriptor?.set === state.fetchSetter
      ) {
        return false;
      }

      const currentFetch = target.fetch;
      if (typeof currentFetch !== 'function') return false;

      state.originalFetch = currentFetch;
      state.originalFetchDescriptor = descriptor || null;
      try {
        Object.defineProperty(target, 'fetch', {
          configurable: true,
          enumerable: descriptor ? descriptor.enumerable : true,
          get: state.fetchGetter || (() => interceptedFetch),
          set: state.fetchSetter || (value => { state.originalFetch = value; }),
        });
        state.fetchAccessorInstalled = true;
        return true;
      } catch (_) {
        try {
          target.fetch = interceptedFetch;
          state.fetchAccessorInstalled = false;
          return true;
        } catch (_) {
          return false;
        }
      }
    }

    function install() {
      if (state.installed) return false;
      if (!target || typeof target.fetch !== 'function') {
        throw new Error('PASI network interceptor requires fetch');
      }

      state.installed = true;
      if (!installFetchAccessor()) {
        state.originalFetch = target.fetch;
        target.fetch = interceptedFetch;
      }
      state.fetchReconcileIntervalId = setIntervalImpl(
        reconcileFetch,
        DEFAULT_FETCH_RECONCILE_INTERVAL_MS,
      );
      return true;
    }

    function uninstall() {
      if (!state.installed) return false;

      if (state.fetchReconcileIntervalId !== null) {
        clearIntervalImpl(state.fetchReconcileIntervalId);
        state.fetchReconcileIntervalId = null;
      }

      const descriptor = Object.getOwnPropertyDescriptor(target, 'fetch');
      const ownsOurAccessor =
        state.fetchAccessorInstalled &&
        descriptor?.get === state.fetchGetter &&
        descriptor?.set === state.fetchSetter;

      try {
        if (ownsOurAccessor) {
          if (state.originalFetchDescriptor) {
            Object.defineProperty(target, 'fetch', state.originalFetchDescriptor);
          } else {
            delete target.fetch;
          }
        } else if (target.fetch === interceptedFetch) {
          target.fetch = state.originalFetch;
        }
      } catch (_) {}

      state.originalFetch = null;
      state.originalFetchDescriptor = null;
      state.fetchGetter = null;
      state.fetchSetter = null;
      state.fetchAccessorInstalled = false;
      state.installed = false;
      return true;
    }

    function bindOperationEventListener() {
      if (typeof target.addEventListener !== 'function' || state.operationEventListener) {
        return Boolean(state.operationEventListener);
      }

      state.operationEventListener = event => {
        let detail = event?.detail;
        if (typeof detail === 'string') {
          try {
            const parsed = JSON.parse(detail);
            if (parsed && typeof parsed === 'object' && parsed.operationId) {
              detail = parsed;
            }
          } catch (_) {}
        }
        const operationId =
          typeof detail === 'string' ? detail : detail?.operationId;
        bindOperation(operationId);
      };

      target.addEventListener('PASI_NETWORK_BIND_OPERATION', state.operationEventListener);
      return true;
    }

    return Object.freeze({
      install,
      uninstall,
      bindOperation,
      bindOperationEventListener,
      health,
      isGenerationRequest: (input, init) =>
        isGenerationRequest(input, init, endpointMarkers),
    });
  }

  const api = Object.freeze({
    DEFAULT_STALL_THRESHOLD_MS,
    MAX_RESPONSE_TEXT_CHARS,
    ENDPOINT_MARKER,
    ENDPOINT_MARKERS,
    createInterceptor,
    classifyHttpStatus,
    classifyPayload,
    extractAssistantResponseText,
    isGenerationRequest,
    parseStreamLines,
  });

  globalThis.PASI_NETWORK_INTERCEPTOR_API = api;

  const instance = createInterceptor({
    target: globalThis,
    emit: event => {
      globalThis.dispatchEvent(
        new CustomEvent('PASI_NETWORK_LIFECYCLE', {
          detail: JSON.stringify(event),
        }),
      );
    },
  });

  instance.bindOperationEventListener();
  instance.install();
  globalThis.__PASI_NETWORK_INTERCEPTOR__ = instance;
  globalThis.__PASI_NETWORK_INTERCEPTOR_HEALTH__ = () => instance.health();

  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})();
