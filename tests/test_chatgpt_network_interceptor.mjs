import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const repoRoot = new URL("../", import.meta.url).pathname;
const interceptorPath = new URL("../extensions/pasi-chatgpt/src/network_interceptor.js", import.meta.url).pathname;
const contentPath = new URL("../extensions/pasi-chatgpt/src/content.js", import.meta.url).pathname;
const recoveryPath = new URL("../extensions/pasi-chatgpt/src/recovery.js", import.meta.url).pathname;

function loadInterceptor({fetchImpl, flags = {}} = {}) {
  const messages = [];
  const listeners = new Map();
  const window = {
    fetch: fetchImpl,
    postMessage(message) { messages.push(message); },
    addEventListener(type, listener) {
      listeners.set(type, listener);
    },
  };
  const sandbox = {
    window,
    location: new URL("https://chatgpt.com/c/test"),
    URL,
    URLSearchParams,
    TextDecoder,
    TextEncoder,
    Response,
    ReadableStream,
    setTimeout,
    clearTimeout,
    setInterval: (...args) => {
      const timer = setInterval(...args);
      timer.unref?.();
      return timer;
    },
    clearInterval,
    Date,
    Math,
    JSON,
    String,
    Number,
    Boolean,
    Object,
    Array,
    Map,
    Set,
    Promise,
    crypto: globalThis.crypto,
    globalThis: null,
  };
  sandbox.globalThis = sandbox;
  Object.assign(sandbox, flags);
  vm.runInNewContext(fs.readFileSync(interceptorPath, "utf8"), sandbox, {filename: interceptorPath});

  return {
    sandbox,
    window,
    messages,
    dispatch(message) {
      listeners.get("message")?.({source: window, data: message});
    },
  };
}

function terminalEvent(messages) {
  return messages.find((message) =>
    message?.kind === "event" &&
    ["completed", "interrupted", "context_exhausted", "usage_limited", "auth_required", "provider_error", "unknown_failure"].includes(message.state)
  );
}

async function waitForTerminal(messages, timeoutMs = 2000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const event = terminalEvent(messages);
    if (event) return event;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  return terminalEvent(messages);
}

test("network interceptor classifies the live ChatGPT conversation endpoint", () => {
  const runtime = loadInterceptor({
    fetchImpl: async () => new Response("", {status: 200}),
    flags: {PASI_NETWORK_INTERCEPTOR_TEST_HOOKS: true},
  });
  const api = runtime.sandbox.PASI_NETWORK_INTERCEPTOR_TEST_API;
  assert.equal(api.likelyGenerationPath(new URL("https://chatgpt.com/backend-api/f/conversation")), true);
  assert.equal(api.likelyGenerationPath(new URL("https://chatgpt.com/backend-api/conversation")), true);
  assert.equal(api.likelyGenerationPath(new URL("https://chatgpt.com/backend-api/tasks/task-1/stream")), true);
  assert.equal(api.likelyGenerationPath(new URL("https://chatgpt.com/backend-api/conversations")), false);
  assert.equal(api.classifyFailure("this conversation has reached its limit", 200), "context_exhausted");
  assert.equal(api.classifyFailure("usage limit reached", 429), "usage_limited");
  assert.equal(api.classifyFailure("please log in to continue", 401), "auth_required");
});

test("network interceptor does not await observer stream before returning fetch response", async () => {
  let release;
  const source = new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode('data: {"message":{"author":{"role":"assistant"},"content":{"parts":["PASI_NONBLOCK"]}}}\n\n'));
      release = () => controller.close();
    },
  });

  const runtime = loadInterceptor({
    fetchImpl: async () => new Response(source, {
      status: 200,
      headers: {"content-type": "text/event-stream"},
    }),
  });

  runtime.dispatch({
    source: "pasi-network-controller",
    target: "pasi-network-interceptor",
    command: "arm",
    operation_id: "op-nonblocking-1",
  });

  const startedAt = Date.now();
  const response = await runtime.window.fetch("https://chatgpt.com/backend-api/f/conversation", {
    method: "POST",
  });
  const elapsed = Date.now() - startedAt;

  assert.ok(response instanceof Response);
  assert.ok(elapsed < 500, "fetch response was delayed " + elapsed + "ms");
  release();
});

test("ordinary assistant text containing context-limit language is not a context-exhaustion signal", () => {
  const runtime = loadInterceptor({
    fetchImpl: async () => new Response("", {status: 200}),
    flags: {PASI_NETWORK_INTERCEPTOR_TEST_HOOKS: true},
  });
  const api = runtime.sandbox.PASI_NETWORK_INTERCEPTOR_TEST_API;
  const state = {responseText: "", diagnostic: "", assistantSeen: false, doneMarker: false, failureKind: null, conversationId: null};
  api.inspectPayload(state, {
    message: {
      author: {role: "assistant"},
      content: {parts: ["For more information, start a new chat to continue reading about conversation limits."]},
      status: "finished_successfully",
    },
  });
  assert.equal(state.failureKind, null);
  assert.equal(state.doneMarker, true);
});

test("Responses-style completion is a network terminal signal", () => {
  const runtime = loadInterceptor({
    fetchImpl: async () => new Response("", {status: 200}),
    flags: {PASI_NETWORK_INTERCEPTOR_TEST_HOOKS: true},
  });
  const api = runtime.sandbox.PASI_NETWORK_INTERCEPTOR_TEST_API;
  const state = {responseText: "", diagnostic: "", assistantSeen: false, doneMarker: false, failureKind: null, conversationId: null};
  api.inspectPayload(state, {type: "response.output_text.delta", delta: "network response"});
  api.inspectPayload(state, {type: "response.completed", response: {id: "resp-1"}});
  assert.equal(state.responseText, "network response");
  assert.equal(state.doneMarker, true);
  assert.equal(state.conversationId, "resp-1");
});

test("network interceptor observes SSE without consuming the page response", async () => {
  const frames = [
    'data: {"message":{"author":{"role":"assistant"},"content":{"parts":[""]},"conversation_id":"conv-1"}}\n\n',
    'data: {"p":"/message/content/parts/0","o":"append","v":"PASI_"}\n\n',
    'data: {"p":"/message/content/parts/0","o":"append","v":"NETWORK_OK"}\n\n',
    'data: [DONE]\n\n',
  ];
  const encoder = new TextEncoder();
  let originalBodyRead = "";
  const source = new ReadableStream({
    pull(controller) {
      if (!frames.length) {
        controller.close();
        return;
      }
      controller.enqueue(encoder.encode(frames.shift()));
    },
  });

  const runtime = loadInterceptor({
    fetchImpl: async () => new Response(source, {
      status: 200,
      headers: {"content-type": "text/event-stream"},
    }),
  });

  runtime.dispatch({
    source: "pasi-network-controller",
    target: "pasi-network-interceptor",
    command: "arm",
    operation_id: "op-network-1",
  });

  const response = await runtime.window.fetch("https://chatgpt.com/backend-api/f/conversation", {
    method: "POST",
  });

  const originalReader = response.body.getReader();
  while (true) {
    const {done, value} = await originalReader.read();
    if (done) break;
    originalBodyRead += new TextDecoder().decode(value);
  }

  const terminal = await waitForTerminal(runtime.messages);
  assert.equal(terminal?.state, "completed");
  assert.equal(terminal?.operation_id, "op-network-1");
  assert.equal(terminal?.conversation_id, "conv-1");
  assert.equal(terminal?.response_text, "PASI_NETWORK_OK");
  assert.match(originalBodyRead, /PASI_/);
  assert.match(originalBodyRead, /NETWORK_OK/);
});

test("network interceptor classifies context exhaustion from a provider error without DOM inspection", async () => {
  const body = JSON.stringify({error: "This conversation has reached its limit. Start a new chat to continue."});
  const runtime = loadInterceptor({
    fetchImpl: async () => new Response(body, {
      status: 400,
      headers: {"content-type": "application/json"},
    }),
  });

  runtime.dispatch({
    source: "pasi-network-controller",
    target: "pasi-network-interceptor",
    command: "arm",
    operation_id: "op-exhausted-1",
  });

  const response = await runtime.window.fetch("https://chatgpt.com/backend-api/f/conversation", {method: "POST"});
  assert.equal(await response.text(), body);

  const terminal = terminalEvent(runtime.messages);
  assert.equal(terminal?.state, "context_exhausted");
  assert.equal(terminal?.operation_id, "op-exhausted-1");
});

test("network interceptor is idempotent and has no DOM dependency", () => {
  const source = fs.readFileSync(interceptorPath, "utf8");
  assert.doesNotMatch(source, /\bdocument\b/);
  assert.doesNotMatch(source, /MutationObserver/);
  assert.doesNotMatch(source, /querySelector/);
  assert.doesNotMatch(source, /getComputedStyle/);
  assert.match(source, /void observeResponse\(response, actualMeta\)/);
  assert.doesNotMatch(source, /await observeResponse\(response, actualMeta\)/);

  const runtime = loadInterceptor({
    fetchImpl: async () => new Response("", {status: 200}),
  });
  const firstFetch = runtime.window.fetch;
  vm.runInNewContext(fs.readFileSync(interceptorPath, "utf8"), runtime.sandbox, {filename: interceptorPath});
  assert.strictEqual(runtime.window.fetch, firstFetch);
});

test("recovery prefers network signals and keeps DOM recovery as an explicit fallback", () => {
  const source = fs.readFileSync(recoveryPath, "utf8");
  assert.match(source, /network_recovery_enabled/);
  assert.match(source, /network_recovery_unavailable/);
  assert.match(source, /async function inspectNetworkOnly/);
  assert.match(source, /recovery_mode: 'network_preferred'/);
  assert.match(source, /verification_source: 'network_interceptor'/);
  assert.match(source, /recovery_action: 'legacy_dom_fallback'/);
});

test("DOM actuator retains bounded submission strategies while network lifecycle is independent", () => {
  const source = fs.readFileSync(contentPath, "utf8");
  assert.match(source, /async function submitPrompt\(expected, options = \{\}/);
  assert.match(source, /networkStartedPromise/);
  assert.match(source, /network_armed/);
  assert.match(source, /const strategies = \[/);
  assert.match(source, /form\.requestSubmit/);
  assert.match(source, /nativeMouseActivate/);
  assert.match(source, /dispatchEnter/);
  const promptPath = source.slice(source.indexOf("case 'prompt':"), source.indexOf("default: throw new Error"));
  assert.match(promptPath, /networkGeneration\.terminalPromise/);
  assert.doesNotMatch(promptPath, /waitForResponse\(/);
});
