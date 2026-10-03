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
      title: "20-Operation Acceptance",
      badgeClass: "m1",
    },
    "168h": {
      id: "168h",
      title: "168-Hour Long-Run Acceptance",
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
      const detail = String(
        payload?.error ||
        response.error ||
        response.text ||
        ""
      ).trim();
      const status = Number(response.status);
      if (Number.isFinite(status) && status > 0) {
        throw new Error(
          detail || "Bridge request failed (HTTP " + String(status) + ")."
        );
      }
      throw new Error(
        detail
          ? "Bridge request failed: " + detail
          : "Bridge request failed: no response from the PASI bridge."
      );
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
    try {
      const response = await bridgeRequest("GET", "/runner/state");
      return parseBridgeResponse(response);
    } catch (error) {
      const message = String(error?.message || error || "runner state unavailable").trim();
      return {
        available: false,
        reason: message || "runner state unavailable",
      };
    }
  }

  function runnerProcessIsLive(state) {
    if (!state?.available) return false;
    if (typeof state.process_alive === "boolean") {
      return state.process_alive;
    }
    return (
      state?.status === "starting" ||
      state?.status === "running" ||
      state?.status === "stopping"
    );
  }

  function runnerProcessIsActive(state) {
    if (!runnerProcessIsLive(state)) return false;
    return state?.status === "running" || state?.status === "stopping";
  }

  function runnerIsActive(state) {
    return runnerProcessIsActive(state);
  }

  function runnerIsStarting(state, profileId) {
    return (
      runnerProcessIsLive(state) &&
      state?.status === "starting" &&
      activeProfileForState(state) === profileId
    );
  }

  function activeProfileForState(state) {
    const processProfile = String(state?.process_profile || "");
    if (processProfile === "m1" || processProfile === "168h") return processProfile;
    const explicit = String(state?.runner_profile || "");
    if (explicit === "m1" || explicit === "168h") return explicit;
    const mode = String(state?.execution_mode || "");
    if (mode === "supervised_m1") return "m1";
    if (mode === "supervised_168h") return "168h";
    return null;
  }

  function runnerIsReady(state, profileId) {
    return (
      state?.available === true &&
      state?.ready === true &&
      runnerProcessIsActive(state) &&
      activeProfileForState(state) === profileId
    );
  }

  function runnerStatusLabel(state, profileId) {
    if (!state?.available) return "Unavailable";
    const activeProfile = activeProfileForState(state);
    const processLive = runnerProcessIsLive(state);

    if (processLive) {
      if (activeProfile !== profileId) return "Another runner active";
      if (state.status === "running" && state.ready === true) return "Running";
      if (state.status === "stopping") return "Stopping";
      if (state.status === "starting") return "Launching";
      return "Active process";
    }

    if (
      state.status === "failed" &&
      (!state.runner_profile || state.runner_profile === profileId)
    ) {
      return "Failed";
    }
    if (
      (
        state.status === "completed" ||
        state.status === "roadmap_complete" ||
        state.status === "deadline_reached"
      ) &&
      (!state.runner_profile || state.runner_profile === profileId)
    ) {
      return "Completed";
    }

    return "";
  }

  function runnerProfileMeta(profileId, state = {}, registryEntry = null) {
    const fallback = RUNNER_PROFILES[profileId] || {
      id: profileId,
      title: profileId,
      badgeClass: "custom",
    };
    const entry = registryEntry && typeof registryEntry === "object" ? registryEntry : {};
    return {
      id: profileId,
      title: String(entry.name || state?.runner_name || fallback.title || profileId),
      source: String(entry.source || state?.runner_source || "user"),
      stableVersion: Number.isFinite(Number(entry.stable_version))
        ? Number(entry.stable_version)
        : null,
      candidateVersion: Number.isFinite(Number(entry.candidate_version))
        ? Number(entry.candidate_version)
        : null,
      badgeClass: fallback.badgeClass || "custom",
    };
  }

  function runnerSummary(state, profileId, registryEntry = null) {
    const status = runnerStatusLabel(state, profileId);
    const profileMeta = runnerProfileMeta(profileId, state, registryEntry);
    if (status === "Launching") return "Runner process launched; waiting for ready state.";
    if (status === "Stopping") return "Stopping runner…";
    if (status === "Running") {
      const phase = String(state?.phase || "");
      const currentStatus = String(state?.current_operation_status || "");
      const currentId = String(state?.current_operation_id || "");
      const runnerName = profileMeta.title;
      const startupPhases = {
        startup: "Starting " + runnerName + " runner.",
        health_check: "Checking bridge and browser health.",
        browser_diagnostics: "Reading the active ChatGPT browser state.",
        ready_for_first_operation: "Ready for the first operation."
      };
      const completed = Number(state.completed_operations);
      const target = Number(state.target_operations);
      const hasProgress = Number.isFinite(completed) && Number.isFinite(target) && target > 0;
      if (hasProgress) {
        const progress = String(completed) + " / " + String(target) + " operations complete";
        if (phase === "waiting_for_cdp_dispatch" || currentStatus === "queued") {
          return currentId
            ? progress + " — Waiting for CDP dispatch of " + currentId
            : progress + " — Waiting for CDP dispatch.";
        }
        if (phase === "processing_response" || currentStatus === "generating") {
          return progress + " — Waiting for the current response to finish processing.";
        }
        if (phase === "ready_for_first_operation") {
          return progress + " — Ready for the first M1 operation.";
        }
        if (phase === "ready_for_next_operation") {
          return progress + " — Ready for the next M1 operation.";
        }
        return progress;
      }
      if (startupPhases[phase]) return startupPhases[phase];
      return "Runner is ready and active.";
    }
    if (status === "Active process") {
      return "Runner process is alive, but its state is not ready.";
    }
    if (status === "Unavailable") {
      const reason = String(state?.reason || state?.error || "").trim();
      return reason
        ? "Runner state unavailable: " + reason
        : "Runner state unavailable.";
    }
    if (status === "Another runner active") return "Another runner is active.";
    if (status === "Completed") return "Last run completed.";
    if (status === "Failed") {
      const reason = String(
        state?.error ||
        state?.stop_reason ||
        state?.last_result ||
        "Last run failed."
      ).trim();
      const phase = String(state?.phase || "").trim();
      return phase ? reason + " (phase: " + phase + ")" : reason;
    }
    return "";
  }

  async function waitForRunnerState(profileId, action, timeoutMs = 5000) {
    const deadline = Date.now() + timeoutMs;
    let state = await getRunnerState();

    while (Date.now() < deadline) {
      const activeProfile = activeProfileForState(state);
      const processLive = runnerProcessIsLive(state);

      if (action === "start") {
        if (runnerIsReady(state, profileId)) return state;
        if (!processLive || state?.status === "failed") return state;
      } else if (!active || activeProfile !== profileId) {
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
    const processLive = runnerProcessIsLive(currentState);
    const running = runnerIsActive(currentState);

    if (action === "start" && processLive) {
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
      if (runnerIsStarting(currentState, profileId)) {
        throw new Error(
          (profileId === "168h" ? "168h" : "M1") +
          " runner is still starting; stop is unavailable until it is running."
        );
      }
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
    if (action === "start") {
      if (!settledState?.available) {
        throw new Error(
          String(
            settledState?.reason ||
            settledState?.error ||
            (profileId === "168h" ? "168h" : "M1") +
            " runner state could not be read after the start request."
          )
        );
      }
      if (settledState?.status === "failed") {
        throw new Error(
          String(
            settledState?.error ||
            settledState?.stop_reason ||
            settledState?.last_result ||
            (profileId === "168h" ? "168h" : "M1") + " runner failed during launch."
          )
        );
      }
      if (runnerIsReady(settledState, profileId)) {
        setStatus((profileId === "168h" ? "168h" : "M1") + " runner ready.");
      } else {
        setStatus((profileId === "168h" ? "168h" : "M1") + " runner is still launching; it is not ready yet.");
      }
    } else if (runnerIsActive(settledState)) {
      setStatus((profileId === "168h" ? "168h" : "M1") + " runner is still stopping.");
    } else {
      setStatus((profileId === "168h" ? "168h" : "M1") + " runner stopped.");
    }

    return action;
  }
  function diagnosticText(state, aggregateState, profileId) {
    const process = state?.process;
    const processes = Array.isArray(aggregateState?.processes) ? aggregateState.processes : [];
    const lines = [
      "Profile: " + profileId,
      "State: " + String(state?.status || "unavailable"),
      "State consistency: " + String(state?.state_consistency || "unknown"),
      "Diagnostic warning: " + String(state?.diagnostic_warning || "none"),
      "Phase: " + String(state?.phase || "n/a"),
      "Execution: " + String(state?.execution_mode || "n/a"),
      "Ready: " + (state?.ready === true ? "yes" : "no"),
      "Process alive: " + (state?.process_alive === true ? "yes" : "no"),
      "Runner PID: " + String(state?.runner_pid ?? "n/a"),
      "Detected PID: " + String(process?.pid ?? state?.process_pid ?? "none"),
      "Detected command: " + String(process?.cmdline ?? state?.process_cmdline ?? "none"),
      "Runtime state: " + String(state?.runtime_state_path || "n/a"),
      "Log path: " + String(state?.log_path || "n/a"),
      "Failed at: " + String(state?.failed_at || "n/a"),
      "Bridge PID: " + String(aggregateState?.bridge_process?.pid ?? "n/a"),
      "Error: " + String(state?.error || "none"),
      "All detected PASI processes:",
    ];
    if (processes.length) {
      for (const item of processes) {
        lines.push(
          "  " + String(item.profile || "unknown") +
          " pid=" + String(item.pid || "n/a") +
          " workspace=" + (item.workspace ? "yes" : "no") +
          " command=" + String(item.cmdline || "n/a")
        );
      }
    } else {
      lines.push("  none");
    }
    return lines.join("\n");
  }

  function createRunnerCard(profileId, state, aggregateState = state, registryEntry = null) {
    const profile = runnerProfileMeta(profileId, state, registryEntry);
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
    badge.textContent = profile.source === "builtin"
      ? (profileId === "m1" ? "Runner: M1" : profileId === "168h" ? "Runner: 168h" : "Runner")
      : "Runner: " + profileId;

    header.append(url, badge);

    const title = document.createElement("div");
    title.className = "card-title";
    title.textContent = profile.title;

    const body = document.createElement("div");
    body.className = "card-body";

    const stateLabelText = runnerStatusLabel(state, profileId);
    if (stateLabelText) {
      const stateClass =
        stateLabelText === "Launching" ? "launching" :
        stateLabelText === "Stopping" ? "stopping" :
        stateLabelText === "Running" ? "running" :
        stateLabelText === "Active process" ? "active" :
        stateLabelText === "Failed" ? "failed" :
        stateLabelText === "Completed" ? "completed" :
        "unavailable";

      const statusText = document.createElement("div");
      statusText.className = "status-text " + stateClass;

      const dot = document.createElement("span");
      dot.className = "status-dot " + stateClass;
      dot.setAttribute("aria-hidden", "true");

      const label = document.createElement("span");
      label.textContent = stateLabelText;
      statusText.append(dot, label);

      const summary = runnerSummary(state, profileId, registryEntry);
      if (summary) {
        const desc = document.createElement("div");
        desc.className = "status-desc";
        desc.textContent = summary;
        body.append(statusText, desc);
      } else {
        body.append(statusText);
      }
    }

    const actions = document.createElement("div");
    actions.className = "card-actions";

    const toggle = document.createElement("button");
    toggle.className = "btn btn-primary";
    toggle.type = "button";
    const activeThisProfile = runnerIsActive(state) && activeProfileForState(state) === profileId;
    const startingThisProfile = runnerIsStarting(state, profileId);
    const anotherRunnerActive = aggregateState?._anotherRunnerActive === true;
    const stoppingThisProfile = activeThisProfile && state?.status === "stopping";
    toggle.textContent =
      startingThisProfile ? "Starting" :
      stoppingThisProfile ? "Stopping" :
      activeThisProfile ? "Stop" :
      "Start";
    toggle.disabled = startingThisProfile || stoppingThisProfile || anotherRunnerActive;

    toggle.onclick = async () => {
      const stopping = activeThisProfile;
      toggle.disabled = true;
      if (stopping) {
        toggle.textContent = "Stopping…";
        toggle.setAttribute("aria-label", "Stopping runner");
      }
      try {
        await controlRunner(profileId, stopping ? "stop" : "start");
        await render();
      } catch (error) {
        setStatus(String(error?.message || error), true);
        await render();
      }
    };

    const candidateVersion = profile.candidateVersion;
    if (candidateVersion != null) {
      const candidate = document.createElement("div");
      candidate.className = "runner-candidate";
      candidate.textContent = "Candidate revision v" + String(candidateVersion) + " staged.";
      body.append(candidate);
    }

    const diagnostic = document.createElement("details");
    diagnostic.className = "runner-diagnostics";
    const diagnosticSummary = document.createElement("summary");
    diagnosticSummary.textContent = "Diagnostics";
    const diagnosticPre = document.createElement("pre");
    diagnosticPre.textContent = diagnosticText(state, aggregateState, profileId);
    diagnostic.append(diagnosticSummary, diagnosticPre);
    body.append(diagnostic);

    actions.append(toggle);
    card.append(header, title, body, actions);
    return card;
  }

  function restorePopupScroll(scrollTop) {
    const value = Math.max(0, Number(scrollTop) || 0);
    const restore = () => {
      const scroller = document.scrollingElement || document.documentElement;
      if (scroller) scroller.scrollTop = value;
      if (window.scrollY !== value && typeof window.scrollTo === "function") {
        window.scrollTo({top: value, left: 0, behavior: "auto"});
      }
    };
    restore();
    requestAnimationFrame(restore);
  }

  function renderRunnerDashboard(state, visible, registry = null) {
    const root = $("runnerCards");
    const scrollTop = (document.scrollingElement || document.documentElement)?.scrollTop || window.scrollY || 0;
    const openDiagnostics = new Map(
      [...root.querySelectorAll(".runner-card")].map((card) => [
        card.dataset.profile,
        card.querySelector(".runner-diagnostics")?.open === true,
      ])
    );

    if (!visible) {
      root.replaceChildren();
      $("connectionBadge").textContent = state?.available ? "Connected" : "Disconnected";
      $("connectionBadge").classList.remove("active");
      restorePopupScroll(scrollTop);
      return;
    }

    const profiles = state?.profiles && typeof state.profiles === "object"
      ? {...state.profiles}
      : {};
    const registryRunners = Array.isArray(registry?.runners) ? registry.runners : [];
    for (const entry of registryRunners) {
      const id = String(entry?.id || "").trim();
      if (id && !profiles[id]) profiles[id] = {available: false, runner_profile: id};
    }
    for (const profileId of Object.keys(RUNNER_PROFILES)) {
      if (!profiles[profileId]) profiles[profileId] = {available: false, runner_profile: profileId};
    }

    root.replaceChildren();
    for (const profileId of Object.keys(profiles)) {
      const profileState = profiles[profileId] || state;
      const registryEntry = registryRunners.find((entry) => String(entry?.id || "") === profileId) || null;
      const globalActiveProfile = String(state?.active_profile || "");
      const anotherRunnerActive =
        Boolean(globalActiveProfile) && globalActiveProfile !== profileId;
      const card = createRunnerCard(
        profileId,
        profileState,
        {...state, _anotherRunnerActive: anotherRunnerActive},
        registryEntry
      );
      const diagnostic = card.querySelector(".runner-diagnostics");
      if (diagnostic) {
        diagnostic.open = openDiagnostics.get(profileId) === true;
      }
      root.append(card);
    }

    $("connectionBadge").textContent = state?.available ? "Connected" : "Disconnected";
    $("connectionBadge").classList.remove("active");
    restorePopupScroll(scrollTop);
  }

  async function renderUserscripts(activeResult) {
    const root = $("scripts");

    const scripts = Array.isArray(activeResult?.scripts) ? activeResult.scripts : [];
    const menu = await send(TYPES.menu);
    const scrollTop = (document.scrollingElement || document.documentElement)?.scrollTop || window.scrollY || 0;
    root.replaceChildren();
    const commands = Array.isArray(menu.commands) ? menu.commands : [];

    if (!scripts.length && !commands.length) {
      restorePopupScroll(scrollTop);
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

    restorePopupScroll(scrollTop);
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

  async function getRunnerRegistry() {
    try {
      const response = await bridgeRequest("GET", "/runner/registry");
      return parseBridgeResponse(response);
    } catch (error) {
      return {schema_version: 1, runners: [], error: String(error?.message || error)};
    }
  }

  function parseArgsJson(value) {
    const raw = String(value || "").trim();
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed) || parsed.some((item) => typeof item !== "string")) {
      throw new Error("Args JSON must be an array of strings.");
    }
    return parsed;
  }

  async function runnerRegistryRequest(path, body) {
    const raw = await bridgeRequest("POST", path, body);
    return parseBridgeResponse(raw);
  }

  async function renderRunnerRegistry(registry) {
    const root = $("runnerRegistryList");
    if (!root) return;
    const scrollTop = (document.scrollingElement || document.documentElement)?.scrollTop || window.scrollY || 0;
    root.replaceChildren();

    const runners = Array.isArray(registry?.runners) ? registry.runners : [];
    if (!runners.length) {
      const empty = document.createElement("div");
      empty.className = "runner-registry-empty";
      empty.textContent = "No user-created runners yet.";
      root.append(empty);
      restorePopupScroll(scrollTop);
      return;
    }

    for (const runner of runners) {
      const row = document.createElement("div");
      row.className = "runner-registry-row";
      const label = document.createElement("div");
      label.className = "runner-registry-label";
      const name = document.createElement("strong");
      name.textContent = String(runner?.name || runner?.id || "Runner");
      const meta = document.createElement("span");
      const stableVersion = runner?.stable?.version == null ? "—" : "v" + String(runner.stable.version);
      const candidateVersion = runner?.candidate?.version == null ? "" : " · candidate v" + String(runner.candidate.version);
      meta.textContent = String(runner?.id || "") + " · stable " + stableVersion + candidateVersion;
      label.append(name, meta);
      row.append(label);

      if (runner?.candidate?.version != null) {
        const promote = document.createElement("button");
        promote.className = "btn btn-secondary";
        promote.type = "button";
        promote.textContent = "Promote";
        promote.onclick = async () => {
          promote.disabled = true;
          try {
            await runnerRegistryRequest("/runner/registry/promote", {
              id: runner.id,
              version: runner.candidate.version,
            });
            setStatus("Candidate promoted to stable.");
            await render();
          } catch (error) {
            setStatus(String(error?.message || error), true);
            promote.disabled = false;
          }
        };
        row.append(promote);
      }

      const rollback = document.createElement("button");
      rollback.className = "btn btn-secondary";
      rollback.type = "button";
      rollback.textContent = runner?.candidate ? "Discard" : "Rollback";
      rollback.onclick = async () => {
        rollback.disabled = true;
        try {
          await runnerRegistryRequest("/runner/registry/rollback", {id: runner.id});
          setStatus(runner?.candidate ? "Candidate discarded." : "Runner rolled back.");
          await render();
        } catch (error) {
          setStatus(String(error?.message || error), true);
          rollback.disabled = false;
        }
      };
      row.append(rollback);
      root.append(row);
    }

    restorePopupScroll(scrollTop);
  }

  async function handleCreateRunner(event) {
    event.preventDefault();
    const button = $("createRunnerButton");
    button.disabled = true;
    try {
      await runnerRegistryRequest("/runner/registry/create", {
        id: $("createRunnerId").value,
        name: $("createRunnerName").value,
        entrypoint: $("createRunnerEntrypoint").value,
        args: parseArgsJson($("createRunnerArgs").value),
        source: "user",
      });
      setStatus("Runner created.");
      event.target.reset();
      await render();
    } catch (error) {
      setStatus(String(error?.message || error), true);
    } finally {
      button.disabled = false;
    }
  }

  async function handleCreateRevision(event) {
    event.preventDefault();
    const button = $("createRevisionButton");
    button.disabled = true;
    try {
      await runnerRegistryRequest("/runner/registry/revision", {
        id: $("revisionRunnerId").value,
        entrypoint: $("revisionEntrypoint").value,
        args: parseArgsJson($("revisionArgs").value),
        source: "user",
      });
      setStatus("Candidate revision staged.");
      event.target.reset();
      await render();
    } catch (error) {
      setStatus(String(error?.message || error), true);
    } finally {
      button.disabled = false;
    }
  }

  async function getLiveActiveUrl(fallbackUrl) {
    let activeUrl = String(fallbackUrl || "");

    // Prefer the active tab in the focused browser window. If that tab is
    // outside PASI's ChatGPT runner target, fall back to any open ChatGPT tab
    // so opening the popup from an unrelated window does not deauthorize the
    // extension while an authorized runner tab is still open elsewhere.
    try {
      const tabs = await chrome.tabs.query({active: true, lastFocusedWindow: true});
      const tabUrl = String(tabs?.[0]?.url || "");
      if (tabUrl) activeUrl = tabUrl;
      if (isRunnerTargetUrl(activeUrl)) return activeUrl;

      const openTabs = await chrome.tabs.query({});
      const authorizedTab = openTabs.find((tab) => isRunnerTargetUrl(tab?.url));
      const authorizedUrl = String(authorizedTab?.url || "");
      if (authorizedUrl) activeUrl = authorizedUrl;
    } catch (_) {}

    return activeUrl;
  }

  async function render({clearStatus = false} = {}) {
    try {
      applyTheme();

      const [activeResult, runnerState, runnerRegistry] = await Promise.all([
        send(TYPES.active),
        getRunnerState(),
        getRunnerRegistry(),
      ]);

      // Resolve authorization from the live active tab on every render.
      const activeUrl = await getLiveActiveUrl(activeResult.url);
      const runnerSupported = isRunnerTargetUrl(activeUrl);
      const userscriptsMatched = hasMatchedUserscripts(activeResult);

      const currentUrl = $("currentUrl") || document.createElement("div");
      currentUrl.id = "currentUrl";
      currentUrl.textContent = activeUrl || "https://chatgpt.com/";
      currentUrl.hidden = true;
      if (!currentUrl.parentElement) document.body.append(currentUrl);

      renderRunnerDashboard(runnerState, runnerSupported, runnerRegistry);
      await renderRunnerRegistry(runnerRegistry);
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

  $("createRunnerForm")?.addEventListener("submit", (event) => {
    void handleCreateRunner(event);
  });
  $("createRevisionForm")?.addEventListener("submit", (event) => {
    void handleCreateRevision(event);
  });

  $("themeToggle").addEventListener("click", () => {
    void toggleTheme().catch((error) => setStatus(String(error?.message || error), true));
  });

  void render();

  // Keep the dashboard synchronized while the popup is open. Runner state is
  // durable on the bridge, but without polling the popup can display an old
  // "Running" or 0/20 snapshot after the process has already advanced/failed.
  const runnerRefreshTimer = setInterval(() => {
    void render();
  }, 750);
  window.addEventListener("unload", () => clearInterval(runnerRefreshTimer), {once: true});
})();
