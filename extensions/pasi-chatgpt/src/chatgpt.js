(() => {
  "use strict";

  function cleanText(value) {
    return String(value || "").replace(/\s+/g, " ").trim();
  }

  function visibleElements(selector) {
    return Array.from(document.querySelectorAll(selector)).filter((element) => {
      const style = globalThis.getComputedStyle(element);
      return style.visibility !== "hidden" && style.display !== "none";
    });
  }

  function elementLabel(element) {
    return cleanText(
      element.getAttribute("aria-label") ||
      element.getAttribute("title") ||
      element.textContent
    );
  }

  function findAction(patterns) {
    for (const element of visibleElements("button, a, [role=\"button\"]")) {
      const label = elementLabel(element);
      if (patterns.some((pattern) => pattern.test(label))) {
        return element;
      }
    }
    return null;
  }

  function currentChatUrl() {
    return location.href;
  }

  function isAuthenticatedPage() {
    const urlOk = /^https:\/\/chatgpt\.com\/c\/[A-Za-z0-9_-]+$/.test(currentChatUrl());
    const composer = findComposer();
    return urlOk && Boolean(composer);
  }

  function thinkingControl() {
    const candidates = visibleElements(
      "button, [role=\"button\"], [role=\"menuitemradio\"], [role=\"option\"], [aria-pressed], [aria-checked]"
    );
    for (const element of candidates) {
      const label = elementLabel(element);
      if (!/\bthinking\b/i.test(label)) {
        continue;
      }
      const pressed = element.getAttribute("aria-pressed");
      const checked = element.getAttribute("aria-checked");
      const state = element.getAttribute("data-state");
      if (pressed === "true" || checked === "true" || state === "on") {
        return { element, enabled: true };
      }
      if (pressed === "false" || checked === "false" || state === "off") {
        return { element, enabled: false };
      }
    }
    return null;
  }

  function thinkingEnabled() {
    const direct = thinkingControl();
    if (direct) {
      return direct.enabled;
    }
    return /\bthinking\b/i.test(
      visibleElements("button, [role=\"button\"], [aria-label], [title]")
        .map(elementLabel)
        .filter(Boolean)
        .join(" ")
    );
  }

  function ensureThinkingEnabled() {
    const direct = thinkingControl();
    if (direct?.enabled) {
      return true;
    }
    if (direct?.element && !direct.element.disabled) {
      direct.element.click();
      return thinkingEnabled();
    }

    const picker = findAction([
      /thinking/i,
      /model/i,
      /instant/i,
    ]);
    if (picker && !picker.disabled) {
      picker.click();
      const option = visibleElements(
        "[role=\"menuitemradio\"], [role=\"option\"], button, [role=\"menuitem\"]"
      ).find((element) => {
        const label = elementLabel(element);
        return /^thinking(?:\s+mode)?$/i.test(label) || /\bthinking\b/i.test(label);
      });
      if (option && !option.disabled) {
        const pressed = option.getAttribute("aria-pressed");
        const checked = option.getAttribute("aria-checked");
        const state = option.getAttribute("data-state");
        if (pressed !== "true" && checked !== "true" && state !== "on") {
          option.click();
        }
      }
    }

    return thinkingEnabled();
  }

  function isGenerating() {
    return Boolean(findAction([
      /stop generating/i,
      /^stop$/i,
      /stop response/i,
      /cancel response/i,
    ]));
  }

  function stopGeneration() {
    const button = findAction([
      /stop generating/i,
      /^stop$/i,
      /stop response/i,
      /cancel response/i,
    ]);
    if (!button) {
      return false;
    }
    button.click();
    return true;
  }

  function createFreshChat() {
    const button = findAction([
      /^new chat$/i,
      /new chat/i,
      /new conversation/i,
    ]);
    if (!button) {
      return false;
    }
    button.click();
    return true;
  }

  function findComposer() {
    const candidates = visibleElements(
      "textarea, [contenteditable=\"true\"], [data-testid*=\"composer\"]"
    );
    return candidates.length ? candidates[candidates.length - 1] : null;
  }

  function hasUserMessage() {
    return visibleElements('[data-message-author-role="user"]').length > 0;
  }

  function setComposerValue(composer, value) {
    composer.focus();

    if (composer instanceof HTMLTextAreaElement) {
      const setter = Object.getOwnPropertyDescriptor(
        HTMLTextAreaElement.prototype,
        "value"
      )?.set;
      if (!setter) {
        return false;
      }
      setter.call(composer, value);
      composer.dispatchEvent(new Event("input", { bubbles: true }));
      composer.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    }

    composer.textContent = value;
    composer.dispatchEvent(new InputEvent("input", {
      bubbles: true,
      inputType: "insertText",
      data: value,
    }));
    return true;
  }

  function sendPrompt() {
    const button = findAction([
      /send prompt/i,
      /^send$/i,
      /send message/i,
    ]);
    if (button && !button.disabled) {
      button.click();
      return true;
    }

    const composer = findComposer();
    if (!composer) {
      return false;
    }

    composer.dispatchEvent(new KeyboardEvent("keydown", {
      key: "Enter",
      code: "Enter",
      bubbles: true,
      cancelable: true,
    }));
    return true;
  }

  function injectPrompt(prompt) {
    if (!prompt || isGenerating() || !isAuthenticatedPage()) {
      return false;
    }
    if (!ensureThinkingEnabled()) {
      return false;
    }
    const composer = findComposer();
    if (!composer || !setComposerValue(composer, prompt)) {
      return false;
    }
    return sendPrompt();
  }

  function latestAssistantMessage() {
    const messages = visibleElements('[data-message-author-role="assistant"]');
    if (!messages.length) {
      return "";
    }
    return cleanText(messages[messages.length - 1].innerText);
  }

  function visibleAlertText() {
    return visibleElements(
      '[role="alert"], [data-testid*="error"], [data-testid*="toast"], [data-testid*="notification"]'
    )
      .map((element) => cleanText(element.innerText || element.textContent))
      .filter(Boolean)
      .join(" ");
  }

  function connectionErrorMessage() {
    const text = visibleAlertText();
    return /connection\s+(?:lost|error)|network\s+error|disconnected|reconnect|error\s+generating|failed\s+to\s+generate|something\s+went\s+wrong/i.test(text)
      ? text
      : "";
  }

  function chatLimitReached() {
    const text = visibleAlertText();
    return /you(?:'|’)ve\s+reached.*(?:limit|max|maximum)|maximum.*(?:reached|length)|conversation.*(?:too\s+long|limit)|start\s+a\s+new\s+chat|new\s+chat\s+to\s+continue/i.test(text);
  }

  function hasCompletePasiResponse(text) {
    return /PASI_RESULT_STATUS:\s*complete/i.test(text);
  }

  globalThis.PASIChatGPT = Object.freeze({
    currentChatUrl,
    isAuthenticatedPage,
    thinkingEnabled,
    ensureThinkingEnabled,
    isGenerating,
    stopGeneration,
    createFreshChat,
    findComposer,
    hasUserMessage,
    injectPrompt,
    latestAssistantMessage,
    connectionErrorMessage,
    chatLimitReached,
    hasCompletePasiResponse,
  });
})();
