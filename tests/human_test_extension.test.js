const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const root = path.resolve(__dirname, "..");
const ext = path.join(root, "extensions", "pasi-human-tester");
const manifest = JSON.parse(fs.readFileSync(path.join(ext, "manifest.json"), "utf8"));
const service = fs.readFileSync(path.join(ext, "src", "service-worker.js"), "utf8");
const runner = fs.readFileSync(path.join(ext, "runner.js"), "utf8");
const protocol = fs.readFileSync(path.join(ext, "protocol.js"), "utf8");
const suite = JSON.parse(fs.readFileSync(path.join(ext, "suites", "p0-smoke-v1.json"), "utf8"));

test("human-test extension is MV3 and defaults to least privilege", () => {
  assert.equal(manifest.manifest_version, 3);
  assert.ok(manifest.permissions.includes("activeTab"));
  assert.ok(manifest.permissions.includes("scripting"));
  assert.equal(manifest.permissions.includes("<all_urls>"), false);
  assert.ok(Array.isArray(manifest.optional_host_permissions));
});

test("test protocol is typed and excludes arbitrary script steps", () => {
  for (const action of [
    "navigate","click","fill","assert_visible","assert_text",
    "wait_for_text","assert_url_contains","wait_ms","reload",
    "screenshot","api_get_json"
  ]) assert.ok(protocol.includes(JSON.stringify(action)) || protocol.includes(action));
  assert.equal(service.includes("executeScript({target"), true);
  assert.equal(service.includes('func:'), false);
  assert.equal(runner.includes("eval("), false);
  assert.equal(runner.includes("new Function"), false);
});

test("human-test evidence is bound to code head and durable backend ingest", () => {
  assert.ok(service.includes("codeHead"));
  assert.ok(service.includes("/v1/human-tests/runs"));
  assert.ok(service.includes("evidence_sha256"));
  assert.ok(service.includes("captureVisibleTab"));
  assert.ok(service.includes("policy_violations"));
});

test("packaged P0 smoke suite requires real runtime health and visible UI", () => {
  assert.equal(suite.schema_version, 1);
  assert.equal(suite.suite_id, "pasi-p0-runtime-smoke");
  assert.ok(suite.steps.some((step) => step.action === "api_get_json"));
  assert.ok(suite.steps.some((step) => step.action === "assert_visible"));
});
