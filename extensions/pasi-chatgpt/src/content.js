(() => {
  "use strict";

  const protocol = globalThis.PASIProtocol;
  const chatgpt = globalThis.PASIChatGPT;
  let operationId = null;
  let priorChatUrl = null;
  let active = false;
  let lastAssistantText = "";
  let checkpoint = null;
  let recoveryCount = 0;

  async function emit(type, payload = {}) {
    const message = protocol.envelope(type, payload);
    try {
      await chrome.runtime.sendMessage(message);
    } catch (_error) {
      // The page remains usable if the local capture bridge is unavailable.
    }
  }

  async function checkpointProgress(phase, value) {
    const text = String(value || "").slice(-512);
    checkpoint = {
      operation_id: operationId,
      resume_phase: phase,
      checkpoint: text || "response-generation checkpoint",
      at: new Date().toISOString(),
    };
    await emit(protocol.TYPES.CHECKPOINT, checkpoint);
  }

  async function startOperation() {
    if (active) {
      return;
    }
    operationId = protocol.makeId();
    active = true;
    recoveryCount = 0;
    await emit(protocol.TYPES.OPERATION_STARTED, {
      operation_id: operationId,
      chat_url: chatgpt.currentChatUrl(),
      thinking_enabled: chatgpt.thinkingEnabled(),
      prior_chat_url: priorChatUrl,
    });
  }

  async function observe() {
    const url = chatgpt.currentChatUrl();
    const thinking = chatgpt.thinkingEnabled();
    const generating = chatgpt.isGenerating();
    const response = chatgpt.latestAssistantMessage();

    await emit(protocol.TYPES.THINKING_STATE, {
      thinking_enabled: thinking,
      chat_url: url,
    });

    if (url && url !== priorChatUrl && priorChatUrl) {
      await emit(protocol.TYPES.FRESH_CHAT, {
        fresh_chat_created_after_usage: true,
        prior_chat_url: priorChatUrl,
        fresh_chat_url: url,
      });
    }

    if (!active && generating) {
      await startOperation();
    }

    if (response && response !== lastAssistantText) {
      lastAssistantText = response;
      await checkpointProgress("response_generation", response);
      await emit(protocol.TYPES.RESPONSE_PROGRESS, {
        operation_id: operationId,
        response_text: response,
        chat_url: url,
      });
    }

    if (active && !generating && chatgpt.hasCompletePasiResponse(response)) {
      await emit(protocol.TYPES.RESPONSE_COMPLETE, {
        operation_id: operationId,
        chat_url: url,
        thinking_enabled: thinking,
        response_text: response,
        recovery_count: recoveryCount,
        fresh_chat_created_after_usage: Boolean(priorChatUrl && priorChatUrl !== url),
      });
      active = false;
    }

    await chrome.storage.local.set({
      last_chat_url: url,
      last_chat_used: Boolean(response),
    });
  }

  async function handleOffline() {
    if (!active || !chatgpt.isGenerating()) {
      return;
    }

    const response = chatgpt.latestAssistantMessage();
    const stopped = chatgpt.stopGeneration();
    await checkpointProgress("connection_loss", response);
    await emit(protocol.TYPES.CONNECTION_LOST, {
      operation_id: operationId,
      response_stopped_on_loss: stopped,
      checkpoint_preserved: Boolean(checkpoint),
      resume_phase: checkpoint?.resume_phase || "connection_loss",
    });
  }

  async function handleOnline() {
    if (!operationId || !checkpoint || recoveryCount >= 1) {
      return;
    }

    recoveryCount += 1;
    const resumed = chatgpt.resumeGeneration();
    await emit(protocol.TYPES.CONNECTION_RESTORED, {
      operation_id: operationId,
      resumed_after_reconnect: resumed,
      same_operation_resumed: Boolean(resumed && operationId),
      resume_phase: checkpoint.resume_phase,
      recovery_count: recoveryCount,
    });

    await emit(protocol.TYPES.RESUME_REQUEST, {
      operation_id: operationId,
      resume_phase: checkpoint.resume_phase,
      resumed,
    });
  }

  async function initialize() {
    priorChatUrl = await chrome.storage.local.get("last_chat_url").then((value) => value.last_chat_url || null);
    await emit(protocol.TYPES.PAGE_READY, {
      chat_url: chatgpt.currentChatUrl(),
      authenticated_page: chatgpt.isAuthenticatedPage(),
      thinking_enabled: chatgpt.thinkingEnabled(),
      prior_chat_url: priorChatUrl,
    });

    window.addEventListener("offline", handleOffline);
    window.addEventListener("online", handleOnline);

    const observer = new MutationObserver(() => {
      void observe();
    });
    observer.observe(document.documentElement, {
      subtree: true,
      childList: true,
      characterData: true,
    });

    window.setInterval(() => {
      void observe();
    }, 1000);

    await observe();
  }

  void initialize();
})();
