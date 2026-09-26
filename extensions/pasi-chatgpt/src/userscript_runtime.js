(() => {
  "use strict";

  function build(definition) {
    const script = {
      id: String(definition.id),
      name: String(definition.name || definition.id),
      grants: Array.isArray(definition.grants) ? definition.grants.map(String) : [],
      mainWorld: definition.grants?.includes("mainWorld") === true,
    };
    const encoded = JSON.stringify({id: script.id, name: script.name, grants: script.grants, mainWorld: script.mainWorld});
    const auth = String(definition.auth || "");
    return `(() => {
  "use strict";
  const META = ${encoded};
  const pending = new Map();
  let sequence = 0;
  const listeners = new Map();
  const menuCallbacks = new Map();
  const connection = chrome.runtime.connect({name: "pasi.userscript:" + META.id});
  let contextAlive = true;
  let disposed = false;

  connection.onMessage.addListener((message) => {
    if (message?.type === "value-change") {
      const callbacks = listeners.get(message.listener_id);
      if (callbacks) callbacks.forEach((listener) => listener(
        message.key,
        message.old_value,
        message.new_value,
        message.remote === true,
      ));
    }
    if (message?.type === "menu-command") {
      const callback = menuCallbacks.get(message.command_id);
      if (callback) Promise.resolve(callback(message.info)).catch((error) => {
        void call("script.error", {
          message: String(error?.message || error),
          stack: String(error?.stack || ""),
        });
      });
    }
  });

  function contextError(message) {
    const error = new Error(message || "PASI extension context invalidated");
    error.name = "PASIExtensionContextError";
    return error;
  }

  function rejectPending(error) {
    for (const [requestId, pendingRequest] of pending.entries()) {
      pending.delete(requestId);
      pendingRequest.reject(error);
    }
  }

  async function safeSendMessage(payload, attempt = 0) {
    if (!contextAlive || disposed || !chrome.runtime?.id) {
      throw contextError("PASI extension context invalidated");
    }
    return new Promise((resolve, reject) => {
      try {
        chrome.runtime.sendMessage(payload, (response) => {
          const error = chrome.runtime.lastError;
          if (error) {
            const message = String(error.message || error);
            if (/context|receiving end|message port/i.test(message) && attempt < 2 && contextAlive && chrome.runtime?.id) {
              setTimeout(() => safeSendMessage(payload, attempt + 1).then(resolve, reject), 100 * (attempt + 1));
              return;
            }
            reject(new Error(message));
            return;
          }
          resolve(response);
        });
      } catch (error) {
        reject(error);
      }
    });
  }

  function call(method, args = {}) {
    if (!contextAlive || disposed) return Promise.reject(contextError());
    const requestId = META.id + ":" + (++sequence);
    return new Promise((resolve, reject) => {
      pending.set(requestId, {resolve, reject});
      safeSendMessage({
        type: "pasi.userscript.rpc",
        script_id: META.id,
        auth: ${JSON.stringify(auth)},
        request_id: requestId,
        method,
        args,
      }, (response) => {
        const error = chrome.runtime.lastError;
        if (error) {
          pending.delete(requestId);
          reject(new Error(error.message));
          return;
        }
        pending.delete(requestId);
        if (!response || response.ok !== true) {
          reject(new Error(response?.error || "PASI userscript request failed"));
          return;
        }
        resolve(response.value);
      });
    });
  }

  function requireGrant(grant, action) {
    if (!META.grants.includes(grant)) {
      throw new Error("PASI userscript grant required: " + action + " -> @" + grant);
    }
  }

  async function getValue(key, defaultValue) {
    requireGrant("storage", "getValue");
    const result = await call("storage.get", {key: String(key), defaultValue});
    return result === undefined ? defaultValue : result;
  }

  async function setValue(key, value) {
    requireGrant("storage", "setValue");
    return call("storage.set", {key: String(key), value});
  }

  async function deleteValue(key) {
    requireGrant("storage", "deleteValue");
    return call("storage.delete", {key: String(key)});
  }

  async function listValues() {
    requireGrant("storage", "listValues");
    return call("storage.list");
  }

  async function addValueChangeListener(key, listener) {
    requireGrant("storage", "addValueChangeListener");
    if (typeof listener !== "function") throw new TypeError("listener must be a function");
    const listenerId = await call("storage.watch", {key: String(key)});
    if (!listeners.has(listenerId)) listeners.set(listenerId, new Set());
    listeners.get(listenerId).add(listener);
    return listenerId;
  }

  function removeValueChangeListener(listenerId) {
    const id = String(listenerId);
    listeners.delete(id);
    return call("storage.unwatch", {listener_id: id});
  }

  function openInTab(url, options = {}) {
    requireGrant("tabs", "openInTab");
    return call("tabs.open", {url: String(url), active: options.active !== false});
  }

  function notify(details, title) {
    requireGrant("notification", "notification");
    const payload = typeof details === "string" ? {message: details, title: title || META.name} : {...details};
    return call("notification", payload);
  }

  function setClipboard(text) {
    requireGrant("clipboard", "setClipboard");
    return call("clipboard.write", {text: String(text)});
  }

  function download(details, name) {
    requireGrant("download", "download");
    const payload = typeof details === "string" ? {url: details, filename: name} : {...details};
    return call("download", payload);
  }

  function registerMenuCommand(name, callback, options = {}) {
    requireGrant("menu", "registerMenuCommand");
    if (typeof callback !== "function") throw new TypeError("menu callback must be a function");
    return call("menu.register", {
      title: String(name),
      accessKey: options.accessKey ? String(options.accessKey) : undefined,
    }).then((commandId) => {
      menuCallbacks.set(commandId, callback);
      return commandId;
    });
  }

  function request(config) {
    requireGrant("http", "xmlhttpRequest");
    const payload = {
      url: String(config?.url || ""),
      method: String(config?.method || "GET").toUpperCase(),
      headers: config?.headers || {},
      data: config?.data == null ? null : String(config.data),
      timeout_ms: Number(config?.timeout || 10000),
    };
    return call("http.request", payload).then((response) => {
      const result = {
        readyState: 4,
        status: response.status,
        statusText: response.statusText || "",
        responseHeaders: response.headers || {},
        response: response.body || "",
        responseText: response.body || "",
        finalUrl: response.url || payload.url,
      };
      if (response.ok) config?.onload?.(result);
      else config?.onerror?.(result);
      return result;
    }).catch((error) => {
      config?.onerror?.({error: String(error)});
      throw error;
    });
  }

  async function fetchApi(input, init = {}) {
    requireGrant("http", "fetch");
    const payload = typeof input === "string" ? {url: input, ...init} : {url: String(input?.url || ""), ...init};
    const result = await call("http.fetch", {
      url: String(payload.url || ""),
      method: String(payload.method || "GET").toUpperCase(),
      headers: payload.headers || {},
      body: payload.body == null ? undefined : String(payload.body),
      timeout_ms: Number(payload.timeout || 10000),
    });
    return {
      ok: result.ok,
      status: result.status,
      statusText: result.statusText,
      url: result.url,
      headers: result.headers,
      text: async () => result.body || "",
      json: async () => JSON.parse(result.body || "null"),
    };
  }

  function addNetworkRule(rule) {
    requireGrant("webRequest", "network.add");
    return call("network.add", {rule});
  }

  function removeNetworkRule(ruleId) {
    requireGrant("webRequest", "network.remove");
    return call("network.remove", {rule_id: String(ruleId)});
  }

  function listNetworkRules() {
    requireGrant("webRequest", "network.list");
    return call("network.list");
  }

  function trackCleanup(callback) {
    if (typeof callback !== "function") throw new TypeError("cleanup callback must be a function");
    cleanupCallbacks.add(callback);
    return () => cleanupCallbacks.delete(callback);
  }

  const cleanupCallbacks = new Set();

  const PASIUserScript = {
    info: Object.freeze({...META}),
    getValue,
    setValue,
    deleteValue,
    listValues,
    addValueChangeListener,
    removeValueChangeListener,
    openInTab,
    notification: notify,
    setClipboard,
    download,
    registerMenuCommand,
    xmlhttpRequest: request,
    httpRequest: request,
    fetch: fetchApi,
    network: Object.freeze({
      addRule: addNetworkRule,
      removeRule: removeNetworkRule,
      listRules: listNetworkRules,
    }),
    trackCleanup,
    get unsafeWindow() {
      if (!META.mainWorld) throw new Error("unsafeWindow requires @grant unsafeWindow and @grant mainWorld");
      return window;
    },
  };

  function cleanup() {
    if (disposed) return;
    try {
      safeSendMessage({
        type: "pasi.userscript.rpc",
        script_id: META.id,
        auth: "",
        method: "lifecycle.cleanup",
        args: {},
      }).catch(() => undefined);
    } catch (_) {}
    disposed = true;
    contextAlive = false;
    for (const callback of cleanupCallbacks) {
      try { callback(); } catch (_) {}
    }
    cleanupCallbacks.clear();
    rejectPending(contextError("PASI extension context closed"));
    try { connection.disconnect(); } catch (_) {}
  }

  connection.onDisconnect.addListener(() => {
    contextAlive = false;
    rejectPending(contextError("PASI extension context invalidated"));
  });

  globalThis.addEventListener?.("pagehide", cleanup, {once: true});
  globalThis.addEventListener?.("beforeunload", cleanup, {once: true});

  function reportError(error) {
    void call("script.error", {
      message: String(error?.message || error || ""),
      stack: String(error?.stack || ""),
    }).catch(() => undefined);
  }

  globalThis.addEventListener?.("error", (event) => reportError(event.error || new Error(event.message || "userscript error")), true);
  globalThis.addEventListener?.("unhandledrejection", (event) => reportError(event.reason), true);

  Object.defineProperties(globalThis, {
    PASIUserScript: {value: Object.freeze(PASIUserScript), configurable: false, enumerable: true},
    GM_info: {value: Object.freeze({script: Object.freeze({...META})}), configurable: false},
    GM_getValue: {value: getValue, configurable: false},
    GM_setValue: {value: setValue, configurable: false},
    GM_deleteValue: {value: deleteValue, configurable: false},
    GM_listValues: {value: listValues, configurable: false},
    GM_addValueChangeListener: {value: addValueChangeListener, configurable: false},
    GM_removeValueChangeListener: {value: removeValueChangeListener, configurable: false},
    GM_openInTab: {value: openInTab, configurable: false},
    GM_notification: {value: notify, configurable: false},
    GM_setClipboard: {value: setClipboard, configurable: false},
    GM_download: {value: download, configurable: false},
    GM_registerMenuCommand: {value: registerMenuCommand, configurable: false},
    GM_xmlhttpRequest: {value: request, configurable: false},
    GM_fetch: {value: fetchApi, configurable: false},
    GM_webRequest: {value: Object.freeze({addRule: addNetworkRule, removeRule: removeNetworkRule, listRules: listNetworkRules}), configurable: false},
  });
})()`;
  }

  function wrapSource(source) {
    return `(() => {
  "use strict";
  Promise.resolve((async () => {
    ${String(source || "")}
  })()).catch((error) => {
    globalThis.dispatchEvent(new ErrorEvent("error", {
      error,
      message: String(error?.message || error || "userscript error"),
    }));
  });
})()`;
  }

  globalThis.PASIUserScriptRuntime = Object.freeze({build, wrapSource});
})();
