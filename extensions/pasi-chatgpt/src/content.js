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
  let resumeRequestSent = false;
  let awaitingFreshChat = false;
  let freshChatCreatedAfterUsage = false;
  let lastReadyUrl = null;
  let lastObservedLimitUrl = null;
  let extensionContextDead = false;
  let recoveringFromContextInvalidation = false;
  let mutationObserver = null;
  let observeTimer = null;
  let heartbeatTimer = null;
  let observationRunning = false;

  function isExtensionContextInvalidated(error) {
    return /extension context invalidated/i.test(String(error?.message || error || ""));
  }

  function promptInjectionState() {
    try {
      const raw = sessionStorage.getItem("pasi_prompt_injection_state");
      if (!raw) {
        return null;
      }
      const state = JSON.parse(raw);
      return state && typeof state === "object" ? state : null;
    } catch (_error) {
      return null;
    }
  }

  function setPromptInjectionState(state) {
    try {
      sessionStorage.setItem(
        "pasi_prompt_injection_state",
        JSON.stringify(state)
      );
    } catch (_error) {
      // Page session storage is best-effort; the bridge remains authoritative.
    }
  }

  function clearPromptInjectionState() {
    try {
      sessionStorage.removeItem("pasi_prompt_injection_state");
    } catch (_error) {
      // Ignore unavailable page session storage.
    }
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
          recovery_completed: recoveryCompleted,
          loss_recorded: lossRecorded,
          resume_request_sent: resumeRequestSent,
          awaiting_fresh_chat: awaitingFreshChat,
          fresh_chat_created_after_usage: freshChatCreatedAfterUsage,
          prompt_injection: promptInjectionState(),
          at: new Date().toISOString(),
        })
      );
    } catch (_error) {
      // Page session storage may be unavailable; the reload still restores the
      // extension context and the bridge retains the authoritative task state.
    }
  }

  function markExtensionContextDead(error) {
    if (!isExtensionContextInvalidated(error) || extensionContextDead) {
      return false;
    }
    extensionContextDead = true;
    persistContextRecoveryState();

    if (mutationObserver) {
      mutationObserver.disconnect();
      mutationObserver = null;
    }
    if (observeTimer !== null) {
      window.clearTimeout(observeTimer);
      observeTimer = null;
    }
    if (heartbeatTimer !== null) {
      window.clearInterval(heartbeatTimer);
      heartbeatTimer = null;
    }
    return true;
  }

  async function storageGet(keys) {
    if (extensionContextDead) {
      return {};
    }
    try {
      return await chrome.storage.local.get(keys);
    } catch (error) {
      if (markExtensionContextDead(error)) {
        return {};
      }
      throw error;
    }
  }

  async function storageSet(values) {
    if (extensionContextDead) {
      return false;
    }
    try {
      await chrome.storage.local.set(values);
      return true;
    } catch (error) {
      if (markExtensionContextDead(error)) {
        return false;
      }
      throw error;
    }
  }

  async function emit(type, payload = {}) {
    if (extensionContextDead) {
      return null;
    }
    const message = protocol.envelope(type, payload);
    try {
      return await chrome.runtime.sendMessage(message);
    } catch (error) {
      if (markExtensionContextDead(error)) {
        return null;
      }
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
    if (extensionContextDead || active || awaitingAcceptance) {
      return false;
    }
    operationId = protocol.makeId();
    recoveryCompleted = false;
    resumeRequestSent = false;
    active = true;
    await emit(protocol.TYPES.OPERATION_STARTED, {
      operation_id: operationId,
      chat_url: chatgpt.currentChatUrl(),
      thinking_enabled: chatgpt.thinkingEnabled(),
      prior_chat_url: priorChatUrl,
    });
    return !extensionContextDead;
  }

  function resetOperationState() {
    active = false;
    awaitingAcceptance = false;
    operationId = null;
    checkpoint = null;
    recoveryPending = false;
    lossRecorded = false;
    recoveryCompleted = false;
    resumeRequestSent = false;
  }

  async function injectCurrentPrompt(prompt, taskId, promptGeneration) {
    if (extensionContextDead || !prompt || !taskId || !Number.isInteger(promptGeneration)) {
      return false;
    }

    const chatUrl = chatgpt.currentChatUrl();
    const stored = await storageGet([
      "pasi_injected_prompt_generation",
      "pasi_injected_chat_url",
    ]);
    if (extensionContextDead) {
      return false;
    }

    if (
      stored.pasi_injected_prompt_generation === promptGeneration &&
      stored.pasi_injected_chat_url === chatUrl &&
      chatgpt.hasUserMessageText(prompt)
    ) {
      return true;
    }

    const sessionInjection = promptInjectionState();
    const sameSessionInjection =
      sessionInjection &&
      sessionInjection.task_id === taskId &&
      sessionInjection.prompt_generation === promptGeneration &&
      sessionInjection.chat_url === chatUrl;

    if (
      sameSessionInjection &&
      sessionInjection.status === "sending" &&
      chatgpt.hasUserMessageText(prompt)
    ) {
      setPromptInjectionState({
        ...sessionInjection,
        status: "sent",
        confirmed_at: new Date().toISOString(),
      });
      return true;
    }

    if (
      sameSessionInjection &&
      sessionInjection.status === "sent" &&
      chatgpt.hasUserMessageText(prompt)
    ) {
      return true;
    }

    if (!chatgpt.isAuthenticatedPage() || chatgpt.isGenerating()) {
      return false;
    }

    if (!await startOperation()) {
      return false;
    }
    if (extensionContextDead) {
      return false;
    }

    if (!await chatgpt.ensureThinkingEnabled()) {
      resetOperationState();
      await emit(protocol.TYPES.RUNTIME_ERROR, {
        error: "Thinking could not be enabled; refusing automatic task prompt injection.",
        task_id: taskId,
        prompt_generation: promptGeneration,
      });
      return false;
    }
    if (extensionContextDead) {
      return false;
    }

    setPromptInjectionState({
      task_id: taskId,
      prompt_generation: promptGeneration,
      chat_url: chatUrl,
      status: "sending",
      started_at: new Date().toISOString(),
    });

    const sent = await chatgpt.injectPrompt(prompt);
    if (!sent) {
      clearPromptInjectionState();
      resetOperationState();
      await emit(protocol.TYPES.RUNTIME_ERROR, {
        error: "PASI could not inject the current task prompt into the ChatGPT composer.",
        task_id: taskId,
        prompt_generation: promptGeneration,
      });
      return false;
    }

    setPromptInjectionState({
      task_id: taskId,
      prompt_generation: promptGeneration,
      chat_url: chatUrl,
      status: "sent",
      confirmed_at: new Date().toISOString(),
    });

    if (!extensionContextDead) {
      await storageSet({
        pasi_injected_prompt_generation: promptGeneration,
        pasi_injected_task_id: taskId,
        pasi_injected_chat_url: chatUrl,
      });
    }

    await emit(protocol.TYPES.CHAT_USAGE, {
      task_id: taskId,
      prompt_generation: promptGeneration,
      chat_url: chatUrl,
      prompt_injected: true,
      operation_id: operationId,
    });
    return true;
  }

  async function markRecoveryPending(reason) {
    if (extensionContextDead || !active || awaitingAcceptance || recoveryCompleted) {
      return;
    }
    recoveryPending = true;
    if (lossRecorded) {
      return;
    }
    const response = chatgpt.latestAssistantMessage();
    const stopped = chatgpt.stopGeneration();
    await checkpointProgress("connection_loss", response);
    await emit(protocol.TYPES.CONNECTION_LOST, {
      operation_id: operationId,
      response_stopped_on_loss: stopped || Boolean(chatgpt.connectionErrorMessage()),
      checkpoint_preserved: Boolean(checkpoint),
      resume_phase: checkpoint?.resume_phase || "connection_loss",
      recovery_reason: reason,
      automatic_recovery_prompt_submitted: false,
    });
    lossRecorded = true;
  }

  async function beginRecovery(reason) {
    if (isExtensionContextInvalidated(reason) || reason === "extension context invalidated") {
      return;
    }
    await markRecoveryPending(reason);
  }

  async function handleChatLimit() {
    if (extensionContextDead) {
      return;
    }
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
    freshChatCreatedAfterUsage = false;
    if (!chatgpt.createFreshChat()) {
      awaitingFreshChat = false;
      freshChatCreatedAfterUsage = false;
      await emit(protocol.TYPES.RUNTIME_ERROR, {
        error: "Chat usage limit detected, but PASI could not create a fresh chat.",
      });
    }
  }

  async function ensureTaskChat() {
    if (extensionContextDead || active || awaitingAcceptance || awaitingFreshChat) {
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

  async function performObserve() {
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
      freshChatCreatedAfterUsage = true;
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

    if (
      active &&
      !awaitingAcceptance &&
      recoveryPending &&
      !connectionError &&
      generating &&
      !recoveryCompleted
    ) {
      await emit(protocol.TYPES.CONNECTION_RESTORED, {
        operation_id: operationId,
        resumed_after_reconnect: true,
        same_operation_resumed: true,
        resume_phase: checkpoint?.resume_phase || "connection_loss",
        recovery_via_new_prompt: false,
      });
      recoveryPending = false;
      recoveryCompleted = true;
      resumeRequestSent = false;
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
        fresh_chat_created_after_usage: freshChatCreatedAfterUsage,
        recovery_count: lossRecorded ? 1 : 0,
      });
    }

    await storageSet({
      last_chat_url: url,
      last_chat_used: Boolean(response),
    });
  }

  async function observe() {
    if (extensionContextDead || observationRunning) {
      return;
    }
    observationRunning = true;
    try {
      await performObserve();
    } catch (error) {
      if (!markExtensionContextDead(error)) {
        // Keep unrelated UI/runtime errors visible without destroying the
        // automation lifecycle.
        console.warn("[PASI] observe failed:", error);
      }
    } finally {
      observationRunning = false;
    }
  }

  function scheduleObserve(delay = 100) {
    if (extensionContextDead || observeTimer !== null) {
      return;
    }
    observeTimer = window.setTimeout(() => {
      observeTimer = null;
      void observe();
    }, delay);
  }

  async function handleBridgeMessage(message) {
    if (extensionContextDead) {
      return;
    }
    if (message.type === "pasi.inject_prompt") {
      const ok = await injectCurrentPrompt(
        message.prompt,
        message.task_id,
        message.prompt_generation
      );
      return {
        ok,
        type: "prompt_injection",
        task_id: message.task_id,
        prompt_generation: message.prompt_generation,
      };
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
      freshChatCreatedAfterUsage = false;
      clearPromptInjectionState();
      await emit(protocol.TYPES.CHAT_READY, {
        chat_url: priorChatUrl,
        authenticated_page: true,
        thinking_enabled: chatgpt.thinkingEnabled(),
        same_chat_continuation: true,
      });
      return {
        ok: !extensionContextDead,
        type: "acceptance_passed",
      };
    }

    return { ok: false, error: "unsupported_bridge_message" };
  }

  async function handleOffline() {
    if (!active || awaitingAcceptance) {
      return;
    }
    await beginRecovery("browser offline event");
  }

  async function handleOnline() {
    if (!active || awaitingAcceptance || !recoveryPending || resumeRequestSent) {
      return;
    }
    resumeRequestSent = true;
    await emit(protocol.TYPES.RESUME_REQUEST, {
      operation_id: operationId,
      resume_phase: checkpoint?.resume_phase || "connection_loss",
      resumed: false,
      recovery_via_new_prompt: false,
      awaiting_same_task_continuation: true,
    });
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
      recoveryPending = Boolean(state.recovery_pending);
      recoveryCompleted = Boolean(state.recovery_completed);
      lossRecorded = Boolean(state.loss_recorded);
      resumeRequestSent = Boolean(state.resume_request_sent);
      awaitingFreshChat = Boolean(state.awaiting_fresh_chat);
      freshChatCreatedAfterUsage = Boolean(state.fresh_chat_created_after_usage);
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

    try {
      chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
        if (extensionContextDead) {
          sendResponse({ ok: false, error: "extension_context_dead" });
          return false;
        }
        void handleBridgeMessage(message)
          .then((result) => sendResponse(result))
          .catch((error) => {
            if (markExtensionContextDead(error)) {
              sendResponse({ ok: false, error: "extension_context_dead" });
              return;
            }
            console.warn("[PASI] bridge message failed:", error);
            sendResponse({ ok: false, error: String(error) });
          });
        return true;
      });
    } catch (error) {
      if (!markExtensionContextDead(error)) {
        throw error;
      }
      return;
    }

    mutationObserver = new MutationObserver(() => {
      scheduleObserve(150);
    });
    mutationObserver.observe(document.documentElement, {
      subtree: true,
      childList: true,
      characterData: true,
    });

    heartbeatTimer = window.setInterval(() => {
      scheduleObserve(0);
    }, 1000);

    await observe();

    if (recoveringFromContextInvalidation) {
      // The operation/checkpoint has already been restored into this fresh
      // extension context. Continue observing the existing ChatGPT response;
      // do not inject or manufacture a recovery message.
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
