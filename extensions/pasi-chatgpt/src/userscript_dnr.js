(() => {
  "use strict";

  const PREFIX = 240000000;
  const MAX_RULES_PER_SCRIPT = 200;

  function hashId(scriptId, ruleId) {
    const text = String(scriptId) + ":" + String(ruleId);
    let hash = 2166136261;
    for (let i = 0; i < text.length; i++) {
      hash ^= text.charCodeAt(i);
      hash = Math.imul(hash, 16777619);
    }
    return PREFIX + (hash >>> 0) % 50000000;
  }

  function normalizeAction(action) {
    const input = action || {};
    if (!input || typeof input !== "object") throw new TypeError("Network action must be an object");
    if (input.type === "block" || input.type === "allow") return {type: input.type};
    if (input.type === "modifyHeaders") {
      const requestHeaders = Array.isArray(input.requestHeaders) ? input.requestHeaders : [];
      const responseHeaders = Array.isArray(input.responseHeaders) ? input.responseHeaders : [];
      const normalize = (items) => items.slice(0, 32).map((item) => {
        const operation = String(item.operation || "");
        if (!["set", "remove", "append"].includes(operation)) {
          throw new TypeError("Unsupported header operation: " + operation);
        }
        const header = String(item.header || "").toLowerCase();
        if (!/^[a-z0-9-]{1,128}$/.test(header)) throw new TypeError("Invalid header name");
        const value = item.value === undefined ? undefined : String(item.value).slice(0, 8000);
        if (value === undefined && operation !== "remove") {
          throw new TypeError("Header operation requires a value");
        }
        return value === undefined
          ? {header, operation}
          : {header, operation, value};
      });
      return {
        type: "modifyHeaders",
        requestHeaders: normalize(requestHeaders),
        responseHeaders: normalize(responseHeaders),
      };
    }
    throw new TypeError("Unsupported declarative network action");
  }

  function normalizeCondition(condition) {
    const input = condition || {};
    const result = {};
    const copy = [
      "urlFilter", "regexFilter", "resourceTypes", "excludedRequestDomains",
      "excludedInitiatorDomains", "requestDomains", "initiatorDomains",
      "excludedResponseHeader", "responseHeaders",
    ];
    for (const key of copy) {
      if (input[key] !== undefined) result[key] = input[key];
    }
    if (result.urlFilter !== undefined && result.regexFilter !== undefined) {
      throw new TypeError("Use urlFilter or regexFilter, not both");
    }
    if (result.urlFilter !== undefined) result.urlFilter = String(result.urlFilter).slice(0, 2000);
    if (result.regexFilter !== undefined) result.regexFilter = String(result.regexFilter).slice(0, 2000);
    if (result.resourceTypes) {
      const allowed = new Set([
        "main_frame", "sub_frame", "stylesheet", "script", "image", "font",
        "object", "xmlhttprequest", "ping", "media", "websocket", "other",
      ]);
      result.resourceTypes = [...new Set(result.resourceTypes.map(String).filter((value) => allowed.has(value)))];
      if (!result.resourceTypes.length) throw new TypeError("At least one valid resource type is required");
    }
    return result;
  }

  function normalizeRule(scriptId, input) {
    if (!input || typeof input !== "object") throw new TypeError("Network rule must be an object");
    const id = String(input.id || "").trim();
    if (!/^[A-Za-z0-9._:-]{1,80}$/.test(id)) throw new TypeError("Invalid network rule id");
    return {
      id: hashId(scriptId, id),
      priority: Math.min(Math.max(Number(input.priority) || 1, 1), 100000),
      condition: normalizeCondition(input.condition),
      action: normalizeAction(input.action),
    };
  }

  function normalizeRules(scriptId, rules) {
    const list = Array.isArray(rules) ? rules.slice(0, MAX_RULES_PER_SCRIPT) : [];
    return list.map((rule) => normalizeRule(scriptId, rule));
  }

  globalThis.PASIUserScriptDNR = Object.freeze({
    PREFIX,
    MAX_RULES_PER_SCRIPT,
    hashId,
    normalizeRule,
    normalizeRules,
  });
})();
