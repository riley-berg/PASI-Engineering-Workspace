(() => {
  "use strict";

  const base = globalThis.PASI;
  const c = globalThis.PASIExtensionAPIContract;
  if (!base || !c) throw new Error("PASI v4 requires the core API");

  function send(type, payload = {}) {
    return new Promise((resolve, reject) => {
      chrome.runtime.sendMessage({
        protocol_version: c.VERSION,
        type,
        ...payload,
      }, (response) => {
        const error = chrome.runtime.lastError;
        if (error) return reject(new Error(error.message));
        if (!response || response.ok !== true) {
          return reject(new Error(response?.error || "PASI userscript manager request failed"));
        }
        resolve(response);
      });
    });
  }

  const userScripts = Object.freeze({
    register(source, options = {}) {
      return send(c.MESSAGE_TYPES.USERSCRIPT_REGISTER, {
        source: String(source || ""),
        id: options.id,
        metadata: options.metadata,
        enabled: options.enabled !== false,
        replace: options.replace === true,
        tags: options.tags,
        group: options.group,
        typeScript: options.typeScript === true,
        host_allowlist: options.hostAllowlist,
        network_rules: options.networkRules,
      }).then((result) => result.script);
    },
    unregister(id) {
      return send(c.MESSAGE_TYPES.USERSCRIPT_UNREGISTER, {id: String(id)}).then((result) => result.removed === true);
    },
    list() {
      return send(c.MESSAGE_TYPES.USERSCRIPT_LIST).then((result) => result.scripts);
    },
    enable(id) {
      return send(c.MESSAGE_TYPES.USERSCRIPT_ENABLE, {id: String(id)}).then((result) => result.script);
    },
    disable(id) {
      return send(c.MESSAGE_TYPES.USERSCRIPT_DISABLE, {id: String(id)}).then((result) => result.script);
    },
    info(id) {
      return send(c.MESSAGE_TYPES.USERSCRIPT_INFO, {id: String(id)}).then((result) => result.script);
    },
    update(id, changes = {}) {
      return send(c.MESSAGE_TYPES.USERSCRIPT_UPDATE, {
        id: String(id),
        source: changes.source,
        metadata: changes.metadata,
        tags: changes.tags,
        group: changes.group,
        enabled: changes.enabled,
      }).then((result) => result.script);
    },
    backup() {
      return send(c.MESSAGE_TYPES.USERSCRIPT_BACKUP).then((result) => result.backup);
    },
    restore(backup, mode = "keep-local") {
      return send(c.MESSAGE_TYPES.USERSCRIPT_RESTORE, {backup, mode}).then((result) => result);
    },
    sync(mode = "preview") {
      return send(c.MESSAGE_TYPES.USERSCRIPT_SYNC, {mode}).then((result) => result);
    },
    networkAdd(id, rule) {
      return send(c.MESSAGE_TYPES.USERSCRIPT_NETWORK_ADD, {id: String(id), rule}).then((result) => result.rules);
    },
    networkRemove(id, ruleId) {
      return send(c.MESSAGE_TYPES.USERSCRIPT_NETWORK_REMOVE, {id: String(id), rule_id: String(ruleId)}).then((result) => result.rules);
    },
    networkList(id) {
      return send(c.MESSAGE_TYPES.USERSCRIPT_NETWORK_LIST, {id: String(id)}).then((result) => result.rules);
    },
    setHostAllowlist(id, hostAllowlist) {
      return send(c.MESSAGE_TYPES.USERSCRIPT_HOSTS, {
        id: String(id),
        host_allowlist: Array.isArray(hostAllowlist) ? hostAllowlist.map(String) : [],
      }).then((result) => result.script);
    },
    activeTab() {
      return send(c.MESSAGE_TYPES.USERSCRIPT_ACTIVE_TAB).then((result) => result);
    },
    source(id) {
      return send(c.MESSAGE_TYPES.USERSCRIPT_SOURCE_GET, {id: String(id)}).then((result) => result);
    },
    saveSource(id, source, options = {}) {
      return send(c.MESSAGE_TYPES.USERSCRIPT_SOURCE_SAVE, {
        id: String(id),
        source: String(source || ""),
        compile: options.compile !== false,
        typeScript: options.typeScript === true,
      }).then((result) => result.script);
    },
    vcsConfig(config = {}) {
      return send(c.MESSAGE_TYPES.USERSCRIPT_VCS_CONFIG, {config}).then((result) => result.config);
    },
    vcsFetch(id) {
      return send(c.MESSAGE_TYPES.USERSCRIPT_VCS_FETCH, {id: String(id)}).then((result) => result);
    },
    vcsPull(id) {
      return send(c.MESSAGE_TYPES.USERSCRIPT_VCS_PULL, {id: String(id)}).then((result) => result);
    },
    vcsPush(id, message) {
      return send(c.MESSAGE_TYPES.USERSCRIPT_VCS_PUSH, {id: String(id), message: String(message || "")}).then((result) => result);
    },
    syncStatus() {
      return send(c.MESSAGE_TYPES.USERSCRIPT_SYNC_STATUS).then((result) => result.status);
    },
  });

  globalThis.PASI = Object.freeze({
    ...base,
    userScripts,
    capabilities: Object.freeze({
      ...base.capabilities,
      userscript_manager: true,
      userscript_isolated_world: true,
      userscript_main_world_opt_in: true,
      userscript_grant_enforcement: true,
      userscript_ipc: true,
      userscript_metadata: true,
      userscript_backup_restore: true,
      userscript_sync: true,
      userscript_tags_groups: true,
      userscript_network_rules: true,
      userscript_context_recovery: true,
      userscript_host_scoping: true,
      userscript_editor: true,
      userscript_vcs: true,
      userscript_sync_status: true,
    }),
  });
})();