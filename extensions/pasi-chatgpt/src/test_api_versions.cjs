const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const ROOT = path.resolve(__dirname, "..");
const manifest = JSON.parse(fs.readFileSync(path.join(ROOT, "manifest.json"), "utf8"));

function contentScriptPaths() {
  return manifest.content_scripts.flatMap((entry) => entry.js || []);
}

test("current page API keeps v2 contract plus v3 extensions and removes obsolete v4 manager layer", () => {
  const scripts = contentScriptPaths();
  assert.deepEqual(
    scripts.slice(0, 4),
    ["src/api_contract.js", "src/api_v2.js", "src/api_v3.js", "src/timeout-config.js"],
  );
  assert.equal(scripts.includes("src/api_v4.js"), false);
  assert.equal(fs.existsSync(path.join(ROOT, "src", "api_v2.js")), true);
  assert.equal(fs.existsSync(path.join(ROOT, "src", "api_v3.js")), true);
  assert.equal(fs.existsSync(path.join(ROOT, "src", "api_v4.js")), false);
});

test("v2 and v3 page API layers still compose into the documented public PASI surface", () => {
  const runtimeListeners = [];
  const chrome = {
    runtime: {
      id: "test-extension",
      onMessage: {
        addListener(listener) {
          runtimeListeners.push(listener);
        },
      },
      sendMessage() {
        throw new Error("sendMessage should not run during API bootstrap");
      },
      connect() {
        throw new Error("connect should not run during API bootstrap");
      },
      getManifest() {
        return {id: "test-extension", name: "PASI ChatGPT Handoff", version: "test"};
      },
    },
  };

  const eventTarget = new EventTarget();
  const context = vm.createContext({
    chrome,
    console,
    crypto,
    URL,
    Request,
    ReadableStream,
    EventTarget,
    CustomEvent,
    TextEncoder,
    TextDecoder,
    AbortController,
    Blob,
    btoa,
    atob,
    setTimeout,
    clearTimeout,
    globalThis: null,
  });
  context.globalThis = context;
  context.addEventListener = eventTarget.addEventListener.bind(eventTarget);
  context.removeEventListener = eventTarget.removeEventListener.bind(eventTarget);
  context.dispatchEvent = eventTarget.dispatchEvent.bind(eventTarget);

  for (const file of ["api_contract.js", "api_v2.js", "api_v3.js"]) {
    const source = fs.readFileSync(path.join(ROOT, "src", file), "utf8");
    vm.runInContext(source, context, {filename: file});
  }

  assert.equal(runtimeListeners.length, 1);
  assert.equal(context.PASI.api.version, "pasi-api-v2");
  assert.equal(typeof context.PASI.storage.get, "function");
  assert.equal(typeof context.PASI.dom.waitFor, "function");
  assert.equal(typeof context.PASI.http.fetch, "function");
  assert.equal(typeof context.PASI.structured.encode, "function");
  assert.equal(context.PASI.capabilities.structured_values, true);
  assert.equal(context.PASI.capabilities.fetch_response_streams, true);
  assert.equal(context.PASI.userScripts, undefined);
});

test("native userscript runtime is the replacement for the removed page manager layer", () => {
  const runtimeSource = fs.readFileSync(
    path.join(ROOT, "src", "userscript_runtime.js"),
    "utf8",
  );
  const backgroundSource = fs.readFileSync(
    path.join(ROOT, "src", "background-userscripts.js"),
    "utf8",
  );
  const typings = fs.readFileSync(
    path.join(ROOT, "pasi-userscript.d.ts"),
    "utf8",
  );
  const extensionSource = fs
    .readdirSync(path.join(ROOT, "src"))
    .filter((file) => file !== "test_api_versions.cjs")
    .filter((file) => /\.(?:js|cjs|mjs|d\.ts)$/.test(file))
    .map((file) => fs.readFileSync(path.join(ROOT, "src", file), "utf8"))
    .join("\n");

  assert.match(runtimeSource, /PASIUserScriptRuntime/);
  assert.match(runtimeSource, /GM_getValue/);
  assert.match(backgroundSource, /pasi\.userscript\.rpc/);
  assert.match(backgroundSource, /chrome\.userScripts/);
  assert.match(typings, /declare namespace PASIUserScript/);
  assert.doesNotMatch(extensionSource, /PASI\.userScripts/);
});
