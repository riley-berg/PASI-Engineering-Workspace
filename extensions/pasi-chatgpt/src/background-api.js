(() => {
  "use strict";

  const contract = globalThis.PASIExtensionAPIContract;
  if (!contract) throw new Error("PASI extension API contract is unavailable");

  const INTERNAL_MENU_KEY = "pasi:api:internal:menus";
  const storageWatchers = new Map();
  const webObservers = new Map();
  let menuHydrated = false;

  function senderIsChatGPT(sender) {
    const url = String(sender?.url || sender?.tab?.url || "");
    return /^https:\/\/chatgpt\.com\//i.test(url);
  }

  function senderTabId(sender) {
    return typeof sender?.tab?.id === "number" ? sender.tab.id : null;
  }

  function namespacedKey(namespace, key) {
    return contract.namespacedKey(namespace, key);
  }

  async function storageSnapshot(namespace) {
    const prefix = contract.namespacePrefix(namespace);
    const values = await chrome.storage.local.get(null);
    const keys = Object.keys(values).filter((key) => key.startsWith(prefix));
    const bytesInUse = await chrome.storage.local.getBytesInUse(keys);
    const quota = Number(chrome.storage.local.QUOTA_BYTES || 0);
    return {
      namespace: contract.normalizeNamespace(namespace),
      key_count: keys.length,
      keys: keys.map((key) => key.slice(prefix.length)).sort(),
      bytes_in_use: bytesInUse,
      quota_bytes: quota,
      remaining_bytes: quota > 0 ? Math.max(0, quota - bytesInUse) : null,
    };
  }

  async function handleStorageGet(message) {
    const key = namespacedKey(message.namespace, message.key);
    const values = await chrome.storage.local.get(key);
    return {ok: true, value: values[key]};
  }

  async function handleStorageSet(message) {
    const key = namespacedKey(message.namespace, message.key);
    await chrome.storage.local.set({[key]: message.value});
    return {ok: true};
  }

  async function handleStorageRemove(message) {
    await chrome.storage.local.remove(namespacedKey(message.namespace, message.key));
    return {ok: true};
  }

  async function handleStorageList(message) {
    return {...await storageSnapshot(message.namespace), ok: true};
  }

  async function handleStorageInfo(message) {
    return {...await storageSnapshot(message.namespace), ok: true};
  }

  async function handleStorageWatch(message, sender) {
    const tabId = senderTabId(sender);
    if (tabId === null) throw new Error("PASI storage.watch requires a tab");
    const namespace = contract.normalizeNamespace(message.namespace);
    if (!storageWatchers.has(namespace)) storageWatchers.set(namespace, new Set());
    storageWatchers.get(namespace).add(tabId);
    return {ok: true, namespace};
  }

  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName !== "local") return;
    for (const [namespace, tabs] of storageWatchers) {
      const prefix = contract.namespacePrefix(namespace);
      const filtered = {};
      for (const [key, change] of Object.entries(changes)) {
        if (key.startsWith(prefix)) {
          filtered[key.slice(prefix.length)] = change;
        }
      }
      if (!Object.keys(filtered).length) continue;
      for (const tabId of tabs) {
        chrome.tabs.sendMessage(tabId, {
          type: "pasi.internal.storage.change",
          namespace,
          changes: filtered,
        }).catch(() => tabs.delete(tabId));
      }
    }
  });

  function validateDatabaseName(value) {
    const name = String(value || "").trim();
    if (!/^[A-Za-z0-9._-]{1,120}$/.test(name)) {
      throw new TypeError("Invalid PASI database name");
    }
    return name;
  }

  function validateStoreName(value) {
    const name = String(value || "").trim();
    if (!/^[A-Za-z0-9._-]{1,120}$/.test(name)) {
      throw new TypeError("Invalid PASI object store name");
    }
    return name;
  }

  function openDatabase(name, stores = []) {
    const database = validateDatabaseName(name);
    const normalizedStores = [...new Set((Array.isArray(stores) ? stores : []).map(validateStoreName))];
    return new Promise((resolve, reject) => {
      const request = indexedDB.open(database, 1);
      request.onupgradeneeded = () => {
        for (const store of normalizedStores) {
          if (!request.result.objectStoreNames.contains(store)) {
            request.result.createObjectStore(store);
          }
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error || new Error("PASI IndexedDB open failed"));
    });
  }

  async function databaseForOperation(message) {
    const database = validateDatabaseName(message.database);
    const db = await openDatabase(database, []);
    if (!db.objectStoreNames.contains(validateStoreName(message.store))) {
      db.close();
      throw new Error("PASI object store does not exist: " + message.store);
    }
    return db;
  }

  async function handleDbEnsure(message) {
    const db = await openDatabase(message.database, message.stores);
    db.close();
    return {ok: true};
  }

  async function handleDbGet(message) {
    const db = await databaseForOperation(message);
    return new Promise((resolve, reject) => {
      const tx = db.transaction(validateStoreName(message.store), "readonly");
      const request = tx.objectStore(message.store).get(message.key);
      request.onsuccess = () => {
        db.close();
        resolve({ok: true, value: request.result});
      };
      request.onerror = () => {
        db.close();
        reject(request.error || new Error("PASI IndexedDB get failed"));
      };
    });
  }

  async function handleDbPut(message) {
    const db = await databaseForOperation(message);
    return new Promise((resolve, reject) => {
      const tx = db.transaction(validateStoreName(message.store), "readwrite");
      tx.objectStore(message.store).put(message.value, message.key);
      tx.oncomplete = () => {
        db.close();
        resolve({ok: true});
      };
      tx.onerror = () => {
        db.close();
        reject(tx.error || new Error("PASI IndexedDB put failed"));
      };
    });
  }

  async function handleDbDelete(message) {
    const db = await databaseForOperation(message);
    return new Promise((resolve, reject) => {
      const tx = db.transaction(validateStoreName(message.store), "readwrite");
      tx.objectStore(message.store).delete(message.key);
      tx.oncomplete = () => {
        db.close();
        resolve({ok: true});
      };
      tx.onerror = () => {
        db.close();
        reject(tx.error || new Error("PASI IndexedDB delete failed"));
      };
    });
  }

  async function handleDbKeys(message) {
    const db = await databaseForOperation(message);
    return new Promise((resolve, reject) => {
      const tx = db.transaction(validateStoreName(message.store), "readonly");
      const request = tx.objectStore(message.store).getAllKeys();
      request.onsuccess = () => {
        db.close();
        resolve({ok: true, keys: request.result || []});
      };
      request.onerror = () => {
        db.close();
        reject(request.error || new Error("PASI IndexedDB keys failed"));
      };
    });
  }

  async function handleDbClear(message) {
    const db = await databaseForOperation(message);
    return new Promise((resolve, reject) => {
      const tx = db.transaction(validateStoreName(message.store), "readwrite");
      tx.objectStore(message.store).clear();
      tx.oncomplete = () => {
        db.close();
        resolve({ok: true});
      };
      tx.onerror = () => {
        db.close();
        reject(tx.error || new Error("PASI IndexedDB clear failed"));
      };
    });
  }

  async function hasOriginPermission(originPattern) {
    return chrome.permissions.contains({origins: [originPattern]});
  }

  async function handleHttpPermission(type, origin) {
    const pattern = String(origin || "").trim();
    if (!/^(?:https?|http):\/\/[^/*]+(?:\/\*)$/.test(pattern)) {
      throw new TypeError("PASI origin must look like https://example.com/*");
    }
    if (type === contract.MESSAGE_TYPES.HTTP_GRANT_ORIGIN) {
      return {ok: true, granted: await chrome.permissions.request({origins: [pattern]})};
    }
    if (type === contract.MESSAGE_TYPES.HTTP_REVOKE_ORIGIN) {
      return {ok: true, removed: await chrome.permissions.remove({origins: [pattern]})};
    }
    return {ok: true, granted: await hasOriginPermission(pattern)};
  }

  async function ensureUrlPermission(url) {
    const parsed = new URL(url);
    const pattern = parsed.protocol + "//" + parsed.host + "/*";
    if (!(await hasOriginPermission(pattern))) {
      throw new Error("No PASI host permission for " + pattern + ". Grant it through PASI.http.grantOrigin().");
    }
  }

  function validateHeaders(headers) {
    return contract.normalizeHeaders(headers);
  }

  async function handleHttpRequest(message) {
    await ensureUrlPermission(message.url);
    const url = new URL(message.url);
    const method = contract.normalizeMethod(message.method);
    const body = message.body == null ? null : String(message.body);
    if (body && body.length > contract.LIMITS.httpBodyChars) {
      throw new TypeError("PASI HTTP request body is too large");
    }
    if ((method === "GET" || method === "HEAD") && body) {
      throw new TypeError(method + " requests cannot include a body");
    }

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), contract.normalizeTimeout(message.timeout_ms));
    try {
      const response = await fetch(url.toString(), {
        method,
        headers: validateHeaders(message.headers),
        body: body || undefined,
        cache: "no-store",
        credentials: "omit",
        redirect: "error",
        signal: controller.signal,
      });
      const responseBody = method === "HEAD" || response.status === 204 ? "" : await response.text();
      return {
        ok: true,
        status: response.status,
        url: response.url || url.toString(),
        headers: Object.fromEntries(response.headers.entries()),
        body: responseBody.slice(0, contract.LIMITS.httpBodyChars),
      };
    } finally {
      clearTimeout(timer);
    }
  }

  async function startHttpStream(port, message) {
    try {
      await ensureUrlPermission(message.url);
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), contract.normalizeTimeout(message.timeout_ms));
      const response = await fetch(message.url, {
        method: contract.normalizeMethod(message.method),
        headers: validateHeaders(message.headers),
        body: message.body || undefined,
        credentials: "omit",
        cache: "no-store",
        redirect: "error",
        signal: controller.signal,
      });
      clearTimeout(timer);

      port.postMessage({
        type: "headers",
        status: response.status,
        url: response.url,
        headers: Object.fromEntries(response.headers.entries()),
      });

      if (!response.body) {
        port.postMessage({type: "done"});
        port.disconnect();
        return;
      }

      const reader = response.body.getReader();
      try {
        while (true) {
          const {done, value} = await reader.read();
          if (done) break;
          if (value.byteLength > contract.LIMITS.httpStreamChunkBytes) {
            throw new Error("PASI HTTP stream chunk exceeded the safety limit");
          }
          port.postMessage({type: "chunk", chunk: value});
        }
        port.postMessage({type: "done"});
      } finally {
        reader.releaseLock();
        port.disconnect();
      }
    } catch (error) {
      try {
        port.postMessage({type: "error", error: String(error?.message || error)});
      } catch (_) {}
      try { port.disconnect(); } catch (_) {}
    }
  }

  async function loadMenus() {
    if (menuHydrated) return;
    menuHydrated = true;
    chrome.contextMenus.onClicked.addListener((info, tab) => {
      const commandId = String(info.menuItemId || "");
      if (!commandId || !tab?.id) return;
      chrome.tabs.sendMessage(tab.id, {
        type: "pasi.internal.menu.invoke",
        command_id: commandId,
        info,
      }).catch(() => undefined);
    });
    await chrome.contextMenus.removeAll();
    const stored = await chrome.storage.local.get(INTERNAL_MENU_KEY);
    const menus = stored[INTERNAL_MENU_KEY] || {};
    for (const [commandId, definition] of Object.entries(menus)) {
      await chrome.contextMenus.create({
        id: commandId,
        title: definition.title,
        contexts: definition.contexts,
      });
    }
  }

  async function menuDefinitions() {
    const stored = await chrome.storage.local.get(INTERNAL_MENU_KEY);
    return stored[INTERNAL_MENU_KEY] || {};
  }

  async function handleMenuRegister(message) {
    await loadMenus();
    const menus = await menuDefinitions();
    const commandId = String(message.command_id || crypto.randomUUID());
    menus[commandId] = {
      title: String(message.title || "PASI command").slice(0, contract.LIMITS.menuTitleChars),
      contexts: Array.isArray(message.contexts) && message.contexts.length ? message.contexts : ["page"],
    };
    await chrome.storage.local.set({[INTERNAL_MENU_KEY]: menus});
    await chrome.contextMenus.create({id: commandId, title: menus[commandId].title, contexts: menus[commandId].contexts});
    return {ok: true, command_id: commandId};
  }

  async function handleMenuUnregister(message) {
    const commandId = String(message.command_id || "");
    const menus = await menuDefinitions();
    delete menus[commandId];
    await chrome.storage.local.set({[INTERNAL_MENU_KEY]: menus});
    await chrome.contextMenus.remove(commandId).catch(() => undefined);
    return {ok: true};
  }

  async function handleMenuList() {
    return {ok: true, menus: await menuDefinitions()};
  }

  async function handleTabOpen(message) {
    const tab = await chrome.tabs.create({
      url: String(message.url || ""),
      active: message.active !== false,
      pinned: message.pinned === true,
    });
    if (!message.wait_for_complete || typeof tab.id !== "number") {
      return {ok: true, tab_id: tab.id, url: tab.url || ""};
    }
    const tabId = tab.id;
    await new Promise((resolve) => {
      let settled = false;
      const timer = setTimeout(() => {
        if (settled) return;
        settled = true;
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      }, 60000);
      const listener = (updatedId, changeInfo) => {
        if (updatedId !== tabId || changeInfo.status !== "complete" || settled) return;
        settled = true;
        clearTimeout(timer);
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      };
      chrome.tabs.onUpdated.addListener(listener);
    });
    const current = await chrome.tabs.get(tabId);
    return {ok: true, tab_id: tabId, url: current.url || ""};
  }

  async function handleTabClose(message) {
    await chrome.tabs.remove(Number(message.tab_id));
    return {ok: true};
  }

  async function handleTabFocus(message) {
    const tabId = Number(message.tab_id);
    const tab = await chrome.tabs.get(tabId);
    await chrome.tabs.update(tabId, {active: true});
    await chrome.windows.update(tab.windowId, {focused: true});
    return {ok: true};
  }

  async function handleTabSend(message) {
    const response = await chrome.tabs.sendMessage(Number(message.tab_id), {
      type: "pasi.internal.tab.message",
      payload: message.payload,
    });
    return {ok: true, response};
  }

  async function handleTabBroadcast(message, sender) {
    const tabs = await chrome.tabs.query({url: message.match || ["https://chatgpt.com/*"]});
    const results = [];
    for (const tab of tabs) {
      if (typeof tab.id !== "number" || tab.id === senderTabId(sender)) continue;
      try {
        results.push({tab_id: tab.id, response: await chrome.tabs.sendMessage(tab.id, {
          type: "pasi.internal.tab.message",
          payload: message.payload,
        })});
      } catch (_) {
        results.push({tab_id: tab.id, response: null});
      }
    }
    return {ok: true, results};
  }

  async function handleTabList() {
    const tabs = await chrome.tabs.query({});
    return {
      ok: true,
      tabs: tabs.map((tab) => ({
        id: tab.id,
        window_id: tab.windowId,
        active: tab.active,
        pinned: tab.pinned,
        status: tab.status,
        url: tab.url || "",
        title: tab.title || "",
      })),
    };
  }

  async function handleNotification(message) {
    const id = await chrome.notifications.create({
      type: "basic",
      title: message.title || "PASI",
      message: message.message || "",
      iconUrl: message.iconUrl || "icons/icon128.png",
    });
    return {ok: true, notification_id: id};
  }

  async function ensureOffscreen() {
    if (!chrome.offscreen) throw new Error("Chrome offscreen API is unavailable");
    if (await chrome.offscreen.hasDocument()) return;
    await chrome.offscreen.createDocument({
      url: "src/offscreen.html",
      reasons: ["CLIPBOARD"],
      justification: "Provide PASI clipboard APIs without exposing page clipboard privileges to the ChatGPT DOM controller.",
    });
  }

  async function offscreenMessage(message) {
    await ensureOffscreen();
    return chrome.runtime.sendMessage(message);
  }

  async function handleClipboard(message) {
    return offscreenMessage(message);
  }

  async function handleDownload(message) {
    const url = String(message.url || "");
    const parsed = new URL(url);
    if (!/^https?:$/.test(parsed.protocol)) throw new TypeError("PASI downloads support only http(s) URLs");
    let filename = String(message.filename || "pasi-download.bin");
    filename = filename.replace(/\\/g, "/").replace(/(^|\/)\.\.(?=\/|$)/g, "_");
    if (filename.startsWith("/")) throw new TypeError("PASI download filename must be relative");
    if (!filename || filename.length > contract.LIMITS.downloadFilenameChars) throw new TypeError("Invalid PASI download filename");
    const id = await chrome.downloads.download({
      url,
      filename,
      saveAs: message.saveAs === true,
      conflictAction: ["uniquify", "overwrite", "prompt"].includes(message.conflictAction)
        ? message.conflictAction
        : "uniquify",
    });
    return {ok: true, download_id: id};
  }

  async function handlePermissions(type, message) {
    const origins = Array.isArray(message.origins) ? message.origins.map(String) : [];
    const permissions = Array.isArray(message.permissions) ? message.permissions.map(String) : [];
    if (type === contract.MESSAGE_TYPES.PERMISSION_REQUEST) {
      return {ok: true, granted: await chrome.permissions.request({origins, permissions})};
    }
    if (type === contract.MESSAGE_TYPES.PERMISSION_REMOVE) {
      return {ok: true, removed: await chrome.permissions.remove({origins, permissions})};
    }
    return {ok: true, granted: await chrome.permissions.contains({origins, permissions})};
  }

  function validateScriptOptions(options) {
    if (!options || typeof options !== "object") throw new TypeError("PASI script options are required");
    const id = String(options.id || "");
    if (!/^[A-Za-z][A-Za-z0-9._-]{0,80}$/.test(id)) throw new TypeError("Invalid PASI content script id");
    const matches = Array.isArray(options.matches) && options.matches.length ? options.matches : ["https://chatgpt.com/*"];
    const js = Array.isArray(options.js) ? options.js.map(String) : [];
    if (!js.length || js.some((path) => path.startsWith("/") || path.includes(".."))) {
      throw new TypeError("PASI scripts may only reference extension-local JS files");
    }
    const runAt = ["document_start", "document_end", "document_idle"].includes(options.runAt)
      ? options.runAt
      : "document_idle";
    const world = ["ISOLATED", "MAIN"].includes(options.world) ? options.world : "ISOLATED";
    return {
      id,
      matches: matches.map(String),
      excludeMatches: Array.isArray(options.excludeMatches) ? options.excludeMatches.map(String) : undefined,
      js,
      css: Array.isArray(options.css) ? options.css.map(String) : undefined,
      runAt,
      world,
      persistAcrossSessions: options.persistAcrossSessions !== false,
    };
  }

  function validateScriptFiles(files) {
    const js = Array.isArray(files) ? files.map(String) : [];
    if (!js.length || js.length > 32 || js.some((file) => (
      !file || file.startsWith("/") || file.includes("..") || /[\\\r\n]/.test(file)
    ))) {
      throw new TypeError("PASI scripts must use bounded extension-local file paths");
    }
    return js;
  }

  async function handleScriptExecute(message, sender) {
    const files = validateScriptFiles(message.files);
    const tabId = Number.isInteger(message.tab_id) && message.tab_id > 0
      ? message.tab_id
      : senderTabId(sender);
    if (tabId === null) throw new Error("PASI script execution requires a browser tab");

    const world = ["ISOLATED", "MAIN"].includes(message.world) ? message.world : "ISOLATED";
    const results = await chrome.scripting.executeScript({
      target: {tabId},
      files,
      world,
      injectImmediately: message.inject_immediately !== false,
    });
    return {ok: true, tab_id: tabId, results};
  }

  async function handleScriptRegister(message) {
    const options = validateScriptOptions(message.options);
    await chrome.scripting.registerContentScripts([options]);
    return {ok: true, id: options.id};
  }

  async function handleScriptUnregister(message) {
    await chrome.scripting.unregisterContentScripts({
      ids: (Array.isArray(message.ids) ? message.ids : []).map(String),
    });
    return {ok: true};
  }

  async function handleScriptList() {
    const scripts = await chrome.scripting.getRegisteredContentScripts();
    return {ok: true, scripts};
  }

  function normalizeWebFilterUrls(urls) {
    const values = Array.isArray(urls) && urls.length ? urls : ["https://chatgpt.com/*"];
    return values.map((value) => {
      const text = String(value);
      if (!text.includes("://") && text !== "<all_urls>") throw new TypeError("Invalid PASI webRequest URL filter");
      return text;
    });
  }

  async function handleWebObserve(message, sender) {
    const tabId = senderTabId(sender);
    if (tabId === null) throw new Error("PASI.web.observe requires a tab");
    const observerId = String(message.requestId || crypto.randomUUID());
    const urls = normalizeWebFilterUrls(message.urls);
    const types = Array.isArray(message.types) && message.types.length
      ? message.types.map(String)
      : ["main_frame", "sub_frame", "xmlhttprequest", "fetch"];
    const filter = {urls, types};

    const onBeforeRequest = (details) => {
      chrome.tabs.sendMessage(tabId, {
        type: "pasi.internal.web.event",
        observer_id: observerId,
        event: {phase: "request", ...details},
      }).catch(() => undefined);
    };
    const onCompleted = (details) => {
      chrome.tabs.sendMessage(tabId, {
        type: "pasi.internal.web.event",
        observer_id: observerId,
        event: {phase: "completed", ...details},
      }).catch(() => undefined);
    };
    const onError = (details) => {
      chrome.tabs.sendMessage(tabId, {
        type: "pasi.internal.web.event",
        observer_id: observerId,
        event: {phase: "error", ...details},
      }).catch(() => undefined);
    };

    chrome.webRequest.onBeforeRequest.addListener(onBeforeRequest, filter);
    chrome.webRequest.onCompleted.addListener(onCompleted, filter);
    chrome.webRequest.onErrorOccurred.addListener(onError, filter);
    webObservers.set(observerId, {tabId, onBeforeRequest, onCompleted, onError});
    return {ok: true, observer_id: observerId};
  }

  async function handleWebUnobserve(message) {
    const observerId = String(message.observer_id || "");
    const observer = webObservers.get(observerId);
    if (!observer) return {ok: true};
    chrome.webRequest.onBeforeRequest.removeListener(observer.onBeforeRequest);
    chrome.webRequest.onCompleted.removeListener(observer.onCompleted);
    chrome.webRequest.onErrorOccurred.removeListener(observer.onError);
    webObservers.delete(observerId);
    return {ok: true};
  }

  async function handleWebRuleAdd(message) {
    if (!message.rule || typeof message.rule !== "object") throw new TypeError("PASI web rule is required");
    const existing = await chrome.declarativeNetRequest.getDynamicRules();
    const used = new Set(existing.map((rule) => rule.id));
    let id = Number(message.rule.id);
    if (!Number.isInteger(id) || id <= 0 || used.has(id)) {
      id = Math.floor(Date.now() % 2000000000) || 1;
      while (used.has(id)) id += 1;
    }
    const rule = {...message.rule, id};
    await chrome.declarativeNetRequest.updateDynamicRules({
      addRules: [rule],
      removeRuleIds: [],
    });
    return {ok: true, rule};
  }

  async function handleWebRuleRemove(message) {
    const ids = (Array.isArray(message.ids) ? message.ids : []).map(Number).filter(Number.isInteger);
    await chrome.declarativeNetRequest.updateDynamicRules({
      addRules: [],
      removeRuleIds: ids,
    });
    return {ok: true, removed: ids};
  }

  async function handleWebRuleList() {
    return {ok: true, rules: await chrome.declarativeNetRequest.getDynamicRules()};
  }

  async function handle(message, sender) {
    if (!senderIsChatGPT(sender)) {
      throw new Error("PASI extension API callers must originate from an authenticated ChatGPT tab");
    }

    switch (message?.type) {
      case contract.MESSAGE_TYPES.STORAGE_GET: return handleStorageGet(message);
      case contract.MESSAGE_TYPES.STORAGE_SET: return handleStorageSet(message);
      case contract.MESSAGE_TYPES.STORAGE_REMOVE: return handleStorageRemove(message);
      case contract.MESSAGE_TYPES.STORAGE_LIST: return handleStorageList(message);
      case contract.MESSAGE_TYPES.STORAGE_INFO: return handleStorageInfo(message);
      case contract.MESSAGE_TYPES.STORAGE_WATCH: return handleStorageWatch(message, sender);
      case contract.MESSAGE_TYPES.DB_ENSURE: return handleDbEnsure(message);
      case contract.MESSAGE_TYPES.DB_GET: return handleDbGet(message);
      case contract.MESSAGE_TYPES.DB_PUT: return handleDbPut(message);
      case contract.MESSAGE_TYPES.DB_DELETE: return handleDbDelete(message);
      case contract.MESSAGE_TYPES.DB_KEYS: return handleDbKeys(message);
      case contract.MESSAGE_TYPES.DB_CLEAR: return handleDbClear(message);
      case contract.MESSAGE_TYPES.HTTP_REQUEST: return handleHttpRequest(message);
      case contract.MESSAGE_TYPES.HTTP_GRANT_ORIGIN:
      case contract.MESSAGE_TYPES.HTTP_REVOKE_ORIGIN:
      case contract.MESSAGE_TYPES.HTTP_HAS_ORIGIN:
        return handleHttpPermission(message.type, message.origin);
      case contract.MESSAGE_TYPES.MENU_REGISTER: return handleMenuRegister(message);
      case contract.MESSAGE_TYPES.MENU_UNREGISTER: return handleMenuUnregister(message);
      case contract.MESSAGE_TYPES.MENU_LIST: return handleMenuList();
      case contract.MESSAGE_TYPES.TAB_OPEN: return handleTabOpen(message);
      case contract.MESSAGE_TYPES.TAB_CLOSE: return handleTabClose(message);
      case contract.MESSAGE_TYPES.TAB_FOCUS: return handleTabFocus(message);
      case contract.MESSAGE_TYPES.TAB_SEND: return handleTabSend(message);
      case contract.MESSAGE_TYPES.TAB_BROADCAST: return handleTabBroadcast(message, sender);
      case contract.MESSAGE_TYPES.TAB_LIST: return handleTabList();
      case contract.MESSAGE_TYPES.NOTIFICATION_SHOW: return handleNotification(message);
      case contract.MESSAGE_TYPES.CLIPBOARD_WRITE:
      case contract.MESSAGE_TYPES.CLIPBOARD_READ:
        return handleClipboard(message);
      case contract.MESSAGE_TYPES.DOWNLOAD: return handleDownload(message);
      case contract.MESSAGE_TYPES.PERMISSION_CONTAINS:
      case contract.MESSAGE_TYPES.PERMISSION_REQUEST:
      case contract.MESSAGE_TYPES.PERMISSION_REMOVE:
        return handlePermissions(message.type, message);
      case contract.MESSAGE_TYPES.SCRIPT_REGISTER: return handleScriptRegister(message);
      case contract.MESSAGE_TYPES.SCRIPT_EXECUTE: return handleScriptExecute(message, sender);
      case contract.MESSAGE_TYPES.SCRIPT_UNREGISTER: return handleScriptUnregister(message);
      case contract.MESSAGE_TYPES.SCRIPT_LIST: return handleScriptList();
      case contract.MESSAGE_TYPES.WEB_OBSERVE: return handleWebObserve(message, sender);
      case contract.MESSAGE_TYPES.WEB_UNOBSERVE: return handleWebUnobserve(message);
      case contract.MESSAGE_TYPES.WEB_RULE_ADD: return handleWebRuleAdd(message);
      case contract.MESSAGE_TYPES.WEB_RULE_REMOVE: return handleWebRuleRemove(message);
      case contract.MESSAGE_TYPES.WEB_RULE_LIST: return handleWebRuleList();
      default: throw new Error("Unknown PASI extension API method");
    }
  }

  void loadMenus();

  globalThis.PASIBackgroundAPI = Object.freeze({
    handle,
    startHttpStream,
  });
})();
