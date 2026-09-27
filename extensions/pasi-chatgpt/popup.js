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
  const $ = (id) => document.getElementById(id);
  function send(type, payload = {}) {
    return new Promise((resolve, reject) => {
      chrome.runtime.sendMessage({type, ...payload}, (response) => {
        const error = chrome.runtime.lastError;
        if (error) return reject(new Error(error.message));
        if (!response?.ok) return reject(new Error(response?.error || "PASI request failed"));
        resolve(response);
      });
    });
  }
  function status(message, error = false) {
    $("status").textContent = message;
    $("status").style.color = error ? "#b91c1c" : "";
  }
  async function requestHosts(patterns) {
    const origins = [...new Set(patterns)];
    if (!origins.length) return false;
    return chrome.permissions.request({origins});
  }
  async function render() {
    try {
      const result = await send(TYPES.active);
      $("site").textContent = result.url || "No inspectable page";
      const root = $("scripts");
      root.replaceChildren();
      const menu = await send(TYPES.menu);
      if (menu.commands?.length) {
        const section = document.createElement("section");
        section.className = "card";
        const title = document.createElement("div");
        title.className = "name";
        title.textContent = "Userscript commands";
        section.append(title);
        for (const command of menu.commands) {
          const button = document.createElement("button");
          button.textContent = command.title;
          button.onclick = async () => {
            await send(TYPES.menuInvoke, {command_id: command.id});
            window.close();
          };
          section.append(button);
        }
        root.append(section);
      }
      if (!result.scripts?.length) {
        const empty = document.createElement("div");
        empty.textContent = "No PASI userscripts match this page.";
        root.append(empty);
        return;
      }
      for (const script of result.scripts) {
        const card = document.createElement("section");
        card.className = "card";
        const row = document.createElement("div");
        row.className = "row";
        const left = document.createElement("div");
        left.innerHTML = '<div class="name"></div><div class="meta"></div>';
        left.querySelector(".name").textContent = script.name;
        left.querySelector(".meta").textContent = script.enabled ? "Enabled" : "Disabled";
        row.append(left);
        const toggle = document.createElement("button");
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
          label.append(input);
          const text = document.createElement("span");
          text.textContent = pattern;
          label.append(text);
          hosts.append(label);
        }
        const apply = document.createElement("button");
        apply.textContent = "Apply site scope";
        apply.onclick = async () => {
          const allowed = [...hosts.querySelectorAll("input:checked")].map((input) => input.dataset.pattern);
          await send(TYPES.hosts, {id: script.id, host_allowlist: allowed});
          status("Site scope updated.");
          await render();
        };
        const grant = document.createElement("button");
        grant.textContent = "Grant checked hosts";
        grant.onclick = async () => {
          const origins = [...hosts.querySelectorAll("input:checked")].map((input) => input.dataset.pattern);
          const granted = await requestHosts(origins);
          status(granted ? "Host access granted." : "Host access declined.");
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
    } catch (error) {
      status(String(error.message || error), true);
    }
  }
  render();
})();
