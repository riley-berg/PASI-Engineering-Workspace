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

PASI.notifications provides native notifications. PASI.downloads supports relative download paths, including subdirectories below Downloads.

Dynamic context menus are exposed by PASI.menu.

## Permissions and scripts

PASI.permissions wraps Chrome runtime permission checks, requests, and removals.

PASI.scripts wraps dynamic content-script registration. Scripts must remain extension-local, and host access remains permission-gated.

## Reliability

All APIs are Promise-first. Network operations have bounded timeouts and size limits. Storage is namespaced. Cross-tab messaging is explicit.

The next controller work can migrate the strongest old PASI DOM-controller primitives against this API without bringing back the abandoned controller or adding a userscript-manager dependency.
