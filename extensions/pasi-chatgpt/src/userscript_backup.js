(() => {
  "use strict";

  const VERSION = 1;
  const SYNC_CHUNK_CHARS = 7000;
  const SYNC_MAX_CHARS = 90000;

  function clone(value) {
    return value == null ? value : JSON.parse(JSON.stringify(value));
  }

  function publicScript(script) {
    const {auth, ...copy} = script;
    return clone(copy);
  }

  function buildSnapshot(registry, valuesByScript) {
    return {
      format: "pasi-userscript-backup",
      version: VERSION,
      created_at: new Date().toISOString(),
      scripts: Object.values(registry).map(publicScript),
      values: clone(valuesByScript || {}),
    };
  }

  function validateSnapshot(input) {
    if (!input || input.format !== "pasi-userscript-backup" || input.version !== VERSION) {
      throw new TypeError("Unsupported PASI userscript backup format");
    }
    if (!Array.isArray(input.scripts)) throw new TypeError("Backup scripts must be an array");
    if (!input.values || typeof input.values !== "object" || Array.isArray(input.values)) {
      throw new TypeError("Backup values must be an object");
    }
    return clone(input);
  }

  function diff(localScripts, incomingScripts) {
    const local = new Map((localScripts || []).map((item) => [item.id, item]));
    const incoming = new Map((incomingScripts || []).map((item) => [item.id, item]));
    const added = [];
    const removed = [];
    const changed = [];
    for (const [id, item] of incoming) {
      if (!local.has(id)) {
        added.push(id);
      } else if (JSON.stringify(local.get(id)) !== JSON.stringify(item)) {
        changed.push(id);
      }
    }
    for (const id of local.keys()) {
      if (!incoming.has(id)) removed.push(id);
    }
    return {added, changed, removed};
  }

  function merge(localScripts, incomingScripts, mode = "replace") {
    if (!["replace", "keep-local"].includes(mode)) {
      throw new TypeError("Unsupported userscript merge mode");
    }
    if (mode === "replace") return clone(incomingScripts || []);
    const incoming = new Map((incomingScripts || []).map((item) => [item.id, item]));
    for (const item of localScripts || []) incoming.set(item.id, item);
    return [...incoming.values()].map(clone);
  }

  function encodeSync(snapshot) {
    const encoded = JSON.stringify(snapshot);
    if (encoded.length > SYNC_MAX_CHARS) {
      throw new RangeError("Backup is too large for Chrome sync storage");
    }
    const chunks = [];
    for (let offset = 0; offset < encoded.length; offset += SYNC_CHUNK_CHARS) {
      chunks.push(encoded.slice(offset, offset + SYNC_CHUNK_CHARS));
    }
    return chunks;
  }

  function decodeSync(chunks) {
    const encoded = (chunks || []).join("");
    if (!encoded) throw new TypeError("No synced PASI userscript snapshot is available");
    return validateSnapshot(JSON.parse(encoded));
  }

  globalThis.PASIUserScriptBackup = Object.freeze({
    VERSION,
    SYNC_MAX_CHARS,
    buildSnapshot,
    validateSnapshot,
    diff,
    merge,
    encodeSync,
    decodeSync,
  });
})();
