(() => {
  "use strict";

  const c = globalThis.PASIExtensionAPIContract;
  const sc = globalThis.PASIUserScriptContract;
  const runtime = globalThis.PASIUserScriptRuntime;
  const backup = globalThis.PASIUserScriptBackup;
  if (!c || !sc || !runtime || !backup) throw new Error("PASI userscript manager dependencies are missing");

  const STORE_KEY = "pasi:userscripts:registry";
  const USER_SCRIPT_PREFIX = "pasi:userscript:";
  const SYNC_PREFIX = "pasi:userscripts:sync:";
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

  function hostPatterns(script) {
    const patterns = new Set();
    for (const raw of [...(script.matches || []), ...(script.connects || [])]) {
      const value = String(raw || "").trim();
      if (!value) continue;
      if (value === "<all_urls>" || value === "*") {
        patterns.add("*://*/*");
        patterns.add("http://*/*");
        patterns.add("https://*/*");
        continue;
      }
      if (/^https?:\/\//i.test(value) || /^\*:\/\//.test(value)) {
        const normalized = value.replace(/\/[^/]*$/, "/*");
        patterns.add(normalized.endsWith("*") ? normalized : normalized + "/*");
        continue;
      }
      const host = value.replace(/^\*\./, "");
      patterns.add("http://" + value + "/*");
      patterns.add("https://" + value + "/*");
      if (host !== value) {
        patterns.add("http://*." + host + "/*");
        patterns.add("https://*." + host + "/*");
      }
    }
    return [...patterns];
  }

  async function hostsGranted(script) {
    const origins = hostPatterns(script);
    if (!origins.length || !chrome.permissions?.contains) return true;
    try {
      return await chrome.permissions.contains({origins});
    } catch (_) {
      return false;
    }
  }

  async function clientScript(script) {
    const {source, auth, ...safe} = script;
    return {
      ...safe,
      hosts: hostPatterns(script),
      hosts_granted: await hostsGranted(script),
    };
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
      if (pattern === "*" || pattern === "<all_urls>") return true;
      if (/^https?:\/\//i.test(pattern)) {
        const base = new URL(pattern);
        if (target.protocol === base.protocol && target.host === base.host) {
          if (base.pathname === "/" || target.pathname.startsWith(base.pathname.replace(/\*$/, ""))) return true;
        }
      } else if (target.hostname === pattern || target.host === pattern || target.hostname.endsWith("." + pattern.replace(/^\*\./, ""))) {
        return true;
      }
    }
    return false;
  }

  function normalizeTags(tags) {
    return [...new Set((Array.isArray(tags) ? tags : [])
      .map((value) => String(value).trim())
      .filter(Boolean)
      .map((value) => value.slice(0, 48)))].slice(0, 24);
  }

  function normalizeGroup(group) {
    return String(group || "").trim().slice(0, 80);
  }

  function stamp(script, previous) {
    const now = new Date().toISOString();
    return {
      ...script,
      auth: previous?.auth || (crypto.randomUUID() + ":" + crypto.randomUUID()),
      created_at: previous?.created_at || now,
      updated_at: now,
      revision: Number(previous?.revision || 0) + 1,
      tags: normalizeTags(script.tags),
      group: normalizeGroup(script.group),
    };
  }

  function userScriptDefinition(script) {
    const source = runtime.build(script);
    return {
      id: script.id,
      matches: script.matches,
      excludeMatches: script.excludes.length ? script.excludes : undefined,
      js: [{code: source}, {code: runtime.wrapSource(script.source)}],
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
    if (!script.enabled) {
      await unregisterNative(script.id);
      return;
    }
    await unregisterNative(script.id);
    await chrome.userScripts.register([userScriptDefinition(script)]);
  }

  async function registerNativeBatch(scripts) {
    ensureAvailable();
    const enabled = scripts.filter((script) => script.enabled);
    if (!enabled.length) return;
    await chrome.userScripts.register(enabled.map(userScriptDefinition));
  }

  async function configureWorld() {
    ensureAvailable();
    await chrome.userScripts.configureWorld({
      messaging: true,
      csp: "script-src 'self'",
    });
  }

  async function registerImpl(message) {
    ensureAvailable();
    const parsed = sc.validateRegistration(message.source, {
      id: message.id,
      metadata: message.metadata,
      enabled: message.enabled !== false,
    });
    const all = await registry();
    if (all[parsed.id] && message.replace !== true) {
      throw new Error("PASI userscript already exists: " + parsed.id);
    }
    const script = stamp({...parsed, tags: message.tags, group: message.group}, all[parsed.id]);
    await configureWorld();
    await saveRegistry({...all, [script.id]: script});
    try {
      await registerNative(script);
    } catch (error) {
      const next = await registry();
      if (all[script.id]) next[script.id] = all[script.id];
      else delete next[script.id];
      await saveRegistry(next);
      throw error;
    }
    return {ok: true, script: await clientScript(script)};
  }

  async function unregisterImpl(message) {
    ensureAvailable();
    const id = String(message.id || "");
    const all = await registry();
    if (!all[id]) return {ok: true, removed: false};
    await unregisterNative(id);
    delete all[id];
    await saveRegistry(all);
    const values = await chrome.storage.local.get(null);
    const keys = Object.keys(values).filter((key) => key.startsWith(USER_SCRIPT_PREFIX + id + ":"));
    if (keys.length) await chrome.storage.local.remove(keys);
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
    return {
      ok: true,
      scripts: await Promise.all(Object.values(all).map(clientScript)),
    };
  }

  async function toggleImpl(id, enabled) {
    ensureAvailable();
    const all = await registry();
    const script = scriptById(all, id);
    script.enabled = Boolean(enabled);
    const stamped = stamp(script, script);
    all[id] = stamped;
    await unregisterNative(id);
    if (stamped.enabled) await registerNative(stamped);
    await saveRegistry(all);
    return {ok: true, script: await clientScript(stamped)};
  }

  function scriptById(all, id) {
    const script = all[id];
    if (!script) throw new Error("Unknown PASI userscript: " + id);
    return script;
  }

  async function updateImpl(message) {
    ensureAvailable();
    const all = await registry();
    const current = scriptById(all, String(message.id || ""));
    const source = message.source === undefined ? current.source : String(message.source);
    const metadata = {
      name: current.name,
      namespace: current.namespace,
      version: current.version,
      description: current.description,
      matches: current.matches,
      excludes: current.excludes,
      grants: current.grants,
      connects: current.connects,
      runAt: current.runAt,
      noframes: current.noframes,
      ...(message.metadata || {}),
    };
    const parsed = sc.validateRegistration(source, {
      id: current.id,
      metadata,
      enabled: message.enabled === undefined ? current.enabled : Boolean(message.enabled),
    });
    const next = stamp({
      ...parsed,
      tags: message.tags === undefined ? current.tags : message.tags,
      group: message.group === undefined ? current.group : message.group,
    }, current);
    all[current.id] = next;
    await configureWorld();
    await registerNative(next);
    await saveRegistry(all);
    return {ok: true, script: await clientScript(next)};
  }

  async function info(id) {
    const all = await registry();
    return {ok: true, script: await clientScript(scriptById(all, String(id)))};
  }

  async function collectValues() {
    const all = await registry();
    const raw = await chrome.storage.local.get(null);
    const values = {};
    for (const script of Object.values(all)) {
      const prefix = USER_SCRIPT_PREFIX + script.id + ":";
      values[script.id] = {};
      for (const [key, value] of Object.entries(raw)) {
        if (!key.startsWith(prefix) || key.endsWith(":last_error")) continue;
        values[script.id][key.slice(prefix.length)] = value;
      }
    }
    return values;
  }

  async function backupNow() {
    return backup.buildSnapshot(await registry(), await collectValues());
  }

  async function normalizedBackupScripts(input) {
    const parsed = backup.validateSnapshot(input);
    return parsed.scripts.map((item) => {
      const source = String(item.source || "");
      const script = sc.validateRegistration(source, {
        id: item.id,
        metadata: item,
        enabled: item.enabled !== false,
      });
      return stamp({
        ...script,
        tags: item.tags,
        group: item.group,
        created_at: item.created_at,
        updated_at: item.updated_at,
        revision: item.revision,
      }, null);
    });
  }

  async function removeValuesForScripts(ids) {
    const raw = await chrome.storage.local.get(null);
    const keys = Object.keys(raw).filter((key) => ids.some((id) => key.startsWith(USER_SCRIPT_PREFIX + id + ":")));
    if (keys.length) await chrome.storage.local.remove(keys);
  }

  async function restoreImpl(message) {
    ensureAvailable();
    const incoming = await normalizedBackupScripts(message.backup);
    const local = await registry();
    const localScripts = Object.values(local);
    const mode = message.mode === "replace" ? "replace" : "keep-local";
    const merged = backup.merge(localScripts, incoming, mode);
    if (mode === "replace") {
      await chrome.userScripts.unregister({ids: localScripts.map((item) => item.id)}).catch(() => undefined);
      await removeValuesForScripts(localScripts.map((item) => item.id));
    }
    const next = Object.fromEntries(merged.map((item) => [item.id, item]));
    const addedIds = new Set(mode === "keep-local" ? incoming.filter((item) => !local[item.id]).map((item) => item.id) : incoming.map((item) => item.id));
    for (const script of incoming) {
      if (mode === "keep-local" && local[script.id]) continue;
      next[script.id] = script;
    }
    await saveRegistry(next);
    await configureWorld();
    await registerNativeBatch(Object.values(next));
    const incomingValues = message.backup.values || {};
    for (const [scriptId, values] of Object.entries(incomingValues)) {
      if (!addedIds.has(scriptId) && mode === "keep-local") continue;
      const writes = {};
      for (const [key, value] of Object.entries(values || {})) writes[storageKey(scriptId, key)] = value;
      if (Object.keys(writes).length) await chrome.storage.local.set(writes);
    }
    return {
      ok: true,
      imported: incoming.length,
      mode,
      diff: backup.diff(localScripts, incoming),
    };
  }

  async function syncSnapshot(snapshot) {
    const chunks = backup.encodeSync(snapshot);
    const existing = await chrome.storage.sync.get(null);
    const oldKeys = Object.keys(existing).filter((key) => key.startsWith(SYNC_PREFIX));
    if (oldKeys.length) await chrome.storage.sync.remove(oldKeys);
    const record = {version: backup.VERSION, count: chunks.length};
    const writes = {[SYNC_PREFIX + "index"]: record};
    chunks.forEach((chunk, index) => { writes[SYNC_PREFIX + index] = chunk; });
    await chrome.storage.sync.set(writes);
    return chunks.length;
  }

  async function readSyncedSnapshot() {
    const index = await chrome.storage.sync.get(SYNC_PREFIX + "index");
    const record = index[SYNC_PREFIX + "index"];
    if (!record) return null;
    const keys = [];
    for (let i = 0; i < Number(record.count || 0); i++) keys.push(SYNC_PREFIX + i);
    const data = await chrome.storage.sync.get(keys);
    return backup.decodeSync(keys.map((key) => data[key] || ""));
  }

  async function sync(message) {
    const local = await backupNow();
    let remote = null;
    try {
      remote = await readSyncedSnapshot();
    } catch (error) {
      return {ok: false, status: "remote-invalid", error: String(error?.message || error)};
    }
    if (!remote) {
      try {
        const chunks = await syncSnapshot(local);
        return {ok: true, status: "pushed", chunks};
      } catch (error) {
        return {ok: false, status: "too-large", error: String(error?.message || error)};
      }
    }
    const diff = backup.diff(local.scripts, remote.scripts);
    if (!diff.added.length && !diff.changed.length && !diff.removed.length) {
      return {ok: true, status: "same", diff};
    }
    if (!["replace", "keep-local"].includes(message.mode)) {
      return {ok: true, status: "conflict", diff};
    }
    await restore({backup: remote, mode: message.mode});
    const current = await backupNow();
    try {
      await syncSnapshot(current);
    } catch (error) {
      return {ok: false, status: "too-large", error: String(error?.message || error)};
    }
    return {ok: true, status: message.mode === "replace" ? "pulled" : "merged", diff};
  }

  async function rpc(message, sender) {
    const all = await registry();
    const script = scriptById(all, String(message.script_id));
    if (String(message.auth || "") !== String(script.auth || "")) {
      throw new Error("PASI userscript authorization failed");
    }
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
        return {ok: true, value: Object.keys(values).filter((key) => key.startsWith(prefix) && !key.endsWith(":last_error")).map((key) => key.slice(prefix.length)).sort()};
      }
      case "storage.watch": {
        requireGrant(script, "storage", method);
        const key = c.normalizeKey(args.key);
        const listenerId = script.id + ":" + sender?.tab?.id + ":" + crypto.randomUUID();
        valueWatchers.set(listenerId, {script_id: script.id, tab_id: sender?.tab?.id, key});
        return {ok: true, value: listenerId};
      }
      case "storage.unwatch":
        requireGrant(script, "storage", method);
        valueWatchers.delete(String(args.listener_id || ""));
        return {ok: true};
      case "tabs.open":
        requireGrant(script, "tabs", method);
        return {ok: true, value: await chrome.tabs.create({url: String(args.url || ""), active: args.active !== false}).then((tab) => ({tab_id: tab.id, url: tab.url || ""}))};
      case "notification":
        requireGrant(script, "notification", method);
        return {ok: true, value: await chrome.notifications.create({
          type: "basic",
          title: String(args.title || script.name),
          message: String(args.message || ""),
          iconUrl: "icons/icon128.png",
        })};
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
        return {ok: true, value: await chrome.downloads.download({url, filename, saveAs: args.saveAs === true, conflictAction: "uniquify"})};
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
        if (!connectAllowed(script, url)) throw new Error("PASI userscript URL is not allowed by @connect: " + url);
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
          return {ok: true, value: {
            ok: response.ok,
            status: response.status,
            statusText: response.statusText,
            url: response.url,
            headers: Object.fromEntries(response.headers.entries()),
            body,
          }};
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
    ports.get(command.script_id + ":" + tabId)?.postMessage({
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
      if (key === "last_error") continue;
      for (const [listenerId, watcher] of valueWatchers.entries()) {
        if (watcher.script_id !== scriptId || watcher.key !== key || typeof watcher.tab_id !== "number") continue;
        ports.get(scriptId + ":" + watcher.tab_id)?.postMessage({
          type: "value-change",
          listener_id: listenerId,
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
      case c.MESSAGE_TYPES.USERSCRIPT_REGISTER: return installQueue.run(() => registerImpl(message));
      case c.MESSAGE_TYPES.USERSCRIPT_UNREGISTER: return installQueue.run(() => unregisterImpl(message));
      case c.MESSAGE_TYPES.USERSCRIPT_LIST: return list();
      case c.MESSAGE_TYPES.USERSCRIPT_ENABLE: return installQueue.run(() => toggleImpl(String(message.id || ""), true));
      case c.MESSAGE_TYPES.USERSCRIPT_DISABLE: return installQueue.run(() => toggleImpl(String(message.id || ""), false));
      case c.MESSAGE_TYPES.USERSCRIPT_INFO: return info(String(message.id || ""));
      case c.MESSAGE_TYPES.USERSCRIPT_UPDATE: return installQueue.run(() => updateImpl(message));
      case c.MESSAGE_TYPES.USERSCRIPT_BACKUP: return {ok: true, backup: await backupNow()};
      case c.MESSAGE_TYPES.USERSCRIPT_RESTORE: return installQueue.run(() => restoreImpl(message));
      case c.MESSAGE_TYPES.USERSCRIPT_SYNC: return sync(message);
      default: throw new Error("Unknown PASI userscript manager method");
    }
  }

  async function restoreRegistered() {
    if (!chrome.userScripts?.register) return;
    await configureWorld();
    const all = await registry();
    await registerNativeBatch(Object.values(all));
    await recoverOpenTabs(Object.values(all));
  }

  function urlLooksLikeMatch(url, script) {
    const excluded = (script.excludes || []).some((pattern) => urlMatchesPattern(url, pattern));
    return !excluded && (script.matches || []).some((pattern) => urlMatchesPattern(url, pattern));
  }

  async function recoverOpenTabs(scripts) {
    if (!chrome.userScripts?.execute) return;
    const tabs = await chrome.tabs.query({});
    for (const tab of tabs) {
      const url = String(tab.url || "");
      if (!/^https?:/.test(url) || typeof tab.id !== "number") continue;
      for (const script of scripts) {
        if (!script.enabled || !urlLooksLikeMatch(url, script)) continue;
        if (Array.isArray(script.host_allowlist) && !script.host_allowlist.some((pattern) => urlMatchesPattern(url, pattern))) continue;
        try {
          await chrome.userScripts.execute({
            target: {tabId: tab.id},
            injectImmediately: true,
            world: script.grants.includes("mainWorld") ? "MAIN" : "USER_SCRIPT",
            js: [{code: runtime.build(script)}, {code: runtime.wrapSource(script.source)}],
          });
        } catch (error) {
          console.warn("PASI userscript recovery failed:", script.id, tab.id, error);
        }
      }
    }
  }

  globalThis.PASIUserScriptManager = Object.freeze({
    handle,
    restore: restoreRegistered,
  });
})();
