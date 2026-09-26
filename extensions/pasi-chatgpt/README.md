# PASI ChatGPT Extension

This extension is self-contained. It does not depend on Tampermonkey, Greasemonkey, or another userscript manager.

## Native PASI API

The extension now provides an internal API layer over native Chromium extension primitives.

The content-controller surface is:

- `PASI.storage`: namespaced persistent key/value state.
- `PASI.http`: Promise-based HTTP requests through the background service worker.
- `PASI.retry`: bounded exponential-backoff retries.
- `PASI.sleep`: async timing primitive.
- `PASI.runtime.info()`: extension/runtime metadata.
- `PASI.events`: local event subscription and emission.

Example:

```javascript
await PASI.storage.set("operation", state, {namespace: "controller"});

const response = await PASI.http.request({
  method: "POST",
  url: "http://127.0.0.1:8765/event",
  headers: {"content-type": "application/json"},
  body: JSON.stringify(event),
});

await PASI.retry(() => doWork(), {
  attempts: 4,
  base_delay_ms: 250,
});
```

The HTTP broker is deliberately narrower than a generic `GM_xmlhttpRequest` implementation. It currently allows only the PASI local bridge origin, bounds timeouts and bodies, restricts headers, rejects redirects, and omits ambient credentials.

This layer is the foundation for migrating the strongest DOM-controller ideas from the abandoned `personal-ai-system` repository without bringing Tampermonkey or the abandoned extension architecture into the authoritative workspace.

The next controller work can build against the PASI API rather than calling raw `chrome.*` primitives throughout the DOM automation code.
