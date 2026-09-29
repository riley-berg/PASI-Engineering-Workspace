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
  timers.tick();

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

test('stall detection produces one terminal event and clears its timer', async () => {
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

  assert.deepEqual(events.map(event => event.eventType), ['STARTED', 'INTERRUPTED']);
  assert.equal(timers.size(), 0);
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
  });
});

test('interceptor source contains no DOM observation APIs', () => {
  const fs = require('node:fs');
  const text = fs.readFileSync(require.resolve('./network-interceptor.js'), 'utf8');
  assert.doesNotMatch(text, /MutationObserver/);
  assert.doesNotMatch(text, /document\.querySelector/);
  assert.doesNotMatch(text, /document\.body/);
});
