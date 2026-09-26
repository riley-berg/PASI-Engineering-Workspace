(() => {
  "use strict";

  const c = globalThis.PASIExtensionAPIContract;
  const sc = globalThis.PASIUserScriptContract;
  const runtime = globalThis.PASIUserScriptRuntime;
  if (!c || !sc || !runtime) throw new Error("PASI userscript manager dependencies are missing");

  const STORE_KEY = "pasi:userscripts:registry";
  const USER_SCRIPT_PREFIX = "pasi:userscript:";
  const ports = new Map();
  const valueWatchers = new Map();
  const menuCommands = new Map();

  function ensureAvailable() {
    if (!chrome.userScripts?.register) {
      throw new Error("PASI dynamic userscripts require Chrome 120+ User Scripts API");
    }
  }

  async function registry() {
    const stored = await chrome.storage.local.get(STORE_KEY);
    return stored[STORE_KEY] || {};
  }

  async function saveRegistry(value) {
    await chrome.storage.local.set({[STORE_KEY]: value});
  }

  function storageKey(scriptId, key) {
    return USER_SCRIPT_PREFIX + scriptId + ":" + c.normalizeKey(key);
  }

  function senderScriptId(sender, explicit) {
    const fromPort = String(sender?.documentId || "");
    const explicitId = String(explicit || "");
    if (!explicitId) throw new Error("Missing PASI userscript id");
    return {id: explicitId, documentId: fromPort};
  }

  function scriptById(all, id) {
    const script = all[id];
    if (!script) throw new Error("Unknown PASI userscript: " + id);
    return script;
  }

  function requireGrant(script, grant, method) {
    if (!script.grants.includes(grant)) {
      throw new Error("PASI userscript " + script.id + " lacks @" + grant + " for " + method);
    }
  }

  function connectAllowed(script, url) {
    const target = new URL(String(url));
    for (const raw of script.connects) {
      const pattern = String(raw).trim();
      if (!pattern) continue;
      if (pattern === "*") return true;
      if (/^https?:\/\//i.test(pattern)) {
        const base = new URL(pattern);
        if (target.protocol === base.protocol && target.host === base.host) {
          if (base.pathname === "/" || target.pathname.startsWith(base.pathname.replace(/\*$/, ""))) return true;
        }
      } else if (target.hostname === pattern || target.host === pattern) {
        return true;
      }
    }
    return false;
  }

  function sanitizeScriptForClient(script) {
    const {source, ...safe} = script;
    return {...safe};
  }

  function userScriptDefinition(script) {
    const source = runtime.build(script);
    return {
      id: script.id,
      matches: script.matches,
      excludeMatches: script.excludes.length ? script.excludes : undefined,
      js: [{code: source}],
      runAt: script.runAt,
      allFrames: !script.noframes,
      world: script.grants.includes("mainWorld") ? "MAIN" : "USER_SCRIPT",
      persistAcrossSessions: true,
    };
  }

  async function unregisterNative(id) {
    if (!chrome.userScripts?.unregister) return;
    await chrome.userScripts.unregister({ids: [id]}).catch(() => undefined);
  }

  async function registerNative(script) {
    ensureAvailable();
    await unregisterNative(script.id);
    if (!script.enabled) return;
    await chrome.userScripts.register([userScriptDefinition(script)]);
  }

  async function configureWorld() {
    ensureAvailable();
    await chrome.userScripts.configureWorld({
      messaging: true,
      csp: "script-src 'self'",
    });
  }

  async function register(message) {
    ensureAvailable();
    const script = sc.validateRegistration(message.source, {
      id: message.id,
      metadata: message.metadata,
      enabled: message.enabled !== false,
    });
    const all = await registry();
    if (all[script.id] && message.replace !== true) {
      throw new Error("PASI userscript already exists: " + script.id);
    }
    await configureWorld();
    await saveRegistry({...all, [script.id]: script});
    try {
      await registerNative(script);
    } catch (error) {
      const next = await registry();
      delete next[script.id];
      await saveRegistry(next);
      throw error;
    }
    return {ok: true, script: sanitizeScriptForClient(script)};
  }

  async function unregister(message) {
    ensureAvailable();
    const id = String(message.id || "");
    const all = await registry();
    if (!all[id]) return {ok: true, removed: false};
    await unregisterNative(id);
    delete all[id];
    await saveRegistry(all);
    for (const key of [...valueWatchers.keys()]) {
      if (key.startsWith(id + ":")) valueWatchers.delete(key);
    }
    for (const [commandId, command] of menuCommands) {
      if (command.script_id !== id) continue;
      await chrome.contextMenus.remove(commandId).catch(() => undefined);
      menuCommands.delete(commandId);
    }
    return {ok: true, removed: true};
  }

  async function list() {
    const all = await registry();
    return {ok: true, scripts: Object.values(all).map(sanitizeScriptForClient)};
  }

  async function toggle(id, enabled) {
    ensureAvailable();
    const all = await registry();
    const script = scriptById(all, id);
    script.enabled = enabled;
    await unregisterNative(id);
    if (enabled) await registerNative(script);
    await saveRegistry(all);
    return {ok: true, script: sanitizeScriptForClient(script)};
  }

  async function info(id) {
    const all = await registry();
    return {ok: true, script: sanitizeScriptForClient(scriptById(all, String(id)))};
  }

  async function restore() {
    if (!chrome.userScripts?.register) return;
    await configureWorld();
    const all = await registry();
    for (const script of Object.values(all)) {
      try {
        await registerNative(script);
      } catch (error) {
        console.warn("PASI userscript restore failed:", script.id, error);
      }
    }
  }

  async function rpc(message, sender) {
    const all = await registry();
    const script = scriptById(all, String(message.script_id));
    if (!script.enabled) throw new Error("PASI userscript is disabled: " + script.id);
    const method = String(message.method || "");
    const args = message.args || {};

    switch (method) {
      case "storage.get": {
        requireGrant(script, "storage", method);
        const key = storageKey(script.id, args.key);
        const values = await chrome.storage.local.get(key);
        return {ok: true, value: values[key]};
      }
      case "storage.set": {
        requireGrant(script, "storage", method);
        await chrome.storage.local.set({[storageKey(script.id, args.key)]: args.value});
        return {ok: true};
      }
      case "storage.delete": {
        requireGrant(script, "storage", method);
        await chrome.storage.local.remove(storageKey(script.id, args.key));
        return {ok: true};
      }
      case "storage.list": {
        requireGrant(script, "storage", method);
        const prefix = USER_SCRIPT_PREFIX + script.id + ":";
        const values = await chrome.storage.local.get(null);
        return {ok: true, value: Object.keys(values).filter((key) => key.startsWith(prefix)).map((key) => key.slice(prefix.length)).sort()};
      }
      case "storage.watch": {
        requireGrant(script, "storage", method);
        const key = c.normalizeKey(args.key);
        const listenerId = script.id + ":" + sender?.tab?.id + ":" + crypto.randomUUID();
        valueWatchers.set(listenerId, {script_id: script.id, tab_id: sender?.tab?.id, key});
        return {ok: true, value: listenerId};
      }
      case "storage.unwatch": {
        requireGrant(script, "storage", method);
        valueWatchers.delete(String(args.listener_id || ""));
        return {ok: true};
      }
      case "tabs.open": {
        requireGrant(script, "tabs", method);
        const tab = await chrome.tabs.create({url: String(args.url || ""), active: args.active !== false});
        return {ok: true, value: {tab_id: tab.id, url: tab.url || ""}};
      }
      case "notification": {
        requireGrant(script, "notification", method);
        const id = await chrome.notifications.create({
          type: "basic",
          title: String(args.title || script.name),
          message: String(args.message || ""),
          iconUrl: "icons/icon128.png",
        });
        return {ok: true, value: id};
      }
      case "clipboard.write": {
        requireGrant(script, "clipboard", method);
        const tabId = sender?.tab?.id;
        if (typeof tabId !== "number") throw new Error("clipboard requires a user script tab");
        await chrome.scripting.executeScript({
          target: {tabId},
          world: "ISOLATED",
          func: (value) => {
            const area = document.createElement("textarea");
            area.value = String(value);
            area.style.position = "fixed";
            area.style.left = "-10000px";
            (document.body || document.documentElement).appendChild(area);
            area.focus();
            area.select();
            const copied = document.execCommand("copy");
            area.remove();
            if (!copied) throw new Error("clipboard write was rejected");
          },
          args: [String(args.text || "")],
        });
        return {ok: true, value: {ok: true}};
      }
      case "download": {
        requireGrant(script, "download", method);
        const url = String(args.url || "");
        const parsed = new URL(url);
        if (!/^https?:$/.test(parsed.protocol)) throw new Error("download requires an http(s) URL");
        const filename = String(args.filename || "pasi-userscript-download.bin");
        const id = await chrome.downloads.download({url, filename, saveAs: args.saveAs === true, conflictAction: "uniquify"});
        return {ok: true, value: id};
      }
      case "menu.register": {
        requireGrant(script, "menu", method);
        const commandId = "pasi-userscript:" + script.id + ":" + crypto.randomUUID();
        await chrome.contextMenus.create({id: commandId, title: String(args.title || script.name), contexts: ["page"]});
        menuCommands.set(commandId, {script_id: script.id});
        return {ok: true, value: commandId};
      }
      case "http.request": {
        requireGrant(script, "http", method);
        const url = String(args.url || "");
        if (!connectAllowed(script, url)) {
          throw new Error("PASI userscript URL is not allowed by @connect: " + url);
        }
        const controller = new AbortController();
        const timeout = Math.min(Math.max(Number(args.timeout_ms) || 10000, 250), 30000);
        const timer = setTimeout(() => controller.abort(), timeout);
        try {
          const response = await fetch(url, {
            method: String(args.method || "GET").toUpperCase(),
            headers: args.headers || {},
            body: args.data || undefined,
            credentials: "omit",
            redirect: "error",
            cache: "no-store",
            signal: controller.signal,
          });
          const body = await response.text();
          return {
            ok: true,
            value: {
              ok: response.ok,
              status: response.status,
              statusText: response.statusText,
              url: response.url,
              headers: Object.fromEntries(response.headers.entries()),
              body,
            },
          };
        } finally {
          clearTimeout(timer);
        }
      }
      case "script.error":
        await chrome.storage.local.set({
          [USER_SCRIPT_PREFIX + script.id + ":last_error"]: {
            message: String(args.message || ""),
            stack: String(args.stack || ""),
            at: new Date().toISOString(),
          },
        });
        return {ok: true};
      default:
        throw new Error("Unknown PASI userscript RPC method: " + method);
    }
  }

  chrome.runtime.onUserScriptMessage?.addListener((message, sender, sendResponse) => {
    if (message?.type !== "pasi.userscript.rpc") return;
    Promise.resolve(rpc(message, sender))
      .then(sendResponse)
      .catch((error) => sendResponse({ok: false, error: String(error?.message || error)}));
    return true;
  });

  chrome.runtime.onUserScriptConnect?.addListener((port) => {
    const name = String(port.name || "");
    if (!name.startsWith("pasi.userscript:")) return;
    const scriptId = name.slice("pasi.userscript:".length);
    const tabId = port.sender?.tab?.id;
    ports.set(scriptId + ":" + tabId, port);
    port.onDisconnect.addListener(() => {
      ports.delete(scriptId + ":" + tabId);
    });
  });

  chrome.contextMenus.onClicked.addListener((info, tab) => {
    const command = menuCommands.get(String(info.menuItemId || ""));
    if (!command) return;
    const tabId = tab?.id;
    const port = ports.get(command.script_id + ":" + tabId);
    if (!port) return;
    port.postMessage({
      type: "menu-command",
      command_id: String(info.menuItemId),
      info,
    });
  });

  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName !== "local") return;
    for (const [fullKey, change] of Object.entries(changes)) {
      if (!fullKey.startsWith(USER_SCRIPT_PREFIX)) continue;
      const remainder = fullKey.slice(USER_SCRIPT_PREFIX.length);
      const separator = remainder.indexOf(":");
      if (separator < 1) continue;
      const scriptId = remainder.slice(0, separator);
      const key = remainder.slice(separator + 1);
      for (const watcher of valueWatchers.values()) {
        if (watcher.script_id !== scriptId || watcher.key !== key || typeof watcher.tab_id !== "number") continue;
        const port = ports.get(scriptId + ":" + watcher.tab_id);
        port?.postMessage({
          type: "value-change",
          listener_id: [...valueWatchers.entries()].find(([, value]) => value === watcher)?.[0],
          key,
          old_value: change.oldValue,
          new_value: change.newValue,
          remote: true,
        });
      }
    }
  });

  async function handle(message) {
    switch (message?.type) {
      case c.MESSAGE_TYPES.USERSCRIPT_REGISTER: return register(message);
      case c.MESSAGE_TYPES.USERSCRIPT_UNREGISTER: return unregister(message);
      case c.MESSAGE_TYPES.USERSCRIPT_LIST: return list();
      case c.MESSAGE_TYPES.USERSCRIPT_ENABLE: return toggle(String(message.id || ""), true);
      case c.MESSAGE_TYPES.USERSCRIPT_DISABLE: return toggle(String(message.id || ""), false);
      case c.MESSAGE_TYPES.USERSCRIPT_INFO: return info(String(message.id || ""));
      default: throw new Error("Unknown PASI userscript manager method");
    }
  }

  globalThis.PASIUserScriptManager = Object.freeze({
    handle,
    restore,
  });
})();