const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const ROOT = path.resolve(__dirname, "..");
const popupHtml = fs.readFileSync(path.join(ROOT, "popup.html"), "utf8");
const popupCss = fs.readFileSync(path.join(ROOT, "popup.css"), "utf8");
const popupJs = fs.readFileSync(path.join(ROOT, "popup.js"), "utf8");
const themeInit = fs.readFileSync(path.join(ROOT, "theme-init.js"), "utf8");

test("popup has one neutral idle state and no legacy duplicate warning", () => {
  assert.match(popupHtml, /id="idleState"[^>]*class="idle-state"[^>]*hidden/);
  assert.match(popupHtml, /Extension idle/);
  assert.match(popupHtml, /Navigate to a supported page to activate userscripts\./);
  assert.doesNotMatch(popupHtml, /No PASI userscripts match this page\./);
  assert.doesNotMatch(popupHtml, /systemWarning/);
  assert.doesNotMatch(popupJs, /No PASI userscripts match this page\./);
  assert.doesNotMatch(popupJs, /systemWarning/);
});

test("ChatGPT is a supported runner target independently of userscript matches", () => {
  assert.match(popupJs, /function isRunnerTargetUrl\(url\)/);
  assert.match(popupJs, /parsed\.hostname === "chatgpt\.com"/);
  assert.match(popupJs, /parsed\.hostname === "www\.chatgpt\.com"/);
  assert.match(popupJs, /const runnerSupported = isRunnerTargetUrl\(activeUrl\);/);
  assert.match(popupJs, /renderRunnerDashboard\(runnerState, selectedProfile, runnerSupported\);/);
  assert.match(popupJs, /idle\.hidden = runnerSupported \|\| userscriptsMatched;/);
});

test("queue-style manual operation dispatch is removed from the popup", () => {
  assert.doesNotMatch(popupHtml, /runNextButton/);
  assert.doesNotMatch(popupHtml, /Run next queued op/);
  assert.doesNotMatch(popupJs, /pasi\.execution\.run-next/);
  assert.doesNotMatch(popupJs, /runNext\(/);
});

test("popup theme architecture has one synchronous initialization path", () => {
  const themeScriptIndex = popupHtml.indexOf('<script src="theme-init.js"></script>');
  const stylesheetIndex = popupHtml.indexOf('<link rel="stylesheet" href="popup.css">');

  assert.ok(themeScriptIndex >= 0);
  assert.ok(stylesheetIndex >= 0);
  assert.ok(themeScriptIndex < stylesheetIndex);
  assert.ok(
    popupHtml.includes('<html lang="en" style="background:#0D0E11;color-scheme:dark">')
  );
  assert.ok(popupHtml.includes('<meta name="color-scheme" content="dark light">'));
  assert.match(themeInit, /localStorage\.getItem\("pasi\.popup\.theme"\)/);
  assert.match(themeInit, /root\.classList\.toggle\("light-theme"/);
  assert.match(themeInit, /root\.style\.colorScheme/);
  assert.match(themeInit, /root\.style\.backgroundColor/);
  assert.doesNotMatch(themeInit, /data-theme-pending|data-popup-paint-pending/);
  assert.doesNotMatch(popupJs, /data-theme-pending|data-popup-paint-pending|startViewTransition/);
  assert.doesNotMatch(popupCss, /data-theme-pending|data-popup-paint-pending|::view-transition/);
  assert.match(popupJs, /function updateThemeToggleButton\(light\)/);
  assert.match(popupJs, /function applyThemeDom\(light\)/);
  assert.match(popupJs, /updateThemeToggleButton\(\s*document\.documentElement\.classList\.contains\("light-theme"\)/);
  assert.doesNotMatch(popupJs, /applyThemeDom\(theme === "light"\)/);
  assert.match(popupJs, /setLocalTheme\(theme\);\s*applyThemeDom\(light\);/);
});

test("popup critical surfaces are painted before external CSS", () => {
  const criticalStyleIndex = popupHtml.indexOf('<style id="pasi-theme-critical">');
  const stylesheetIndex = popupHtml.indexOf('<link rel="stylesheet" href="popup.css">');

  assert.ok(criticalStyleIndex >= 0);
  assert.ok(stylesheetIndex >= 0);
  assert.ok(criticalStyleIndex < stylesheetIndex);
  assert.ok(
    popupHtml.includes(
      "html {\n      background: #0D0E11;\n      color-scheme: dark;"
    )
  );
  assert.ok(
    popupHtml.includes(
      "html.light-theme {\n      background: #FAF8F5;\n      color-scheme: light;"
    )
  );
  assert.ok(
    popupHtml.includes(
      "body {\n      background: #0D0E11;\n      color: #F4F1EC;"
    )
  );
  assert.ok(
    popupHtml.includes(
      "html.light-theme body {\n      background: #FAF8F5;\n      color: #1A1B20;"
    )
  );
  assert.doesNotMatch(popupHtml, /visibility:\s*hidden/);
});

test("light and dark surface palettes are explicit and stable", () => {
  assert.match(popupCss, /--bg-main:\s*#0D0E11/);
  assert.match(popupCss, /--bg-card:\s*#17191E/);
  assert.match(popupCss, /--text-primary:\s*#F4F1EC/);
  assert.match(popupCss, /html\.light-theme body\s*\{[\s\S]*--bg-main:\s*#FAF8F5/);
  assert.match(popupCss, /html\.light-theme body\s*\{[\s\S]*--bg-card:\s*#FFFFFF/);
  assert.match(popupCss, /html\.light-theme body\s*\{[\s\S]*--text-primary:\s*#1A1B20/);
  assert.match(popupCss, /html\.light-theme body\s*\{[\s\S]*--text-secondary:\s*#55565A/);
  assert.match(popupCss, /html\.light-theme body\s*\{[\s\S]*--text-muted:\s*#707174/);
  assert.match(popupCss, /--btn-primary:\s*#45464A/);
  assert.match(popupCss, /--btn-primary-hover:\s*#5B5C61/);
  assert.match(popupCss, /html\.light-theme body\s*\{[\s\S]*--btn-primary-hover:\s*#333438/);
  assert.match(popupCss, /--status-badge-text:\s*#45464A/);
  assert.match(popupCss, /--idle-border:\s*#DCD4C7/);
  assert.doesNotMatch(popupCss, /#(?:2563eb|3b82f6|6F766F|6E756F|8B7B70|8D7768|8A6258|7C6B5F)/i);
});

test("runner dropdown control gets a stable hover outline without touching options", () => {
  assert.match(
    popupCss,
    /\.runner-select:hover,\s*\.runner-select\.open\s*\{[\s\S]*outline:\s*1px solid var\(--runner-option-outline\);[\s\S]*outline-offset:\s*0;/
  );
  assert.doesNotMatch(
    popupCss,
    /\.runner-select:hover,\s*\.runner-select\.open\s*\{[\s\S]*border-color:\s*var\(--btn-primary\)/
  );
});

test("custom runner picker has a single, stable hover/focus rule", () => {
  assert.match(popupHtml, /id="runnerSelect"[^>]*role="combobox"/);
  assert.match(popupHtml, /id="runnerSelectMenu"[^>]*role="listbox"[^>]*hidden/);
  assert.match(popupHtml, /id="runnerOption-m1"[^>]*role="option"/);
  assert.match(popupHtml, /id="runnerOption-168h"[^>]*role="option"/);
  assert.match(popupJs, /function initializeRunnerPicker\(\)/);
  assert.match(popupJs, /function selectRunnerProfile\(profile\)/);
  assert.match(popupJs, /ArrowDown/);
  assert.match(popupJs, /Escape/);
  assert.doesNotMatch(popupHtml, /<select[^>]*id="runnerSelect"/);
  assert.doesNotMatch(popupCss, /#runnerSelect option/);

  const hoverRuleMatches = popupCss.match(/\.runner-select-option:hover,\s*\.runner-select-option:focus-visible\s*\{/g) || [];
  assert.equal(hoverRuleMatches.length, 1);

  assert.match(
    popupCss,
    /\.runner-select-option:hover,\s*\.runner-select-option:focus-visible\s*\{[\s\S]*background:\s*var\(--runner-option-hover\);[\s\S]*outline:\s*1px solid var\(--runner-option-outline\);/
  );
  assert.match(popupCss, /--runner-option-hover:\s*#24262C/);
  assert.match(popupCss, /--runner-option-outline:\s*#45464A/);
  assert.match(popupCss, /html\.light-theme body\s*\{[\s\S]*--runner-option-hover:\s*#F2F2F2/);
  assert.match(popupCss, /html\.light-theme body\s*\{[\s\S]*--runner-option-outline:\s*#45464A/);
  assert.match(
    popupCss,
    /\.runner-select-option\[aria-selected="true"\]:hover,\s*\.runner-select-option\[aria-selected="true"\]:focus-visible\s*\{[\s\S]*background:\s*var\(--runner-option-selected\);[\s\S]*outline:\s*1px solid var\(--runner-option-outline\);/
  );

  assert.doesNotMatch(popupCss, /html\.light-theme body \.runner-select-option/);
  assert.doesNotMatch(popupCss, /--runner-option-selected-hover:/);
  assert.doesNotMatch(popupCss, /html\.light-theme body \.status-dot\.(running|paused|failed|completed)/);
  assert.doesNotMatch(popupCss, /html\.light-theme body \.btn-primary/);
  assert.doesNotMatch(popupCss, /html\.light-theme body \.warn/);
  assert.doesNotMatch(popupCss, /box-shadow:\s*inset 0 0 0 1px var\(--runner-option-outline\)/);
  assert.match(popupCss, /\.runner-select-option\s*\{[\s\S]*outline-offset:\s*0/);
  assert.match(popupCss, /\.runner-select-option\s*\{[\s\S]*font-weight:\s*600/);
  assert.match(popupCss, /\.runner-select-option\s*\{[\s\S]*transition:\s*none/);
});

test("popup uses connected/idle semantics without duplicate Ready labels", () => {
  assert.match(popupHtml, /id="connectionBadge" class="status-badge">Connected<\/span>/);
  assert.match(popupJs, /return "Idle";/);
  assert.match(popupJs, /return "Awaiting start\.";/);
  assert.match(popupJs, /textContent = state\?\.available \? "Connected" : "Disconnected";/);
  assert.doesNotMatch(popupJs, /"Ready"/);
  assert.doesNotMatch(popupHtml, /class="status-badge">Ready<\/span>/);
});

test("popup remains inside the compact width and hides only intentional UI regions", () => {
  assert.match(popupCss, /width:\s*360px/);
  assert.match(popupCss, /\.runner-card,\s*\.card[\s\S]*width:\s*100%/);
  assert.match(popupCss, /\.runners-list[\s\S]*width:\s*100%/);
  assert.match(popupCss, /\.card-actions[\s\S]*min-width:\s*0/);
  assert.match(popupCss, /^\[hidden\]\s*\{\s*display:\s*none\s*!important;\s*\}/m);
});

test("popup control and text colors meet WCAG AA targets", () => {
  const relativeLuminance = (hex) => {
    const value = hex.replace("#", "");
    const channels = [0, 2, 4].map((offset) => parseInt(value.slice(offset, offset + 2), 16) / 255);
    const linear = channels.map((channel) =>
      channel <= 0.04045
        ? channel / 12.92
        : ((channel + 0.055) / 1.055) ** 2.4
    );
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
  };

  const contrastRatio = (foreground, background) => {
    const a = relativeLuminance(foreground);
    const b = relativeLuminance(background);
    return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
  };

  assert.ok(contrastRatio("#1A1B20", "#FAF8F5") >= 4.5);
  assert.ok(contrastRatio("#707174", "#FAF8F5") >= 4.5);
  assert.ok(contrastRatio("#55565A", "#FFFFFF") >= 4.5);
  assert.ok(contrastRatio("#FFFFFF", "#45464A") >= 4.5);
  assert.ok(contrastRatio("#F4F1EC", "#17191E") >= 4.5);
  assert.ok(contrastRatio("#707174", "#FFFFFF") >= 4.5);
});