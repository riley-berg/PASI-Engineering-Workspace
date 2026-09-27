const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..");
const extensionRoot = path.join(root, "extensions", "pasi-chatgpt");
const manifest = JSON.parse(fs.readFileSync(path.join(extensionRoot, "manifest.json"), "utf8"));
const background = fs.readFileSync(path.join(extensionRoot, "src", "background.js"), "utf8");
const content = fs.readFileSync(path.join(extensionRoot, "src", "content.js"), "utf8");
const recovery = fs.readFileSync(path.join(extensionRoot, "src", "recovery.js"), "utf8");
const recoveryProgress = fs.readFileSync(path.join(extensionRoot, "src", "recovery_progress.js"), "utf8");
const timeoutConfig = fs.readFileSync(path.join(extensionRoot, "src", "timeout-config.js"), "utf8");
const detectors = fs.readFileSync(path.join(extensionRoot, "src", "detectors.js"), "utf8");

test("consolidated extension is Manifest V3 and points at the Engineering Workspace worker", () => {
  assert.equal(manifest.manifest_version, 3);
  assert.equal(manifest.background.service_worker, "src/background.js");
  assert.ok(manifest.host_permissions.includes("http://127.0.0.1:8765/*"));
  assert.ok(manifest.host_permissions.includes("https://chatgpt.com/*"));
  assert.ok(manifest.host_permissions.includes("https://www.chatgpt.com/*"));
  const scripts = manifest.content_scripts[0].js;
  for (const required of [
    "src/timeout-config.js",
    "src/detectors.js",
    "src/recovery_progress.js",
    "src/content.js",
    "src/recovery.js",
  ]) {
    assert.ok(scripts.includes(required), required);
  }
});

test("native service worker is the only loopback bridge caller and authenticates with the deployed token", () => {
  assert.ok(background.includes("const BRIDGE = 'http://127.0.0.1:8765';"));
  assert.ok(background.includes("chrome.runtime.getURL('.bridge-token')"));
  assert.ok(background.includes("Authorization"));
  assert.ok(background.includes("Bearer"));
  assert.ok(background.includes("if (response.status === 401)"));
  assert.ok(background.includes("type !== 'pasi-bridge-request'"));
  assert.ok(background.includes("'GET /next-operation'"));
  assert.ok(background.includes("'POST /chat/finished'"));
  assert.ok(background.includes("'POST /chat/failed'"));
  assert.equal(content.includes("127.0.0.1:8765"), false);
  assert.equal(recovery.includes("127.0.0.1:8765"), false);
});

test("completion handoff is immediate and durable", () => {
  assert.ok(content.includes("let immediateOperationQueued = false"));
  assert.ok(content.includes("function scheduleImmediateOperation(operation)"));
  assert.ok(content.includes("next_operation"));
  assert.ok(content.includes("scheduleImmediateOperation(chainedOperation)"));
  assert.ok(background.includes("BRIDGE_OPERATION_RE"));
  assert.ok(background.includes("operation_id="));
});

test("connection loss stops generation and preserves recovery state", () => {
  assert.ok(recovery.includes("connectionFailure()"));
  assert.ok(recovery.includes("decideRecovery"));
  assert.ok(content.includes("/chat/failed"));
  assert.ok(content.includes("recovery_context"));
});

test("usage or context exhaustion creates a bounded fresh-chat recovery path", () => {
  assert.ok(content.includes("CHAT_EXHAUSTED:"));
  assert.ok(content.includes("CHAT_USAGE_LIMITED:"));
  assert.ok(content.includes("case 'new_chat'"));
  assert.ok(recovery.includes("replacementReason()"));
  assert.ok(recovery.includes("operation_type: 'new_chat'"));
  assert.ok(recovery.includes("MAX_CONTEXT_RECOVERIES"));
});

test("live acceptance captures runtime errors including RECOVERY_DEFAULTS", () => {
  assert.ok(content.includes("chatgpt_runtime_error"));
  assert.ok(content.includes("recovery_defaults_match"));
  assert.ok(content.includes("unhandledrejection"));
  assert.ok(content.includes("__PASI_RUNTIME_ERROR_TELEMETRY_INSTALLED__"));
});

test("native controller uses bounded timeouts and progress-based recovery", () => {
  assert.ok(timeoutConfig.includes("heartbeatMs: 15 * 1000"));
  assert.ok(timeoutConfig.includes("staleMs: 45 * 1000"));
  assert.ok(recovery.includes("RECOVERY_HARD_CEILING_MS"));
  assert.ok(recovery.includes("RECOVERY_STALL_MS"));
  assert.ok(recoveryProgress.includes("no_progress"));
  assert.ok(detectors.includes("usage_limited"));
  assert.ok(detectors.includes("context_exhausted"));
});

test("recovery progress can be evaluated repeatedly in one isolated world", () => {
  const context = vm.createContext({ console });
  vm.runInContext(recoveryProgress, context, { filename: "recovery_progress.js" });
  vm.runInContext(recoveryProgress, context, { filename: "recovery_progress.js" });
  assert.equal(typeof context.PASI_RECOVERY_PROGRESS?.decideRecovery, "function");
  assert.equal(typeof context.PASI_RECOVERY_PROGRESS?.ProgressTracker, "function");
});

test("userscript runtime remains loaded alongside the native bridge plane", () => {
  assert.ok(background.includes("PASIUserScriptManager"));
  assert.ok(background.includes("PASIBackgroundAPI"));
  assert.ok(background.includes("pasi.userscript.rpc"));
  assert.ok(background.includes("pasi.http.stream"));
});
