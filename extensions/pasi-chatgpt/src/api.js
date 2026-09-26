(() => {
  "use strict";

  const contract = globalThis.PASIExtensionAPIContract;
  if (!contract) {
    throw new Error("PASI extension API contract is unavailable");
  }

  function message(type, payload = {}) {
    return {
      protocol_version: contract.VERSION,
      type,
      ...payload,
    };
  }

  function send(messagePayload) {
    return new Promise((resolve, reject) => {
      try {
        chrome.runtime.sendMessage(messagePayload, (response) => {
          const runtimeError = chrome.runtime.lastError;
          if (runtimeError) {
            reject(new Error(runtimeError.message));
            return;
          }
          if (!response || response.ok !== true) {
            reject(new Error(response?.error || "PASI API request failed"));
            return;
          }
          resolve(response);
        });
      } catch (error) {
        reject(error);
      }
    });
  }

  const storage = Object.freeze({
    async get(key, options = {}) {
      const namespace = contract.normalizeNamespace(options.namespace || "default");
      const response = await send(message(contract.MESSAGE_TYPES.STORAGE_GET, {
        namespace,
        key: contract.normalizeKey(key),
      }));
      return response.value;
    },

    async set(key, value, options = {}) {
      const namespace = contract.normalizeNamespace(options.namespace || "default");
      await send(message(contract.MESSAGE_TYPES.STORAGE_SET, {
        namespace,
        key: contract.normalizeKey(key),
        value,
      }));
    },

    async remove(key, options = {}) {
      const namespace = contract.normalizeNamespace(options.namespace || "default");
      await send(message(contract.MESSAGE_TYPES.STORAGE_REMOVE, {
        namespace,
        key: contract.normalizeKey(key),
      }));
    },
  });

  async function request(options = {}) {
    const method = contract.normalizeMethod(options.method || "GET");
    const url = String(options.url || "");
    if (!/^https?:\/\//i.test(url)) {
      throw new TypeError("PASI HTTP requests require an absolute http(s) URL");
    }

    const response = await send(message(contract.MESSAGE_TYPES.HTTP_REQUEST, {
      method,
      url,
      headers: options.headers && typeof options.headers === "object"
        ? options.headers
        : {},
      body: options.body == null ? null : String(options.body),
      timeout_ms: contract.normalizeHttpTimeout(options.timeout_ms),
    }));

    return Object.freeze({
      ok: response.status >= 200 && response.status < 300,
      status: response.status,
      url: response.url || url,
      headers: Object.freeze(response.headers || {}),
      text: async () => String(response.body || ""),
      json: async () => JSON.parse(String(response.body || "")),
    });
  }

  function sleep(milliseconds) {
    return new Promise((resolve) => setTimeout(resolve, Math.max(0, Number(milliseconds) || 0)));
  }

  async function retry(operation, options = {}) {
    const attempts = Math.min(Math.max(Number(options.attempts) || 3, 1), 8);
    const baseDelayMs = Math.min(Math.max(Number(options.base_delay_ms) || 250, 25), 10000);
    let lastError = null;

    for (let attempt = 1; attempt <= attempts; attempt += 1) {
      try {
        return await operation(attempt);
      } catch (error) {
        lastError = error;
        if (attempt === attempts) {
          break;
        }
        await sleep(baseDelayMs * (2 ** (attempt - 1)));
      }
    }
    throw lastError || new Error("PASI retry failed");
  }

  function runtimeInfo() {
    const manifest = chrome.runtime.getManifest();
    return Object.freeze({
      id: chrome.runtime.id,
      name: manifest.name,
      version: manifest.version,
      api_version: contract.VERSION,
    });
  }

  const eventTarget = new EventTarget();

  const events = Object.freeze({
    on(type, listener) {
      eventTarget.addEventListener(String(type), listener);
      return () => eventTarget.removeEventListener(String(type), listener);
    },

    emit(type, detail = {}) {
      eventTarget.dispatchEvent(new CustomEvent(String(type), { detail }));
    },
  });

  globalThis.PASI = Object.freeze({
    api: Object.freeze({
      version: contract.VERSION,
    }),
    storage,
    http: Object.freeze({ request }),
    retry,
    sleep,
    runtime: Object.freeze({ info: runtimeInfo }),
    events,
  });
})();
