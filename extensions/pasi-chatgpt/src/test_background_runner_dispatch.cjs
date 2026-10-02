const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const ROOT = path.resolve(__dirname, "..");
const background = fs.readFileSync(path.join(ROOT, "src", "background.js"), "utf8");

test("background gates supervised operation dispatch on an active runner", () => {
  assert.match(background, /const supervisedExecutionWaiters = new Map\(\);/);
  assert.match(background, /function runnerStateIsDispatchable\(state\)/);
  assert.match(background, /state\.status !== 'running'/);
  assert.match(background, /state\.process_alive !== true/);
  assert.match(background, /state\.ready !== true/);
  assert.match(background, /execution_mode[^\n]+supervised_/);
  assert.match(background, /async function waitForSupervisedRunnerReady\(timeoutMs = 15000\)/);
  assert.match(background, /waitForSupervisedRunnerReady\(\)/);
  assert.match(background, /ensureSupervisedExecutionWaiter\(tabId, controllerIdForTab\(tabId\)\);/);
});

test("background accepts the long-poll query on the bridge route", () => {
  assert.match(
    background,
    /bridgeJson\(\s*['"]\/next-operation\?controller_id=['"]\s*\+\s*encodeURIComponent\(controllerId\)\s*\+\s*['"]&wait_ms=['"]\s*\+\s*String\(Math\.round\(boundedWaitMs\)\)/
  );
});
