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

  async function forward(event, sender) {
    try {
      const result = await bridgeRequest(EVENT_URL, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(event),
      });

      if (event.type === "fresh_chat" && sender.tab?.id && result.ok) {
        const prompt = await bridgeRequest(PROMPT_URL);
        if (prompt.ok) {
          await chrome.tabs.sendMessage(sender.tab.id, {
            type: "pasi.inject_prompt",
            task_id: prompt.payload.task_id,
            prompt: prompt.payload.prompt,
            prompt_generation: prompt.payload.prompt_generation,
          });
        }
      }

      if (
        event.type === "response_complete" &&
        sender.tab?.id &&
        result.ok &&
        result.payload.acceptance?.status === "passed"
      ) {
        await chrome.tabs.sendMessage(sender.tab.id, {
          type: "pasi.acceptance_passed",
          task_id: result.payload.next_task_id,
          prompt_generation: result.payload.prompt_generation,
        });
      }

      return result.payload;
    } catch (error) {
      await chrome.storage.local.set({
        last_bridge_error: {
          message: String(error),
          at: new Date().toISOString(),
        },
      });
      return { ok: false, error: String(error) };
    }
  }

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    forward(message, sender).then((result) => sendResponse(result));
    return true;
  });

  chrome.runtime.onInstalled.addListener(() => {
    chrome.storage.local.set({
      installed_at: new Date().toISOString(),
      protocol_version: "m0-v1",
    });
  });
})();
