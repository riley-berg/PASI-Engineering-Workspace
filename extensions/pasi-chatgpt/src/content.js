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
  let contextRecoveryScheduled = false;
  let recoveringFromContextInvalidation = false;

  function isExtensionContextInvalidated(error) {
    return /extension context invalidated/i.test(String(error?.message || error || ""));
  }

  function persistContextRecoveryState() {
    try {
      sessionStorage.setItem(
        "pasi_extension_context_recovery",
        JSON.stringify({
          operation_id: operationId,
          active,
          checkpoint,
          last_assistant_text: lastAssistantText,
          prior_chat_url: priorChatUrl,
          recovery_pending: recoveryPending,
          awaiting_fresh_chat: awaitingFreshChat,
          at: new Date().toISOString(),
        })
      );
    } catch (_error) {
      // Page session storage may be unavailable; the reload still restores the
      // extension context and the bridge retains the authoritative task state.
    }
  }

  function scheduleContextRecovery(error) {
    if (!isExtensionContextInvalidated(error) || contextRecoveryScheduled) {
      return false;
    }
    contextRecoveryScheduled = true;
    persistContextRecoveryState();
    window.setTimeout(() => {
      window.location.reload();
    }, 0);
    return true;
  }

  async function storageGet(keys) {
    try {
      return await chrome.storage.local.get(keys);
    } catch (error) {
      scheduleContextRecovery(error);
      return {};
    }
  }

  async function storageSet(values) {
    try {
      await chrome.storage.local.set(values);
      return true;
    } catch (error) {
      scheduleContextRecovery(error);
      return false;
    }
  }

  async function emit(type, payload = {}) {
    const message = protocol.envelope(type, payload);
    try {
      return await chrome.runtime.sendMessage(message);
    } catch (error) {
      scheduleContextRecovery(error);
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

    const stored = await storageGet([
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

    await storageSet({
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

    const stored = await storageGet(["pasi_injected_task_id"]);
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
    if (isExtensionContextInvalidated(reason) || reason === "extension context invalidated") {
      // Extension context invalidation is handled by page reload + state restore.
      // It must never generate a new ChatGPT prompt.
      return;
    }
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
    const stored = await storageGet(["pasi_automation_chat_url"]);
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
    const stored = await storageGet(["pasi_automation_chat_url"]);
    const automationChatUrl = stored.pasi_automation_chat_url || url;

    if (!stored.pasi_automation_chat_url) {
      await storageSet({
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
      await storageSet({
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

    await storageSet({
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

  function restoreContextRecoveryState() {
    try {
      const raw = sessionStorage.getItem("pasi_extension_context_recovery");
      if (!raw) {
        return false;
      }
      sessionStorage.removeItem("pasi_extension_context_recovery");
      const state = JSON.parse(raw);
      if (!state || !state.active || !state.operation_id) {
        return false;
      }

      operationId = state.operation_id;
      active = true;
      awaitingAcceptance = false;
      checkpoint = state.checkpoint || null;
      lastAssistantText = state.last_assistant_text || "";
      priorChatUrl = state.prior_chat_url || null;

      // Extension-context invalidation is a browser-extension lifecycle event,
      // not a ChatGPT connection loss. Do not submit a recovery prompt here.
      // If ChatGPT is actually still showing a connection error after reload,
      // observe() will detect the real connection error and invoke normal
      // connection recovery exactly once.
      recoveryPending = false;
      awaitingFreshChat = Boolean(state.awaiting_fresh_chat);
      recoveringFromContextInvalidation = true;
      return true;
    } catch (_error) {
      return false;
    }
  }

  async function initialize() {
    restoreContextRecoveryState();

    const stored = await storageGet(["last_chat_url"]);
    if (!priorChatUrl) {
      priorChatUrl = stored.last_chat_url || null;
    }

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

    if (recoveringFromContextInvalidation) {
      // The operation/checkpoint has already been restored into this fresh
      // extension context. Continue observing the existing ChatGPT response;
      // never inject a synthetic "PASI CONNECTION RECOVERY" prompt merely
      // because the browser extension was reloaded.
      recoveringFromContextInvalidation = false;
      await emit(protocol.TYPES.CHECKPOINT, {
        operation_id: operationId,
        resume_phase: checkpoint?.resume_phase || "extension_context_restored",
        checkpoint: checkpoint?.checkpoint || "extension context restored",
        context_restored: true,
        synthetic_recovery_prompt: false,
      });
    }
  }

  void initialize();
})();
