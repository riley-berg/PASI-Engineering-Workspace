(() => {
  "use strict";

  const CONFIG_KEY = "pasi:userscripts:vcs:config";
  const TOKEN_KEY = "pasi:userscripts:vcs:token";

  function encodeBase64(text) {
    const bytes = new TextEncoder().encode(String(text));
    let binary = "";
    for (let i = 0; i < bytes.length; i += 0x8000) {
      binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
    }
    return btoa(binary);
  }

  function decodeBase64(value) {
    const binary = atob(String(value || "").replace(/\s+/g, ""));
    const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
    return new TextDecoder().decode(bytes);
  }

  function normalizeConfig(input = {}) {
    const provider = String(input.provider || "github").toLowerCase();
    if (!["github", "gitlab"].includes(provider)) throw new TypeError("Unsupported VCS provider");
    const owner = String(input.owner || "").trim();
    const repo = String(input.repo || "").trim();
    const path = String(input.path || "").replace(/^\/+/, "");
    const branch = String(input.branch || "main").trim();
    const baseUrl = String(input.baseUrl || "https://gitlab.com").replace(/\/+$/, "");
    if (!owner || !repo || !path || !branch) throw new TypeError("VCS owner, repo, path, and branch are required");
    return {provider, owner, repo, path, branch, baseUrl};
  }

  async function readConfig() {
    const value = await chrome.storage.local.get(CONFIG_KEY);
    return value[CONFIG_KEY] || null;
  }

  async function readToken() {
    const value = await chrome.storage.session.get(TOKEN_KEY);
    return String(value[TOKEN_KEY] || "");
  }

  async function saveConfig(input) {
    const config = normalizeConfig(input.config || input);
    await chrome.storage.local.set({[CONFIG_KEY]: config});
    if (input.token !== undefined) {
      const token = String(input.token || "").trim();
      if (token) await chrome.storage.session.set({[TOKEN_KEY]: token});
      else await chrome.storage.session.remove(TOKEN_KEY);
    }
    return {config, token_set: Boolean(await readToken())};
  }

  function authHeaders(provider, token) {
    if (!token) throw new Error("VCS token is not configured for this browser session");
    return provider === "github"
      ? {Authorization: "Bearer " + token, Accept: "application/vnd.github+json"}
      : {Authorization: "Bearer " + token, "Content-Type": "application/json"};
  }

  async function request(url, options) {
    const response = await fetch(url, {...options, redirect: "error", cache: "no-store"});
    const text = await response.text();
    let body;
    try { body = text ? JSON.parse(text) : {}; } catch (_) { body = {raw: text}; }
    if (!response.ok) {
      throw new Error("VCS request failed (" + response.status + "): " + String(body?.message || body?.error || text).slice(0, 500));
    }
    return body;
  }

  async function pull(input) {
    const config = normalizeConfig(input.config);
    const token = await readToken();
    const headers = authHeaders(config.provider, token);
    if (config.provider === "github") {
      const url = "https://api.github.com/repos/" + encodeURIComponent(config.owner) + "/" + encodeURIComponent(config.repo) + "/contents/" + config.path.split("/").map(encodeURIComponent).join("/") + "?ref=" + encodeURIComponent(config.branch);
      const data = await request(url, {headers});
      return {source: decodeBase64(data.content), sha: data.sha || "", etag: data.etag || null};
    }
    const project = encodeURIComponent(config.owner + "/" + config.repo);
    const file = encodeURIComponent(config.path);
    const url = config.baseUrl + "/api/v4/projects/" + project + "/repository/files/" + file + "?ref=" + encodeURIComponent(config.branch);
    const data = await request(url, {headers});
    return {source: decodeBase64(data.content), sha: data.blob_id || data.content_sha256 || "", commit_id: data.commit_id || ""};
  }

  async function push(input) {
    const config = normalizeConfig(input.config);
    const token = await readToken();
    const headers = authHeaders(config.provider, token);
    const source = String(input.source || "");
    const message = String(input.message || "Update PASI userscript").slice(0, 240);
    if (config.provider === "github") {
      let remote = null;
      try { remote = await pull({config}); } catch (_) {}
      const url = "https://api.github.com/repos/" + encodeURIComponent(config.owner) + "/" + encodeURIComponent(config.repo) + "/contents/" + config.path.split("/").map(encodeURIComponent).join("/");
      const body = {
        message,
        content: encodeBase64(source),
        branch: config.branch,
      };
      if (remote?.sha) body.sha = remote.sha;
      const data = await request(url, {
        method: "PUT",
        headers: {...headers, "Content-Type": "application/json"},
        body: JSON.stringify(body),
      });
      return {sha: data.content?.sha || "", commit: data.commit?.sha || ""};
    }
    let remote = null;
    try { remote = await pull({config}); } catch (_) {}
    const project = encodeURIComponent(config.owner + "/" + config.repo);
    const file = encodeURIComponent(config.path);
    const url = config.baseUrl + "/api/v4/projects/" + project + "/repository/files/" + file;
    const body = {
      branch: config.branch,
      content: encodeBase64(source),
      encoding: "base64",
      commit_message: message,
    };
    if (remote?.commit_id) body.last_commit_id = remote.commit_id;
    const data = await request(url, {
      method: "PUT",
      headers,
      body: JSON.stringify(body),
    });
    return {branch: config.branch, commit: data.commit_id || ""};
  }

  globalThis.PASIUserScriptVCS = Object.freeze({
    CONFIG_KEY,
    TOKEN_KEY,
    normalizeConfig,
    readConfig,
    saveConfig,
    pull,
    push,
  });
})();
