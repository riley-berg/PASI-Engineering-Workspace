const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const root = path.resolve(__dirname, "..");
const extensionRoot = path.join(root, "extensions", "pasi-chatgpt");
const manifest = JSON.parse(fs.readFileSync(path.join(extensionRoot, "manifest.json"), "utf8"));
const background = fs.readFileSync(path.join(extensionRoot, "src", "background.js"), "utf8");
const content = fs.readFileSync(path.join(extensionRoot, "src", "content.js"), "utf8");
const recovery = fs.readFileSync(path.join(extensionRoot, "src", "recovery.js"), "utf8");
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
  assert.match(background, /Authorization/);
  assert.match(background, /Bearer/);
  assert.ok(background.includes("if (response.status === 401)"));
  assert.match(background, /type !== 'pasi-bridge-request'/);
  assert.ok(background.includes("'GET /next-operation'"));
  assert.match(background, /POST \/chat/finished/);
  assert.match(background, /POST \/chat/failed/);
  assert.equal(content.includes("127.0.0.1:8765"), false);
  assert.equal(recovery.includes("127.0.0.1:8765"), false);
});

test("completion handoff is immediate and durable", () => {
  assert.match(content, /let immediateOperationQueued = false/);
  assert.match(content, /function scheduleImmediateOperation(operation)/);
  assert.match(content, /next_operation/);
  assert.match(content, /scheduleImmediateOperation(chainedOperation)/);
  assert.ok(background.includes("BRIDGE_OPERATION_RE") && background.includes("operation_id"));
});

test("connection loss stops generation and preserves recovery state", () => {
  assert.ok(recovery.includes("connectionFailure()"));
  assert.ok(recovery.includes("connection_error"));
  assert.ok(content.includes("/chat/failed"));
  assert.match(content, /recovery_context/);
  assert.match(recovery, /connectionFailure()/);
  assert.match(recovery, /decideRecovery/);
});

test("usage or context exhaustion creates a bounded fresh-chat recovery path", () => {
  assert.match(content, /CHAT_EXHAUSTED:/);
  assert.match(content, /CHAT_USAGE_LIMITED:/);
  assert.ok(content.includes("case 'new_chat'"));
  assert.match(recovery, /replacementReason()/);
  assert.match(recovery, /operation_type: 'new_chat'/);
  assert.match(recovery, /MAX_CONTEXT_RECOVERIES/);
});

test("native controller uses bounded timeouts and progress-based recovery", () => {
  assert.match(timeoutConfig, /heartbeatMs: 15 * 1000/);
  assert.match(timeoutConfig, /staleMs: 45 * 1000/);
  assert.match(recovery, /RECOVERY_HARD_CEILING_MS/);
  assert.match(recovery, /RECOVERY_STALL_MS/);
  assert.match(recovery, /no-progress|no_progress/);
  assert.match(detectors, /usage_limited/);
  assert.match(detectors, /context_exhausted/);
});

test("userscript runtime remains loaded alongside the native bridge plane", () => {
  assert.match(background, /PASIUserScriptManager/);
  assert.match(background, /PASIBackgroundAPI/);
  assert.match(background, /pasi.userscript.rpc/);
  assert.match(background, /pasi.http.stream/);
});
