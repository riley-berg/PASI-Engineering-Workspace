(() => {
  "use strict";

  const contract = globalThis.PASIExtensionAPIContract;
  if (!contract) {
    throw new Error("PASI extension API contract is unavailable");
  }

  const ALLOWED_HTTP_ORIGINS = new Set([
    "http://127.0.0.1:8765",
  ]);

  const SAFE_HEADER_NAMES = new Set([
    "accept",
    "authorization",
    "content-type",
    "idempotency-key",
    "x-pasi-request-id",
  ]);

  function senderIsChatGPT(sender) {
    const url = String(sender?.url || sender?.tab?.url || "");
    return /^https:\/\/chatgpt\.com\//i.test(url);
  }

  function prefixedKey(namespace, key) {
    return contract.namespacedKey(namespace, key);
  }

  function validateHeaders(headers) {
    if (!headers || typeof headers !== "object" || Array.isArray(headers)) {
      return {};
    }

    const result = {};
    const entries = Object.entries(headers);
    if (entries.length > contract.LIMITS.httpHeaderCount) {
      throw new TypeError("Too many PASI HTTP headers");
    }

    for (const [rawName, rawValue] of entries) {
      const name = String(rawName).trim().toLowerCase();
      const value = String(rawValue);
      if (!SAFE_HEADER_NAMES.has(name)) {
        throw new TypeError("HTTP header is not permitted: " + name);
      }
      if (value.length > contract.LIMITS.httpHeaderValueChars) {
        throw new TypeError("HTTP header is too large: " + name);
      }
      result[name] = value;
    }
    return result;
  }

  function validateHttpUrl(value) {
    const url = new URL(String(value || ""));
    if (!/^https?:$/.test(url.protocol)) {
      throw new TypeError("PASI HTTP supports only http(s) URLs");
    }
    if (!ALLOWED_HTTP_ORIGINS.has(url.origin)) {
      throw new Error("PASI HTTP origin is not permitted: " + url.origin);
    }
    return url;
  }

  async function handleStorageGet(message) {
    const key = prefixedKey(message.namespace, message.key);
    const values = await chrome.storage.local.get(key);
    return { ok: true, value: values[key] };
  }

  async function handleStorageSet(message) {
    const key = prefixedKey(message.namespace, message.key);
    await chrome.storage.local.set({ [key]: message.value });
    return { ok: true };
  }

  async function handleStorageRemove(message) {
    const key = prefixedKey(message.namespace, message.key);
    await chrome.storage.local.remove(key);
    return { ok: true };
  }

  async function handleHttpRequest(message) {
    const url = validateHttpUrl(message.url);
    const method = contract.normalizeMethod(message.method);
    const headers = validateHeaders(message.headers);
    const body = message.body == null ? null : String(message.body);

    if (body && body.length > contract.LIMITS.httpBodyChars) {
      throw new TypeError("PASI HTTP request body is too large");
    }
    if ((method === "GET" || method === "HEAD") && body) {
      throw new TypeError(method + " requests cannot include a PASI request body");
    }

    const controller = new AbortController();
    const timeout = setTimeout(
      () => controller.abort(),
      contract.normalizeHttpTimeout(message.timeout_ms)
    );

    try {
      const response = await fetch(url.toString(), {
        method,
        headers,
        body: body || undefined,
        credentials: "omit",
        cache: "no-store",
        redirect: "error",
        signal: controller.signal,
      });

      const responseBody = (method === "HEAD" || response.status === 204)
        ? ""
        : await response.text();

      return {
        ok: true,
        status: response.status,
        url: response.url,
        headers: Object.fromEntries(response.headers.entries()),
        body: responseBody.slice(0, contract.LIMITS.httpBodyChars),
      };
    } finally {
      clearTimeout(timeout);
    }
  }

  async function handle(message, sender) {
    if (!senderIsChatGPT(sender)) {
      throw new Error("PASI extension API caller is not an authenticated ChatGPT tab");
    }

    switch (message?.type) {
      case contract.MESSAGE_TYPES.STORAGE_GET:
        return handleStorageGet(message);
      case contract.MESSAGE_TYPES.STORAGE_SET:
        return handleStorageSet(message);
      case contract.MESSAGE_TYPES.STORAGE_REMOVE:
        return handleStorageRemove(message);
      case contract.MESSAGE_TYPES.HTTP_REQUEST:
        return handleHttpRequest(message);
      default:
        throw new Error("Unknown PASI extension API method");
    }
  }

  globalThis.PASIBackgroundAPI = Object.freeze({ handle });
})();
