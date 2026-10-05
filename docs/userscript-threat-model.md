# PASI userscript threat boundaries

PASI treats every installed userscript as untrusted code.

## Trust zones

**Extension service worker:** privileged. It owns the registry, grants, storage namespaces, network brokerage, DNR rules, backups, VCS credentials, cloud credentials, and recovery.

**USER_SCRIPT world:** untrusted but brokered. User code cannot see the service worker directly. Every privileged request is checked against the stored script grant set and per-script authorization token.

**MAIN world:** explicitly dangerous. The actual user source runs in a private wrapper closure instead of receiving broker functions as globals. This preserves page-level execution capability while keeping the broker token and service-worker helpers lexically private. MAIN-world code is still visible to the host page by definition, so secrets must never be placed in userscript source or globals.

## Broker checks

1. a known script ID;
2. a matching private authorization token;
3. an enabled registry entry;
4. an authenticated userscript IPC port for the sending tab;
5. the requested API grant;
6. URL and host-scope checks for network operations.

Manager mutations additionally require the message to come from the extension context rather than the dedicated userscript RPC path.

## Privileged grants

@grant http gates brokered HTTP and GM_fetch.

@connect further constrains the destination of brokered HTTP.

@grant webRequest gates the declarative network-rule translator.

@grant tabs, clipboard, notification, download, menu, and storage each gate their corresponding broker RPCs.

unsafeWindow automatically opts the script into MAIN-world mode. This is an explicit trust downgrade and is never treated as equivalent to the default isolated world.

## Network rules

DNR rules are bounded per script, use deterministic extension-owned rule IDs, and are removed when scripts are disabled, deleted, replaced, or restored.

The implementation uses Chrome dynamic DNR rules rather than webRequestBlocking. Chrome documents dynamic rules as persistent and atomic, with separate quotas for safe/unsafe and regex-based rules.

## Credential boundaries

Userscript authorization tokens never enter backups or cloud sync.

GitHub/GitLab tokens are kept in extension session storage.

WebDAV credentials are kept in extension session storage.

Remote source is validated through the same metadata/compiler path as local source before registration.

## Context invalidation

Extension updates can invalidate running contexts. The wrapper catches runtime failures, rejects pending calls, disconnects its port, and reports lifecycle cleanup. The service worker restores registered scripts after extension install/update and attempts live-tab recovery where userScripts.execute() is available.

## Deliberate non-goals

PASI does not attempt to force JavaScript garbage collection because browser extensions do not have a portable supported GC API. Memory reduction is achieved with bounded queues, disconnected-port cleanup, storage namespaces, and removal of dynamic DNR state.

Firefox/Safari equivalents are a separate portability layer; the current contract is native Chromium MV3.
