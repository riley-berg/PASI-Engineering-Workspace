importScripts("src/api_contract.js", "src/background-api.js");

(() => {
  "use strict";

  const EVENT_URL = "http://127.0.0.1:8765/event";
  const PROMPT_URL = "http://127.0.0.1:8765/prompt";

  async function bridgeRequest(url, options = {}) {
    const response = await fetch(url, options);
    const payload = await response.json();
    return {
      ok: response.ok && payload.ok !== false,
      payload,
    };
  }

  function isContextInvalidated(error) {
    return /extension context invalidated/i.test(String(error?.message || error || ""));
  }

  async function sendTabMessage(tabId, message) {
    try {
      const response = await chrome.tabs.sendMessage(tabId, message);
      return response?.ok === true;
    } catch (error) {
      return false;
    }
  }

  async function safeStorageSet(values) {
    try {
      await chrome.storage.local.set(values);
    } catch (error) {
      if (!isContextInvalidated(error)) {
        throw error;
      }
    }
  }

  async function sendCurrentPrompt(tabId) {
    const prompt = await bridgeRequest(PROMPT_URL);
    if (!prompt.ok) {
      return prompt;
    }
    const delivered = await sendTabMessage(tabId, {
      type: "pasi.inject_prompt",
      task_id: prompt.payload.task_id,
      prompt: prompt.payload.prompt,
      prompt_generation: prompt.payload.prompt_generation,
    });
    if (!delivered) {
      return {
        ok: false,
        error: "prompt_delivery_failed",
      };
    }
    return prompt;
  }

  async function forward(event, sender) {
    try {
      const result = await bridgeRequest(EVENT_URL, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(event),
      });

      if (
        (event.type === "chat_ready" || event.type === "fresh_chat") &&
        sender.tab?.id &&
        result.ok
      ) {
        await sendCurrentPrompt(sender.tab.id);
      }

      if (
        event.type === "response_complete" &&
        sender.tab?.id &&
        result.ok &&
        result.payload.acceptance?.status === "passed"
      ) {
        await sendTabMessage(sender.tab.id, {
          type: "pasi.acceptance_passed",
          task_id: result.payload.next_task_id,
          prompt_generation: result.payload.prompt_generation,
        });
      }

      return result.payload;
    } catch (error) {
      try {
        await safeStorageSet({
          last_bridge_error: {
            message: String(error),
            at: new Date().toISOString(),
          },
        });
      } catch (_storageError) {
        // Keep browser lifecycle failures from becoming a second automation error.
      }
      return { ok: false, error: String(error) };
    }
  }

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (String(message?.type || "").startsWith("pasi.api.")) {
      globalThis.PASIBackgroundAPI
        .handle(message, sender)
        .then(sendResponse)
        .catch((error) => sendResponse({
          ok: false,
          error: String(error?.message || error),
        }));
      return true;
    }

    forward(message, sender).then((result) => sendResponse(result));
    return true;
  });

  chrome.runtime.onInstalled.addListener(() => {
    void safeStorageSet({
      installed_at: new Date().toISOString(),
      protocol_version: "m0-v2",
      pasi_api_version: globalThis.PASIExtensionAPIContract.VERSION,
    });
  });
})();
