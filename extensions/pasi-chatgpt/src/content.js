(() => {
  "use strict";

  const protocol = globalThis.PASIProtocol;
  const chatgpt = globalThis.PASIChatGPT;
  let operationId = null;
  let priorChatUrl = null;
  let active = false;
  let awaitingAcceptance = false;
  let lastAssistantText = "";
  let checkpoint = null;
  let recoveryCount = 0;
  let lastFreshChatUrl = null;

  async function emit(type, payload = {}) {
    const message = protocol.envelope(type, payload);
    try {
      return await chrome.runtime.sendMessage(message);
    } catch (_error) {
      return null;
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
    if (active || awaitingAcceptance) {
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

  async function injectCurrentPrompt(prompt, taskId, promptGeneration) {
    if (!prompt || !taskId || !Number.isInteger(promptGeneration)) {
      return;
    }

    const stored = await chrome.storage.local.get(["pasi_injected_prompt_generation"]);
    if (stored.pasi_injected_prompt_generation === promptGeneration) {
      return;
    }

    if (!chatgpt.isAuthenticatedPage() || chatgpt.isGenerating()) {
      return;
    }

    if (!chatgpt.thinkingEnabled()) {
      await emit(protocol.TYPES.RUNTIME_ERROR, {
        error: "Thinking is not enabled; refusing automatic prompt injection.",
        task_id: taskId,
        prompt_generation: promptGeneration,
      });
      return;
    }

    const sent = chatgpt.injectPrompt(prompt);
    if (!sent) {
      await emit(protocol.TYPES.RUNTIME_ERROR, {
        error: "PASI could not inject the current task prompt into the ChatGPT composer.",
        task_id: taskId,
        prompt_generation: promptGeneration,
      });
      return;
    }

    await chrome.storage.local.set({
      pasi_injected_prompt_generation: promptGeneration,
      pasi_injected_task_id: taskId,
      pasi_injected_chat_url: chatgpt.currentChatUrl(),
    });

    await emit(protocol.TYPES.CHAT_USAGE, {
      task_id: taskId,
      prompt_generation: promptGeneration,
      chat_url: chatgpt.currentChatUrl(),
      prompt_injected: true,
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

    if (url && url !== priorChatUrl && priorChatUrl && url !== lastFreshChatUrl) {
      lastFreshChatUrl = url;
      await emit(protocol.TYPES.FRESH_CHAT, {
        fresh_chat_created_after_usage: true,
        prior_chat_url: priorChatUrl,
        fresh_chat_url: url,
      });
    }

    if (!active && !awaitingAcceptance && generating) {
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

    if (
      active &&
      !awaitingAcceptance &&
      !generating &&
      chatgpt.hasCompletePasiResponse(response)
    ) {
      awaitingAcceptance = true;
      await emit(protocol.TYPES.RESPONSE_COMPLETE, {
        operation_id: operationId,
        chat_url: url,
        thinking_enabled: thinking,
        response_text: response,
        recovery_count: recoveryCount,
        fresh_chat_created_after_usage: Boolean(priorChatUrl && priorChatUrl !== url),
      });
    }

    priorChatUrl = priorChatUrl || url;

    await chrome.storage.local.set({
      last_chat_url: url,
      last_chat_used: Boolean(response),
    });
  }

  async function handleBridgeMessage(message) {
    if (message.type === "pasi.inject_prompt") {
      await injectCurrentPrompt(
        message.prompt,
        message.task_id,
        message.prompt_generation
      );
      return;
    }

    if (message.type === "pasi.acceptance_passed") {
      active = false;
      awaitingAcceptance = false;
      operationId = null;
      checkpoint = null;
      recoveryCount = 0;
      lastAssistantText = "";
      priorChatUrl = chatgpt.currentChatUrl();
      await chrome.storage.local.set({
        last_chat_url: chatgpt.currentChatUrl(),
      });
      chatgpt.createFreshChat();
    }
  }

  async function handleOffline() {
    if (!active || awaitingAcceptance || !chatgpt.isGenerating()) {
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
    const stored = await chrome.storage.local.get(["last_chat_url"]);
    priorChatUrl = stored.last_chat_url || null;

    await emit(protocol.TYPES.PAGE_READY, {
      chat_url: chatgpt.currentChatUrl(),
      authenticated_page: chatgpt.isAuthenticatedPage(),
      thinking_enabled: chatgpt.thinkingEnabled(),
      prior_chat_url: priorChatUrl,
    });

    window.addEventListener("offline", handleOffline);
    window.addEventListener("online", handleOnline);

    chrome.runtime.onMessage.addListener((message) => {
      void handleBridgeMessage(message);
    });

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
