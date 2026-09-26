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
    const composer = document.querySelector(
      "textarea, [contenteditable=\"true\"], [data-testid*=\"composer\"]"
    );
    return urlOk && Boolean(composer);
  }

  function thinkingEnabled() {
    const labels = visibleElements("button, [role=\"button\"], [aria-label]")
      .map(elementLabel)
      .filter(Boolean)
      .join(" ");
    return /thinking/i.test(labels);
  }

  function isGenerating() {
    return Boolean(findAction([
      /stop generating/i,
      /^stop$/i,
      /stop response/i,
    ]));
  }

  function stopGeneration() {
    const button = findAction([
      /stop generating/i,
      /^stop$/i,
      /stop response/i,
    ]);
    if (!button) {
      return false;
    }
    button.click();
    return true;
  }

  function resumeGeneration() {
    const button = findAction([
      /continue generating/i,
      /resume generating/i,
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
    ]);
    if (!button) {
      return false;
    }
    button.click();
    return true;
  }

  function latestAssistantMessage() {
    const messages = visibleElements('[data-message-author-role="assistant"]');
    if (!messages.length) {
      return "";
    }
    return cleanText(messages[messages.length - 1].innerText);
  }

  function hasCompletePasiResponse(text) {
    return /PASI_RESULT_STATUS:\s*complete/i.test(text);
  }

  globalThis.PASIChatGPT = Object.freeze({
    currentChatUrl,
    isAuthenticatedPage,
    thinkingEnabled,
    isGenerating,
    stopGeneration,
    resumeGeneration,
    createFreshChat,
    latestAssistantMessage,
    hasCompletePasiResponse,
  });
})();
