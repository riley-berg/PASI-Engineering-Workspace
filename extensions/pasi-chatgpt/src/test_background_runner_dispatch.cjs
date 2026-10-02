const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const ROOT = path.resolve(__dirname, "..");
const background = fs.readFileSync(path.join(ROOT, "src", "background.js"), "utf8");

test("background has a live operation waiter for the ChatGPT tab", () => {
  assert.match(background, /const supervisedExecutionWaiters = new Map\(\);/);
  assert.match(background, /function ensureSupervisedExecutionWaiter\(tabId, controllerId\)/);
  assert.match(background, /waitForNextOperationForController\(tabId, controllerId\)/);
  assert.match(background, /ensureSupervisedExecutionWaiter\(tabId, controllerIdForTab\(tabId\)\);/);
  assert.doesNotMatch(background, /supervisedExecutionAuthorized\(\)/);
});

test("background accepts the long-poll query on the bridge route", () => {
  assert.match(
    background,
    /bridgeJson\(\s*['"]\/next-operation\?controller_id=['"]\s*\+\s*encodeURIComponent\(controllerId\)\s*\+\s*['"]&wait_ms=['"]\s*\+\s*String\(Math\.round\(boundedWaitMs\)\)/
  );
});
