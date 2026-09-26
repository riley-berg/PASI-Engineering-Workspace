(() => {
  "use strict";

  const VERSION = "pasi-api-v2";

  const MESSAGE_TYPES = Object.freeze({
    STORAGE_GET: "pasi.api.storage.get",
    STORAGE_SET: "pasi.api.storage.set",
    STORAGE_REMOVE: "pasi.api.storage.remove",
    STORAGE_LIST: "pasi.api.storage.list",
    STORAGE_INFO: "pasi.api.storage.info",
    STORAGE_WATCH: "pasi.api.storage.watch",
    DB_ENSURE: "pasi.api.db.ensure",
    DB_GET: "pasi.api.db.get",
    DB_PUT: "pasi.api.db.put",
    DB_DELETE: "pasi.api.db.delete",
    DB_KEYS: "pasi.api.db.keys",
    DB_CLEAR: "pasi.api.db.clear",
    HTTP_REQUEST: "pasi.api.http.request",
    HTTP_GRANT_ORIGIN: "pasi.api.http.grant_origin",
    HTTP_REVOKE_ORIGIN: "pasi.api.http.revoke_origin",
    HTTP_HAS_ORIGIN: "pasi.api.http.has_origin",
    HTTP_STREAM_START: "pasi.api.http.stream.start",
    MENU_REGISTER: "pasi.api.menu.register",
    MENU_UNREGISTER: "pasi.api.menu.unregister",
    MENU_LIST: "pasi.api.menu.list",
    TAB_OPEN: "pasi.api.tab.open",
    TAB_CLOSE: "pasi.api.tab.close",
    TAB_FOCUS: "pasi.api.tab.focus",
    TAB_SEND: "pasi.api.tab.send",
    TAB_BROADCAST: "pasi.api.tab.broadcast",
    TAB_LIST: "pasi.api.tab.list",
    WEB_OBSERVE: "pasi.api.web.observe",
    WEB_UNOBSERVE: "pasi.api.web.unobserve",
    WEB_RULE_ADD: "pasi.api.web.rule.add",
    WEB_RULE_REMOVE: "pasi.api.web.rule.remove",
    WEB_RULE_LIST: "pasi.api.web.rule.list",
    NOTIFICATION_SHOW: "pasi.api.notification.show",
    CLIPBOARD_WRITE: "pasi.api.clipboard.write",
    CLIPBOARD_READ: "pasi.api.clipboard.read",
    DOWNLOAD: "pasi.api.download",
    PERMISSION_CONTAINS: "pasi.api.permission.contains",
    PERMISSION_REQUEST: "pasi.api.permission.request",
    PERMISSION_REMOVE: "pasi.api.permission.remove",
    SCRIPT_REGISTER: "pasi.api.script.register",
    SCRIPT_UNREGISTER: "pasi.api.script.unregister",
    SCRIPT_EXECUTE: "pasi.api.script.execute",
    SCRIPT_LIST: "pasi.api.script.list",
    USERSCRIPT_INSTALL: "pasi.userscript.install",
    USERSCRIPT_REGISTER: "pasi.userscript.register",
    USERSCRIPT_UNREGISTER: "pasi.userscript.unregister",
    USERSCRIPT_LIST: "pasi.userscript.list",
    USERSCRIPT_ENABLE: "pasi.userscript.enable",
    USERSCRIPT_DISABLE: "pasi.userscript.disable",
    USERSCRIPT_INFO: "pasi.userscript.info",
    USERSCRIPT_UPDATE: "pasi.userscript.update",
    USERSCRIPT_BACKUP: "pasi.userscript.backup",
    USERSCRIPT_RESTORE: "pasi.userscript.restore",
    USERSCRIPT_SYNC: "pasi.userscript.sync",
    USERSCRIPT_NETWORK_ADD: "pasi.userscript.network.add",
    USERSCRIPT_NETWORK_REMOVE: "pasi.userscript.network.remove",
    USERSCRIPT_NETWORK_LIST: "pasi.userscript.network.list",
    USERSCRIPT_HOSTS: "pasi.userscript.hosts",
    USERSCRIPT_ACTIVE_TAB: "pasi.userscript.active_tab",
    USERSCRIPT_SOURCE_GET: "pasi.userscript.source.get",
    USERSCRIPT_SOURCE_SAVE: "pasi.userscript.source.save",
    USERSCRIPT_VCS_CONFIG: "pasi.userscript.vcs.config",
    USERSCRIPT_VCS_FETCH: "pasi.userscript.vcs.fetch",
    USERSCRIPT_VCS_PULL: "pasi.userscript.vcs.pull",
    USERSCRIPT_VCS_PUSH: "pasi.userscript.vcs.push",
    USERSCRIPT_SYNC_STATUS: "pasi.userscript.sync.status",
    USERSCRIPT_SYNC_RESOLVE: "pasi.userscript.sync.resolve",
    USERSCRIPT_RPC: "pasi.userscript.rpc",
  });

  const HTTP_METHODS = Object.freeze([
    "GET",
    "HEAD",
    "POST",
    "PUT",
    "PATCH",
    "DELETE",
    "OPTIONS",
  ]);

  const LIMITS = Object.freeze({
    storageKeyChars: 200,
    storageNamespaceChars: 100,
    httpTimeoutMs: 30000,
    httpBodyChars: 1000000,
    httpHeaderCount: 48,
    httpHeaderValueChars: 8000,
    httpStreamChunkBytes: 256 * 1024,
    httpStreamBufferBytes: 4 * 1024 * 1024,
    databaseNameChars: 120,
    objectStoreNameChars: 120,
    menuTitleChars: 80,
    tabMessageChars: 500000,
    downloadFilenameChars: 240,
    notificationTitleChars: 120,
    notificationMessageChars: 1000,
    userScriptSourceChars: 500000,
    userScriptIdChars: 100,
    userScriptNameChars: 160,
    userScriptConnectChars: 120,
    userScriptRuleIdChars: 80,
  });

  const SAFE_HTTP_HEADERS = Object.freeze([
    "accept",
    "accept-language",
    "authorization",
    "content-type",
    "idempotency-key",
    "if-match",
    "if-none-match",
    "x-pasi-request-id",
    "x-pasi-client",
  ]);

  function normalizeString(value, name, maxChars, allowEmpty = false) {
    const result = String(value ?? "").trim();
    if (!allowEmpty && !result) {
      throw new TypeError("Missing PASI " + name);
    }
    if (result.length > maxChars) {
      throw new TypeError("PASI " + name + " exceeds its size limit");
    }
    return result;
  }

  function normalizeNamespace(value) {
    const namespace = normalizeString(
      value || "default",
      "storage namespace",
      LIMITS.storageNamespaceChars,
    );
    if (!/^[A-Za-z0-9._:-]+$/.test(namespace)) {
      throw new TypeError("Invalid PASI storage namespace");
    }
    return namespace;
  }

  function normalizeKey(value) {
    const key = normalizeString(value, "storage key", LIMITS.storageKeyChars);
    if (!/^[A-Za-z0-9._:/@+-]+$/.test(key)) {
      throw new TypeError("Invalid PASI storage key");
    }
    return key;
  }

  function namespacedKey(namespace, key) {
    return "pasi:api:" + normalizeNamespace(namespace) + ":" + normalizeKey(key);
  }

  function namespacePrefix(namespace) {
    return "pasi:api:" + normalizeNamespace(namespace) + ":";
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

  function normalizeUrl(value) {
    const url = new URL(String(value || ""));
    if (!/^https?:$/.test(url.protocol)) {
      throw new TypeError("PASI HTTP supports only http(s) URLs");
    }
    return url;
  }

  function normalizeHeaders(headers) {
    if (!headers || typeof headers !== "object" || Array.isArray(headers)) {
      return {};
    }
    const result = {};
    for (const [rawName, rawValue] of Object.entries(headers)) {
      const name = String(rawName).trim().toLowerCase();
      const value = String(rawValue);
      if (!SAFE_HTTP_HEADERS.includes(name)) {
        throw new TypeError("HTTP header is not permitted: " + name);
      }
      if (value.length > LIMITS.httpHeaderValueChars) {
        throw new TypeError("HTTP header is too large: " + name);
      }
      result[name] = value;
    }
    if (Object.keys(result).length > LIMITS.httpHeaderCount) {
      throw new TypeError("Too many PASI HTTP headers");
    }
    return result;
  }

  function normalizeTimeout(value) {
    const timeout = Number(value);
    if (!Number.isFinite(timeout)) return 10000;
    return Math.min(Math.max(Math.floor(timeout), 250), LIMITS.httpTimeoutMs);
  }

  function normalizeOriginPattern(value) {
    const url = normalizeUrl(value);
    return url.protocol + "//" + url.host + "/*";
  }

  globalThis.PASIExtensionAPIContract = Object.freeze({
    VERSION,
    MESSAGE_TYPES,
    HTTP_METHODS,
    LIMITS,
    SAFE_HTTP_HEADERS,
    normalizeNamespace,
    normalizeKey,
    namespacedKey,
    namespacePrefix,
    normalizeMethod,
    normalizeUrl,
    normalizeHeaders,
    normalizeTimeout,
    normalizeOriginPattern,
  });
})();
