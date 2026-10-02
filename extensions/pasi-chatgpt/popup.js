(() => {
  "use strict";

  const TYPES = {
    active: "pasi.userscript.active_tab",
    menu: "pasi.userscript.menu.list",
    menuInvoke: "pasi.userscript.menu.invoke",
    enable: "pasi.userscript.enable",
    disable: "pasi.userscript.disable",
    hosts: "pasi.userscript.hosts",
  };

  const RUNNER_PROFILES = {
    m1: {
      id: "m1",
      label: "M1",
      title: "20-Operation Acceptance",
      executionMode: "supervised_m1",
      badgeClass: "m1",
    },
    "168h": {
      id: "168h",
      label: "168h",
      title: "168-Hour Long-Run Acceptance",
      executionMode: "supervised_168h",
      badgeClass: "long-run",
    },
  };

  const $ = (id) => document.getElementById(id);

  function isRunnerTargetUrl(url) {
    try {
      const parsed = new URL(String(url || ""));
      return (
        parsed.protocol === "https:" &&
        (parsed.hostname === "chatgpt.com" || parsed.hostname === "www.chatgpt.com")
      );
    } catch (_) {
      return false;
    }
  }

  function hasMatchedUserscripts(activeResult) {
    return Array.isArray(activeResult?.scripts) && activeResult.scripts.length > 0;
  }

  function send(type, payload = {}) {
    return new Promise((resolve, reject) => {
      chrome.runtime.sendMessage({type, ...payload}, (response) => {
        const error = chrome.runtime.lastError;
        if (error) return reject(new Error(error.message));
        if (!response?.ok) {
          return reject(new Error(response?.error || "PASI request failed"));
        }
        resolve(response);
      });
    });
  }

  async function bridgeRequest(method, path, body = null) {
    return new Promise((resolve, reject) => {
      chrome.runtime.sendMessage({
        type: "pasi-control-center-bridge-request",
        method,
        path,
        body,
      }, (response) => {
        const error = chrome.runtime.lastError;
        if (error) return reject(new Error(error.message));
        resolve(response || null);
      });
    });
  }

  function parseBridgeResponse(response) {
    if (!response) {
      throw new Error("No response from the PASI bridge.");
    }

    let payload = {};
    try {
      payload = JSON.parse(response.text || "{}");
    } catch (_) {
      payload = {};
    }

    if (!response.ok) {
      const detail = String(payload?.error || response.text || "").trim();
      throw new Error(detail || "Bridge request failed (HTTP " + String(response.status || "unknown") + ").");
    }

    return payload;
  }

  function setStatus(message, error = false) {
    const node = $("status");
    node.textContent = message;
    node.classList.toggle("error", error);
  }

  async function requestHosts(patterns) {
    const origins = [...new Set(patterns)];
    if (!origins.length) return false;
    return chrome.permissions.request({origins});
  }

  async function getRunnerState() {
    const response = await bridgeRequest("GET", "/runner/state");
    try {
      return parseBridgeResponse(response);
    } catch (_) {
      return {available: false, reason: "runner state unavailable"};
    }
  }

  async function getProfile() {
    const stored = await chrome.storage.local.get("pasi.runner.profile");
    return stored?.["pasi.runner.profile"] === "168h" ? "168h" : "m1";
  }

  async function setProfile(profile) {
    await chrome.storage.local.set({"pasi.runner.profile": profile});
  }

  function runnerIsRunning(state) {
    return state?.available === true && state?.status === "running";
  }

  function activeProfileForState(state) {
    const mode = String(state?.execution_mode || "");
    if (mode === "supervised_m1") return "m1";
    if (mode === "supervised_168h") return "168h";
    return null;
  }

  function runnerStatusLabel(state, profileId) {
    if (!state?.available) return "Unavailable";
    if (runnerIsRunning(state)) {
      return activeProfileForState(state) === profileId ? "Running" : "Another runner active";
    }
    if (state.status === "paused") return "Paused";
    if (state.status === "failed") return "Failed";
    if (state.status === "completed") return "Completed";
    return "Ready";
  }

  function runnerSummary(state, profileId) {
    const status = runnerStatusLabel(state, profileId);
    if (status === "Running") {
      const completed = Number(state.completed_operations);
      const target = Number(state.target_operations);
      if (Number.isFinite(completed) && Number.isFinite(target) && target > 0) {
        return String(completed) + " / " + String(target) + " operations complete";
      }
      return "Supervised runner is active.";
    }
    if (status === "Another runner active") {
      return "Pause the active runner before starting this profile.";
    }
    if (status === "Completed") return "Last run completed.";
    if (status === "Paused") return "Runner is paused and can be started again.";
    if (status === "Failed") return String(state?.error || state?.stop_reason || state?.last_result || "Last run failed.");
    return "Ready to execute.";
  }

  async function waitForRunnerState(profileId, action, timeoutMs = 5000) {
    const deadline = Date.now() + timeoutMs;
    let state = await getRunnerState();

    while (Date.now() < deadline) {
      const activeProfile = activeProfileForState(state);
      const running = runnerIsRunning(state);

      if (action === "start") {
        if (running && activeProfile === profileId) return state;
        if (state?.status === "failed" || state?.status === "completed" || state?.status === "cancelled") return state;
      } else if (!running || activeProfile !== profileId) {
        return state;
      }

      await new Promise((resolve) => setTimeout(resolve, 100));
      state = await getRunnerState();
    }

    return state;
  }

  async function controlRunner(profileId, requestedAction) {
    const action = requestedAction === "stop" ? "stop" : "start";
    const currentState = await getRunnerState();
    const activeProfile = activeProfileForState(currentState);
    const running = runnerIsRunning(currentState);

    if (action === "start" && running) {
      if (activeProfile && activeProfile !== profileId) {
        throw new Error(
          String(activeProfile).toUpperCase() +
          " runner is already running. Pause it before starting " +
          (profileId === "168h" ? "168h" : "M1") +
          "."
        );
      }
      throw new Error(
        (profileId === "168h" ? "168h" : "M1") +
        " runner is already running."
      );
    }

    if (action === "stop" && (!running || activeProfile !== profileId)) {
      throw new Error(
        (profileId === "168h" ? "168h" : "M1") +
        " runner is not running."
      );
    }

    const raw = await bridgeRequest("POST", "/runner/control", {
      action,
      profile: profileId,
    });
    const result = parseBridgeResponse(raw);

    if (!result.accepted || result.action !== action) {
      throw new Error(String(result.reason || "Runner action was rejected."));
    }

    const settledState = await waitForRunnerState(profileId, action);
    if (action === "start" && settledState?.status === "failed") {
      throw new Error(
        String(
          settledState?.error ||
          settledState?.stop_reason ||
          settledState?.last_result ||
          (profileId === "168h" ? "168h" : "M1") + " runner failed during startup."
        )
      );
    }

    setStatus(
      action === "stop"
        ? (profileId === "168h" ? "168h" : "M1") + " runner stopped."
        : (profileId === "168h" ? "168h" : "M1") + " runner started."
    );

    return action;
  }
  function createRunnerCard(profileId, state) {
    const profile = RUNNER_PROFILES[profileId];
    const card = document.createElement("section");
    card.className = "runner-card";
    card.dataset.profile = profileId;

    const header = document.createElement("div");
    header.className = "card-header";

    const url = document.createElement("span");
    url.className = "url";
    url.title = $("currentUrl")?.textContent || "https://chatgpt.com/";
    url.textContent = $("currentUrl")?.textContent || "chatgpt.com";

    const badge = document.createElement("span");
    badge.className = "badge " + profile.badgeClass;
    badge.textContent = profileId === "m1"
      ? "Runner: M1"
      : "Runner: 168h";

    header.append(url, badge);

    const title = document.createElement("div");
    title.className = "card-title";
    title.textContent = profile.title;

    const body = document.createElement("div");
    body.className = "card-body";

    const stateLabelText = runnerStatusLabel(state, profileId);
    const statusText = document.createElement("div");
    const stateClass =
      stateLabelText === "Running" ? "running" :
      stateLabelText === "Paused" ? "paused" :
      stateLabelText === "Failed" ? "failed" :
      stateLabelText === "Completed" ? "completed" :
      stateLabelText === "Ready" ? "ready" :
      "idle";
    statusText.className = "status-text " + stateClass;

    const dot = document.createElement("span");
    dot.className = "status-dot " + stateClass;
    dot.setAttribute("aria-hidden", "true");

    const label = document.createElement("span");
    label.textContent = stateLabelText;
    statusText.append(dot, label);

    const desc = document.createElement("div");
    desc.className = "status-desc" + (stateLabelText === "Ready" ? " idle-summary" : "");
    desc.textContent = runnerSummary(state, profileId);
    body.append(statusText, desc);

    const actions = document.createElement("div");
    actions.className = "card-actions";

    const toggle = document.createElement("button");
    toggle.className = "btn btn-primary";
    toggle.type = "button";
    const runningThisProfile = runnerIsRunning(state) && activeProfileForState(state) === profileId;
    toggle.textContent = runningThisProfile ? "Stop" : "Start";
    toggle.disabled = runnerIsRunning(state) && activeProfileForState(state) !== profileId;

    toggle.onclick = async () => {
      toggle.disabled = true;
      try {
        await setProfile(profileId);
        await controlRunner(profileId, runningThisProfile ? "stop" : "start");
        await render();
      } catch (error) {
        setStatus(String(error?.message || error), true);
      } finally {
        toggle.disabled = false;
      }
    };

    actions.append(toggle);
    card.append(header, title, body, actions);
    return card;
  }

  function renderRunnerDashboard(state, visible) {
    const root = $("runnerCards");
    const controls = $("runnerControls");
    root.replaceChildren();
    controls.hidden = !visible;

    if (!visible) {
      $("connectionBadge").textContent = state?.available ? "Connected" : "Disconnected";
      $("connectionBadge").classList.remove("active");
      return;
    }

    for (const profileId of Object.keys(RUNNER_PROFILES)) {
      root.append(createRunnerCard(profileId, state));
    }

    $("connectionBadge").textContent = state?.available ? "Connected" : "Disconnected";
    $("connectionBadge").classList.remove("active");
  }

  async function renderUserscripts(activeResult) {
    const root = $("scripts");
    root.replaceChildren();

    const scripts = Array.isArray(activeResult?.scripts) ? activeResult.scripts : [];
    const menu = await send(TYPES.menu);
    const commands = Array.isArray(menu.commands) ? menu.commands : [];

    if (!scripts.length && !commands.length) {
      return;
    }

    const title = document.createElement("div");
    title.className = "userscript-section-title";
    title.textContent = "PASI userscripts";
    root.append(title);

    if (commands.length) {
      const section = document.createElement("section");
      section.className = "card userscript-card";

      for (const command of commands) {
        const button = document.createElement("button");
        button.className = "btn btn-secondary";
        button.type = "button";
        button.textContent = command.title;
        button.style.width = "100%";
        button.onclick = async () => {
          await send(TYPES.menuInvoke, {command_id: command.id});
          window.close();
        };
        section.append(button);
      }

      root.append(section);
    }

    for (const script of scripts) {
      const card = document.createElement("section");
      card.className = "card userscript-card";

      const row = document.createElement("div");
      row.className = "row";

      const left = document.createElement("div");
      left.innerHTML = '<div class="name"></div><div class="meta"></div>';
      left.querySelector(".name").textContent = script.name;
      left.querySelector(".meta").textContent = script.enabled ? "Enabled" : "Disabled";
      row.append(left);

      const toggle = document.createElement("button");
      toggle.className = "btn btn-secondary";
      toggle.type = "button";
      toggle.textContent = script.enabled ? "Disable" : "Enable";
      toggle.onclick = async () => {
        await send(script.enabled ? TYPES.disable : TYPES.enable, {id: script.id});
        await render();
      };
      row.append(toggle);
      card.append(row);

      const hosts = document.createElement("div");
      hosts.className = "hosts";
      const selected = new Set(script.host_allowlist || script.matches || []);

      for (const pattern of script.matches || []) {
        const label = document.createElement("label");
        label.className = "host";

        const input = document.createElement("input");
        input.type = "checkbox";
        input.checked = selected.has(pattern);
        input.dataset.pattern = pattern;

        const text = document.createElement("span");
        text.textContent = pattern;

        label.append(input, text);
        hosts.append(label);
      }

      const apply = document.createElement("button");
      apply.className = "btn btn-secondary";
      apply.type = "button";
      apply.textContent = "Apply site scope";
      apply.onclick = async () => {
        const allowed = [...hosts.querySelectorAll("input:checked")].map((input) => input.dataset.pattern);
        await send(TYPES.hosts, {id: script.id, host_allowlist: allowed});
        setStatus("Site scope updated.");
        await render();
      };

      const grant = document.createElement("button");
      grant.className = "btn btn-secondary";
      grant.type = "button";
      grant.textContent = "Grant checked hosts";
      grant.onclick = async () => {
        const origins = [...hosts.querySelectorAll("input:checked")].map((input) => input.dataset.pattern);
        const granted = await requestHosts(origins);
        setStatus(granted ? "Host access granted." : "Host access declined.");
        await render();
      };

      const actions = document.createElement("div");
      actions.className = "actions";
      actions.append(apply, grant);
      card.append(hosts, actions);

      if (!script.host_granted) {
        const warn = document.createElement("div");
        warn.className = "warn";
        warn.textContent = "Some required host permissions are not granted.";
        card.append(warn);
      }

      root.append(card);
    }
  }

  function updateThemeToggleButton(light) {
    const button = $("themeToggle");
    button.textContent = light ? "☀️" : "🌙";
    button.title = light ? "Switch to dark theme" : "Switch to light theme";
    button.setAttribute("aria-label", button.title);
  }

  function applyThemeDom(light) {
    const root = document.documentElement;
    root.classList.toggle("light-theme", light);
    root.style.backgroundColor = light ? "#FAF8F5" : "#0D0E11";
    root.style.colorScheme = light ? "light" : "dark";

    const colorSchemeMeta = document.querySelector('meta[name="color-scheme"]');
    if (colorSchemeMeta) {
      colorSchemeMeta.setAttribute("content", light ? "light dark" : "dark light");
    }

    updateThemeToggleButton(light);
  }

  function getLocalTheme() {
    try {
      return localStorage.getItem("pasi.popup.theme");
    } catch (_) {
      return null;
    }
  }

  function setLocalTheme(theme) {
    try {
      localStorage.setItem("pasi.popup.theme", theme);
    } catch (_) {
      // chrome.storage.local remains the durable fallback.
    }
  }

  function applyTheme() {
    updateThemeToggleButton(
      document.documentElement.classList.contains("light-theme")
    );
  }

  async function toggleTheme() {
    const light = !document.documentElement.classList.contains("light-theme");
    const theme = light ? "light" : "dark";

    setLocalTheme(theme);
    applyThemeDom(light);

    try {
      await chrome.storage.local.set({"pasi.popup.theme": theme});
    } catch (_) {
      // localStorage has already persisted the theme for the next popup paint.
    }
  }

  async function render({clearStatus = false} = {}) {
    try {
      applyTheme();

      const [activeResult, runnerState] = await Promise.all([
        send(TYPES.active),
        getRunnerState(),
      ]);

      const activeUrl = String(activeResult.url || "");
      const runnerSupported = isRunnerTargetUrl(activeUrl);
      const userscriptsMatched = hasMatchedUserscripts(activeResult);

      const currentUrl = $("currentUrl") || document.createElement("div");
      currentUrl.id = "currentUrl";
      currentUrl.textContent = activeUrl || "https://chatgpt.com/";
      currentUrl.hidden = true;
      if (!currentUrl.parentElement) document.body.append(currentUrl);

      renderRunnerDashboard(runnerState, runnerSupported);
      await renderUserscripts(activeResult);

      const idle = $("idleState");
      if (idle) {
        idle.hidden = runnerSupported || userscriptsMatched;
      }

      if (clearStatus && !$("status").classList.contains("error")) {
        setStatus("");
      }
    } catch (error) {
      setStatus(String(error?.message || error), true);
    }
  }

  $("themeToggle").addEventListener("click", () => {
    void toggleTheme().catch((error) => setStatus(String(error?.message || error), true));
  });

  void render();
})();
