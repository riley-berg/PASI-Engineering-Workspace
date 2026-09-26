(() => {
  "use strict";

  const c = globalThis.PASIExtensionAPIContract;
  if (!c) throw new Error("PASI API contract is unavailable");

  const send = (type, payload = {}) => new Promise((resolve, reject) => {
    chrome.runtime.sendMessage({protocol_version: c.VERSION, type, ...payload}, (response) => {
      const error = chrome.runtime.lastError;
      if (error) return reject(new Error(error.message));
      if (!response || response.ok !== true) return reject(new Error(response?.error || "PASI API request failed"));
      resolve(response);
    });
  });

  const eventTarget = new EventTarget();
  const events = Object.freeze({
    on(type, listener) {
      const name = "pasi:event:" + String(type);
      eventTarget.addEventListener(name, listener);
      return () => eventTarget.removeEventListener(name, listener);
    },
    once(type, listener) {
      const name = "pasi:event:" + String(type);
      const wrapped = (event) => {
        eventTarget.removeEventListener(name, wrapped);
        listener(event);
      };
      eventTarget.addEventListener(name, wrapped);
      return () => eventTarget.removeEventListener(name, wrapped);
    },
    emit(type, detail = {}) {
      eventTarget.dispatchEvent(new CustomEvent("pasi:event:" + String(type), {detail}));
    },
  });

  chrome.runtime.onMessage.addListener((message) => {
    if (message?.type === "pasi.internal.storage.change") {
      globalThis.dispatchEvent(new CustomEvent("pasi-storage-change:" + message.namespace, {detail: message.changes}));
      events.emit("storage.change", message);
    } else if (message?.type === "pasi.internal.web.event") {
      globalThis.dispatchEvent(new CustomEvent("pasi-web-event:" + message.observer_id, {detail: message.event}));
      events.emit("web.request", message);
    } else if (message?.type === "pasi.internal.menu.invoke") {
      globalThis.dispatchEvent(new CustomEvent("pasi-menu-invoke", {detail: message}));
      events.emit("menu.invoke", message);
    } else if (message?.type === "pasi.internal.tab.message") {
      events.emit("tab.message", message.payload);
    }
  });

  const codecTypes = new Map();
  const codec = Object.freeze({
    register(name, definition) {
      const key = String(name || "").trim();
      if (!/^[A-Za-z][A-Za-z0-9._:-]{0,80}$/.test(key)) throw new TypeError("Invalid PASI codec type");
      if (!definition || typeof definition.test !== "function" || typeof definition.serialize !== "function" || typeof definition.deserialize !== "function") {
        throw new TypeError("PASI codec needs test, serialize, and deserialize");
      }
      codecTypes.set(key, definition);
      return () => codecTypes.delete(key);
    },
    encode(value) {
      const seen = new WeakSet();
      const walk = (input) => {
        for (const [name, definition] of codecTypes) {
          if (definition.test(input)) return {$pasi_type: name, value: walk(definition.serialize(input))};
        }
        if (!input || typeof input !== "object") return input;
        if (seen.has(input)) throw new TypeError("Cyclic values are not supported");
        seen.add(input);
        if (Array.isArray(input)) return input.map(walk);
        const out = {};
        for (const [key, child] of Object.entries(input)) out[key] = walk(child);
        return out;
      };
      return walk(value);
    },
    decode(value) {
      const walk = (input) => {
        if (Array.isArray(input)) return input.map(walk);
        if (!input || typeof input !== "object") return input;
        if (typeof input.$pasi_type === "string" && codecTypes.has(input.$pasi_type)) {
          return codecTypes.get(input.$pasi_type).deserialize(walk(input.value));
        }
        const out = {};
        for (const [key, child] of Object.entries(input)) out[key] = walk(child);
        return out;
      };
      return walk(value);
    },
  });

  const storage = Object.freeze({
    get(key, options = {}) {
      return send(c.MESSAGE_TYPES.STORAGE_GET, {
        namespace: c.normalizeNamespace(options.namespace || "default"),
        key: c.normalizeKey(key),
      }).then((r) => codec.decode(r.value));
    },
    set(key, value, options = {}) {
      return send(c.MESSAGE_TYPES.STORAGE_SET, {
        namespace: c.normalizeNamespace(options.namespace || "default"),
        key: c.normalizeKey(key),
        value: codec.encode(value),
      });
    },
    remove(key, options = {}) {
      return send(c.MESSAGE_TYPES.STORAGE_REMOVE, {
        namespace: c.normalizeNamespace(options.namespace || "default"),
        key: c.normalizeKey(key),
      });
    },
    list(options = {}) {
      return send(c.MESSAGE_TYPES.STORAGE_LIST, {
        namespace: c.normalizeNamespace(options.namespace || "default"),
      }).then((r) => r.keys);
    },
    info(options = {}) {
      return send(c.MESSAGE_TYPES.STORAGE_INFO, {
        namespace: c.normalizeNamespace(options.namespace || "default"),
      });
    },
    watch(listener, options = {}) {
      const namespace = c.normalizeNamespace(options.namespace || "default");
      if (typeof listener !== "function") throw new TypeError("PASI.storage.watch needs a function");
      const eventName = "pasi-storage-change:" + namespace;
      const handler = (event) => listener(event.detail);
      globalThis.addEventListener(eventName, handler);
      send(c.MESSAGE_TYPES.STORAGE_WATCH, {namespace}).catch(() => globalThis.removeEventListener(eventName, handler));
      return () => globalThis.removeEventListener(eventName, handler);
    },
  });

  const db = Object.freeze({
    ensure(database, stores) {
      return send(c.MESSAGE_TYPES.DB_ENSURE, {database, stores});
    },
    get(database, store, key) {
      return send(c.MESSAGE_TYPES.DB_GET, {database, store, key: codec.encode(key)}).then((r) => codec.decode(r.value));
    },
    put(database, store, key, value) {
      return send(c.MESSAGE_TYPES.DB_PUT, {database, store, key: codec.encode(key), value: codec.encode(value)});
    },
    delete(database, store, key) {
      return send(c.MESSAGE_TYPES.DB_DELETE, {database, store, key: codec.encode(key)});
    },
    keys(database, store) {
      return send(c.MESSAGE_TYPES.DB_KEYS, {database, store}).then((r) => r.keys.map(codec.decode));
    },
    clear(database, store) {
      return send(c.MESSAGE_TYPES.DB_CLEAR, {database, store});
    },
  });

  const http = Object.freeze({
    grantOrigin(url) {
      return send(c.MESSAGE_TYPES.HTTP_GRANT_ORIGIN, {origin: c.normalizeOriginPattern(url)});
    },
    revokeOrigin(url) {
      return send(c.MESSAGE_TYPES.HTTP_REVOKE_ORIGIN, {origin: c.normalizeOriginPattern(url)});
    },
    hasOrigin(url) {
      return send(c.MESSAGE_TYPES.HTTP_HAS_ORIGIN, {origin: c.normalizeOriginPattern(url)}).then((r) => r.granted === true);
    },
    async request(options = {}) {
      const url = c.normalizeUrl(options.url).toString();
      const response = await send(c.MESSAGE_TYPES.HTTP_REQUEST, {
        method: c.normalizeMethod(options.method || "GET"),
        url,
        headers: c.normalizeHeaders(options.headers),
        body: options.body == null ? null : String(options.body),
        timeout_ms: c.normalizeTimeout(options.timeout_ms),
      });
      const body = String(response.body || "");
      return Object.freeze({
        ok: response.status >= 200 && response.status < 300,
        status: response.status,
        url: response.url || url,
        headers: Object.freeze(response.headers || {}),
        text: async () => body,
        json: async () => JSON.parse(body),
      });
    },
    stream(options = {}) {
      const port = chrome.runtime.connect({name: "pasi.http.stream"});
      const url = c.normalizeUrl(options.url).toString();
      let ended = false;
      let failure = null;
      let queue = [];
      let wake = null;
      port.onMessage.addListener((message) => {
        if (message.type === "chunk") queue.push(message.chunk);
        else if (message.type === "done") ended = true;
        else if (message.type === "error") {
          failure = new Error(message.error || "PASI stream failed");
          ended = true;
        }
        wake?.();
      });
      port.onDisconnect.addListener(() => {
        if (!ended) failure = new Error("PASI stream disconnected");
        ended = true;
        wake?.();
      });
      port.postMessage({
        type: c.MESSAGE_TYPES.HTTP_STREAM_START,
        method: c.normalizeMethod(options.method || "GET"),
        url,
        headers: c.normalizeHeaders(options.headers),
        body: options.body == null ? null : String(options.body),
        timeout_ms: c.normalizeTimeout(options.timeout_ms),
      });
      return new ReadableStream({
        async pull(controller) {
          while (!queue.length && !ended && !failure) await new Promise((resolve) => {wake = resolve;});
          wake = null;
          if (failure) {
            controller.error(failure);
            port.disconnect();
            return;
          }
          if (queue.length) {
            controller.enqueue(queue.shift());
            return;
          }
          controller.close();
          port.disconnect();
        },
        cancel() {
          ended = true;
          port.disconnect();
        },
      });
    },
  });

  const menuHandlers = new Map();
  globalThis.addEventListener("pasi-menu-invoke", (event) => {
    const handler = menuHandlers.get(event.detail?.command_id);
    if (handler) void handler(event.detail);
  });
  const menu = Object.freeze({
    async register(options, handler) {
      if (!options || typeof handler !== "function") throw new TypeError("PASI.menu.register needs options and a handler");
      const result = await send(c.MESSAGE_TYPES.MENU_REGISTER, {
        title: String(options.title || "PASI command").slice(0, c.LIMITS.menuTitleChars),
        contexts: Array.isArray(options.contexts) && options.contexts.length ? options.contexts : ["page"],
        command_id: crypto.randomUUID(),
      });
      menuHandlers.set(result.command_id, handler);
      return result.command_id;
    },
    unregister(commandId) {
      menuHandlers.delete(String(commandId));
      return send(c.MESSAGE_TYPES.MENU_UNREGISTER, {command_id: String(commandId)});
    },
    list() {
      return send(c.MESSAGE_TYPES.MENU_LIST);
    },
  });

  const tabs = Object.freeze({
    open(options = {}) {
      return send(c.MESSAGE_TYPES.TAB_OPEN, {
        url: String(options.url || ""),
        active: options.active !== false,
        pinned: options.pinned === true,
        wait_for_complete: options.wait_for_complete !== false,
      });
    },
    close(tabId) { return send(c.MESSAGE_TYPES.TAB_CLOSE, {tab_id: Number(tabId)}); },
    focus(tabId) { return send(c.MESSAGE_TYPES.TAB_FOCUS, {tab_id: Number(tabId)}); },
    send(tabId, payload) { return send(c.MESSAGE_TYPES.TAB_SEND, {tab_id: Number(tabId), payload}); },
    broadcast(payload, options = {}) {
      return send(c.MESSAGE_TYPES.TAB_BROADCAST, {
        payload,
        match: options.match || ["https://chatgpt.com/*"],
      });
    },
    list() { return send(c.MESSAGE_TYPES.TAB_LIST); },
    onMessage(listener) { return events.on("tab.message", listener); },
  });

  const web = Object.freeze({
    async observe(options = {}, listener) {
      if (typeof listener !== "function") throw new TypeError("PASI.web.observe needs a listener");
      const observerId = options.requestId || crypto.randomUUID();
      const result = await send(c.MESSAGE_TYPES.WEB_OBSERVE, {
        urls: options.urls || [],
        types: options.types || [],
        requestId: observerId,
      });
      const eventName = "pasi-web-event:" + result.observer_id;
      const handler = (event) => listener(event.detail);
      globalThis.addEventListener(eventName, handler);
      return {
        observer_id: result.observer_id,
        stop: async () => {
          globalThis.removeEventListener(eventName, handler);
          await send(c.MESSAGE_TYPES.WEB_UNOBSERVE, {observer_id: result.observer_id});
        },
      };
    },
    rules: Object.freeze({
      add(rule) { return send(c.MESSAGE_TYPES.WEB_RULE_ADD, {rule}); },
      remove(ids) { return send(c.MESSAGE_TYPES.WEB_RULE_REMOVE, {ids: Array.isArray(ids) ? ids : [ids]}); },
      list() { return send(c.MESSAGE_TYPES.WEB_RULE_LIST); },
    }),
  });

  const dom = Object.freeze({
    addStyle(cssText, options = {}) {
      const node = document.createElement("style");
      node.textContent = String(cssText || "");
      node.dataset.pasiOwner = "pasi";
      (options.root || document.head || document.documentElement).appendChild(node);
      return () => node.remove();
    },
    addElement(tagName, options = {}) {
      const node = document.createElement(String(tagName || "div"));
      for (const [name, value] of Object.entries(options.attributes || {})) node.setAttribute(name, String(value));
      if (options.text != null) node.textContent = String(options.text);
      if (options.html != null) node.innerHTML = String(options.html);
      (options.parent || document.body || document.documentElement).appendChild(node);
      if (!options.shadow) return node;
      const root = node.attachShadow({mode: options.shadow === "closed" ? "closed" : "open"});
      if (options.shadowHtml != null) root.innerHTML = String(options.shadowHtml);
      return Object.freeze({element: node, shadowRoot: options.shadow === "closed" ? null : root, remove: () => node.remove()});
    },
    waitFor(selectorOrPredicate, options = {}) {
      const timeout = Math.min(Math.max(Number(options.timeout_ms) || 30000, 0), 300000);
      const root = options.root || document;
      return new Promise((resolve, reject) => {
        let stopped = false;
        let observer = null;
        let timer = null;
        const finish = (value, error) => {
          if (stopped) return;
          stopped = true;
          observer?.disconnect();
          if (timer) clearTimeout(timer);
          error ? reject(error) : resolve(value);
        };
        const matches = () => {
          if (typeof selectorOrPredicate === "function") return selectorOrPredicate() || null;
          const list = [...root.querySelectorAll(String(selectorOrPredicate))].filter((node) => {
            if (options.visible === false) return true;
            const style = getComputedStyle(node);
            return style.display !== "none" && style.visibility !== "hidden" && node.getClientRects().length > 0;
          });
          return options.all ? list : list[0] || null;
        };
        const check = () => {
          try {
            const value = matches();
            if (value && (!Array.isArray(value) || value.length)) finish(value);
          } catch (_) {}
        };
        observer = new MutationObserver(check);
        observer.observe(root === document ? document.documentElement : root, {subtree: true, childList: true, attributes: true, characterData: true});
        timer = timeout ? setTimeout(() => finish(null, new Error("PASI DOM wait timed out")), timeout) : null;
        check();
      });
    },
    onPresent(selectorOrPredicate, handler, options = {}) {
      let active = true;
      const interval = Math.max(Number(options.debounce_ms) || 0, 0);
      const run = async () => {
        if (!active) return;
        try {
          const value = await this.waitFor(selectorOrPredicate, options);
          if (active) await handler(value);
        } catch (_) {}
      };
      if (interval) setTimeout(run, interval); else void run();
      return () => {active = false;};
    },
  });

  const downloads = Object.freeze({
    download(options = {}) {
      const filename = String(options.filename || "pasi-download.bin");
      if (!filename || filename.startsWith("/") || filename.includes("\\")) throw new TypeError("PASI download filename must be relative");
      return send(c.MESSAGE_TYPES.DOWNLOAD, {
        url: String(options.url || ""),
        filename: filename.slice(0, c.LIMITS.downloadFilenameChars),
        saveAs: options.saveAs === true,
        conflictAction: options.conflictAction || "uniquify",
      });
    },
  });

  const notifications = Object.freeze({
    show(options = {}) {
      return send(c.MESSAGE_TYPES.NOTIFICATION_SHOW, {
        title: String(options.title || "PASI").slice(0, c.LIMITS.notificationTitleChars),
        message: String(options.message || "").slice(0, c.LIMITS.notificationMessageChars),
        iconUrl: String(options.iconUrl || ""),
      });
    },
  });

  const permissions = Object.freeze({
    contains(options = {}) { return send(c.MESSAGE_TYPES.PERMISSION_CONTAINS, options).then((r) => r.granted === true); },
    request(options = {}) { return send(c.MESSAGE_TYPES.PERMISSION_REQUEST, options).then((r) => r.granted === true); },
    remove(options = {}) { return send(c.MESSAGE_TYPES.PERMISSION_REMOVE, options).then((r) => r.removed === true); },
  });

  const scripts = Object.freeze({
    register(options) { return send(c.MESSAGE_TYPES.SCRIPT_REGISTER, {options}); },
    unregister(ids) { return send(c.MESSAGE_TYPES.SCRIPT_UNREGISTER, {ids: Array.isArray(ids) ? ids : [ids]}); },
    list() { return send(c.MESSAGE_TYPES.SCRIPT_LIST); },
  });

  const PASI = Object.freeze({
    api: Object.freeze({version: c.VERSION}),
    codec,
    storage,
    db,
    http,
    dom,
    menu,
    tabs,
    web,
    downloads,
    notifications,
    permissions,
    scripts,
    events,
    sleep(ms) {
      return new Promise((resolve) => setTimeout(resolve, Math.max(0, Number(ms) || 0)));
    },
    async retry(operation, options = {}) {
      const attempts = Math.min(Math.max(Number(options.attempts) || 3, 1), 8);
      const baseDelay = Math.min(Math.max(Number(options.base_delay_ms) || 250, 25), 10000);
      let last;
      for (let attempt = 1; attempt <= attempts; attempt += 1) {
        try { return await operation(attempt); }
        catch (error) {
          last = error;
          if (attempt < attempts) await new Promise((resolve) => setTimeout(resolve, baseDelay * (2 ** (attempt - 1))));
        }
      }
      throw last || new Error("PASI retry failed");
    },
    runtime: Object.freeze({
      info() {
        const manifest = chrome.runtime.getManifest();
        return {id: chrome.runtime.id, name: manifest.name, version: manifest.version, api_version: c.VERSION};
      },
    }),
  });

  globalThis.PASI = PASI;
})();
