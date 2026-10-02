const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const ROOT = path.resolve(__dirname, "..");
const popupHtml = fs.readFileSync(path.join(ROOT, "popup.html"), "utf8");
const popupCss = fs.readFileSync(path.join(ROOT, "popup.css"), "utf8");
const popupJs = fs.readFileSync(path.join(ROOT, "popup.js"), "utf8");

test("popup has one neutral idle state and no legacy duplicate userscript warning", () => {
  assert.match(
    popupHtml,
    /id="idleState"[^>]*class="idle-state"[^>]*hidden/
  );
  assert.match(
    popupHtml,
    /Extension idle/
  );
  assert.match(
    popupHtml,
    /Navigate to a supported page to activate userscripts\./
  );
  assert.doesNotMatch(popupHtml, /No PASI userscripts match this page\./);
  assert.doesNotMatch(popupHtml, /systemWarning/);
  assert.doesNotMatch(popupJs, /No PASI userscripts match this page\./);
  assert.doesNotMatch(popupJs, /systemWarning/);
});

test("ChatGPT is treated as a supported runner target independently of userscript matches", () => {
  assert.match(popupJs, /function isRunnerTargetUrl\(url\)/);
  assert.match(popupJs, /parsed\.hostname === "chatgpt\.com"/);
  assert.match(popupJs, /parsed\.hostname === "www\.chatgpt\.com"/);
  assert.match(
    popupJs,
    /const runnerSupported = isRunnerTargetUrl\(activeUrl\);/
  );
  assert.match(
    popupJs,
    /renderRunnerDashboard\(runnerState, selectedProfile, runnerSupported\);/
  );
  assert.match(
    popupJs,
    /idle\.hidden = runnerSupported \|\| userscriptsMatched;/
  );
});

test("queue dispatch is represented once because it is not runner-profile specific", () => {
  assert.equal(
    (popupHtml.match(/id="runNextButton"/g) || []).length,
    1
  );
  assert.match(popupJs, /async function runNext\(\)/);
  assert.doesNotMatch(
    popupJs,
    /runNext\(profileId\)/
  );
  assert.match(
    popupJs,
    /setStatus\("Dispatching one queued operation…"\);/
  );
});

test("light theme overrides the complete popup surface palette", () => {
  assert.match(popupCss, /body\.light-theme\s*\{/);
  assert.match(popupCss, /--bg-main:\s*#f4f1ec/);
  assert.match(popupCss, /--bg-card:\s*#ffffff/);
  assert.match(popupCss, /--text-primary:\s*#1c1818/);
  assert.match(popupCss, /--text-secondary:\s*#544b43/);
  assert.match(popupCss, /--text-muted:\s*#756b62/);
  assert.match(popupCss, /color-scheme:\s*light/);
  assert.match(popupCss, /#runnerSelect[\s\S]*background:\s*var\(--bg-card\)/);
  assert.match(popupCss, /#runnerSelect[\s\S]*color:\s*var\(--text-primary\)/);
});

test("popup is compact and does not retain the old 380px layout width", () => {
  assert.match(popupCss, /width:\s*348px/);
  assert.doesNotMatch(popupCss, /width:\s*380px/);
});
