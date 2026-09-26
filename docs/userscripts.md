# PASI userscript platform

PASI provides a native Chromium MV3 userscript platform built around the browser's User Scripts API. Chrome 120+ provides the USER_SCRIPT execution world, dedicated user-script messaging handlers, and durable registration support through the extension install/update lifecycle. Chrome 135+ additionally exposes userScripts.execute(), which PASI uses opportunistically for live-tab recovery after an extension context refresh. See the Chrome User Scripts API documentation.

## Runtime architecture

1. A privileged service worker owns registration, grants, storage, network requests, DNR rules, backups, VCS access, sync, and recovery.
2. A generated bootstrap wrapper exposes only the declared PASI/GM APIs and keeps the private broker authorization token inside its closure.
3. User code is injected as a separate code unit into Chrome's USER_SCRIPT world by default. MAIN is opt-in through the unsafeWindow/mainWorld grants.

The wrapper watches runtime.onDisconnect, catches runtime.lastError/context failures, rejects pending calls, and runs bounded lifecycle cleanup. pagehide/beforeunload cleanup removes value watchers and menu registrations on the service-worker side.

Chrome can invalidate an extension context during extension updates; PASI cannot prevent that browser event, so recovery is explicit. After reinstall/upgrade the service worker restores registered scripts, and on browsers with userScripts.execute() it also attempts a live-tab recovery injection.

## APIs

Supported metadata: @match, @exclude, @run-at, @grant, @connect, @noframes.

External @require and @resource are intentionally rejected so the runtime does not fetch arbitrary remote JavaScript.

Supported grants include storage, HTTP, tabs, menus, clipboard, notifications, downloads, unsafeWindow, mainWorld, and webRequest.

The runtime exposes GM_getValue / GM_setValue and related value APIs, GM_xmlhttpRequest, GM_fetch, GM_webRequest, GM_openInTab, GM_notification, GM_setClipboard, GM_download, GM_registerMenuCommand, PASIUserScript.network.*, and PASIUserScript.trackCleanup().

GM_fetch and HTTP APIs run through the service worker and enforce @connect. User-script request headers are normalized against the PASI HTTP contract before the privileged fetch.

## MV3 network rules

@grant webRequest can register declarative network rules. PASI translates user-script rule definitions into Chrome declarativeNetRequest dynamic rules and removes those rules when scripts are disabled, deleted, or replaced.

MV3 does not expose the old blocking webRequestBlocking model to ordinary extensions; declarativeNetRequest is the native mechanism for browser-side request blocking/modification.

PASI intentionally supports a bounded subset of DNR actions rather than exposing arbitrary interception logic.

## Storage, sync, and backups

Every userscript gets a private storage namespace. Chrome Sync is bounded to a safe subset of its approximately 100 KB total / 8 KB per-item limits. PASI stores the snapshot as several sub-8 KB chunks and now avoids rewriting unchanged chunks.

The backup format contains script source, authoring source when TypeScript is used, metadata, tags/groups, host scope, network rules, and script values.

Private broker authorization tokens are never included.

Sync conflicts return the local and cloud snapshots. The dashboard renders a per-script diff and lets the user select local or cloud before resolving. For source-level manual merging, the editor provides a side-by-side diff workflow.

## Remote backup

A provider abstraction currently ships with WebDAV support. Credentials are held in chrome.storage.session and are not included in backups.

The dashboard exposes endpoint and filename configuration, connection/auth status, push, pull, and automatic retry for transient connection failures.

Google Drive and Dropbox are intentionally provider slots rather than partial OAuth implementations; adding those providers should reuse the same snapshot contract.

## Developer tooling

The dashboard has a source editor with TypeScript mode, PASI/GM declarations in pasi-userscript.d.ts, and a modern ECMAScript ESLint environment.

The editor uses the full TypeScript compiler when src/vendor/typescript.js has been produced by the build tool. The repository pins TypeScript 7.0.2 and provides npm run build:userscript-compiler.

When that artifact is absent, PASI falls back to a small built-in TypeScript syntax lowering path so source-only development remains usable. The full compiler artifact is the intended distribution path.

## Repository integration

The editor supports GitHub and GitLab-compatible repositories: pull a script file, preview remote changes, apply remote changes, and push authored source as a repository commit.

Repository tokens are kept in extension session storage and are never exported with backups.

## Permissions

Host access is deliberately user-controlled. The extension keeps broad host patterns optional and requests them at runtime from the management UI. Chrome's Permissions API supports optional host permissions and runtime requests from user gestures.

The active-site popup shows which PASI scripts match the current tab, whether host access is granted, and which match patterns are currently allowed by that script's own host scope.

## Installation and performance

Remote script installation is serialized through an install queue. Downloads are capped at the PASI userscript source limit and pass through the same metadata validation/compiler/registration path as local installs.

Restore registers enabled scripts in batches. The service worker does not maintain a permanent page context, and user-script IPC only keeps bounded per-tab watcher/menu state while a script is connected.

## Limits and non-goals

PASI does not attempt to force JavaScript garbage collection because extension APIs do not provide a supported portable GC control. Memory reduction is therefore handled through smaller runtime state, teardown of broker registrations, bounded queues, and prompt cleanup of disconnected contexts.

The platform is intentionally native-Chromium first. Firefox/Safari portability is a separate transport/execution-world project.