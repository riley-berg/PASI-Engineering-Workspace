const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const ROOT = path.resolve(__dirname, "..");
const popupHtml = fs.readFileSync(path.join(ROOT, "popup.html"), "utf8");
const popupCss = fs.readFileSync(path.join(ROOT, "popup.css"), "utf8");
const popupJs = fs.readFileSync(path.join(ROOT, "popup.js"), "utf8");
const themeInit = fs.readFileSync(path.join(ROOT, "theme-init.js"), "utf8");
const backgroundJs = fs.readFileSync(path.join(ROOT, "src", "background.js"), "utf8");
const manifest = JSON.parse(fs.readFileSync(path.join(ROOT, "manifest.json"), "utf8"));

test("popup keeps the page-not-authorized warning separate from runner ready state", () => {
  assert.match(popupHtml, /id="idleState"[^>]*class="idle-state"[^>]*hidden/);
  assert.match(popupHtml, /Page not authorized/);
  assert.match(popupHtml, /This page is not authorized for the PASI extension\./);
  assert.doesNotMatch(popupHtml, /No PASI userscripts match this page\./);
  assert.doesNotMatch(popupHtml, /systemWarning/);
  assert.doesNotMatch(popupJs, /No PASI userscripts match this page\./);
  assert.doesNotMatch(popupJs, /systemWarning/);
  assert.doesNotMatch(popupHtml, /<div class="idle-title">Ready<\/div>/);
  assert.doesNotMatch(popupHtml, /Ready to execute on this ChatGPT page\./);
});

test("ChatGPT is a supported runner target independently of userscript matches", () => {
  assert.match(popupJs, /function isRunnerTargetUrl\(url\)/);
  assert.match(popupJs, /parsed\.hostname === "chatgpt\.com"/);
  assert.match(popupJs, /parsed\.hostname === "www\.chatgpt\.com"/);
  assert.match(popupJs, /const runnerSupported = isRunnerTargetUrl\(activeUrl\);/);
  assert.match(popupJs, /renderRunnerDashboard\(runnerState, runnerSupported\);/);
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
    popupHtml.includes('<html lang="en" style="background:#0B0D10;color-scheme:dark">')
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
      "html {\n      background: #0B0D10;\n      color-scheme: dark;"
    )
  );
  assert.ok(
    popupHtml.includes(
      "html.light-theme {\n      background: #F5F7FA;\n      color-scheme: light;"
    )
  );
  assert.ok(
    popupHtml.includes(
      "body {\n      background: #0B0D10;\n      color: #F5F7FA;"
    )
  );
  assert.ok(
    popupHtml.includes(
      "html.light-theme body {\n      background: #F5F7FA;\n      color: #171A20;"
    )
  );
  assert.doesNotMatch(popupHtml, /visibility:\s*hidden/);
});

test("light and dark palettes are unmistakably distinct", () => {
  assert.match(popupCss, /--bg-main:\s*#0B0D10/);
  assert.match(popupCss, /--bg-card:\s*#15181D/);
  assert.match(popupCss, /--text-primary:\s*#F5F7FA/);
  assert.match(popupCss, /html\.light-theme body\s*\{[\s\S]*--bg-main:\s*#F5F7FA/);
  assert.match(popupCss, /html\.light-theme body\s*\{[\s\S]*--bg-card:\s*#FFFFFF/);
  assert.match(popupCss, /html\.light-theme body\s*\{[\s\S]*--text-primary:\s*#171A20/);
  assert.match(popupCss, /html\.light-theme body\s*\{[\s\S]*--text-secondary:\s*#4F5661/);
  assert.match(popupCss, /html\.light-theme body\s*\{[\s\S]*--text-muted:\s*#666E78/);
  assert.match(popupCss, /--btn-primary:\s*#2B3138/);
  assert.match(popupCss, /--btn-primary-hover:\s*#37404A/);
  assert.match(popupCss, /html\.light-theme body\s*\{[\s\S]*--btn-primary:\s*#4A5563/);
  assert.match(popupCss, /html\.light-theme body\s*\{[\s\S]*--btn-primary-hover:\s*#5B6877/);
  assert.doesNotMatch(popupCss, /#E7E2DA|#F4F1EC/);
  assert.doesNotMatch(popupCss, /runner-option|status-stopped/);
});

test("popup connection badge reflects bridge reachability, not runner availability", () => {
  assert.match(popupHtml, /id="connectionBadge" class="status-badge">Connected<\/span>/);
  assert.match(popupJs, /async function getBridgeHealth\(\)/);
  assert.match(popupJs, /bridgeRequest\("GET", "\/health"\)/);
  assert.match(popupJs, /function renderBridgeBadge\(health\)/);
  assert.match(popupJs, /connected \? "Connected" : "Bridge unavailable"/);
  assert.match(popupJs, /PASI bridge is responding on 127\.0\.0\.1:8765\./);
  assert.doesNotMatch(popupJs, /state\?\.available \? "Connected" : "Disconnected"/);
  assert.match(backgroundJs, /new Set\(\['\/health', '\/status'/);
  assert.match(popupJs, /state\.status === "starting"/);
  assert.match(popupJs, /state\.status === "stopping"/);
  assert.match(popupJs, /typeof state\.process_alive === "boolean"/);
  assert.match(popupJs, /state\?\.ready === true/);
  assert.match(popupJs, /activeProfileForState\(state\) === profileId/);
  assert.doesNotMatch(popupJs, /Ready to execute|return "Ready";/);
  assert.doesNotMatch(popupJs, /return "Stopped"|Not running/);
  assert.doesNotMatch(popupCss, /status-text\.stopped|status-dot\.stopped/);
});

test("runner cards do not animate theme surface or border changes", () => {
  const runnerCardStart = popupCss.indexOf(".runner-card {");
  const runnerCardEnd = popupCss.indexOf("}", runnerCardStart);
  assert.ok(runnerCardStart >= 0);
  assert.ok(runnerCardEnd > runnerCardStart);

  const runnerCardRule = popupCss.slice(runnerCardStart, runnerCardEnd);
  assert.match(runnerCardRule, /transition:\s*box-shadow\s*\.15s/);
  assert.doesNotMatch(runnerCardRule, /background-color|border-color/);
});

test("dashboard remains the native toolbar popup", () => {
  assert.equal(manifest.action.default_popup, "popup.html");
  assert.doesNotMatch(JSON.stringify(manifest.permissions), /system\.display/);
  assert.doesNotMatch(backgroundJs, /chrome\.action\?\.onClicked/);
  assert.doesNotMatch(backgroundJs, /chrome\.windows\.create/);
  assert.doesNotMatch(backgroundJs, /chrome\.system\.display/);
});

test("runner state labels never claim inactive work is running", () => {
  assert.doesNotMatch(popupJs, /return "Ready";|Ready to execute/);
  assert.match(popupJs, /state\.status === "starting"/);
  assert.match(popupJs, /state\.status === "stopping"/);
  assert.match(popupJs, /return "";/);
  assert.doesNotMatch(popupJs, /return "Stopped";|Not running/);
});

test("active runner cards expose Stop only after startup is operational", () => {
  assert.match(popupJs, /function runnerProcessIsLive\(state\)/);
  assert.match(popupJs, /function runnerProcessIsActive\(state\)/);
  assert.match(popupJs, /return state\?\.status === "running" \|\| state\?\.status === "stopping";/);
  assert.match(popupJs, /function runnerIsStarting\(state, profileId\)/);
  assert.match(popupJs, /return "Launching"/);
  assert.match(popupJs, /return "Active process"/);
  assert.match(popupJs, /const activeThisProfile = runnerIsActive\(state\)/);
  assert.match(
    popupJs,
    /startingThisProfile \? "Starting" :[\s\S]*stoppingThisProfile \? "Stopping" :[\s\S]*activeThisProfile \? "Stop" :[\s\S]*"Start"/
  );
  assert.match(popupJs, /toggle\.disabled = startingThisProfile \|\| stoppingThisProfile \|\| anotherRunnerActive;/);
});

test("light and dark primary controls use distinct high-contrast palettes", () => {
  assert.match(popupCss, /--btn-primary:\s*#2B3138/);
  assert.match(popupCss, /--btn-primary-text:\s*#FFFFFF/);
  assert.match(popupCss, /html\.light-theme body\s*\{[\s\S]*--btn-primary:\s*#4A5563/);
  assert.match(popupCss, /html\.light-theme body\s*\{[\s\S]*--btn-primary-text:\s*#FFFFFF/);
});

test("runner controls keep Stop disabled until bridge state confirms termination", () => {
  assert.match(
    popupJs,
    /const stopping = activeThisProfile;/
  );
  assert.match(
    popupJs,
    /toggle\.disabled = true;/
  );
  assert.match(
    popupJs,
    /if \(stopping\) \{[\s\S]*toggle\.textContent = "Stopping…";/
  );
  assert.match(
    popupJs,
    /await controlRunner\(profileId, stopping \? "stop" : "start"\);[\s\S]*await render\(\);/
  );
  assert.doesNotMatch(
    popupJs,
    /finally \{[\s\S]*toggle\.disabled = false;/
  );
});

test("runner controls use explicit start or stop actions", () => {
  assert.match(popupJs, /async function controlRunner\(profileId, requestedAction\)/);
  assert.match(popupJs, /const action = requestedAction === "stop" \? "stop" : "start";/);
  assert.match(popupJs, /action,\s*profile: profileId/);
  assert.doesNotMatch(
    popupJs,
    /bridgeRequest\("POST", "\/runner\/control", \{\s*action:\s*"toggle"/
  );
  assert.match(
    popupJs,
    /await controlRunner\(profileId, stopping \? "stop" : "start"\);/
  );
  assert.match(
    popupJs,
    /toggle\.disabled = true;[\s\S]*toggle\.textContent = "Stopping…";/
  );
  assert.match(
    popupJs,
    /catch \(error\) \{[\s\S]*setStatus\(String\(error\?\.message \|\| error\), true\);[\s\S]*await render\(\);/
  );
});

test("runner controls display Start, Starting, or Stop according to live state", () => {
  assert.match(popupJs, /startingThisProfile \? "Starting"/);
  assert.match(popupJs, /stoppingThisProfile \? "Stopping"/);
  assert.match(popupJs, /activeThisProfile \? "Stop"/);
  assert.match(popupJs, /"Start";/);
});

test("inactive runner cards do not render redundant stopped status", () => {
  assert.match(popupJs, /if \(stateLabelText\) \{/);
  assert.match(popupJs, /return "";/);
  assert.doesNotMatch(popupJs, /Not running\./);
});

test("runner picker is absent because each runner card owns its own control", () => {
  assert.doesNotMatch(popupHtml, /runnerSelect|runnerSelectMenu|Selected runner/);
  assert.doesNotMatch(popupJs, /initializeRunnerPicker|selectRunnerProfile|runnerOptionElements|setRunnerSelection/);
  assert.doesNotMatch(popupCss, /runner-select|runner-select-option|profile-summary/);
});

test("runner cards no longer depend on a selected runner", () => {
  assert.match(popupJs, /function createRunnerCard\(profileId, state, aggregateState = state\)/);
  assert.match(popupJs, /function renderRunnerDashboard\(state, visible\)/);
  assert.doesNotMatch(popupJs, /selectedProfile/);
});


test("runner start requires ready state before claiming the runner is ready", () => {
  assert.match(popupJs, /function runnerIsReady\(state, profileId\)/);
  assert.match(popupJs, /state\?\.ready === true/);
  assert.match(popupJs, /if \(runnerIsReady\(state, profileId\)\) return state;/);
  assert.match(popupJs, /runner ready\./);
  assert.doesNotMatch(popupJs, /runner started\./);
});

test("popup preserves bridge errors without assigning them to a runner", () => {
  assert.match(popupJs, /const message = String\(error\?\.message \|\| error/);
  assert.match(popupJs, /available: false,[\s\S]*reason: message/);
  assert.match(popupJs, /async function getBridgeHealth\(\)/);
  assert.match(popupJs, /Bridge unavailable/);
  assert.doesNotMatch(popupJs, /Runner state unavailable: /);
});

test("popup preserves bridge transport errors instead of collapsing them to HTTP unknown", () => {
  assert.match(popupJs, /response\.error/);
  assert.match(popupJs, /Bridge request failed: no response from the PASI bridge\./);
  assert.doesNotMatch(popupJs, /HTTP unknown/);
});

test("popup refuses to call a runner ready when post-start state is unavailable", () => {
  assert.match(
    popupJs,
    /if \(!settledState\?\.available\) \{[\s\S]*throw new Error/
  );
});

test("failed runner state exposes its diagnostic error", () => {
  assert.match(popupJs, /state\?\.error[\s\S]*state\?\.stop_reason/);
});

test("runner action status survives the post-action render", () => {
  assert.match(
    popupJs,
    /async function render\(\{clearStatus = false\} = \{\}\)/
  );
  assert.match(
    popupJs,
    /if \(clearStatus && !\$\("status"\)\.classList\.contains\("error"\)\)/
  );
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

  assert.ok(contrastRatio("#171A20", "#F5F7FA") >= 4.5);
  assert.ok(contrastRatio("#666E78", "#F5F7FA") >= 4.5);
  assert.ok(contrastRatio("#4F5661", "#FFFFFF") >= 4.5);
  assert.ok(contrastRatio("#FFFFFF", "#20252B") >= 4.5);
  assert.ok(contrastRatio("#FFFFFF", "#171A20") >= 4.5);
  assert.ok(contrastRatio("#343A42", "#FFFFFF") >= 4.5);
  assert.ok(contrastRatio("#F5F7FA", "#15181D") >= 4.5);
  assert.ok(contrastRatio("#666E78", "#FFFFFF") >= 4.5);
});


test("popup scopes terminal runner diagnostics to the matching profile", () => {
  assert.match(
    popupJs,
    /state\.status === "failed"[\s\S]*state\.runner_profile === profileId/
  );
  assert.match(
    popupJs,
    /state\.status === "completed"[\s\S]*state\.runner_profile === profileId/
  );
});

test("popup surfaces M1 dispatch wait diagnostics", () => {
  assert.match(popupJs, /waiting_for_cdp_dispatch/);
  assert.match(popupJs, /Waiting for CDP dispatch/);
  assert.match(popupJs, /current_operation_id/);
});


test("popup shows M1 progress from zero before the first dispatch", () => {
  assert.match(popupJs, /const hasProgress = Number\.isFinite\(completed\) && Number\.isFinite\(target\) && target > 0;/);
  assert.match(popupJs, /ready_for_first_operation[\s\S]*progress \+ " — Ready for the first M1 operation\."/);
  assert.match(popupJs, /progress = String\(completed\) \+ " \/ " \+ String\(target\) \+ " operations complete"/);
});


test("runner authorization follows the live active ChatGPT tab", () => {
  assert.match(
    popupJs,
    /chrome\.tabs\.query\(\{active: true, lastFocusedWindow: true\}\)/
  );
  assert.match(
    popupJs,
    /const tabUrl = String\(tabs\?\.\[0\]\?\.url \|\| ""\);/
  );
  assert.match(
    popupJs,
    /if \(tabUrl\) activeUrl = tabUrl;/
  );
});


test("runner authorization survives opening the popup from another window", () => {
  assert.match(
    popupJs,
    /const openTabs = await chrome\.tabs\.query\(\{\}\)/
  );
  assert.match(
    popupJs,
    /openTabs\.find\(\(tab\) => isRunnerTargetUrl\(tab\?\.url\)\)/
  );
  assert.match(
    popupJs,
    /if \(authorizedUrl\) activeUrl = authorizedUrl;/
  );
});

test("M1 startup phases are surfaced instead of appearing silently stuck", () => {
  assert.match(popupJs, /startup: "Starting M1 runner\."/);
  assert.match(popupJs, /health_check: "Checking bridge and browser health\."/);
  assert.match(popupJs, /browser_diagnostics: "Reading the active ChatGPT browser state\."/);
  assert.match(popupJs, /ready_for_first_operation: "Ready for the first M1 operation\."/);
});


test("popup diagnostics include failure phase and log location", () => {
  assert.match(popupJs, /Log path:/);
  assert.match(popupJs, /Failed at:/);
  assert.match(popupJs, /state\?\.phase/);
});

test("popup diagnostics expose persisted-state consistency", () => {
  assert.match(popupJs, /State consistency:/);
  assert.match(popupJs, /Diagnostic warning:/);
  assert.match(popupJs, /state\?\.state_consistency/);
  assert.match(popupJs, /state\?\.diagnostic_warning/);
});

test("popup surfaces actual background-process diagnostics per runner profile", () => {
  assert.match(popupJs, /function diagnosticText\(state, aggregateState, profileId\)/);
  assert.match(popupJs, /Process alive:/);
  assert.match(popupJs, /Detected PID:/);
  assert.match(popupJs, /Detected command:/);
  assert.match(popupJs, /Bridge PID:/);
  assert.match(popupJs, /All detected PASI processes:/);
  assert.match(popupJs, /state\.profiles/);
  assert.match(popupJs, /profileState = profiles\[profileId\] \|\| state/);
});

test("popup renders terminal diagnostics from the matching profile state", () => {
  assert.match(
    popupJs,
    /const card = createRunnerCard\(\s*profileId,\s*profileState,/
  );
  assert.match(popupJs, /root\.append\(card\)/);
  assert.match(popupJs, /String\(state\?\.error \|\| "none"\)/);
});

test("popup card factory accepts aggregate process diagnostics", () => {
  assert.match(popupJs, /function createRunnerCard\(profileId, state, aggregateState = state\)/);
});


test("popup refreshes live runner state while it remains open", () => {
  const timerIndex = popupJs.indexOf("const runnerRefreshTimer = setInterval(() => {");
  assert.notEqual(timerIndex, -1);
  const timerSource = popupJs.slice(timerIndex, timerIndex + 180);
  assert.match(timerSource, /void render\(\);/);
  assert.match(timerSource, /\}, 750\);/);
  assert.match(popupJs, /clearInterval\(runnerRefreshTimer\)/);
});

test("popup preserves open diagnostics while refreshing runner state", () => {
  assert.match(popupJs, /const openDiagnostics = new Map\(/);
  assert.match(popupJs, /card\.querySelector\("\.runner-diagnostics"\)\?\.open === true/);
  assert.match(popupJs, /diagnostic\.open = openDiagnostics\.get\(profileId\) === true/);
});


test("popup preserves scroll position while runner state is re-rendered", () => {
  assert.match(popupJs, /function restorePopupScroll\(scrollTop\)/);
  assert.match(popupJs, /const scrollTop = .*scrollTop.*\|\| window\.scrollY/);
  assert.match(popupJs, /root\.replaceChildren\(\)/);
  assert.match(popupJs, /restorePopupScroll\(scrollTop\)/);
});

test("popup runner cards come from the backend registry instead of a fixed two-runner list", () => {
  assert.match(popupJs, /function getRunnerRegistry\(\)/);
  assert.match(popupJs, /\/runner\/registry/);
  assert.match(popupJs, /for \(const entry of registryRunners\)/);
  assert.match(popupJs, /const registryEntry = registryRunners\.find/);
  assert.match(popupJs, /function runnerProfileMeta\(profileId/);
});

test("popup exposes user runner creation and candidate revision controls", () => {
  assert.match(popupHtml, /id="createRunnerForm"/);
  assert.match(popupHtml, /id="createRevisionForm"/);
  assert.match(popupJs, /\/runner\/registry\/create/);
  assert.match(popupJs, /\/runner\/registry\/revision/);
  assert.match(popupJs, /\/runner\/registry\/promote/);
  assert.match(popupJs, /\/runner\/registry\/rollback/);
});


test("popup recognizes arbitrary supervised runner profiles", () => {
  assert.match(popupJs, /function activeProfileForState\(state\)[\s\S]*mode\.startsWith\("supervised_"/);
  assert.match(popupJs, /\^\[a-z\]\[a-z0-9\.\_\-\]\{1,63\}\$/);
  assert.match(popupJs, /runnerProfileMeta\(profileId, state, registryEntry\)/);
});


test("popup does not surface an unavailable runner as a blanket error", () => {
  assert.match(popupJs, /if \(!state\?\.available\) return "";/);
  assert.match(popupJs, /if \(status === "Unavailable"\) return "";/);
});

test("popup diagnostics expose evidence behind runner failures", () => {
  assert.match(popupJs, /Failure evidence: /);
  assert.match(popupJs, /Live process evidence: /);
  assert.match(popupJs, /State file evidence: /);
  assert.match(popupJs, /evidence\?\.failure_confirmed === true/);
});
