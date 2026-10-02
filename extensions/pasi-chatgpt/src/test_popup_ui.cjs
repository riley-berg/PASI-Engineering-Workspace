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

test("queue-style manual operation dispatch is removed from the popup", () => {
  assert.doesNotMatch(popupHtml, /runNextButton/);
  assert.doesNotMatch(popupHtml, /Run next queued op/);
  assert.doesNotMatch(popupJs, /pasi\.execution\.run-next/);
  assert.doesNotMatch(popupJs, /runNext\(/);
});

test("light theme overrides the complete popup surface palette", () => {
  assert.match(popupCss, /body\.light-theme\s*\{/);
  assert.match(popupCss, /--bg-main:\s*#FAF8F5/);
  assert.match(popupCss, /--bg-card:\s*#F2EEE8/);
  assert.match(popupCss, /--text-primary:\s*#111215/);
  assert.match(popupCss, /--text-secondary:\s*#423731/);
  assert.match(popupCss, /--text-muted:\s*#665953/);
  assert.match(popupCss, /--btn-primary:\s*#1A1B20/);
  assert.match(popupCss, /--btn-primary-hover:\s*#2C2E35/);
  assert.match(popupCss, /--btn-secondary:\s*#F2EEE8/);
  assert.doesNotMatch(popupCss, /body\.light-theme[\s\S]*--btn-primary:\s*#2F261D/);
  assert.match(popupCss, /body\.light-theme[\s\S]*\.runner-card\.selected/);
  assert.match(popupCss, /color-scheme:\s*light/);
  assert.match(popupCss, /#runnerSelect[\s\S]*background:\s*var\(--bg-card\)/);
  assert.match(popupCss, /#runnerSelect[\s\S]*color:\s*var\(--text-primary\)/);
  assert.match(popupCss, /--status-badge-bg:\s*rgba\(26, 27, 32/);
  assert.match(popupCss, /--status-badge-active-bg:\s*rgba\(26, 27, 32/);
  assert.match(popupCss, /--status-badge-text:\s*#1A1B20/);
  assert.doesNotMatch(popupCss, /#(?:2563eb|3b82f6)/i);
  assert.doesNotMatch(popupCss, /rgba?\(\s*59\s*,\s*130\s*,\s*246\b/i);
});

test("popup islands stay inside the compact popup width", () => {
  assert.match(popupCss, /width:\s*360px/);
  assert.match(popupCss, /\.runner-card,\n\.card[\s\S]*width:\s*100%/);
  assert.match(popupCss, /\.runners-list[\s\S]*width:\s*100%/);
  assert.match(popupCss, /\.card-actions[\s\S]*min-width:\s*0/);
  assert.match(popupCss, /\[hidden\]\s*\{\s*display:\s*none\s*!important;\s*\}/);
});

test("popup uses the requested sepia/obsidian dark palette and no blue primary buttons", () => {
  assert.match(popupCss, /--bg-main:\s*#0D0E11/);
  assert.match(popupCss, /--bg-card:\s*#17191E/);
  assert.match(popupCss, /--btn-primary:\s*#45464A/);
  assert.match(popupCss, /--btn-primary-hover:\s*#5B5C61/);
  assert.match(popupCss, /--badge-m1:\s*#45464A/);
  assert.match(popupCss, /--badge-long:\s*#45464A/);
  assert.match(popupCss, /\.status-dot\.running[\s\S]*background:\s*#45464A/);
  assert.match(popupCss, /\.btn-primary\s*\{[\s\S]*color:\s*#fff/);
  assert.match(popupCss, /--text-primary:\s*#F4F1EC/);
  assert.doesNotMatch(popupCss, /--btn-primary:\s*#(?:2563eb|3b82f6)/i);
  assert.doesNotMatch(popupCss, /--btn-primary:\s*#54433A/);
  assert.doesNotMatch(popupCss, /--btn-primary-hover:\s*#69564B/);
});
