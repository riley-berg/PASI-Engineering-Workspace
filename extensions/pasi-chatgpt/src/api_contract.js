(() => {
  "use strict";

  const VERSION = "pasi-api-v1";
  const MESSAGE_TYPES = Object.freeze({
    STORAGE_GET: "pasi.api.storage.get",
    STORAGE_SET: "pasi.api.storage.set",
    STORAGE_REMOVE: "pasi.api.storage.remove",
    HTTP_REQUEST: "pasi.api.http.request",
  });

  const HTTP_METHODS = Object.freeze([
    "GET",
    "HEAD",
    "POST",
    "PUT",
    "PATCH",
    "DELETE",
  ]);

  const LIMITS = Object.freeze({
    storageKeyChars: 200,
    storageNamespaceChars: 100,
    httpTimeoutMs: 30000,
    httpBodyChars: 1000000,
    httpHeaderCount: 32,
    httpHeaderValueChars: 4000,
  });

  function normalizeNamespace(value) {
    const namespace = String(value || "default").trim();
    if (!/^[A-Za-z0-9._:-]{1,100}$/.test(namespace)) {
      throw new TypeError("Invalid PASI storage namespace");
    }
    return namespace;
  }

  function normalizeKey(value) {
    const key = String(value || "").trim();
    if (!/^[A-Za-z0-9._:/-]{1,200}$/.test(key)) {
      throw new TypeError("Invalid PASI storage key");
    }
    return key;
  }

  function namespacedKey(namespace, key) {
    return "pasi:api:" + normalizeNamespace(namespace) + ":" + normalizeKey(key);
  }

  function normalizeMethod(value) {
    const method = String(value || "GET").trim().toUpperCase();
    if (!HTTP_METHODS.includes(method)) {
      throw new TypeError("Unsupported HTTP method: " + method);
    }
    return method;
  }

  function normalizeHttpTimeout(value) {
    const timeout = Number(value);
    if (!Number.isFinite(timeout)) {
      return 10000;
    }
    return Math.min(Math.max(Math.floor(timeout), 250), LIMITS.httpTimeoutMs);
  }

  globalThis.PASIExtensionAPIContract = Object.freeze({
    VERSION,
    MESSAGE_TYPES,
    HTTP_METHODS,
    LIMITS,
    normalizeNamespace,
    normalizeKey,
    namespacedKey,
    normalizeMethod,
    normalizeHttpTimeout,
  });
})();
