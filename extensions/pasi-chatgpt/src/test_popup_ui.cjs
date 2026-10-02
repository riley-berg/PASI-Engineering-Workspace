const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const ROOT = path.resolve(__dirname, "..");
const popupHtml = fs.readFileSync(path.join(ROOT, "popup.html"), "utf8");
const popupCss = fs.readFileSync(path.join(ROOT, "popup.css"), "utf8");
const popupJs = fs.readFileSync(path.join(ROOT, "popup.js"), "utf8");
const themeInit = fs.readFileSync(path.join(ROOT, "theme-init.js"), "utf8");

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
  assert.match(popupCss, /html\.light-theme body\s*\{/);
  assert.match(popupCss, /--bg-main:\s*#FAF8F5/);
  assert.match(popupCss, /--bg-card:\s*#FFFFFF/);
  assert.match(popupCss, /--text-primary:\s*#1A1B20/);
  assert.match(popupCss, /--text-secondary:\s*#55565A/);
  assert.match(popupCss, /--text-muted:\s*#707174/);
  assert.match(popupCss, /--btn-primary:\s*#45464A/);
  assert.match(popupCss, /--btn-primary-hover:\s*#333438/);
  assert.match(popupCss, /--btn-secondary:\s*#FFFFFF/);
  assert.doesNotMatch(popupCss, /html\.light-theme body[\s\S]*--btn-primary:\s*#2F261D/);
  assert.match(popupCss, /html\.light-theme body[\s\S]*\.runner-card\.selected/);
  assert.match(popupCss, /color-scheme:\s*light/);
  assert.match(popupCss, /html\s*\{[\s\S]*background:\s*#0D0E11/);
  assert.match(popupCss, /html\.light-theme\s*\{[\s\S]*background:\s*#FAF8F5/);
  assert.match(popupCss, /transition:\s*none/);
  assert.match(popupCss, /\.runner-select[\s\S]*background:\s*var\(--bg-card\)/);
  assert.match(popupCss, /\.runner-select[\s\S]*color:\s*var\(--text-primary\)/);





  assert.match(popupCss, /--status-badge-bg:\s*rgba\(112, 113, 116, .12\)/);
  assert.match(popupCss, /--status-badge-active-bg:\s*rgba\(112, 113, 116, .18\)/);
  assert.match(popupCss, /--status-badge-text:\s*#45464A/);
  assert.match(popupCss, /--border-color:\s*#DCD4C7/);
  assert.match(popupCss, /--idle-border:\s*#DCD4C7/);
  assert.match(popupCss, /--idle-bg:\s*rgba\(255, 255, 255/);
  assert.doesNotMatch(popupCss, /#(?:2563eb|3b82f6)/i);
  assert.doesNotMatch(popupCss, /rgba?\(\s*59\s*,\s*130\s*,\s*246\b/i);
  assert.doesNotMatch(popupCss, /#(?:6F766F|6E756F|8B7B70|8D7768|8A6258|7C6B5F)/i);
  assert.match(popupCss, /html\.light-theme body[\s\S]*--status-completed:\s*#707174/);
  assert.match(popupCss, /html\.light-theme body[\s\S]*--status-paused:\s*#707174/);
  assert.match(popupCss, /\.status-dot\.running[\s\S]*background:\s*var\(--status-running, #45464A\)/);
  assert.match(popupJs, /status-desc" \+ \(stateLabelText === "Idle" \? " idle-summary" : ""\)/);
  assert.match(popupCss, /\.status-desc\.idle-summary[\s\S]*color:\s*var\(--status-idle-summary\)/);
  assert.match(popupCss, /--status-idle:\s*#1A1B20/);
  assert.match(popupCss, /html\.light-theme body[\s\S]*--status-idle:\s*#1A1B20/);
  assert.match(popupCss, /html\.light-theme body[\s\S]*--status-badge-bg:\s*rgba\(112, 113, 116, .12\)/);
  assert.match(popupCss, /html\.light-theme body[\s\S]*--status-badge-active-bg:\s*rgba\(112, 113, 116, .18\)/);
  assert.match(popupCss, /--runner-option-hover:\s*#F0ECE6/);
  assert.match(popupCss, /\.runner-select-option\s*\{[\s\S]*font-weight:\s*600/);
  const pickerHoverRule = popupCss.match(/\.runner-select-option:hover,\s*\.runner-select-option:focus-visible\s*\{[^}]*\}/)?.[0];
  assert.ok(pickerHoverRule);
  assert.doesNotMatch(pickerHoverRule, /font-weight:\s*700/);
  assert.match(popupCss, /box-shadow:\s*inset 0 0 0 1px var\(--border-color\)/);
  assert.match(popupCss, /--status-idle-summary:\s*#707174/);
  assert.match(popupCss, /\.status-dot\.idle\s*\{\s*display:\s*none/);
});

test("popup bootstraps the saved theme before first paint", () => {
  const themeScriptIndex = popupHtml.indexOf('<script src="theme-init.js"></script>');
  const stylesheetIndex = popupHtml.indexOf('<link rel="stylesheet" href="popup.css">');

  assert.ok(themeScriptIndex >= 0);
  assert.ok(stylesheetIndex >= 0);
  assert.ok(themeScriptIndex < stylesheetIndex);
  assert.match(themeInit, /localStorage\.getItem\("pasi\.popup\.theme"\)/);
  assert.match(themeInit, /document\.documentElement\.classList\.add\("light-theme"\)/);
  assert.match(themeInit, /data-theme-pending/);
  assert.match(popupCss, /html\[data-theme-pending\] body\s*\{\s*visibility:\s*hidden/);
  assert.match(popupJs, /localStorage\.getItem\("pasi\.popup\.theme"\)/);
  assert.match(popupJs, /document\.documentElement\.classList\.toggle\("light-theme"/);
  assert.match(popupJs, /document\.documentElement\.removeAttribute\("data-theme-pending"\)/);
});

test("popup uses Connected plus Idle runner semantics without duplicate Ready labels", () => {
  assert.match(popupHtml, /id="connectionBadge" class="status-badge">Connected<\/span>/);
  assert.match(popupJs, /return "Idle";/);
  assert.match(popupJs, /return "Awaiting start\.";/);
  assert.match(popupJs, /textContent = state\?\.available \? "Connected" : "Disconnected";/);
  assert.match(popupJs, /stateLabelText === "Idle"/);
  assert.doesNotMatch(popupJs, /"Ready"/);
  assert.doesNotMatch(popupHtml, /class="status-badge">Ready<\/span>/);
});

test("popup uses a custom themed runner picker with no native select styling", () => {
  assert.match(popupHtml, /id="runnerSelect"[^>]*role="combobox"/);
  assert.match(popupHtml, /id="runnerSelectMenu"[^>]*role="listbox"[^>]*hidden/);
  assert.match(popupHtml, /id="runnerOption-m1"[^>]*role="option"/);
  assert.match(popupHtml, /id="runnerOption-168h"[^>]*role="option"/);
  assert.match(popupJs, /function initializeRunnerPicker\(\)/);
  assert.match(popupJs, /function selectRunnerProfile\(profile\)/);
  assert.match(popupJs, /ArrowDown/);
  assert.match(popupJs, /Escape/);
  assert.match(popupCss, /\.runner-select-menu[\s\S]*background:\s*var\(--bg-card\)/);
  assert.match(popupCss, /\.runner-select-option\[aria-selected="true"\][\s\S]*background:\s*var\(--btn-primary\)/);
  assert.match(popupCss, /content:\s*"✓"/);
  assert.doesNotMatch(popupHtml, /<select[^>]*id="runnerSelect"/);
  assert.doesNotMatch(popupCss, /#runnerSelect option/);
});

test("popup islands stay inside the compact popup width", () => {
  assert.match(popupCss, /width:\s*360px/);
  assert.match(popupCss, /\.runner-card,\s*\.card[\s\S]*width:\s*100%/);
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
  assert.match(popupCss, /\.status-dot\.running[\s\S]*background:\s*var\(--status-running, #45464A\)/);
  assert.match(popupCss, /\.btn-primary\s*\{[\s\S]*color:\s*#FFFFFF/);
  assert.match(popupCss, /--text-primary:\s*#F4F1EC/);
  assert.match(popupCss, /\.btn-primary[\s\S]*color:\s*#FFFFFF/);
  assert.match(popupCss, /\.runner-select:focus-visible\s*\{[^}]*box-shadow:\s*0 0 0 2px rgba\(244, 241, 236/);
  assert.match(popupCss, /\.btn:focus-visible/);
  assert.doesNotMatch(popupCss, /--btn-primary:\s*#(?:2563eb|3b82f6)/i);
  assert.doesNotMatch(popupCss, /--btn-primary:\s*#54433A/);
  assert.doesNotMatch(popupCss, /--btn-primary-hover:\s*#69564B/);
});

test("popup critical text and control colors meet WCAG AA contrast targets", () => {
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
  assert.ok(contrastRatio("#FFFFFF", "#45464A") >= 4.5);
});
