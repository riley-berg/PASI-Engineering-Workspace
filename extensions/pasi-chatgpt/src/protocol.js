(() => {
  "use strict";

  const VERSION = "m0-v1";

  function makeId() {
    if (globalThis.crypto?.randomUUID) {
      return globalThis.crypto.randomUUID();
    }
    return "pasi-" + Date.now() + "-" + Math.random().toString(16).slice(2);
  }

  function envelope(type, payload = {}) {
    return {
      protocol_version: VERSION,
      type,
      timestamp: new Date().toISOString(),
      ...payload,
    };
  }

  globalThis.PASIProtocol = Object.freeze({
    VERSION,
    makeId,
    envelope,
    TYPES: Object.freeze({
      PAGE_READY: "page_ready",
      CHAT_USAGE: "chat_usage",
      FRESH_CHAT: "fresh_chat",
      THINKING_STATE: "thinking_state",
      OPERATION_STARTED: "operation_started",
      RESPONSE_PROGRESS: "response_progress",
      RESPONSE_COMPLETE: "response_complete",
      CONNECTION_LOST: "connection_lost",
      CONNECTION_RESTORED: "connection_restored",
      CHECKPOINT: "checkpoint",
      RESUME_REQUEST: "resume_request",
      RUNTIME_ERROR: "runtime_error",
    }),
  });
})();
