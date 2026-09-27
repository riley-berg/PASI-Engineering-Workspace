(() => {
  "use strict";

  const CONFIG_KEY = "pasi:userscripts:cloud:config";
  const STATUS_KEY = "pasi:userscripts:cloud:status";
  const CREDENTIALS_KEY = "pasi:userscripts:cloud:credentials";

  async function readConfig() {
    const value = await chrome.storage.local.get(CONFIG_KEY);
    return value[CONFIG_KEY] || null;
  }

  async function saveConfig(input = {}) {
    const config = {
      provider: String(input.provider || "webdav").toLowerCase(),
      endpoint: String(input.endpoint || "").replace(/\/+$/, ""),
      filename: String(input.filename || "pasi-userscripts-backup.json").replace(/^\/+/, ""),
    };
    if (config.provider !== "webdav") throw new TypeError("Supported remote backup provider: webdav");
    if (!/^https?:\/\//i.test(config.endpoint)) throw new TypeError("WebDAV endpoint must be http(s)");
    await chrome.storage.local.set({[CONFIG_KEY]: config});
    if (input.username !== undefined || input.password !== undefined) {
      await chrome.storage.session.set({
        [CREDENTIALS_KEY]: {
          username: String(input.username || ""),
          password: String(input.password || ""),
        },
      });
    }
    return {config, credentials_set: Boolean(await readCredentials())};
  }

  async function readCredentials() {
    const value = await chrome.storage.session.get(CREDENTIALS_KEY);
    const credentials = value[CREDENTIALS_KEY];
    return credentials && (credentials.username || credentials.password) ? credentials : null;
  }

  async function setStatus(state, extra = {}) {
    const status = {...extra, state, updated_at: new Date().toISOString()};
    await chrome.storage.local.set({[STATUS_KEY]: status});
    return status;
  }

  async function request(config, options = {}) {
    const credentials = await readCredentials();
    const headers = new Headers(options.headers || {});
    if (credentials) {
      const token = btoa(unescape(encodeURIComponent(credentials.username + ":" + credentials.password)));
      headers.set("Authorization", "Basic " + token);
    }
    const response = await fetch(config.endpoint + "/" + config.filename, {
      ...options,
      headers,
      redirect: "error",
      cache: "no-store",
    });
    if (!response.ok) {
      const reason = response.status === 401 || response.status === 403 ? "authentication-error" : "connection-error";
      const error = new Error("Remote backup request failed: HTTP " + response.status);
      error.status = response.status;
      error.reason = reason;
      throw error;
    }
    return response;
  }

  async function status() {
    const config = await readConfig();
    if (!config) return {state: "unconfigured"};
    try {
      await request(config, {method: "HEAD"});
      return await setStatus("ok", {provider: config.provider});
    } catch (error) {
      return await setStatus(error.reason || "connection-error", {provider: config.provider, error: String(error.message || error)});
    }
  }

  async function push(snapshot) {
    const config = await readConfig();
    if (!config) throw new Error("Remote backup is not configured");
    await setStatus("syncing", {provider: config.provider});
    try {
      await request(config, {
        method: "PUT",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(snapshot),
      });
      return await setStatus("ok", {provider: config.provider, operation: "push"});
    } catch (error) {
      return await setStatus(error.reason || "connection-error", {provider: config.provider, error: String(error.message || error)});
    }
  }

  async function pull() {
    const config = await readConfig();
    if (!config) throw new Error("Remote backup is not configured");
    await setStatus("syncing", {provider: config.provider});
    try {
      const response = await request(config, {method: "GET"});
      const snapshot = await response.json();
      return {snapshot, status: await setStatus("ok", {provider: config.provider, operation: "pull"})};
    } catch (error) {
      return {snapshot: null, status: await setStatus(error.reason || "connection-error", {provider: config.provider, error: String(error.message || error)})};
    }
  }

  async function readStatus() {
    const value = await chrome.storage.local.get(STATUS_KEY);
    return value[STATUS_KEY] || {state: "idle"};
  }

  globalThis.PASIUserScriptCloud = Object.freeze({
    CONFIG_KEY,
    STATUS_KEY,
    saveConfig,
    readConfig,
    status,
    push,
    pull,
    readStatus,
  });
})();
