(() => {
  "use strict";
  if (globalThis.__PASI_HUMAN_TEST_RUNNER__) return;
  globalThis.__PASI_HUMAN_TEST_RUNNER__ = true;

  const MAX_TEXT = 20_000;

  function element(selector) {
    if (!selector) throw new Error("selector is required");
    const node = document.querySelector(selector);
    if (!node) throw new Error("element not found: " + selector);
    return node;
  }

  function visible(node) {
    const style = getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    return style.visibility !== "hidden"
      && style.display !== "none"
      && rect.width > 0
      && rect.height > 0;
  }

  async function waitFor(predicate, timeoutMs) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if (predicate()) return;
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    throw new Error("timed out waiting for condition");
  }

  async function run(step) {
    const timeoutMs = Number(step.timeout_ms || 10000);
    switch (step.action) {
      case "click": {
        const node = element(step.selector);
        if (!visible(node)) throw new Error("element is not visible");
        node.scrollIntoView({block: "center", inline: "center"});
        node.click();
        await new Promise((resolve) => setTimeout(resolve, 150));
        return {visible: true};
      }
      case "fill": {
        const node = element(step.selector);
        if (!visible(node)) throw new Error("input is not visible");
        const value = String(step.value ?? "");
        if (!("value" in node)) throw new Error("target is not a form control");
        const setter = Object.getOwnPropertyDescriptor(
          HTMLInputElement.prototype, "value"
        )?.set || Object.getOwnPropertyDescriptor(
          HTMLTextAreaElement.prototype, "value"
        )?.set;
        if (setter) setter.call(node, value);
        else node.value = value;
        node.dispatchEvent(new Event("input", {bubbles: true}));
        node.dispatchEvent(new Event("change", {bubbles: true}));
        return {filled: true};
      }
      case "assert_visible": {
        const node = element(step.selector);
        if (!visible(node)) throw new Error("element is not visible");
        return {visible: true};
      }
      case "assert_text": {
        const node = element(step.selector || "body");
        const actual = String(node.innerText || node.textContent || "").slice(0, MAX_TEXT);
        const expected = String(step.text ?? "");
        const ok = step.contains === false ? actual === expected : actual.includes(expected);
        if (!ok) throw new Error("assert_text failed");
        return {matched_text: expected};
      }
      case "wait_for_text": {
        const expected = String(step.text ?? "");
        await waitFor(() => {
          const actual = String(document.body?.innerText || "").slice(0, MAX_TEXT);
          return actual.includes(expected);
        }, timeoutMs);
        return {matched_text: expected};
      }
      case "assert_url_contains": {
        const expected = String(step.text ?? "");
        if (!location.href.includes(expected)) throw new Error("assert_url_contains failed");
        return {url: location.href};
      }
      case "wait_ms": {
        const duration = Math.min(Math.max(Number(step.timeout_ms || step.value || 100), 0), 120000);
        await new Promise((resolve) => setTimeout(resolve, duration));
        return {waited_ms: duration};
      }
      default:
        throw new Error("unsupported content action: " + step.action);
    }
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type !== "pasi-human-test-step") return undefined;
    run(message.step)
      .then((observed) => sendResponse({ok: true, observed}))
      .catch((error) => sendResponse({ok: false, error: String(error?.message || error)}));
    return true;
  });
})();