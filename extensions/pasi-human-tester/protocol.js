(() => {
  "use strict";

  const ACTIONS = Object.freeze([
    "navigate",
    "click",
    "fill",
    "assert_visible",
    "assert_text",
    "wait_for_text",
    "assert_url_contains",
    "wait_ms",
    "reload",
    "screenshot",
    "api_get_json"
  ]);

  function originOf(url) {
    const parsed = new URL(String(url));
    return parsed.origin;
  }

  function originAllowed(url, allowedOrigins) {
    const origin = originOf(url);
    return allowedOrigins.some((pattern) => {
      const value = String(pattern);
      if (value.endsWith("*")) return origin.startsWith(value.slice(0, -1));
      return origin === value;
    });
  }

  function validateStep(step) {
    if (!step || typeof step !== "object") throw new TypeError("step must be an object");
    if (!/^[A-Za-z][A-Za-z0-9._-]{0,80}$/.test(String(step.id || ""))) {
      throw new TypeError("invalid step id");
    }
    if (!ACTIONS.includes(step.action)) {
      throw new TypeError("unsupported test action");
    }
    if (step.selector !== undefined && String(step.selector).length > 1000) {
      throw new TypeError("selector exceeds configured bound");
    }
    if (step.timeout_ms !== undefined) {
      const timeout = Number(step.timeout_ms);
      if (!Number.isInteger(timeout) || timeout < 100 || timeout > 120000) {
        throw new TypeError("timeout_ms is outside the configured bound");
      }
    }
    return true;
  }

  function validateSuite(suite) {
    if (!suite || typeof suite !== "object") throw new TypeError("suite must be an object");
    if (suite.schema_version !== 1) throw new TypeError("unsupported suite schema version");
    if (!suite.suite_id || !suite.suite_version) throw new TypeError("suite identity is required");
    if (!Array.isArray(suite.allowed_origins) || suite.allowed_origins.length === 0) {
      throw new TypeError("suite allowed_origins is required");
    }
    if (!Array.isArray(suite.steps) || suite.steps.length === 0 || suite.steps.length > 200) {
      throw new TypeError("suite steps must contain 1..200 steps");
    }
    const ids = new Set();
    for (const step of suite.steps) {
      validateStep(step);
      if (ids.has(step.id)) throw new TypeError("duplicate step id");
      ids.add(step.id);
    }
    return true;
  }

  globalThis.PASIHumanTestProtocol = Object.freeze({
    ACTIONS,
    originOf,
    originAllowed,
    validateStep,
    validateSuite
  });
})();