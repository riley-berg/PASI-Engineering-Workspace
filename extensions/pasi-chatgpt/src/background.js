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
      await chrome.tabs.sendMessage(tabId, message);
      return true;
    } catch (error) {
      // A stale content script can disappear while the service worker is
      // delivering a prompt. Treat that as a delivery failure, not as a new
      // task or a reason to create a new chat.
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
        // The service worker may be terminating at the same time as the
        // bridge request. Do not turn that lifecycle event into another
        // automation failure.
      }
      return { ok: false, error: String(error) };
    }
  }

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    forward(message, sender).then((result) => sendResponse(result));
    return true;
  });

  chrome.runtime.onInstalled.addListener(() => {
    void safeStorageSet({
      installed_at: new Date().toISOString(),
      protocol_version: "m0-v2",
    });
  });
})();
