(() => {
  "use strict";

  const c = globalThis.PASIExtensionAPIContract;
  if (!c) throw new Error("PASI userscript contract requires the extension contract");

  const GRANTS = Object.freeze([
    "storage",
    "http",
    "tabs",
    "menu",
    "clipboard",
    "notification",
    "download",
    "unsafeWindow",
    "mainWorld",
  ]);

  const RUN_AT = Object.freeze(["document_start", "document_end", "document_idle"]);

  function normalizeGrant(value) {
    const grant = String(value || "").trim();
    if (grant === "none" || grant === "") return null;
    if (!GRANTS.includes(grant)) {
      throw new TypeError("Unsupported PASI userscript grant: " + grant);
    }
    return grant;
  }

  function parseMetadata(source) {
    const text = String(source || "");
    const match = text.match(/(^|\n)\s*\/\/\s*==UserScript==([\s\S]*?)\/\/\s*==\/UserScript==/i);
    if (!match) return {name: "PASI User Script", matches: [], grants: []};

    const metadata = {
      name: "PASI User Script",
      namespace: "pasi.local",
      version: "1.0.0",
      description: "",
      matches: [],
      excludes: [],
      grants: [],
      connects: [],
      runAt: "document_idle",
      noframes: false,
    };

    for (const rawLine of match[2].split(/\r?\n/)) {
      const line = rawLine.replace(/^\s*\/\/\s?/, "").trim();
      if (!line.startsWith("@")) continue;
      const match = line.match(/^@(\\S+)(?:\\s+(.*))?$/);
      if (!match) continue;
      const key = match[1];
      const value = String(match[2] || "").trim();
      switch (key) {
        case "name": metadata.name = value || metadata.name; break;
        case "namespace": metadata.namespace = value || metadata.namespace; break;
        case "version": metadata.version = value || metadata.version; break;
        case "description": metadata.description = value; break;
        case "match": metadata.matches.push(value); break;
        case "exclude": metadata.excludes.push(value); break;
        case "grant": {
          const grant = normalizeGrant(value);
          if (grant) metadata.grants.push(grant);
          break;
        }
        case "connect": metadata.connects.push(value); break;
        case "run-at":
          if (!RUN_AT.includes(value)) throw new TypeError("Unsupported @run-at: " + value);
          metadata.runAt = value;
          break;
        case "noframes": metadata.noframes = true; break;
        case "include":
          throw new TypeError("@include is not supported; use @match patterns");
        case "require":
        case "resource":
          throw new TypeError("External @require/@resource is not supported; bundle dependencies into the script");
        default:
          break;
      }
    }

    metadata.matches = [...new Set(metadata.matches.filter(Boolean))];
    metadata.excludes = [...new Set(metadata.excludes.filter(Boolean))];
    metadata.grants = [...new Set(metadata.grants)];
    metadata.connects = [...new Set(metadata.connects.filter(Boolean))];

    if (!metadata.matches.length) {
      throw new TypeError("PASI userscripts require at least one @match");
    }
    if (metadata.connects.length && !metadata.grants.includes("http")) {
      throw new TypeError("@connect requires the http grant");
    }
    if (metadata.grants.includes("unsafeWindow") && !metadata.grants.includes("mainWorld")) {
      metadata.grants.push("mainWorld");
    }
    return metadata;
  }

  function validateRegistration(source, options = {}) {
    const code = String(source || "");
    if (!code.trim()) throw new TypeError("PASI userscript source is empty");
    if (code.length > c.LIMITS.userScriptSourceChars) {
      throw new TypeError("PASI userscript source exceeds its safety limit");
    }
    const metadata = options.metadata
      ? {
          ...parseMetadata(code),
          ...options.metadata,
          matches: Array.isArray(options.metadata.matches) ? options.metadata.matches.map(String) : parseMetadata(code).matches,
          grants: Array.isArray(options.metadata.grants) ? options.metadata.grants.map(normalizeGrant).filter(Boolean) : parseMetadata(code).grants,
        }
      : parseMetadata(code);
    const id = String(options.id || metadata.name || crypto.randomUUID()).trim().slice(0, c.LIMITS.userScriptIdChars);
    if (!/^[A-Za-z][A-Za-z0-9._:-]{0,99}$/.test(id)) throw new TypeError("Invalid PASI userscript id");
    const grants = [...new Set((metadata.grants || []).map(normalizeGrant).filter(Boolean))];
    if (grants.includes("unsafeWindow") && !grants.includes("mainWorld")) grants.push("mainWorld");
    const matches = [...new Set((metadata.matches || []).map(String).filter(Boolean))];
    if (!matches.length) throw new TypeError("PASI userscript requires at least one match pattern");
    const runAt = RUN_AT.includes(metadata.runAt) ? metadata.runAt : "document_idle";
    return {
      id,
      name: String(metadata.name || id).slice(0, c.LIMITS.userScriptNameChars),
      namespace: String(metadata.namespace || "pasi.local").slice(0, c.LIMITS.userScriptNameChars),
      version: String(metadata.version || "1.0.0").slice(0, c.LIMITS.userScriptNameChars),
      description: String(metadata.description || "").slice(0, 1000),
      matches,
      excludes: [...new Set((metadata.excludes || []).map(String).filter(Boolean))],
      grants,
      connects: [...new Set((metadata.connects || []).map(String).filter(Boolean))],
      runAt,
      noframes: Boolean(metadata.noframes),
      enabled: options.enabled !== false,
      source: code,
    };
  }

  globalThis.PASIUserScriptContract = Object.freeze({
    GRANTS,
    RUN_AT,
    parseMetadata,
    validateRegistration,
  });
})();