(() => {
  "use strict";

  if (globalThis.__PASI_NETWORK_INTERCEPTOR_STARTED__ === true) return;
  globalThis.__PASI_NETWORK_INTERCEPTOR_STARTED__ = true;

  const VERSION = "pasi-network-v1";
  const SOURCE = "pasi-network-interceptor";
  const MAX_RESPONSE_CHARS = 120000;
  const MAX_DIAGNOSTIC_CHARS = 32000;
  const STALL_MS = 30000;

  let sequence = 0;
  let armed = null;
  let originalFetch = null;
  let originalXhrOpen = null;
  let originalXhrSend = null;

  function now() {
    return Date.now();
  }

  function safeString(value, limit = MAX_DIAGNOSTIC_CHARS) {
    return String(value == null ? "" : value).slice(0, limit);
  }

  function post(kind, payload = {}) {
    sequence += 1;
    window.postMessage({
      source: SOURCE,
      version: VERSION,
      kind,
      sequence,
      captured_at: new Date().toISOString(),
      ...payload
    }, location.origin);
  }

  function urlOf(input) {
    try {
      if (typeof input === "string") return new URL(input, location.href);
      if (input && typeof input.url === "string") return new URL(input.url, location.href);
    } catch (_) {}
    return null;
  }

  function isChatGPTUrl(url) {
    return Boolean(url && /^(?:www\.)?chatgpt\.com$/i.test(url.host));
  }

  function likelyGenerationPath(url) {
    if (!isChatGPTUrl(url)) return false;
    const path = String(url.pathname || "");
    return (
      /\/backend-api\/f\/conversation(?:\/|$)/i.test(path) ||
      /\/backend-api\/conversation$/i.test(path) ||
      /\/backend-api\/responses(?:\/|$)/i.test(path) ||
      /\/backend-api\/codex\/responses(?:\/|$)/i.test(path) ||
      /\/backend-api\/tasks\/[^/]+\/stream(?:\/|$)/i.test(path)
    );
  }

  function likelyBackendPath(url) {
    return Boolean(
      isChatGPTUrl(url) &&
      /\/backend-(?:api|anon)\//i.test(String(url.pathname || ""))
    );
  }

  function isPost(initOrMethod) {
    if (typeof initOrMethod === "string") return initOrMethod.toUpperCase() === "POST";
    return String(initOrMethod?.method || "GET").toUpperCase() === "POST";
  }

  function classifyRequest(input, init) {
    const url = urlOf(input);
    return {
      url,
      likely_generation: likelyGenerationPath(url),
      likely_backend: likelyBackendPath(url),
      method_post: isPost(init)
    };
  }

  function contextExhaustedText(text) {
    const value = safeString(text, MAX_DIAGNOSTIC_CHARS).toLowerCase();
    return [
      "this conversation has reached its limit",
      "this conversation has reached the maximum",
      "conversation has reached its maximum length",
      "conversation has reached its limit",
      "maximum conversation length",
      "conversation is too long",
      "this chat is too long",
      "start a new chat to continue",
      "continue in a new chat",
      "new chat to continue",
      "conversation context is exhausted"
    ].some((marker) => value.includes(marker));
  }

  function usageLimitedText(text) {
    const value = safeString(text, MAX_DIAGNOSTIC_CHARS).toLowerCase();
    return [
      "usage limit reached",
      "you've reached your limit",
      "you’ve reached your limit",
      "rate limit",
      "too many requests",
      "try again later because you have reached"
    ].some((marker) => value.includes(marker));
  }

  function authRequiredText(text) {
    const value = safeString(text, MAX_DIAGNOSTIC_CHARS).toLowerCase();
    return [
      "authentication required",
      "please log in",
      "please sign in",
      "captcha required",
      "cloudflare",
      "turnstile",
      "log in to continue",
      "sign in to continue"
    ].some((marker) => value.includes(marker));
  }

  function classifyFailure(text, status = 0) {
    const value = safeString(text);
    if (contextExhaustedText(value)) return "context_exhausted";
    if (usageLimitedText(value)) return "usage_limited";
    if (authRequiredText(value)) return "auth_required";
    if (Number(status) >= 500) return "provider_error";
    if (Number(status) >= 400) return "provider_error";
    return "unknown_failure";
  }

  function appendRolling(state, text) {
    if (!text) return;
    state.responseText = (state.responseText + text).slice(-MAX_RESPONSE_CHARS);
  }

  function appendDiagnostic(state, text) {
    if (!text) return;
    state.diagnostic = (state.diagnostic + text).slice(-MAX_DIAGNOSTIC_CHARS);
  }

  function setResponseText(state, text) {
    const value = String(text || "");
    if (!value) return;
    state.responseText = value.slice(-MAX_RESPONSE_CHARS);
  }

  function applyPatch(state, patch) {
    if (!patch || typeof patch !== "object") return;
    const path = String(patch.p || "");
    const op = String(patch.o || "");
    const value = patch.v;

    if (/\/message\/content\/parts\/0$/.test(path) || /\/content\/parts\/0$/.test(path)) {
      if (typeof value === "string") {
        if (op === "append") appendRolling(state, value);
        else if (op === "replace") setResponseText(state, value);
      }
      return;
    }

    if (/\/message\/status$/.test(path) && typeof value === "string") {
      if (value === "finished_successfully") state.doneMarker = true;
      return;
    }

    if (path === "/message" && value && typeof value === "object") {
      const role = value?.author?.role;
      const parts = value?.content?.parts;
      if (role === "assistant" && Array.isArray(parts) && typeof parts[0] === "string") {
        state.assistantSeen = true;
        setResponseText(state, parts[0]);
      }
      return;
    }

    if (typeof value === "string" && op === "append" && state.assistantSeen) {
      appendRolling(state, value);
    }
  }

  function inspectPayload(state, payload) {
    const raw = typeof payload === "string" ? payload : safeString(JSON.stringify(payload));
    appendDiagnostic(state, raw.slice(0, 4096));

    if (raw === "[DONE]") {
      state.doneMarker = true;
      return;
    }

    let value;
    try {
      value = typeof payload === "string" ? JSON.parse(payload) : payload;
    } catch (_) {
      return;
    }

    if (!value || typeof value !== "object") return;

    const structuredError = (
      value.error ||
      value.detail ||
      value.response?.error ||
      value.message?.error
    );
    if (structuredError) {
      state.failureKind = classifyFailure(
        typeof structuredError === "string"
          ? structuredError
          : JSON.stringify(structuredError),
        500
      );
    }

    if (value.type === "response.output_text.delta" && typeof value.delta === "string") {
      state.assistantSeen = true;
      appendRolling(state, value.delta);
    }
    if (value.type === "response.output_text.done" && typeof value.text === "string") {
      state.assistantSeen = true;
      setResponseText(state, value.text);
    }
    if (value.type === "response.completed") {
      state.doneMarker = true;
      if (value.response?.id) state.conversationId = String(value.response.id);
    }
    if (value.type === "response.failed" || value.type === "response.incomplete" || value.type === "error") {
      state.failureKind = classifyFailure(
        JSON.stringify(value.error || value.response?.error || value),
        500
      );
    }

    if (value.message?.author?.role === "assistant") {
      const parts = value.message?.content?.parts;
      if (Array.isArray(parts) && typeof parts[0] === "string") {
        state.assistantSeen = true;
        setResponseText(state, parts[0]);
      }
      if (value.message?.status === "finished_successfully") {
        state.doneMarker = true;
      }
      if (value.conversation_id) state.conversationId = String(value.conversation_id);
    }

    if (value.conversation_id) state.conversationId = String(value.conversation_id);
    applyPatch(state, value);

    const batch = Array.isArray(value.v) ? value.v : null;
    if (value.o === "patch" && batch) {
      for (const patch of batch) applyPatch(state, patch);
    }

    if (typeof value.v === "string" && !value.p && state.assistantSeen) {
      appendRolling(state, value.v);
    }
  }

  function consumeSseChunk(state, bytes) {
    const text = new TextDecoder().decode(bytes, {stream: true}).replace(/\r\n/g, "\n");
    state.sseBuffer += text;

    let boundary = -1;
    while ((boundary = state.sseBuffer.indexOf("\n\n")) >= 0) {
      const frame = state.sseBuffer.slice(0, boundary);
      state.sseBuffer = state.sseBuffer.slice(boundary + 2);
      const lines = frame.split(/\r?\n/);
      const dataLines = [];
      for (const line of lines) {
        if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
      }
      if (dataLines.length) inspectPayload(state, dataLines.join("\n"));
      if (state.doneMarker) break;
    }

    if (state.sseBuffer.includes("data: [DONE]")) state.doneMarker = true;
    state.lastProgressAt = now();
  }

  function streamState(meta) {
    return {
      ...meta,
      responseText: "",
      diagnostic: "",
      sseBuffer: "",
      assistantSeen: false,
      doneMarker: false,
      failureKind: null,
      conversationId: null,
      startedAt: Number(meta.request_started_at_ms || 0) || now(),
      lastProgressAt: now(),
      stallReportedAt: 0,
      terminal: false
    };
  }

  function emitTerminal(state, stateName, reason = null, extra = {}) {
    if (state.terminal) return;
    state.terminal = true;

    const payload = {
      operation_id: state.operation_id,
      request_id: state.request_id,
      state: stateName,
      reason,
      url: state.url,
      http_status: state.http_status,
      response_text: state.responseText.slice(-MAX_RESPONSE_CHARS),
      response_text_available: Boolean(state.responseText.trim()),
      conversation_id: state.conversationId,
      request_started_at_ms: state.startedAt,
      response_observed_at_ms: state.responseObservedAtMs || null,
      completed_at_ms: now(),
      stream_progress_at_ms: state.lastProgressAt,
      ...extra
    };
    post("event", payload);
    if (armed?.operation_id === state.operation_id) {
      armed = null;
    }
  }

  function startStallWatch(state) {
    state.stallTimer = setInterval(() => {
      if (state.terminal) {
        clearInterval(state.stallTimer);
        return;
      }
      const elapsed = now() - state.lastProgressAt;
      if (elapsed < STALL_MS) return;
      if (state.stallReportedAt && now() - state.stallReportedAt < STALL_MS) return;
      state.stallReportedAt = now();
      post("event", {
        operation_id: state.operation_id,
        request_id: state.request_id,
        state: "stalled",
        reason: "no_stream_progress",
        url: state.url,
        http_status: state.http_status,
        last_progress_at_ms: state.lastProgressAt,
        stalled_at_ms: now()
      });
    }, Math.min(STALL_MS, 5000));
  }

  async function readBoundedText(response) {
    if (!response?.body) return "";
    const clone = response.clone();
    const reader = clone.body.getReader();
    const decoder = new TextDecoder();
    let output = "";
    let bytes = 0;
    try {
      while (true) {
        const {done, value} = await reader.read();
        if (done) break;
        bytes += value.byteLength;
        output += decoder.decode(value, {stream: true});
        if (output.length > MAX_DIAGNOSTIC_CHARS || bytes > MAX_DIAGNOSTIC_CHARS * 4) {
          try { await reader.cancel(); } catch (_) {}
          break;
        }
      }
      output += decoder.decode();
    } finally {
      reader.releaseLock();
    }
    return output.slice(-MAX_DIAGNOSTIC_CHARS);
  }

  async function observeResponse(response, meta) {
    const state = streamState({
      operation_id: meta.operation_id,
      request_id: meta.request_id,
      url: meta.url,
      method: meta.method,
      request_started_at_ms: Number(meta.request_started_at_ms || 0) || now(),
      http_status: Number(response?.status || 0),
      responseObservedAtMs: now()
    });

    post("event", {
      operation_id: state.operation_id,
      request_id: state.request_id,
      state: "started",
      url: state.url,
      http_status: state.http_status,
      response_observed_at_ms: state.responseObservedAtMs
    });

    if (!response?.ok) {
      const body = await readBoundedText(response);
      state.diagnostic = body;
      state.failureKind = classifyFailure(body, state.http_status);
      emitTerminal(state, state.failureKind, "http_error", {
        diagnostic: body.slice(-MAX_DIAGNOSTIC_CHARS)
      });
      return;
    }

    if (!response.body) {
      emitTerminal(state, "completed", "empty_body");
      return;
    }

    const contentType = String(response.headers?.get?.("content-type") || "").toLowerCase();
    if (!contentType.includes("text/event-stream") && !likelyGenerationPath(new URL(state.url))) {
      const body = await readBoundedText(response);
      appendDiagnostic(state, body);
      if (contextExhaustedText(body) || usageLimitedText(body) || authRequiredText(body)) {
        state.failureKind = classifyFailure(body, state.http_status);
        emitTerminal(state, state.failureKind, "response_body");
      } else {
        emitTerminal(state, "completed", "non_streaming_response");
      }
      return;
    }

    startStallWatch(state);
    const clone = response.clone();
    const reader = clone.body.getReader();

    try {
      while (true) {
        const {done, value} = await reader.read();
        if (done) break;
        consumeSseChunk(state, value);
        if (state.failureKind) {
          try { await reader.cancel(); } catch (_) {}
          break;
        }
        if (state.doneMarker) {
          try { await reader.cancel(); } catch (_) {}
          break;
        }
      }

      if (state.failureKind) {
        emitTerminal(state, state.failureKind, "stream_payload");
      } else if (state.doneMarker) {
        emitTerminal(state, "completed", "done_marker");
      } else if (state.assistantSeen && state.responseText.trim()) {
        emitTerminal(state, "interrupted", "stream_end_without_terminal_marker");
      } else {
        emitTerminal(state, "interrupted", "empty_or_incomplete_stream");
      }
    } catch (error) {
      const reason = safeString(error?.message || error, 500);
      emitTerminal(state, "interrupted", "stream_error", {error: reason});
    } finally {
      clearInterval(state.stallTimer);
      reader.releaseLock();
    }
  }

  function requestId() {
    return globalThis.crypto?.randomUUID
      ? globalThis.crypto.randomUUID()
      : "pasi-net-" + now() + "-" + Math.random().toString(16).slice(2);
  }

  function shouldTrack(meta, response = null) {
    if (!armed || armed.terminal) return false;
    if (meta.method_post && meta.likely_generation) return true;

    const contentType = String(response?.headers?.get?.("content-type") || "").toLowerCase();
    return meta.method_post && meta.likely_generation;
  }

  function makeMeta(input, init) {
    const classified = classifyRequest(input, init);
    return {
      ...classified,
      url: classified.url?.toString() || "",
      method: String(init?.method || input?.method || "GET").toUpperCase(),
      generation_candidate: classified.likely_generation === true
    };
  }

  function installFetch() {
    if (typeof window.fetch !== "function") return;
    if (window.fetch.__PASI_NETWORK_INTERCEPTOR__ === true) return;
    originalFetch = window.fetch;

    const wrapped = function(...args) {
      if (!armed) return originalFetch.apply(this, args);

      const meta = makeMeta(args[0], args[1]);
      let shouldCandidate = meta.method_post && meta.likely_generation;
      let currentRequestId = null;
      let requestStartedAtMs = null;

      if (shouldCandidate) {
        currentRequestId = requestId();
        requestStartedAtMs = now();
        post("event", {
          operation_id: armed.operation_id,
          request_id: currentRequestId,
          state: "request",
          url: meta.url,
          method: meta.method,
          generation_candidate: meta.generation_candidate === true,
          request_started_at_ms: requestStartedAtMs
        });
      }

      let result;
      try {
        result = originalFetch.apply(this, args);
      } catch (error) {
        if (currentRequestId && armed?.operation_id) {
          const state = streamState({
            operation_id: armed.operation_id,
            request_id: currentRequestId,
            url: meta.url,
            method: meta.method
          });
          emitTerminal(state, "interrupted", "request_error", {error: safeString(error?.message || error, 500)});
        }
        throw error;
      }

      if (!shouldCandidate) return result;

      return Promise.resolve(result).then((response) => {
        if (!armed) return response;
        const actualMeta = {
          ...meta,
          operation_id: armed.operation_id,
          request_id: currentRequestId,
          request_started_at_ms: requestStartedAtMs
        };
        if (shouldTrack(actualMeta, response)) {
          void observeResponse(response, actualMeta).catch((error) => {
            const state = streamState(actualMeta);
            emitTerminal(state, "interrupted", "observer_error", {
              error: safeString(error?.message || error, 500)
            });
          });
        }
        return response;
      }, (error) => {
        if (armed?.operation_id && currentRequestId) {
          const state = streamState({
            operation_id: armed.operation_id,
            request_id: currentRequestId,
            url: meta.url,
            method: meta.method
          });
          emitTerminal(state, "interrupted", "network_error", {error: safeString(error?.message || error, 500)});
        }
        throw error;
      });
    };

    Object.defineProperty(wrapped, "__PASI_NETWORK_INTERCEPTOR__", {value: true});
    try {
      window.fetch = wrapped;
    } catch (_) {}

    if (!globalThis.__PASI_NETWORK_FETCH_WATCHDOG__) {
      globalThis.__PASI_NETWORK_FETCH_WATCHDOG__ = setInterval(() => {
        if (window.fetch?.__PASI_NETWORK_INTERCEPTOR__ !== true) {
          installFetch();
        }
      }, 1000);
    }
  }

  function installXhr() {
    if (typeof XMLHttpRequest !== "function") return;
    if (XMLHttpRequest.prototype.open?.__PASI_NETWORK_INTERCEPTOR__) return;
    originalXhrOpen = XMLHttpRequest.prototype.open;
    originalXhrSend = XMLHttpRequest.prototype.send;

    const originalSetHandler = XMLHttpRequest.prototype.open;

    function wrappedOpen(method, url, ...rest) {
      this.__pasi_network_meta__ = {
        method: String(method || "GET").toUpperCase(),
        url: String(new URL(url, location.href))
      };
      return originalXhrOpen.call(this, method, url, ...rest);
    }

    Object.defineProperty(wrappedOpen, "__PASI_NETWORK_INTERCEPTOR__", {value: true});
    XMLHttpRequest.prototype.open = wrappedOpen;

    XMLHttpRequest.prototype.send = function(...args) {
      if (!armed || !this.__pasi_network_meta__) {
        return originalXhrSend.apply(this, args);
      }
      const metaUrl = new URL(this.__pasi_network_meta__.url);
      if (this.__pasi_network_meta__.method !== "POST" || !likelyGenerationPath(metaUrl)) {
        return originalXhrSend.apply(this, args);
      }

      const request = {
        operation_id: armed.operation_id,
        request_id: requestId(),
        url: this.__pasi_network_meta__.url,
        method: "POST"
      };
      post("event", { ...request, state: "request" });

      const startedAt = now();
      request.request_started_at_ms = startedAt;
      const onLoad = () => {
        const state = streamState({
          ...request,
          request_started_at_ms: startedAt,
          responseObservedAtMs: now(),
          http_status: Number(this.status || 0)
        });

        let body = "";
        try { body = String(this.responseText || ""); } catch (_) {}
        if (state.http_status < 200 || state.http_status >= 300) {
          state.failureKind = classifyFailure(body, state.http_status);
          emitTerminal(state, state.failureKind, "xhr_http_error");
          return;
        }

        appendRolling(state, body);
        emitTerminal(state, "completed", "xhr_load");
      };
      const onError = () => {
        const state = streamState({...request, startedAt, http_status: Number(this.status || 0)});
        emitTerminal(state, "interrupted", "xhr_error");
      };
      const onAbort = () => {
        const state = streamState({...request, startedAt, http_status: Number(this.status || 0)});
        emitTerminal(state, "interrupted", "xhr_abort");
      };

      this.addEventListener("load", onLoad, {once: true});
      this.addEventListener("error", onError, {once: true});
      this.addEventListener("abort", onAbort, {once: true});
      return originalXhrSend.apply(this, args);
    };
  }

  function arm(operationId) {
    const value = String(operationId || "").trim();
    if (!value) return false;
    armed = {
      operation_id: value,
      armed_at: now(),
      terminal: false
    };
    post("armed", {operation_id: value});
    return true;
  }

  function disarm(operationId) {
    if (!armed) return true;
    if (operationId && armed.operation_id !== String(operationId)) return false;
    armed = null;
    post("disarmed", {operation_id: String(operationId || "")});
    return true;
  }

  function ping() {
    post("ready", {
      interceptor_version: VERSION,
      installed_at: globalThis.__PASI_NETWORK_INTERCEPTOR_STARTED_AT__ || null,
      armed_operation_id: armed?.operation_id || null
    });
  }

  globalThis.__PASI_NETWORK_INTERCEPTOR_STARTED_AT__ = now();

  window.addEventListener("message", (event) => {
    if (event.source !== window) return;
    const data = event.data;
    if (!data || data.source !== "pasi-network-controller") return;
    if (data.target !== SOURCE) return;

    switch (data.command) {
      case "arm":
        arm(data.operation_id);
        break;
      case "disarm":
        disarm(data.operation_id);
        break;
      case "ping":
        ping();
        break;
      default:
        break;
    }
  });

  installFetch();
  installXhr();
  post("ready", {
    interceptor_version: VERSION,
    installed_at: globalThis.__PASI_NETWORK_INTERCEPTOR_STARTED_AT__
  });

  if (globalThis.PASI_NETWORK_INTERCEPTOR_TEST_HOOKS === true) {
    globalThis.PASI_NETWORK_INTERCEPTOR_TEST_API = Object.freeze({
      classifyRequest,
      likelyGenerationPath,
      classifyFailure,
      contextExhaustedText,
      usageLimitedText,
      authRequiredText,
      applyPatch,
      inspectPayload
    });
  }
})();
