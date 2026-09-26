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
  let recoveryPending = false;
  let lossRecorded = false;
  let recoveryCompleted = false;
  let awaitingFreshChat = false;
  let lastReadyUrl = null;
  let lastObservedLimitUrl = null;

  async function emit(type, payload = {}) {
    const message = protocol.envelope(type, payload);
    try {
      return await chrome.runtime.sendMessage(message);
    } catch (_error) {
      return null;
    }
  }

  async function sleep(milliseconds) {
    await new Promise((resolve) => setTimeout(resolve, milliseconds));
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
    recoveryCompleted = false;
    active = true;
    await emit(protocol.TYPES.OPERATION_STARTED, {
      operation_id: operationId,
      chat_url: chatgpt.currentChatUrl(),
      thinking_enabled: chatgpt.thinkingEnabled(),
      prior_chat_url: priorChatUrl,
    });
  }

  async function injectCurrentPrompt(prompt, taskId, promptGeneration) {
    if (!prompt || !taskId || !Number.isInteger(promptGeneration)) {
      return false;
    }

    const stored = await chrome.storage.local.get([
      "pasi_injected_prompt_generation",
      "pasi_injected_chat_url",
    ]);
    if (
      stored.pasi_injected_prompt_generation === promptGeneration &&
      stored.pasi_injected_chat_url === chatgpt.currentChatUrl()
    ) {
      return true;
    }

    if (!chatgpt.isAuthenticatedPage() || chatgpt.isGenerating()) {
      return false;
    }

    if (!await chatgpt.ensureThinkingEnabled()) {
      await emit(protocol.TYPES.RUNTIME_ERROR, {
        error: "Thinking could not be enabled; refusing automatic task prompt injection.",
        task_id: taskId,
        prompt_generation: promptGeneration,
      });
      return false;
    }

    const sent = await chatgpt.injectPrompt(prompt);
    if (!sent) {
      await emit(protocol.TYPES.RUNTIME_ERROR, {
        error: "PASI could not inject the current task prompt into the ChatGPT composer.",
        task_id: taskId,
        prompt_generation: promptGeneration,
      });
      return false;
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
    return true;
  }

  async function submitRecoveryPrompt(reason) {
    if (
      !operationId ||
      awaitingAcceptance ||
      !recoveryPending ||
      recoveryCompleted
    ) {
      return false;
    }
    if (!chatgpt.isAuthenticatedPage() || chatgpt.isGenerating()) {
      return false;
    }
    if (!await chatgpt.ensureThinkingEnabled()) {
      return false;
    }

    const stored = await chrome.storage.local.get(["pasi_injected_task_id"]);
    const taskId = stored.pasi_injected_task_id || "unknown";
    const recentChanges = (
      lastAssistantText ||
      checkpoint?.checkpoint ||
      "No prior assistant output captured."
    ).slice(-2500);
    const prompt = [
      "PASI CONNECTION RECOVERY",
      "Current task: " + taskId,
      "Reason: " + reason,
      "The active response was interrupted. Do not advance to another task.",
      "Resume the same task from the preserved operation/checkpoint.",
      "Most recent assistant output / changes before interruption:",
      recentChanges,
      "Re-check your latest changes, continue from that exact state, and preserve the task identity.",
      "When the task is actually complete, return the required PASI completion markers and unified patch.",
    ].join("\n");

    const sent = await chatgpt.injectPrompt(prompt);
    if (!sent) {
      return false;
    }

    await emit(protocol.TYPES.CONNECTION_RESTORED, {
      operation_id: operationId,
      resumed_after_reconnect: true,
      same_operation_resumed: true,
      resume_phase: checkpoint?.resume_phase || "connection_loss",
      recovery_via_new_prompt: true,
      recovery_reason: reason,
    });
    await emit(protocol.TYPES.RESUME_REQUEST, {
      operation_id: operationId,
      resume_phase: checkpoint?.resume_phase || "connection_loss",
      resumed: true,
      recovery_via_new_prompt: true,
    });
    recoveryPending = false;
    recoveryCompleted = true;
    return true;
  }

  async function beginRecovery(reason) {
    if (!active || awaitingAcceptance || recoveryCompleted) {
      return;
    }
    recoveryPending = true;
    if (!lossRecorded) {
      const response = chatgpt.latestAssistantMessage();
      const stopped = chatgpt.stopGeneration();
      await checkpointProgress("connection_loss", response);
      await emit(protocol.TYPES.CONNECTION_LOST, {
        operation_id: operationId,
        response_stopped_on_loss: stopped || Boolean(chatgpt.connectionErrorMessage()),
        checkpoint_preserved: Boolean(checkpoint),
        resume_phase: checkpoint?.resume_phase || "connection_loss",
        recovery_reason: reason,
      });
      lossRecorded = true;
    }
    await sleep(250);
    await submitRecoveryPrompt(reason);
  }

  async function handleChatLimit() {
    const url = chatgpt.currentChatUrl();
    const stored = await chrome.storage.local.get(["pasi_automation_chat_url"]);
    if (
      !chatgpt.chatLimitReached() ||
      url === lastObservedLimitUrl ||
      (stored.pasi_automation_chat_url && stored.pasi_automation_chat_url !== url)
    ) {
      return;
    }
    if (awaitingAcceptance) {
      return;
    }

    // A new chat is a recovery action for an exhausted conversation only.
    // Before creating it, verify that the desired Thinking state is available;
    // a normal usable chat must never trigger chat creation just because the
    // Thinking selector is currently off.
    if (!await chatgpt.ensureThinkingEnabled()) {
      await emit(protocol.TYPES.RUNTIME_ERROR, {
        error: "Chat usage limit detected, but the desired Thinking state could not be verified before fresh-chat recovery.",
        operation_id: operationId,
      });
      return;
    }

    lastObservedLimitUrl = url;
    if (active) {
      const response = chatgpt.latestAssistantMessage();
      const stopped = chatgpt.stopGeneration();
      await checkpointProgress("chat_limit", response);
      await emit(protocol.TYPES.RUNTIME_ERROR, {
        error: "Chat usage limit detected; creating a fresh conversation without advancing the current task.",
        operation_id: operationId,
        response_stopped: stopped,
      });
    }
    priorChatUrl = url;
    awaitingFreshChat = true;
    if (!chatgpt.createFreshChat()) {
      awaitingFreshChat = false;
      await emit(protocol.TYPES.RUNTIME_ERROR, {
        error: "Chat usage limit detected, but PASI could not create a fresh chat.",
      });
    }
  }

  async function ensureTaskChat() {
    if (active || awaitingAcceptance || awaitingFreshChat) {
      return;
    }
    if (!chatgpt.isAuthenticatedPage()) {
      return;
    }

    const url = chatgpt.currentChatUrl();
    const stored = await chrome.storage.local.get(["pasi_automation_chat_url"]);
    const automationChatUrl = stored.pasi_automation_chat_url || url;

    if (!stored.pasi_automation_chat_url) {
      await chrome.storage.local.set({
        pasi_automation_chat_url: url,
      });
    }

    if (automationChatUrl !== url) {
      // Chat switching is reuse, not chat creation. Return to the durable
      // automation conversation whenever the browser is on another chat.
      if (chatgpt.isGenerating()) {
        return;
      }
      await emit(protocol.TYPES.CHAT_USAGE, {
        chat_switch_requested: true,
        from_chat_url: url,
        to_chat_url: automationChatUrl,
      });
      location.assign(automationChatUrl);
      return;
    }

    if (url !== lastReadyUrl) {
      lastReadyUrl = url;
      await emit(protocol.TYPES.CHAT_READY, {
        chat_url: url,
        authenticated_page: true,
        thinking_enabled: chatgpt.thinkingEnabled(),
        same_automation_chat: true,
      });
    }
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

    if (awaitingFreshChat && url !== priorChatUrl) {
      awaitingFreshChat = false;
      await chrome.storage.local.set({
        pasi_automation_chat_url: url,
      });
      await emit(protocol.TYPES.FRESH_CHAT, {
        fresh_chat_created_after_usage: true,
        fresh_chat_creation_reason: "usage_limit",
        prior_chat_url: priorChatUrl,
        fresh_chat_url: url,
      });
    }

    await handleChatLimit();

    const connectionError = chatgpt.connectionErrorMessage();
    if (active && !awaitingAcceptance && connectionError) {
      await beginRecovery(connectionError);
    }

    await ensureTaskChat();

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
        fresh_chat_created_after_usage: Boolean(priorChatUrl && priorChatUrl !== url),
        recovery_count: lossRecorded ? 1 : 0,
      });
    }

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
      recoveryPending = false;
      lossRecorded = false;
      recoveryCompleted = false;
      lastAssistantText = "";
      priorChatUrl = chatgpt.currentChatUrl();
      awaitingFreshChat = false;
      lastReadyUrl = null;
      lastObservedLimitUrl = null;
      await emit(protocol.TYPES.CHAT_READY, {
        chat_url: priorChatUrl,
        authenticated_page: true,
        thinking_enabled: chatgpt.thinkingEnabled(),
        same_chat_continuation: true,
      });
    }
  }

  async function handleOffline() {
    if (!active || awaitingAcceptance) {
      return;
    }
    await beginRecovery("browser offline event");
  }

  async function handleOnline() {
    if (!active || awaitingAcceptance || !recoveryPending) {
      return;
    }
    await submitRecoveryPrompt("browser online event");
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
