const assert = require('node:assert/strict');
const {test} = require('node:test');

const fs = require('node:fs');
const vm = require('node:vm');

const interceptorPath = require.resolve('./network-interceptor.js');
const interceptorSource = fs.readFileSync(interceptorPath, 'utf8');
const sandbox = {
  module: {exports: {}},
  exports: {},
  fetch: async () => ({ok: true, status: 200}),
  TextDecoder,
  URL,
  setInterval,
  clearInterval,
};
vm.runInNewContext(interceptorSource, sandbox, {filename: interceptorPath});
const source = sandbox.module.exports;

function fakeResponse(chunks, {status = 200, ok = true} = {}) {
  const makeReader = () => ({
    index: 0,
    async read() {
      if (this.index >= chunks.length) return {done: true, value: undefined};
      return {
        done: false,
        value: new Uint8Array(Array.from(chunks[this.index++]).map(ch => ch.charCodeAt(0))),
      };
    },
  });

  return {
    ok,
    status,
    body: {getReader: makeReader},
    clone() {
      return fakeResponse(chunks, {status, ok});
    },
  };
}

function timerHarness() {
  const callbacks = new Set();
  return {
    setInterval(fn) {
      callbacks.add(fn);
      return fn;
    },
    clearInterval(id) {
      callbacks.delete(id);
    },
    tick() {
      for (const fn of [...callbacks]) fn();
    },
    size() {
      return callbacks.size;
    },
  };
}

test('detects only POST ChatGPT generation requests', () => {
  assert.equal(
    source.isGenerationRequest(
      'https://chatgpt.com/backend-api/conversation',
      {method: 'POST'},
    ),
    true,
  );
  assert.equal(
    source.isGenerationRequest(
      'https://chatgpt.com/backend-api/conversation',
      {method: 'GET'},
    ),
    false,
  );
  assert.equal(
    source.isGenerationRequest(
      'https://chatgpt.com/backend-api/conversation/implicit_message_feedback',
      {method: 'POST'},
    ),
    false,
  );
  assert.equal(
    source.isGenerationRequest(
      'https://chatgpt.com/backend-api/conversation?foo=bar',
      {method: 'POST'},
    ),
    true,
  );
  assert.equal(
    source.isGenerationRequest(
      'https://chatgpt.com/backend-api/f/conversation',
      {method: 'POST'},
    ),
    true,
  );
  assert.equal(
    source.isGenerationRequest(
      'https://chatgpt.com/backend-api/f/conversation?foo=bar',
      {method: 'POST'},
    ),
    true,
  );
  assert.equal(
    source.isGenerationRequest(
      'https://chatgpt.com/backend-api/files',
      {method: 'POST'},
    ),
    false,
  );
});

test('classifies provider and transport boundary statuses without DOM access', () => {
  assert.equal(source.classifyHttpStatus(401).classification, 'auth_failure');
  assert.equal(source.classifyHttpStatus(403).classification, 'auth_failure');
  assert.equal(source.classifyHttpStatus(429).classification, 'usage_limit');
  assert.equal(source.classifyHttpStatus(500).classification, 'provider_failure');
  assert.equal(
    source.classifyPayload({error: {code: 'context_length_exceeded'}}).reason,
    'CONTEXT_EXHAUSTED',
  );
  assert.equal(
    source.classifyPayload({error: {code: 'rate_limit_exceeded'}}).reason,
    'USAGE_LIMIT_REACHED',
  );
});

test('parses complete SSE lines and preserves an incomplete tail', () => {
  const seen = [];
  const tail = source.parseStreamLines(
    'data: {"ok":1}\n\ndata: {"ok":2}',
    value => seen.push(value),
  );
  assert.deepEqual(seen, ['{"ok":1}']);
  assert.equal(tail, 'data: {"ok":2}');
});

test('extracts assistant response from direct, patch-envelope, and delta SSE payloads', () => {
  const snapshot = source.extractAssistantResponseText({
    message: {
      author: {role: 'assistant'},
      content: {parts: ['Hello ', 'network']}
    }
  });
  assert.equal(snapshot.mode, 'snapshot');
  assert.equal(snapshot.text, 'Hello network');

  const envelope = source.extractAssistantResponseText({
    p: '',
    o: 'add',
    v: {
      message: {
        author: {role: 'assistant'},
        content: {parts: ['Envelope response']}
      }
    }
  });
  assert.equal(envelope.mode, 'snapshot');
  assert.equal(envelope.text, 'Envelope response');

  const patch = source.extractAssistantResponseText({
    p: '/message/content/parts/0',
    o: 'append',
    v: 'PATCHED_RESPONSE'
  });
  assert.equal(patch.mode, 'delta');
  assert.equal(patch.text, 'PATCHED_RESPONSE');

  const delta = source.extractAssistantResponseText({delta: 'response'});
  assert.equal(delta.mode, 'delta');
  assert.equal(delta.text, 'response');
});

test('emits correlated response text on terminal network completion', async () => {
  const events = [];
  const target = {
    fetch: async () =>
      fakeResponse([
        'data: {"message":{"author":{"role":"assistant"},"content":{"parts":["NETWORK_CORRELATION_"]}}}\n\n',
        'data: {"message":{"author":{"role":"assistant"},"content":{"parts":["NETWORK_CORRELATION_OK_2026"]}}}\n\n',
        'data: [DONE]\n\n'
      ]),
  };
  const interceptor = source.createInterceptor({
    target,
    emit: event => events.push(event),
    setIntervalImpl: () => 1,
    clearIntervalImpl: () => {},
  });

  interceptor.bindOperation('op-network-response');
  interceptor.install();
  await target.fetch(
    'https://chatgpt.com/backend-api/f/conversation',
    {method: 'POST'},
  );
  for (let i = 0; i < 12; i += 1) await Promise.resolve();

  assert.equal(events[0].operationId, 'op-network-response');
  assert.equal(events.at(-1).eventType, 'COMPLETED');
  assert.equal(events.at(-1).operationId, 'op-network-response');
  assert.equal(events.at(-1).responseText, 'NETWORK_CORRELATION_OK_2026');
});

test('observes a generation stream without consuming the original response', async () => {
  const events = [];
  const target = {
    fetch: async () =>
      fakeResponse(['data: {"delta":"hi"}\n\n', 'data: [DONE]\n\n']),
  };
  const interceptor = source.createInterceptor({
    target,
    emit: event => events.push(event),
    randomId: (() => {
      let id = 0;
      return () => 'evt-' + (++id);
    })(),
    setIntervalImpl: () => 1,
    clearIntervalImpl: () => {},
  });

  interceptor.bindOperation('phase1-op');
  assert.equal(interceptor.install(), true);

  const response = await target.fetch(
    'https://chatgpt.com/backend-api/conversation',
    {method: 'POST'},
  );
  for (let i = 0; i < 12; i += 1) await Promise.resolve();

  assert.equal(response.ok, true);
  assert.deepEqual(events.map(event => event.eventType), ['STARTED', 'COMPLETED']);
  assert.equal(events[0].operationId, 'phase1-op');
  assert.equal(events[0].requestId, events[1].requestId);
  assert.equal(interceptor.health().trackingRequestId, null);
});

test('does not intercept unrelated POST traffic', async () => {
  let calls = 0;
  const target = {
    fetch: async () => {
      calls += 1;
      return {ok: true, status: 200};
    },
  };
  const events = [];
  const interceptor = source.createInterceptor({
    target,
    emit: event => events.push(event),
  });
  interceptor.install();
  await target.fetch('https://chatgpt.com/backend-api/files', {method: 'POST'});
  assert.equal(calls, 1);
  assert.deepEqual(events, []);
});

test('survives page fetch reassignment after installation', async () => {
  const events = [];
  const timers = timerHarness();
  let replacementCalls = 0;
  const target = {
    fetch: async () => ({ok: true, status: 200}),
  };
  const interceptor = source.createInterceptor({
    target,
    emit: event => events.push(event),
    setIntervalImpl: timers.setInterval,
    clearIntervalImpl: timers.clearInterval,
  });

  interceptor.install();

  const replacement = async () => {
    replacementCalls += 1;
    return {
      ok: true,
      status: 200,
      clone() {
        return {
          body: {
            getReader: () => ({
              read: async () => ({done: true, value: undefined}),
            }),
          },
        };
      },
    };
  };

  target.fetch = replacement;
  assert.notEqual(target.fetch, replacement);

  const response = await target.fetch(
    'https://chatgpt.com/backend-api/f/conversation',
    {method: 'POST'},
  );
  for (let i = 0; i < 12; i += 1) await Promise.resolve();

  assert.equal(replacementCalls, 1);
  assert.equal(response.ok, true);
  assert.deepEqual(events.map(event => event.eventType), ['STARTED', 'COMPLETED']);

  events.length = 0;
  Object.defineProperty(target, 'fetch', {
    configurable: true,
    enumerable: true,
    writable: true,
    value: replacement,
  });
  timers.tick();

  await target.fetch(
    'https://chatgpt.com/backend-api/f/conversation',
    {method: 'POST'},
  );
  for (let i = 0; i < 12; i += 1) await Promise.resolve();

  assert.deepEqual(events.map(event => event.eventType), ['STARTED', 'COMPLETED']);
  assert.equal(replacementCalls, 2);

  interceptor.uninstall();
});

test('classifies terminal context exhaustion even when the final SSE line has no newline', async () => {
  const events = [];
  const target = {
    fetch: async () =>
      fakeResponse(['data: {"error":{"code":"context_length_exceeded"}}']),
  };
  const interceptor = source.createInterceptor({
    target,
    emit: event => events.push(event),
    setIntervalImpl: () => 1,
    clearIntervalImpl: () => {},
  });
  interceptor.install();
  await target.fetch(
    'https://chatgpt.com/backend-api/conversation',
    {method: 'POST'},
  );
  for (let i = 0; i < 12; i += 1) await Promise.resolve();
  assert.deepEqual(events.map(event => event.eventType), ['STARTED', 'INTERRUPTED']);
  assert.equal(events.at(-1).reason, 'CONTEXT_EXHAUSTED');
});

test('classifies a dropped generation stream as a retryable transport interruption', async () => {
  const events = [];
  const target = {
    fetch: async () => ({
      ok: true,
      status: 200,
      clone() {
        return {
          body: {
            getReader: () => ({
              read: async () => {
                throw new Error('socket closed');
              },
            }),
          },
        };
      },
    }),
  };
  const interceptor = source.createInterceptor({
    target,
    emit: event => events.push(event),
    setIntervalImpl: () => 1,
    clearIntervalImpl: () => {},
  });
  interceptor.install();
  await target.fetch(
    'https://chatgpt.com/backend-api/conversation',
    {method: 'POST'},
  );
  for (let i = 0; i < 12; i += 1) await Promise.resolve();
  assert.equal(events.at(-1).eventType, 'INTERRUPTED');
  assert.equal(events.at(-1).classification, 'retryable_transport_failure');
});

test('stall detection is non-terminal and clears its timer after uninstall', async () => {
  const events = [];
  const timers = timerHarness();
  let clock = 0;
  let releaseRead;
  const target = {
    fetch: async () => ({
      ok: true,
      status: 200,
      clone() {
        return {
          body: {
            getReader: () => ({
              read: () => new Promise(resolve => { releaseRead = resolve; }),
            }),
          },
        };
      },
    }),
  };

  const interceptor = source.createInterceptor({
    target,
    emit: event => events.push(event),
    now: () => clock,
    stallThresholdMs: 1000,
    setIntervalImpl: timers.setInterval,
    clearIntervalImpl: timers.clearInterval,
  });
  interceptor.install();

  await target.fetch(
    'https://chatgpt.com/backend-api/conversation',
    {method: 'POST'},
  );
  clock = 1000;
  timers.tick();

  releaseRead({done: true, value: undefined});
  for (let i = 0; i < 12; i += 1) await Promise.resolve();

  assert.deepEqual(events.map(event => event.eventType), ['STARTED', 'STALL_DETECTED', 'COMPLETED']);
  assert.equal(events[1].reason, 'GENERATION_STALLED');
  assert.equal(timers.size(), 1);

  interceptor.uninstall();
  assert.equal(timers.size(), 0);
});

test('binds operation IDs from lifecycle events', () => {
  const listeners = new Map();
  const target = {
    fetch: async () => ({ok: true, status: 200}),
    addEventListener(type, listener) {
      listeners.set(type, listener);
    },
  };
  const interceptor = source.createInterceptor({target});
  assert.equal(interceptor.bindOperationEventListener(), true);
  listeners.get('PASI_NETWORK_BIND_OPERATION')({
    detail: 'op-shadow-1',
  });
  assert.equal(interceptor.health().currentOperationId, 'op-shadow-1');
  listeners.get('PASI_NETWORK_BIND_OPERATION')({
    detail: JSON.stringify({operationId: 'op-shadow-2'}),
  });
  assert.equal(interceptor.health().currentOperationId, 'op-shadow-2');
  assert.equal(interceptor.bindOperationEventListener(), true);
});

test('keeps captured operation ID on terminal event after global clear', async () => {
  const events = [];
  let releaseRead;
  const target = {
    fetch: async () => ({
      ok: true,
      status: 200,
      clone() {
        return {
          body: {
            getReader: () => ({
              read: () => new Promise(resolve => { releaseRead = resolve; }),
            }),
          },
        };
      },
    }),
  };
  const interceptor = source.createInterceptor({
    target,
    emit: event => events.push(event),
    setIntervalImpl: () => 1,
    clearIntervalImpl: () => {},
  });
  interceptor.install();
  interceptor.bindOperation('op-captured');
  await target.fetch(
    'https://chatgpt.com/backend-api/f/conversation',
    {method: 'POST'},
  );
  interceptor.bindOperation(null);
  releaseRead({done: true, value: undefined});
  for (let i = 0; i < 12; i += 1) await Promise.resolve();

  assert.equal(events[0].eventType, 'STARTED');
  assert.equal(events[0].operationId, 'op-captured');
  assert.equal(events.at(-1).eventType, 'COMPLETED');
  assert.equal(events.at(-1).operationId, 'op-captured');
});

test('defers operation correlation clear until active stream terminates', async () => {
  const events = [];
  let releaseRead;
  const target = {
    fetch: async () => ({
      ok: true,
      status: 200,
      clone() {
        return {
          body: {
            getReader: () => ({
              read: () => new Promise(resolve => { releaseRead = resolve; }),
            }),
          },
        };
      },
    }),
  };
  const interceptor = source.createInterceptor({
    target,
    emit: event => events.push(event),
    setIntervalImpl: () => 1,
    clearIntervalImpl: () => {},
  });
  interceptor.install();
  interceptor.bindOperation('op-live');
  const response = await target.fetch(
    'https://chatgpt.com/backend-api/f/conversation',
    {method: 'POST'},
  );
  assert.equal(response.ok, true);
  interceptor.bindOperation(null);
  assert.equal(interceptor.health().currentOperationId, 'op-live');

  releaseRead({done: true, value: undefined});
  for (let i = 0; i < 12; i += 1) await Promise.resolve();

  assert.equal(events[0].operationId, 'op-live');
  assert.equal(events.at(-1).eventType, 'COMPLETED');
  assert.equal(events.at(-1).operationId, 'op-live');
  assert.equal(interceptor.health().currentOperationId, null);
});

test('installation is idempotent and health exposes operation state', () => {
  const target = {fetch: async () => ({ok: true, status: 200})};
  const interceptor = source.createInterceptor({
    target,
    now: () => 1234,
    randomId: () => 'fixed',
  });
  assert.equal(interceptor.install(), true);
  assert.equal(interceptor.install(), false);
  interceptor.bindOperation('op-9');
  assert.deepEqual(JSON.parse(JSON.stringify(interceptor.health())), {
    status: 'HEALTHY',
    timestamp: 1234,
    installed: true,
    trackingRequestId: null,
    currentOperationId: 'op-9',
    fetchWrapped: true,
    fetchAccessorInstalled: true,
    fetchFunctionName: 'interceptedFetch',
  });
});

test('interceptor source contains no DOM observation APIs', () => {
  const fs = require('node:fs');
  const text = fs.readFileSync(require.resolve('./network-interceptor.js'), 'utf8');
  assert.doesNotMatch(text, /MutationObserver/);
  assert.doesNotMatch(text, /document\.querySelector/);
  assert.doesNotMatch(text, /document\.body/);
});
