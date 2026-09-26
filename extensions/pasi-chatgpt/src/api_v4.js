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
    }),
  });
})();