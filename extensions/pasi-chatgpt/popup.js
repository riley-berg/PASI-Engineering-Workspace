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
      title: "20-operation acceptance",
      executionMode: "supervised_m1",
      badgeClass: "m1",
    },
    "168h": {
      id: "168h",
      label: "168h",
      title: "long-run acceptance",
      executionMode: "supervised_168h",
      badgeClass: "long-run",
    },
  };

  const $ = (id) => document.getElementById(id);

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
    if (status === "Failed") return String(state?.stop_reason || state?.last_result || "Last run failed.");
    return "Ready to start.";
  }

  async function toggleRunner(profileId, state) {
    const activeProfile = activeProfileForState(state);
    if (runnerIsRunning(state) && activeProfile && activeProfile !== profileId) {
      setStatus(
        String(activeProfile).toUpperCase() + " is already running. Pause it before starting " +
        (profileId === "168h" ? "168h" : "M1") + ".",
        true
      );
      return;
    }

    const raw = await bridgeRequest("POST", "/runner/control", {
      action: "toggle",
      profile: profileId,
    });
    const result = parseBridgeResponse(raw);

    if (!result.accepted) {
      throw new Error(String(result.reason || "Runner action was rejected."));
    }

    setStatus(
      result.action === "stop"
        ? (profileId === "168h" ? "168h" : "M1") + " runner paused."
        : (profileId === "168h" ? "168h" : "M1") + " runner started."
    );
  }

  async function runNext(profileId) {
    setStatus("Dispatching one queued operation from " + (profileId === "168h" ? "168h" : "M1") + "…");
    const response = await new Promise((resolve, reject) => {
      chrome.runtime.sendMessage({type: "pasi.execution.run-next"}, (result) => {
        const runtimeError = chrome.runtime.lastError;
        if (runtimeError) {
          reject(new Error(runtimeError.message));
          return;
        }
        resolve(result || null);
      });
    });

    if (response?.dispatched) {
      setStatus("Dispatched " + response.operation_id + ".");
      return;
    }

    const reason = String(response?.reason || response?.error || "unknown result");
    if (reason === "no_queued_operation") {
      setStatus("No queued operation is waiting.", true);
    } else {
      setStatus("Run-next did not dispatch: " + reason, true);
    }
  }

  function createRunnerCard(profileId, state, selectedProfile) {
    const profile = RUNNER_PROFILES[profileId];
    const card = document.createElement("section");
    card.className = "runner-card " + (profileId === selectedProfile ? "selected" : "");
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
      ? "M1 · 20-op acceptance"
      : "168h · long-run";

    header.append(url, badge);

    const title = document.createElement("div");
    title.className = "card-title";
    title.textContent = profile.title;

    const body = document.createElement("div");
    body.className = "card-body";

    const stateLabelText = runnerStatusLabel(state, profileId);
    const statusText = document.createElement("div");
    statusText.className = "status-text " + stateLabelText.toLowerCase().replace(/[^a-z]+/g, "-");
    statusText.textContent =
      stateLabelText === "Running" ? "🟢 Running" :
      stateLabelText === "Paused" ? "🟡 Paused" :
      stateLabelText === "Failed" ? "🔴 Failed" :
      stateLabelText === "Completed" ? "🟢 Completed" :
      "🟢 Ready";

    const desc = document.createElement("div");
    desc.className = "status-desc";
    desc.textContent = runnerSummary(state, profileId);
    body.append(statusText, desc);

    const actions = document.createElement("div");
    actions.className = "card-actions";

    const toggle = document.createElement("button");
    toggle.className = "btn btn-primary";
    toggle.type = "button";
    const runningThisProfile = runnerIsRunning(state) && activeProfileForState(state) === profileId;
    toggle.textContent = runningThisProfile ? "Pause runner" : "Start runner";
    toggle.disabled = runnerIsRunning(state) && activeProfileForState(state) !== profileId;
    toggle.onclick = async () => {
      toggle.disabled = true;
      try {
        await toggleRunner(profileId, state);
        await render();
      } catch (error) {
        setStatus(String(error?.message || error), true);
      } finally {
        toggle.disabled = false;
      }
    };

    const runNextButton = document.createElement("button");
    runNextButton.className = "btn btn-secondary";
    runNextButton.type = "button";
    runNextButton.textContent = "Run next queued op";
    runNextButton.onclick = async () => {
      runNextButton.disabled = true;
      try {
        await runNext(profileId);
      } catch (error) {
        setStatus(String(error?.message || error), true);
      } finally {
        runNextButton.disabled = false;
      }
    };

    actions.append(toggle, runNextButton);
    card.append(header, title, body, actions);
    return card;
  }

  function renderRunnerDashboard(state, selectedProfile) {
    const root = $("runnerCards");
    root.replaceChildren();

    for (const profileId of Object.keys(RUNNER_PROFILES)) {
      root.append(createRunnerCard(profileId, state, selectedProfile));
    }

    const active = runnerIsRunning(state);
    $("connectionBadge").textContent = active ? "Active" : "Ready";
    $("connectionBadge").classList.toggle("active", active);
  }

  async function renderUserscripts(activeResult) {
    const root = $("scripts");
    root.replaceChildren();

    const menu = await send(TYPES.menu);
    if (menu.commands?.length) {
      const section = document.createElement("section");
      section.className = "card userscript-card";
      const title = document.createElement("div");
      title.className = "section-title";
      title.textContent = "Userscript commands";
      section.append(title);

      for (const command of menu.commands) {
        const button = document.createElement("button");
        button.className = "btn btn-secondary";
        button.type = "button";
        button.textContent = command.title;
        button.onclick = async () => {
          await send(TYPES.menuInvoke, {command_id: command.id});
          window.close();
        };
        section.append(button);
      }
      root.append(section);
    }

    if (!activeResult.scripts?.length) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      empty.textContent = "No PASI userscripts match this page.";
      root.append(empty);
      return;
    }

    for (const script of activeResult.scripts) {
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

  async function applyTheme() {
    const stored = await chrome.storage.local.get("pasi.popup.theme");
    const light = stored?.["pasi.popup.theme"] === "light";
    document.body.classList.toggle("light-theme", light);
    const button = $("themeToggle");
    button.textContent = light ? "☀️" : "🌙";
    button.title = light ? "Switch to dark theme" : "Switch to light theme";
    button.setAttribute("aria-label", button.title);
  }

  async function toggleTheme() {
    const light = !document.body.classList.contains("light-theme");
    document.body.classList.toggle("light-theme", light);
    await chrome.storage.local.set({"pasi.popup.theme": light ? "light" : "dark"});
    const button = $("themeToggle");
    button.textContent = light ? "☀️" : "🌙";
    button.title = light ? "Switch to dark theme" : "Switch to light theme";
    button.setAttribute("aria-label", button.title);
  }

  async function render() {
    try {
      const [activeResult, runnerState, selectedProfile] = await Promise.all([
        send(TYPES.active),
        getRunnerState(),
        getProfile(),
      ]);

      const activeUrl = activeResult.url || "https://chatgpt.com/";
      const currentUrl = $("currentUrl") || document.createElement("div");
      currentUrl.id = "currentUrl";
      currentUrl.textContent = activeUrl;
      currentUrl.hidden = true;
      if (!currentUrl.parentElement) document.body.append(currentUrl);

      renderRunnerDashboard(runnerState, selectedProfile);
      await renderUserscripts(activeResult);
      const warning = $("systemWarning");
      if (warning) {
        warning.hidden = Boolean(activeResult.scripts?.length);
      }
      await applyTheme();
      $("profileSummary").textContent =
        selectedProfile === "168h"
          ? "168h long-run · 20-operation acceptance"
          : "M1 · 20-operation acceptance";
      if (!$("status").classList.contains("error")) {
        setStatus("");
      }
    } catch (error) {
      setStatus(String(error?.message || error), true);
    }
  }

  $("themeToggle").addEventListener("click", () => {
    void toggleTheme().catch((error) => setStatus(String(error?.message || error), true));
  });

  $("profileSummary").addEventListener("click", async () => {
    try {
      const next = (await getProfile()) === "m1" ? "168h" : "m1";
      await setProfile(next);
      await render();
    } catch (error) {
      setStatus(String(error?.message || error), true);
    }
  });

  void render();
})();
