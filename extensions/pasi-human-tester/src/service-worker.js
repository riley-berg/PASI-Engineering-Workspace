importScripts("protocol.js");

const BACKEND_DEFAULT = "http://127.0.0.1:8790";
let running = false;

function now() { return new Date().toISOString(); }

function backendUrl() {
  return globalThis.__PASI_HUMAN_TEST_BACKEND__ || BACKEND_DEFAULT;
}

async function backendRequest(path, method = "GET", body = null, token = "") {
  const response = await fetch(backendUrl() + path, {
    method,
    headers: {
      ...(body ? {"Content-Type": "application/json"} : {}),
      ...(token ? {"Authorization": "Bearer " + token} : {})
    },
    body: body ? JSON.stringify(body) : undefined,
    credentials: "omit",
    cache: "no-store"
  });
  const text = await response.text();
  let payload = null;
  try { payload = JSON.parse(text); } catch (_) {}
  if (!response.ok) throw new Error("backend HTTP " + response.status);
  return payload;
}

async function sha256Text(value) {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest)).map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function captureEvidenceScreenshot(tabId, failureOnly = false) {
  try {
    const dataUrl = await chrome.tabs.captureVisibleTab(undefined, {format: "png"});
    const digest = await sha256Text(dataUrl);
    const key = "pasi-human-test:screenshot:" + digest;
    await chrome.storage.local.set({[key]: {
      captured_at: now(),
      tab_id: tabId,
      failure_only: failureOnly,
      sha256: digest
    }});
    return {sha256: digest, stored_locally: true};
  } catch (error) {
    return {sha256: "", stored_locally: false, error: String(error?.message || error)};
  }
}

async function ensurePermission(origin) {
  const permission = origin.endsWith("/") ? origin + "*" : origin + "/*";
  const present = await chrome.permissions.contains({origins: [permission]});
  if (present) return true;
  throw new Error("origin permission is not granted: " + origin);
}

async function tabComplete(tabId) {
  const tab = await chrome.tabs.get(tabId);
  return tab.status === "complete";
}

async function navigate(tabId, url, allowedOrigins) {
  if (!PASIHumanTestProtocol.originAllowed(url, allowedOrigins)) {
    throw new Error("navigation target is outside suite allowlist");
  }
  await chrome.tabs.update(tabId, {url});
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    if (await tabComplete(tabId)) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error("navigation timed out");
}

async function injectRunner(tabId) {
  await chrome.scripting.executeScript({
    target: {tabId},
    files: ["runner.js"]
  });
}

async function runContentStep(tabId, step) {
  await injectRunner(tabId);
  return await chrome.tabs.sendMessage(tabId, {
    type: "pasi-human-test-step",
    step
  });
}

function canonicalize(value) {
  if (Array.isArray(value)) return "[" + value.map(canonicalize).join(",") + "]";
  if (value && typeof value === "object") {
    return "{" + Object.keys(value).sort().map((key) => JSON.stringify(key) + ":" + canonicalize(value[key])).join(",") + "}";
  }
  return JSON.stringify(value);
}

function resolveTarget(rawUrl, baseOrigin) {
  const value = String(rawUrl || "");
  return new URL(value, baseOrigin + "/").toString();
}

async function runStep(tabId, step, allowedOrigins, targetOrigin, runtimeControlToken) {
  const startedAt = now();

  try {
    let observed = {};
    if (step.action === "navigate") {
      const tab = await chrome.tabs.get(tabId);
      const url = new URL(String(step.url || tab.url || targetOrigin + "/"), targetOrigin + "/");
      await navigate(tabId, url.toString(), allowedOrigins);
      await injectRunner(tabId);
      observed = {url: url.toString()};
    } else if (step.action === "reload") {
      await chrome.tabs.reload(tabId);
      const deadline = Date.now() + 30_000;
      while (Date.now() < deadline && !(await tabComplete(tabId))) {
        await new Promise((resolve) => setTimeout(resolve, 100));
      }
      await injectRunner(tabId);
      observed = {url: (await chrome.tabs.get(tabId)).url || ""};
    } else if (step.action === "screenshot") {
      observed = await captureEvidenceScreenshot(tabId, false);
    } else if (step.action === "api_get_json") {
      const target = resolveTarget(step.url, targetOrigin);
      if (!PASIHumanTestProtocol.originAllowed(target, allowedOrigins)) {
        throw new Error("API target is outside suite allowlist");
      }
      const response = await fetch(target, {credentials: "omit", cache: "no-store"});
      if (step.expected_status && response.status !== Number(step.expected_status)) {
        throw new Error("expected HTTP " + step.expected_status + ", got " + response.status);
      }
      const payload = await response.json();
      observed = {status: response.status, json: payload};
      if (step.json_path) {
        const value = String(step.json_path).split(".").reduce(
          (current, key) => current == null ? undefined : current[key],
          payload
        );
        if (value === undefined) throw new Error("json_path was not found");
        observed.json_path_value = value;
        if (step.value !== undefined && String(value) !== String(step.value)) {
          throw new Error("json_path value did not match expected value");
        }
      }
    } else if (step.action === "runtime_control") {
      if (!runtimeControlToken) throw new Error("runtime control token is required");
      const operationId = String(step.operation_id || "");
      if (!operationId) throw new Error("runtime_control requires operation_id");
      const operation = await backendRequest(
        "/v1/runtime/operations/" + encodeURIComponent(operationId),
        "GET"
      );
      const expectedRevision = Number(operation?.operation?.state_revision);
      if (!Number.isInteger(expectedRevision) || expectedRevision < 0) {
        throw new Error("runtime operation revision is unavailable");
      }
      const response = await fetch(resolveTarget("/v1/runtime/controls", targetOrigin), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": "Bearer " + runtimeControlToken,
          "Idempotency-Key": "htr-" + crypto.randomUUID()
        },
        body: JSON.stringify({
          operation_id: operationId,
          action: step.control_action,
          expected_revision: expectedRevision,
          reason: "human-test-extension",
        }),
        credentials: "omit",
        cache: "no-store",
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error("runtime control HTTP " + response.status);
      }
      observed = payload;
    } else {
      const response = await runContentStep(tabId, step);
      if (!response?.ok) throw new Error(response?.error || "content step failed");
      observed = response.observed || {};
    }
    return {
      step_id: step.id,
      action: step.action,
      status: "PASS",
      started_at: startedAt,
      ended_at: now(),
      observed,
      error: ""
    };
  } catch (error) {
    const screenshot = await captureEvidenceScreenshot(tabId, true);
    return {
      step_id: step.id,
      action: step.action,
      status: "FAIL",
      started_at: startedAt,
      ended_at: now(),
      observed: {failure_screenshot: screenshot},
      error: String(error?.message || error)
    };
  }
}

async function runSuite({suite, targetOrigin, codeHead, backendToken, runtimeControlToken}) {
  if (running) throw new Error("human-test harness is already running");
  PASIHumanTestProtocol.validateSuite(suite);
  if (!PASIHumanTestProtocol.originAllowed(targetOrigin + "/", suite.allowed_origins)) {
    throw new Error("target origin is outside suite allowlist");
  }
  const tabs = await chrome.tabs.query({active: true, currentWindow: true});
  if (!tabs[0]?.id) throw new Error("an active browser tab is required");
  await ensurePermission(targetOrigin);

  running = true;
  const runId = "htr-" + crypto.randomUUID();
  const startedAt = now();
  const steps = [];
  try {
    let tabId = tabs[0].id;
    for (const step of suite.steps) {
      const result = await runStep(tabId, step, suite.allowed_origins, targetOrigin, runtimeControlToken);
      steps.push(result);
      if (result.status === "FAIL") break;
      const current = await chrome.tabs.get(tabId);
      if (typeof current.id !== "number") throw new Error("test tab disappeared");
      tabId = current.id;
    }
    let status = steps.every((step) => step.status === "PASS") ? "PASS" : "FAIL";
    if (suite.negative_control === true) {
      const expectedFailure = steps.find(
        (step) => step.step_id === suite.expected_failure_step_id && step.status === "FAIL"
      );
      status = expectedFailure ? "PASS" : "FAIL";
      if (expectedFailure) {
        expectedFailure.observed = {
          ...(expectedFailure.observed || {}),
          expected_failure_detected: true
        };
      }
    }
    const finishedAt = now();
    const evidence = {
      schema_version: 1,
      run_id: runId,
      suite_id: suite.suite_id,
      suite_version: suite.suite_version,
      code_head: codeHead,
      browser_name: "Chromium",
      browser_version: navigator.userAgent,
      target_origin: targetOrigin,
      extension_version: chrome.runtime.getManifest().version,
      execution_source: "mv3-human-test-extension",
      started_at: startedAt,
      ended_at: finishedAt,
      status,
      steps,
      negative_control: suite.negative_control === true,
      policy_violations: [],
      evidence_sha256: ""
    };
    const canonical = {...evidence, evidence_sha256: ""};
    evidence.evidence_sha256 = await sha256Text(canonicalize(canonical));
    try {
      await backendRequest("/v1/human-tests/runs", "POST", evidence, backendToken);
    } catch (error) {
      evidence.policy_violations.push("evidence_ingest_failed:" + String(error?.message || error));
      const postIngestCanonical = {...evidence, evidence_sha256: ""};
      evidence.evidence_sha256 = await sha256Text(canonicalize(postIngestCanonical));
    }
    await chrome.storage.local.set({"pasi-human-test:last-run": evidence});
    return evidence;
  } finally {
    running = false;
  }
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type !== "pasi-human-test-run-suite") return undefined;
  runSuite(message)
    .then((result) => sendResponse({ok: true, result}))
    .catch((error) => sendResponse({ok: false, error: String(error?.message || error)}));
  return true;
});