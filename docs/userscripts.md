# PASI userscript product surface

PASI uses Chrome's native MV3 User Scripts API. The default execution world is `USER_SCRIPT`; requesting `@grant unsafeWindow` also requires `@grant mainWorld` and moves the script to Chrome's `MAIN` world.

## Developer experience

The extension ships `pasi-userscript.d.ts` with declarations for the PASI and GM-compatible APIs, plus `.eslintrc.userscript.json` for modern ECMAScript linting without a custom global list.

External `@require` and `@resource` dependencies remain intentionally unsupported. Bundle dependencies before registration so runtime code is deterministic and does not depend on remote JavaScript.

A full TypeScript compiler is not embedded in the runtime. The declarations provide editor completion and type checking; compilation is a build-time concern rather than executable extension payload.

## Performance

PASI registers enabled scripts in batches during restore instead of performing one registration call per script. It also avoids the repeated external-`@require` source scanning that can make multi-frame user-script injection expensive.

## State, backup, and sync

PASI stores script definitions in `chrome.storage.local` and user values under an isolated per-script namespace. The dashboard can export a versioned JSON backup containing script source, metadata, tags, groups, and stored values.

Chrome Sync is optional and intentionally bounded. PASI chunks sync snapshots below Chrome Sync's per-item quota and caps the aggregate snapshot at 90 KB. When local and synced definitions differ, the dashboard shows a conflict state and lets the user choose `replace` or `keep-local`.

## Management UI

Open the extension options page to search, filter by group, enable/disable, remove, tag, and group scripts. The host-access action requests only the origins derived from the script's match/connect declarations.

Host permissions are separate from the userscript registry. A registered script can remain disabled until the required host access is granted.

## Safety boundaries

The privileged bootstrap and the userscript source are injected as separate JavaScript units. The private RPC authorization token stays inside the bootstrap closure and is never part of the userscript's lexical environment.

- Privileged APIs are mediated by the service worker.
- Each script gets a private authorization token that is not exposed in the management UI or backup.
- `@connect` is enforced before cross-origin HTTP.
- `unsafeWindow` is an explicit opt-in to the host page world.
- Sync and backup omit the private authorization token.
