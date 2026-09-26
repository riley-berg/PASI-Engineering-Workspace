(() => {
  "use strict";

  const TYPES = {
    list: "pasi.userscript.list",
    enable: "pasi.userscript.enable",
    disable: "pasi.userscript.disable",
    update: "pasi.userscript.update",
    backup: "pasi.userscript.backup",
    restore: "pasi.userscript.restore",
    sync: "pasi.userscript.sync",
  };

  const state = {scripts: [], query: "", group: "", conflict: null};
  const $ = (id) => document.getElementById(id);

  function send(type, payload = {}) {
    return new Promise((resolve, reject) => {
      chrome.runtime.sendMessage({type, ...payload}, (response) => {
        const error = chrome.runtime.lastError;
        if (error) return reject(new Error(error.message));
        if (!response?.ok) return reject(new Error(response?.error || "PASI userscript request failed"));
        resolve(response);
      });
    });
  }

  function setStatus(message, error = false) {
    $("status").textContent = message;
    $("status").style.opacity = error ? "1" : ".8";
  }


  function showConflict(result) {
    state.conflict = result;
    const root = $("conflict");
    root.hidden = false;
    root.replaceChildren();
    const heading = document.createElement("div");
    heading.innerHTML = "<strong>Sync conflict</strong><div class=\"meta\">Choose local or cloud per changed script, then resolve.</div>";
    root.append(heading);
    const ids = [...new Set([...(result.diff?.added || []), ...(result.diff?.changed || []), ...(result.diff?.removed || [])])];
    for (const id of ids) {
      const local = (result.local?.scripts || []).find((item) => item.id === id);
      const remote = (result.remote?.scripts || []).find((item) => item.id === id);
      const item = document.createElement("div");
      item.className = "conflict-item";
      item.dataset.id = id;
      const title = document.createElement("div");
      title.textContent = remote?.name || local?.name || id;
      item.append(title);
      const select = document.createElement("select");
      select.innerHTML = '<option value="local">Keep local</option><option value="cloud">Use cloud</option>';
      item.append(select);
      if (local?.source || remote?.source) {
        const diff = document.createElement("pre");
        diff.className = "diff";
        const rows = globalThis.PASIUserScriptDiff.compare(local?.source || "", remote?.source || "");
        for (const row of rows) {
          const line = document.createElement("div");
          line.className = row.type === "add" ? "diff-add" : row.type === "remove" ? "diff-remove" : "";
          line.textContent = (row.type === "add" ? "+ " : row.type === "remove" ? "- " : "  ") + (row.type === "remove" ? row.left : row.right);
          diff.append(line);
        }
        item.append(diff);
      }
      root.append(item);
    }
    const actions = document.createElement("div");
    actions.className = "conflict-actions";
    const resolve = document.createElement("button");
    resolve.textContent = "Resolve sync";
    resolve.onclick = async () => {
      const decisions = {};
      root.querySelectorAll(".conflict-item").forEach((item) => {
        decisions[item.dataset.id] = item.querySelector("select").value;
      });
      await send("pasi.userscript.sync.resolve", {decisions});
      root.hidden = true;
      state.conflict = null;
      setStatus("Sync conflict resolved.");
      await refresh();
    };
    actions.append(resolve);
    root.append(actions);
  }

  function renderGroups() {
    const select = $("group");
    const current = select.value;
    const groups = [...new Set(state.scripts.map((s) => String(s.group || "").trim()).filter(Boolean))].sort();
    select.replaceChildren(new Option("All groups", ""));
    for (const group of groups) select.append(new Option(group, group));
    select.value = groups.includes(current) ? current : "";
  }

  function visibleScripts() {
    const q = state.query.toLowerCase();
    return state.scripts.filter((script) => {
      if (state.group && script.group !== state.group) return false;
      if (!q) return true;
      return [
        script.id, script.name, script.description, script.group,
        ...(script.tags || []), ...(script.matches || []),
      ].join(" ").toLowerCase().includes(q);
    });
  }

  function render() {
    renderGroups();
    const root = $("scripts");
    root.replaceChildren();
    for (const script of visibleScripts()) {
      const card = document.createElement("article");
      card.className = "card";
      const tags = (script.tags || []).map((tag) => '<span class="tag"></span>').join("");
      card.innerHTML = `
        <h2></h2>
        <div class="meta"></div>
        <div class="tags">${tags}</div>
        <div class="hosts"></div>
        <div class="actions">
          <button data-action="toggle">${script.enabled ? "Disable" : "Enable"}</button>
          <button data-action="edit">Edit</button>
          <button data-action="host">Host access</button>
          <button data-action="tag">Edit tags</button>
          <button data-action="remove">Remove</button>
        </div>`;
      card.querySelector("h2").textContent = script.name;
      card.querySelector(".meta").textContent = `${script.id} · ${script.version} · ${script.group || "ungrouped"}`;
      card.querySelectorAll(".tag").forEach((node, i) => node.textContent = script.tags?.[i] || "");
      card.querySelector(".hosts").textContent = (script.matches || []).join(", ");
      if (!script.hosts_granted) {
        const warning = document.createElement("div");
        warning.className = "warning";
        warning.textContent = "Host access has not been granted for every match pattern.";
        card.append(warning);
      }
      card.querySelector('[data-action="toggle"]').onclick = async () => {
        await send(script.enabled ? TYPES.disable : TYPES.enable, {id: script.id});
        await refresh();
      };
      card.querySelector('[data-action="edit"]').onclick = () => {
        window.open("editor.html?id=" + encodeURIComponent(script.id), "_blank");
      };
      card.querySelector('[data-action="host"]').onclick = async () => {
        await requestHostAccess(script);
      };
      card.querySelector('[data-action="tag"]').onclick = async () => {
        const tagsInput = prompt("Comma-separated tags:", (script.tags || []).join(", "));
        if (tagsInput === null) return;
        const groupInput = prompt("Group:", script.group || "");
        await send(TYPES.update, {id: script.id, tags: tagsInput.split(",").map((v) => v.trim()).filter(Boolean), group: groupInput || ""});
        await refresh();
      };
      card.querySelector('[data-action="remove"]').onclick = async () => {
        if (!confirm(`Remove ${script.name}?`)) return;
        await send("pasi.userscript.unregister", {id: script.id});
        await refresh();
      };
      root.append(card);
    }
    if (!visibleScripts().length) root.textContent = "No matching userscripts.";
  }

  async function requestHostAccess(script) {
    const normalized = [...new Set(script.hosts || script.matches || [])];
    try {
      const granted = await chrome.permissions.request({origins: normalized});
      setStatus(granted ? "Host access granted." : "Host access was declined.");
      await refresh();
    } catch (error) {
      setStatus(String(error.message || error), true);
    }
  }

  async function refresh() {
    const result = await send(TYPES.list);
    state.scripts = result.scripts || [];
    render();
  }


  $("cloud-save").onclick = async () => {
    try {
      const result = await send("pasi.userscript.cloud.config", {config: {
        provider: "webdav",
        endpoint: $("cloud-endpoint").value,
        filename: $("cloud-file").value,
        username: $("cloud-user").value,
        password: $("cloud-pass").value,
      }});
      $("cloud-state").textContent = result.credentials_set ? "credentials saved for this session" : "endpoint saved";
      $("cloud-pass").value = "";
    } catch (error) { setStatus(String(error.message || error), true); }
  };
  $("cloud-status").onclick = async () => {
    try {
      const result = await send("pasi.userscript.cloud.status");
      $("cloud-state").textContent = result.status?.state || "idle";
    } catch (error) { setStatus(String(error.message || error), true); }
  };
  $("cloud-push").onclick = async () => {
    try {
      const result = await send("pasi.userscript.cloud.push");
      $("cloud-state").textContent = result.status?.state || "error";
    } catch (error) { setStatus(String(error.message || error), true); }
  };
  $("cloud-pull").onclick = async () => {
    try {
      const result = await send("pasi.userscript.cloud.pull");
      $("cloud-state").textContent = result.status?.state || "error";
      if (result.ok) await refresh();
    } catch (error) { setStatus(String(error.message || error), true); }
  };

  $("search").oninput = (event) => { state.query = event.target.value; render(); };
  $("group").onchange = (event) => { state.group = event.target.value; render(); };
  $("reload").onclick = () => refresh().catch((e) => setStatus(String(e), true));
  $("backup").onclick = async () => {
    const result = await send(TYPES.backup);
    const blob = new Blob([JSON.stringify(result.backup, null, 2)], {type: "application/json"});
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = "pasi-userscripts-backup.json"; a.click();
    URL.revokeObjectURL(url);
    setStatus("Backup exported.");
  };
  $("restore-file").onchange = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    const text = await file.text();
    const backup = JSON.parse(text);
    const mode = prompt("Restore mode: replace or keep-local", "keep-local");
    if (!mode) return;
    const result = await send(TYPES.restore, {backup, mode});
    setStatus(`Restore complete: ${result.imported} scripts imported.`);
    await refresh();
  };
  $("install").onclick = async () => {
    const url = String($("install-url").value || "").trim();
    if (!url) return;
    try {
      const result = await send("pasi.userscript.install", {url});
      setStatus(`Installed ${result.script?.name || "userscript"}.`);
      $("install-url").value = "";
      await refresh();
    } catch (error) {
      setStatus(String(error.message || error), true);
    }
  };
  $("sync").onclick = async () => {
    let result = await send(TYPES.sync);
    if (result.status === "conflict") {
      showConflict(result);
      setStatus("Review the visual diff before resolving.");
    } else {
      setStatus(`Sync complete: ${result.status}.`);
    }
    await refresh();
  };


  send("pasi.userscript.cloud.status").then((result) => {
    if (result.status?.state) $("cloud-state").textContent = result.status.state;
  }).catch(() => undefined);
  refresh().catch((error) => setStatus(String(error.message || error), true));
})();
