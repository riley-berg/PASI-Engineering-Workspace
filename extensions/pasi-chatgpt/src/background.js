(() => {
  "use strict";

  const BRIDGE = "http://127.0.0.1:8765/event";

  async function forward(event) {
    try {
      await fetch(BRIDGE, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(event),
      });
    } catch (error) {
      await chrome.storage.local.set({
        last_bridge_error: {
          message: String(error),
          at: new Date().toISOString(),
        },
      });
    }
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    forward(message).then(() => sendResponse({ ok: true }));
    return true;
  });

  chrome.runtime.onInstalled.addListener(() => {
    chrome.storage.local.set({
      installed_at: new Date().toISOString(),
      protocol_version: "m0-v1",
    });
  });
})();
