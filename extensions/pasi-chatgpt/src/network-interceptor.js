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
      : [String(options.endpointMarker || ENDPOINT_MARKER)];
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
      fetchReconcileIntervalId: null,
      currentOperationId: null,
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
        operationId: state.currentOperationId,
        requestId: trackingState?.requestId || null,
        timestamp: now(),
        ...payload,
        telemetry: payload.telemetry || {},
      });
      return true;
    }

    function inspectPayload(raw, trackingState) {
      if (raw === '[DONE]') return;
      let parsed;
      try {
        parsed = JSON.parse(raw);
      } catch (_) {
        return;
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

      try {
        const reader = response.body.getReader();
        const decoder = decoderFactory();

        const checkStall = () => {
          if (trackingState.isTerminal) {
            if (intervalId !== null) clearIntervalImpl(intervalId);
            return;
          }
          const elapsed = now() - lastChunkAt;
          if (elapsed >= stallThresholdMs) {
            emit('INTERRUPTED', trackingState, {
              reason: 'GENERATION_STALLED',
              classification: 'retryable_transport_failure',
              telemetry: {durationSinceLastChunk: elapsed},
            });
            if (intervalId !== null) clearIntervalImpl(intervalId);
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
              if (!trackingState.isTerminal) inspectPayload(raw, trackingState);
            });
            if (!trackingState.isTerminal) {
              emit('COMPLETED', trackingState, {
                telemetry: {totalChunksProcessed: chunkCount},
              });
            }
            break;
          }

          lastChunkAt = now();
          chunkCount += 1;
          buffer += decoder.decode(result.value, {stream: true});
          buffer = parseStreamLines(buffer, raw => {
            if (!trackingState.isTerminal) inspectPayload(raw, trackingState);
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
      state.currentOperationId =
        operationId == null || operationId === '' ? null : String(operationId);
      return state.currentOperationId;
    }

    function health() {
      return {
        status: 'HEALTHY',
        timestamp: now(),
        installed: state.installed,
        trackingRequestId: state.activeGeneration?.requestId || null,
        currentOperationId: state.currentOperationId,
      };
    }

    function reconcileFetch() {
      if (!state.installed || !target) return false;
      const currentFetch = target.fetch;
      if (currentFetch === interceptedFetch) return false;
      if (typeof currentFetch !== 'function') return false;

      state.originalFetch = currentFetch;
      try {
        target.fetch = interceptedFetch;
        return true;
      } catch (_) {
        return false;
      }
    }

    function install() {
      if (state.installed) return false;
      if (!target || typeof target.fetch !== 'function') {
        throw new Error('PASI network interceptor requires fetch');
      }

      state.originalFetch = target.fetch;
      target.fetch = interceptedFetch;
      state.installed = true;
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

      target.fetch = state.originalFetch;
      state.originalFetch = null;
      state.installed = false;
      return true;
    }

    function bindOperationEventListener() {
      if (typeof target.addEventListener !== 'function' || state.operationEventListener) {
        return Boolean(state.operationEventListener);
      }

      state.operationEventListener = event => {
        const detail = event?.detail;
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
    ENDPOINT_MARKER,
    ENDPOINT_MARKERS,
    createInterceptor,
    classifyHttpStatus,
    classifyPayload,
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
