(() => {
  "use strict";

  const base = globalThis.PASI;
  const c = globalThis.PASIExtensionAPIContract;
  if (!base || !c) throw new Error("PASI v3 requires the core API");

  const STRUCTURED_TAG = "$pasi_structured_v1";
  const structuredTypes = new Map();

  function structuredEncode(value, seen = new WeakSet()) {
    if (value === null || typeof value === "string" || typeof value === "boolean" || typeof value === "number") return value;
    if (typeof value === "bigint") return {[STRUCTURED_TAG]: "bigint", value: String(value)};
    if (value === undefined) return {[STRUCTURED_TAG]: "undefined"};
    if (typeof value !== "object") throw new TypeError("Unsupported PASI structured value");
    if (seen.has(value)) throw new TypeError("Cyclic PASI structured values are not supported");
    seen.add(value);

    if (value instanceof Date) return {[STRUCTURED_TAG]: "date", value: value.toISOString()};
    if (value instanceof RegExp) return {[STRUCTURED_TAG]: "regexp", source: value.source, flags: value.flags};
    if (value instanceof Map) {
      return {[STRUCTURED_TAG]: "map", value: [...value.entries()].map(([k, v]) => [structuredEncode(k, seen), structuredEncode(v, seen)])};
    }
    if (value instanceof Set) {
      return {[STRUCTURED_TAG]: "set", value: [...value.values()].map((item) => structuredEncode(item, seen))};
    }
    if (value instanceof ArrayBuffer) {
      return {[STRUCTURED_TAG]: "arraybuffer", value: btoa(String.fromCharCode(...new Uint8Array(value)))};
    }
    if (ArrayBuffer.isView(value)) {
      return {
        [STRUCTURED_TAG]: "typedarray",
        constructor: value.constructor.name,
        value: btoa(String.fromCharCode(...new Uint8Array(value.buffer.slice(value.byteOffset, value.byteOffset + value.byteLength)))),
      };
    }
    if (Array.isArray(value)) return value.map((item) => structuredEncode(item, seen));
    const output = {};
    for (const [key, child] of Object.entries(value)) output[key] = structuredEncode(child, seen);
    return output;
  }

  function structuredDecode(value) {
    if (Array.isArray(value)) return value.map(structuredDecode);
    if (!value || typeof value !== "object") return value;
    if (value[STRUCTURED_TAG] === "undefined") return undefined;
    if (value[STRUCTURED_TAG] === "custom") {
      const definition = structuredTypes.get(value.type);
      if (!definition) throw new TypeError("Unknown PASI structured type: " + value.type);
      return definition.deserialize(structuredDecode(value.value));
    }
    if (value[STRUCTURED_TAG] === "bigint") return BigInt(value.value);
    if (value[STRUCTURED_TAG] === "date") return new Date(value.value);
    if (value[STRUCTURED_TAG] === "regexp") return new RegExp(value.source, value.flags);
    if (value[STRUCTURED_TAG] === "map") return new Map(value.value.map(([k, v]) => [structuredDecode(k), structuredDecode(v)]));
    if (value[STRUCTURED_TAG] === "set") return new Set(value.value.map(structuredDecode));
    if (value[STRUCTURED_TAG] === "arraybuffer") {
      const raw = atob(value.value);
      const buffer = new ArrayBuffer(raw.length);
      const bytes = new Uint8Array(buffer);
      for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i);
      return buffer;
    }
    if (value[STRUCTURED_TAG] === "typedarray") {
      const raw = atob(value.value);
      const bytes = new Uint8Array(raw.length);
      for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i);
      const constructors = {
        Uint8Array, Uint8ClampedArray, Uint16Array, Uint32Array,
        Int8Array, Int16Array, Int32Array, Float32Array, Float64Array,
        BigInt64Array, BigUint64Array,
      };
      const Ctor = constructors[value.constructor];
      if (!Ctor) throw new TypeError("Unsupported stored typed array: " + value.constructor);
      return new Ctor(bytes.buffer);
    }
    const output = {};
    for (const [key, child] of Object.entries(value)) output[key] = structuredDecode(child);
    return output;
  }

  const storage = {
    get(key, options = {}) {
      return base.storage.get(key, options).then(structuredDecode);
    },
    set(key, value, options = {}) {
      return base.storage.set(key, structuredEncode(value), options);
    },
    remove: base.storage.remove,
    list: base.storage.list,
    info: base.storage.info,
    watch: base.storage.watch,
  };

  function normalizeFetchInput(input, init = {}) {
    if (typeof input === "string" || input instanceof URL) {
      return {
        url: input.toString(),
        ...init,
      };
    }
    if (input instanceof Request) {
      return {
        url: input.url,
        method: init.method || input.method,
        headers: init.headers || Object.fromEntries(input.headers.entries()),
        body: init.body ?? null,
        timeout_ms: init.timeout_ms,
      };
    }
    if (input && typeof input === "object" && input.url) return {...input, ...init};
    throw new TypeError("PASI.http.fetch requires a URL or Request");
  }

  function streamResponse(options = {}) {
    const port = chrome.runtime.connect({name: "pasi.http.stream"});
    const url = c.normalizeUrl(options.url).toString();
    let metadata = null;
    let metadataResolve;
    let metadataReject;
    const metadataPromise = new Promise((resolve, reject) => {
      metadataResolve = resolve;
      metadataReject = reject;
    });
    let ended = false;
    let failure = null;
    const queue = [];
    let wake;

    port.onMessage.addListener((message) => {
      if (message.type === "headers") {
        metadata = message;
        metadataResolve(message);
      } else if (message.type === "chunk") {
        queue.push(message.chunk);
      } else if (message.type === "done") {
        ended = true;
      } else if (message.type === "error") {
        failure = new Error(message.error || "PASI HTTP stream failed");
        ended = true;
        metadataReject(failure);
      }
      wake?.();
    });

    port.onDisconnect.addListener(() => {
      if (!ended) {
        failure = new Error("PASI HTTP stream disconnected");
        metadataReject(failure);
        ended = true;
        wake?.();
      }
    });

    port.postMessage({
      type: c.MESSAGE_TYPES.HTTP_STREAM_START,
      method: c.normalizeMethod(options.method || "GET"),
      url,
      headers: c.normalizeHeaders(options.headers),
      body: options.body == null ? null : String(options.body),
      timeout_ms: c.normalizeTimeout(options.timeout_ms),
    });

    const body = new ReadableStream({
      async pull(controller) {
        while (!queue.length && !ended && !failure) {
          await new Promise((resolve) => { wake = resolve; });
        }
        wake = null;
        if (failure) {
          controller.error(failure);
          port.disconnect();
          return;
        }
        if (queue.length) {
          controller.enqueue(queue.shift());
          return;
        }
        controller.close();
        port.disconnect();
      },
      cancel() {
        ended = true;
        port.disconnect();
      },
    });

    return metadataPromise.then((meta) => new Response(body, {
      status: meta.status,
      headers: meta.headers,
    }));
  }

  const http = Object.freeze({
    ...base.http,
    async fetch(input, init = {}) {
      const options = normalizeFetchInput(input, init);
      if (options.stream === true || init.stream === true) return streamResponse(options);
      const response = await base.http.request(options);
      const body = await response.text();
      return new Response(body, {
        status: response.status,
        headers: response.headers,
      });
    },
    streamResponse,
    rules: Object.freeze({
      add: base.web.rules.add,
      remove: base.web.rules.remove,
      list: base.web.rules.list,
      async modifyHeaders(options = {}) {
        const requestHeaders = Array.isArray(options.requestHeaders) ? options.requestHeaders : [];
        const responseHeaders = Array.isArray(options.responseHeaders) ? options.responseHeaders : [];
        if (!requestHeaders.length && !responseHeaders.length) throw new TypeError("No header modifications supplied");
        return base.web.rules.add({
          id: options.id,
          priority: Number(options.priority) || 1,
          action: {type: "modifyHeaders", requestHeaders, responseHeaders},
          condition: {
            urlFilter: String(options.urlFilter || ""),
            resourceTypes: options.resourceTypes || ["xmlhttprequest", "fetch"],
            initiatorDomains: options.initiatorDomains,
            requestDomains: options.requestDomains,
          },
        });
      },
    }),
  });

  function normalizeControl(control) {
    const item = {...control};
    item.id = String(item.id || crypto.randomUUID());
    item.type = ["text", "number", "checkbox", "select", "textarea"].includes(item.type) ? item.type : "text";
    item.label = String(item.label || item.id).slice(0, 120);
    if (item.type === "select") {
      item.options = Array.isArray(item.options) ? item.options.map((option) => ({
        value: String(option.value),
        label: String(option.label ?? option.value),
      })) : [];
    }
    return item;
  }

  async function openControlPanel(controls, commandEvent, handler) {
    const host = base.dom.addElement("div", {shadow: true, attributes: {"aria-label": "PASI command controls"}});
    const root = host.shadowRoot;
    if (!root) throw new Error("PASI command controls require an open ShadowRoot");

    const styles = document.createElement("style");
    styles.textContent = [
      ":host{all:initial}", "section{font:13px system-ui;padding:14px;background:#fff;color:#111;border:1px solid #999;border-radius:10px;box-shadow:0 8px 30px #0003;position:fixed;top:12px;right:12px;z-index:2147483647;min-width:280px}",
      "label{display:block;font-weight:600;margin:8px 0 4px}", "input,textarea,select{box-sizing:border-box;width:100%;padding:7px;border:1px solid #aaa;border-radius:6px}",
      ".row{display:flex;gap:8px;justify-content:flex-end;margin-top:12px}", "button{padding:7px 12px;border-radius:6px;border:1px solid #888;background:#f5f5f5;cursor:pointer}",
    ].join("");

    const section = document.createElement("section");
    section.appendChild(styles);
    const title = document.createElement("div");
    title.textContent = commandEvent?.info?.menuItemId ? "PASI command" : "PASI controls";
    title.style.fontWeight = "700";
    section.appendChild(title);

    const fields = new Map();
    for (const control of controls) {
      const label = document.createElement("label");
      label.textContent = control.label;
      let input;
      if (control.type === "select") {
        input = document.createElement("select");
        for (const option of control.options) {
          const node = document.createElement("option");
          node.value = option.value;
          node.textContent = option.label;
          if (option.value === String(control.default ?? "")) node.selected = true;
          input.appendChild(node);
        }
      } else if (control.type === "textarea") {
        input = document.createElement("textarea");
        input.value = String(control.default ?? "");
        input.rows = Number(control.rows) || 4;
      } else {
        input = document.createElement("input");
        input.type = control.type;
        if (control.type === "checkbox") input.checked = control.default === true;
        else input.value = String(control.default ?? "");
        if (control.placeholder) input.placeholder = String(control.placeholder);
      }
      input.name = control.id;
      label.appendChild(input);
      section.appendChild(label);
      fields.set(control.id, {control, input});
    }

    const row = document.createElement("div");
    row.className = "row";
    const cancel = document.createElement("button");
    cancel.textContent = "Cancel";
    const run = document.createElement("button");
    run.textContent = "Run";
    row.append(cancel, run);
    section.appendChild(row);
    root.appendChild(section);

    return new Promise((resolve) => {
      cancel.onclick = () => {
        host.remove();
        resolve(false);
      };
      run.onclick = async () => {
        const values = {};
        for (const [id, field] of fields) {
          values[id] = field.control.type === "checkbox" ? field.input.checked : field.input.value;
        }
        host.remove();
        await handler({...commandEvent, controls: values});
        resolve(true);
      };
    });
  }

  const clipboard = Object.freeze({
    writeText(value) {
      const text = String(value ?? "");
      const area = document.createElement("textarea");
      area.value = text;
      area.setAttribute("readonly", "");
      area.style.position = "fixed";
      area.style.left = "-10000px";
      area.style.top = "-10000px";
      (document.body || document.documentElement).appendChild(area);
      area.focus();
      area.select();
      let copied = false;
      try {
        copied = document.execCommand("copy");
      } finally {
        area.remove();
      }
      if (!copied) {
        return Promise.reject(new Error("PASI clipboard write was rejected by the browser"));
      }
      return Promise.resolve({ok: true});
    },
    readText() {
      return Promise.reject(new Error("PASI intentionally does not expose ambient clipboard reads"));
    },
  });

  const tabs = Object.freeze({
    ...base.tabs,
    channel(tabId) {
      const id = Number(tabId);
      if (!Number.isInteger(id) || id <= 0) throw new TypeError("Invalid PASI tab id");
      return Object.freeze({
        send(payload) {
          return base.tabs.send(id, payload);
        },
        onMessage(listener) {
          const off = base.tabs.onMessage(listener);
          return off;
        },
        close() {
          return base.tabs.close(id);
        },
        focus() {
          return base.tabs.focus(id);
        },
      });
    },
  });

  const web = Object.freeze({
    ...base.web,
    async observe(options = {}, listener) {
      if (typeof listener !== "function") throw new TypeError("PASI.web.observe needs a listener");
      const observerId = options.requestId || crypto.randomUUID();
      const result = await new Promise((resolve, reject) => {
        chrome.runtime.sendMessage({
          protocol_version: c.VERSION,
          type: c.MESSAGE_TYPES.WEB_OBSERVE,
          urls: options.urls || [],
          types: options.types || [],
          requestId: observerId,
          include_headers: options.include_headers === true,
        }, (response) => {
          const error = chrome.runtime.lastError;
          if (error) reject(new Error(error.message));
          else if (!response || response.ok !== true) reject(new Error(response?.error || "PASI web observer failed"));
          else resolve(response);
        });
      });
      const eventName = "pasi-web-event:" + result.observer_id;
      const handler = (event) => listener(event.detail);
      globalThis.addEventListener(eventName, handler);
      return {
        observer_id: result.observer_id,
        stop: async () => {
          globalThis.removeEventListener(eventName, handler);
          await new Promise((resolve, reject) => {
            chrome.runtime.sendMessage({
              protocol_version: c.VERSION,
              type: c.MESSAGE_TYPES.WEB_UNOBSERVE,
              observer_id: result.observer_id,
            }, (response) => {
              const error = chrome.runtime.lastError;
              if (error) reject(new Error(error.message));
              else if (!response || response.ok !== true) reject(new Error(response?.error || "PASI web observer stop failed"));
              else resolve(response);
            });
          });
        },
      };
    },
  });

  const menu = Object.freeze({
    async register(options, handler) {
      const controls = Array.isArray(options?.controls) ? options.controls.map(normalizeControl) : [];
      if (!controls.length) return base.menu.register(options, handler);
      return base.menu.register({...options, title: options.title}, async (event) => {
        await openControlPanel(controls, event, handler);
      });
    },
    unregister: base.menu.unregister,
    list: base.menu.list,
  });

  const scripts = Object.freeze({
    async register(options) {
      if (!options || typeof options !== "object") throw new TypeError("PASI.scripts.register needs options");
      const runAt = options.runAt;
      if (!runAt || typeof runAt === "string") return base.scripts.register(options);

      if (runAt.type !== "element-present" || !runAt.selector) {
        throw new TypeError("Unsupported PASI runAt object; use {type:'element-present', selector:'...'}");
      }

      const files = Array.isArray(options.js) ? options.js.map(String) : [];
      if (!files.length || files.some((file) => file.startsWith("/") || file.includes(".."))) {
        throw new TypeError("PASI element-present scripts require extension-local JS files");
      }
      let active = true;
      const stop = base.dom.onPresent(runAt.selector, async () => {
        if (!active) return;
        await new Promise((resolve, reject) => {
          chrome.runtime.sendMessage({
            protocol_version: c.VERSION,
            type: c.MESSAGE_TYPES.SCRIPT_EXECUTE,
            files,
            tab_id: null,
            world: options.world || "ISOLATED",
            inject_immediately: true,
          }, (result) => {
            const error = chrome.runtime.lastError;
            if (error) reject(new Error(error.message));
            else if (!result || result.ok !== true) reject(new Error(result?.error || "PASI script execution failed"));
            else resolve(result);
          });
        });
      }, {timeout_ms: options.timeout_ms || 300000});
      return {
        id: String(options.id || crypto.randomUUID()),
        kind: "element-present",
        selector: runAt.selector,
        stop: () => { active = false; stop(); },
      };
    },
    execute(files, options = {}) {
      return new Promise((resolve, reject) => {
        chrome.runtime.sendMessage({
          protocol_version: c.VERSION,
          type: c.MESSAGE_TYPES.SCRIPT_EXECUTE,
          files,
          tab_id: options.tabId ?? null,
          world: options.world || "ISOLATED",
          inject_immediately: options.injectImmediately !== false,
        }, (result) => {
          const error = chrome.runtime.lastError;
          if (error) reject(new Error(error.message));
          else if (!result || result.ok !== true) reject(new Error(result?.error || "PASI script execution failed"));
          else resolve(result);
        });
      });
    },
    unregister: base.scripts.unregister,
    list: base.scripts.list,
  });

  const original = base;
  globalThis.PASI = Object.freeze({
    ...original,
    storage,
    http,
    menu,
    scripts,
    structured: Object.freeze({
      encode: structuredEncode,
      decode: structuredDecode,
      register(name, definition) {
        const key = String(name || "").trim();
        if (!/^[A-Za-z][A-Za-z0-9._:-]{0,80}$/.test(key)) throw new TypeError("Invalid PASI structured type");
        if (!definition || typeof definition.test !== "function" ||
            typeof definition.serialize !== "function" ||
            typeof definition.deserialize !== "function") {
          throw new TypeError("PASI structured type needs test, serialize, and deserialize");
        }
        structuredTypes.set(key, definition);
        return () => structuredTypes.delete(key);
      },
    }),
    capabilities: Object.freeze({
      userscript_style: true,
      structured_values: true,
      fetch_response_streams: true,
      shadow_dom_helpers: true,
      menu_controls: true,
      element_present_run_at: true,
      dynamic_header_rules: true,
      relative_download_paths: true,
      cross_tab_messaging: true,
    }),
  });
})();
