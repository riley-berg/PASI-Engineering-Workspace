# PASI browser API

The PASI extension is self-contained. It does not depend on Tampermonkey or Greasemonkey.

The API borrows the useful shape of userscript managers but uses native Chromium extension primitives and a narrower security model.

## Data

Use PASI.storage for namespaced persistent state. It supports get, set, remove, list, quota profiling, and cross-tab change notifications.

For large datasets, PASI.db wraps extension IndexedDB using structured-clone storage.

PASI.codec.register provides opt-in application type serialization for classes that need reconstruction after storage.

## Network

PASI.http.request provides Promise-based fetch-style requests through the service worker. Origins are granted at runtime using the browser permission system.

PASI.http.stream exposes response-body streaming through a ReadableStream.

PASI.web.observe provides request/complete/error metadata. PASI.web.rules uses declarative network rules for supported request/response header modifications. Arbitrary response-body interception is intentionally not exposed.

## DOM

PASI.dom.addElement and PASI.dom.addStyle provide controlled DOM helpers.

PASI.dom.waitFor and PASI.dom.onPresent implement an element-present style lifecycle without forcing every controller to maintain interval loops.

Shadow DOM roots can be requested when UI isolation is needed.

## Browser/system

PASI.tabs supports open, close, focus, send, broadcast, list, and subscriptions.

PASI.notifications provides native notifications. PASI.downloads supports relative download paths, including subdirectories below Downloads. PASI.copyText provides a user-gesture-friendly text copy primitive; ambient reads are intentionally not exposed.

Dynamic context menus are exposed by PASI.menu.

## Permissions and scripts

PASI.permissions wraps Chrome runtime permission checks, requests, and removals.

PASI.scripts wraps dynamic content-script registration. Scripts must remain extension-local, and host access remains permission-gated.

## Reliability

All APIs are Promise-first. Network operations have bounded timeouts and size limits. Storage is namespaced. Cross-tab messaging is explicit.

The next controller work can migrate the strongest old PASI DOM-controller primitives against this API without bringing back the abandoned controller or adding a userscript-manager dependency.

## API v3 improvements

The v3 layer adds the improvements that motivated the PASI-native API:

- structured values for Date, RegExp, Map, Set, ArrayBuffer, typed arrays, BigInt, and registered application classes;
- `PASI.http.fetch()` with a Fetch-style `Response`, plus streaming `Response.body`;
- high-level `PASI.http.rules.modifyHeaders()` for declarative request/response header changes;
- optional request/response header observation through `PASI.web.observe({include_headers: true}, listener)`;
- menu controls for text, textarea, number, checkbox, and select inputs rendered in an isolated Shadow DOM panel;
- automatic `element-present` script execution through `PASI.scripts.register({runAt: {type: "element-present", selector: "..."}, ...})`;
- explicit runtime `PASI.scripts.execute()` for extension-local files;
- a capability manifest exposed through `PASI.capabilities`.

Chrome MV3 scripting itself provides document-start, document-end, and document-idle registration phases. PASI implements `element-present` as a bounded page-local lifecycle helper on top of the DOM observer rather than pretending Chrome provides a native fourth run phase.

Network header modification uses Chrome declarative network rules, which support modifying request and response headers without exposing raw response bodies to the extension.

Downloads accept relative paths beneath the browser Downloads directory, including subdirectories; parent traversal is rejected.


## Native userscript architecture

PASI now implements a three-tier userscript boundary using Chrome's native MV3 User Scripts API rather than a userscript-manager dependency.

### 1. Privileged core

`src/background-userscripts.js` owns the userscript registry, dynamic registration, grant checks, per-script storage, cross-context messaging, menus, downloads, notifications, clipboard mediation, and cross-origin HTTP.

Each registered script receives a stable PASI script identity. Privileged RPCs are accepted only through the dedicated User Scripts messaging channel and are checked against the stored grants.

### 2. Injection / wrapper layer

`src/userscript_runtime.js` builds the code injected by `chrome.userScripts.register()`.

The wrapper establishes a private IPC channel, exposes the declared PASI/GM-compatible APIs, forwards storage/network/system operations to the privileged core, and records script errors without giving the page direct access to extension APIs.

PASI accepts the useful userscript metadata model:

- `@match`, `@exclude`, `@run-at`, `@grant`, `@connect`, and `@noframes`;
- `@include` is rejected in favor of explicit match patterns;
- external `@require` and `@resource` loading is rejected; dependencies must be bundled;
- supported grants are explicit and enforced by the service worker.

### 3. Isolated userscript world

By default, scripts run in Chrome's `USER_SCRIPT` execution world. This is isolated from the host page JavaScript while retaining DOM access.

A script that requests `@grant unsafeWindow` must also opt into `@grant mainWorld`; PASI then registers the whole script in Chrome's `MAIN` world. That boundary is intentionally explicit because it removes the normal JavaScript isolation.

The wrapper exposes compatible aliases including `GM_getValue`, `GM_setValue`, `GM_addValueChangeListener`, `GM_registerMenuCommand`, `GM_notification`, `GM_download`, `GM_setClipboard`, and `GM_xmlhttpRequest`. PASI still enforces the same native grant and network-origin rules behind those aliases.

### Durable registration

Chrome clears dynamically registered user scripts when an extension updates, so PASI persists its registry in `chrome.storage.local` and restores enabled scripts from the service worker's install/update lifecycle.

This architecture gives PASI the useful Tampermonkey-style separation—privileged core, injection wrapper, isolated script runtime—without making Tampermonkey itself part of the system.
