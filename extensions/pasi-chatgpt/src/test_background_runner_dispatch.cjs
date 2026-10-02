const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const ROOT = path.resolve(__dirname, "..");
const background = fs.readFileSync(path.join(ROOT, "src", "background.js"), "utf8");

test("background keeps the dispatcher dormant until a live supervised runner exists", () => {
  assert.match(background, /const supervisedExecutionWaiters = new Map\(\);/);
  assert.match(background, /function runnerStateIsDispatchable\(state\)/);
  assert.match(background, /state\.status !== 'running'/);
  assert.match(background, /state\.process_alive !== true/);
  assert.match(background, /state\.ready !== true/);
  assert.match(background, /execution_mode[^\n]+supervised_/);
  assert.match(background, /if \(!runnerStateIsDispatchable\(runnerState\)\)/);
  assert.match(background, /setTimeout\(resolve, 500\)/);
  assert.match(background, /waitForNextOperationForController\(tabId, waitMs = 3000\)/);
  assert.doesNotMatch(background, /waitForSupervisedRunnerReady\(/);
  assert.match(background, /ensureSupervisedExecutionWaiter\\(tabId\\);/);
});

test("background accepts the long-poll query on the bridge route", () => {
  assert.match(
    background,
    /bridgeJson\(\s*['"]\/next-operation\?controller_id=['"]\s*\+\s*encodeURIComponent\(controllerId\)\s*\+\s*['"]&wait_ms=['"]\s*\+\s*String\(Math\.round\(boundedWaitMs\)\)/
  );
});


test("popup runner start explicitly rearms the ChatGPT dispatcher", () => {
  assert.match(
    background,
    /pasi-control-center-bridge-request/
  );
  assert.match(
    background,
    /method === 'POST' && path === '\/runner\/control'/
  );
  assert.match(
    background,
    /body\?\.accepted === true && body\?\.action === 'start'/
  );
  assert.match(
    background,
    /await attachExistingChatTabs\(\)/
  );
});


test("CDP controller identity is scoped to the supervised runner run", () => {
  assert.match(
    background,
    /function controllerIdForTab\(tabId, runId = ''\)/
  );
  assert.match(
    background,
    /'cdp-tab:' \+ String\(tabId\) \+ ':run:' \+ normalizedRunId/
  );
  assert.match(
    background,
    /const runId = String\(runnerState\.run_id \|\| ''\)\.trim\(\)/
  );
  assert.match(
    background,
    /const controllerId = controllerIdForTab\(tabId, runId\)/
  );
});
