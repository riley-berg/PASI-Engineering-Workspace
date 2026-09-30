const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');

const sourcePath = require.resolve('./cdp-network-controller.js');
const sourceText = fs.readFileSync(sourcePath, 'utf8');
const sandbox = {
  module: {exports: {}},
  exports: {},
  TextEncoder,
  TextDecoder,
  URL,
  setInterval,
  clearInterval,
  setTimeout,
  clearTimeout,
  Promise,
  atob: (value) => Buffer.from(value, 'base64').toString('binary'),
  btoa: (value) => Buffer.from(value, 'binary').toString('base64')
};
vm.runInNewContext(sourceText, sandbox, {filename: sourcePath});
const source = sandbox.module.exports;

function fakeDebugger() {
  const events = new Set();
  const detachEvents = new Set();
  const commands = [];
  const streams = new Map();
  return {
    commands,
    onEvent: {addListener: (listener) => events.add(listener)},
    onDetach: {addListener: (listener) => detachEvents.add(listener)},
    attach(_debuggee, _version, callback) { callback(); },
    detach(_debuggee, callback) { callback(); },
    sendCommand(_debuggee, method, params, callback) {
      commands.push({method, params});
      if (method === 'Runtime.evaluate') {
        return callback({
          result: {
            type: 'object',
            value: {focused: true, kind: 'textarea'}
          }
        });
      }
      if (method === 'Input.insertText' || method === 'Input.dispatchKeyEvent') return callback({});
      if (method === 'Fetch.enable' || method === 'Fetch.continueRequest' || method === 'Fetch.continueResponse' || method === 'Fetch.failRequest') return callback({});
      if (method === 'Fetch.fulfillRequest') return callback({});
      if (method === 'Fetch.takeResponseBodyAsStream') {
        streams.set('stream-1', [
          {data: 'data: {"p":"","o":"add","v":{"message":{"id":"old","author":{"role":"assistant"},"content":{"parts":["OLD"]}}}}\n\n'},
          {data: 'data: {"p":"","o":"add","v":{"message":{"id":"new","author":{"role":"assistant"},"content":{"parts":["NETWORK_PATCH_OK_2026"]}}}}\n\n'},
          {data: 'data: [DONE]\n\n', eof: true}
        ]);
        return callback({stream: 'stream-1'});
      }
      if (method === 'IO.read') {
        const list = streams.get(params.handle) || [];
        return callback(list.shift() || {data: '', eof: true});
      }
      if (method === 'IO.close') return callback({});
      if (method === 'Fetch.getResponseBody') return callback({body: '', base64Encoded: false});
      callback({});
    },
    async emit(sourceValue, method, params) {
      await Promise.all([...events].map((listener) => listener(sourceValue, method, params)));
    },
    detachEmit(sourceValue, reason) { for (const listener of detachEvents) listener(sourceValue, reason); }
  };
}

test('CDP submit operation uses native input and never clicks a DOM send control', async () => {
  const debuggerApi = fakeDebugger();
  const controller = source.createController({debuggerApi});
  controller.install();
  await controller.bindOperation({
    tabId: 6,
    operationId: 'op-6',
    controllerId: 'controller-6',
    prompt: '[PASI_OPERATION op-6]\\nReply with NETWORK_PATCH_OK_2026',
    completionMarkers: ['NETWORK_PATCH_OK_2026']
  });

  const result = await controller.submitOperation(6, 'op-6', 'controller-6');
  assert.equal(result.submitted, true);
  assert.equal(result.submissionMethod, 'cdp_input');
  assert.equal(result.targetKind, 'textarea');

  const runtime = debuggerApi.commands.find((command) => command.method === 'Runtime.evaluate');
  assert.ok(runtime);
  assert.match(runtime.params.expression, /textarea/);
  assert.match(runtime.params.expression, /contenteditable/);
  assert.doesNotMatch(runtime.params.expression, /send-button/i);

  const insertIndex = debuggerApi.commands.findIndex((command) => command.method === 'Input.insertText');
  assert.ok(insertIndex >= 0);
  assert.equal(debuggerApi.commands[insertIndex].params.text, '[PASI_OPERATION op-6]\\nReply with NETWORK_PATCH_OK_2026');

  const keyEvents = debuggerApi.commands.filter((command) => command.method === 'Input.dispatchKeyEvent');
  assert.deepEqual(keyEvents.map((command) => command.params.type), ['keyDown', 'keyUp']);
  assert.ok(!debuggerApi.commands.some((command) => command.method === 'Runtime.evaluate' && /button/i.test(command.params.expression) && /click/i.test(command.params.expression)));
});

test('request-to-task correlation binds the exact POST generation request', async () => {
  const debuggerApi = fakeDebugger();
  const events = [];
  const controller = source.createController({debuggerApi, onEvent: (event) => events.push(event)});
  controller.install();
  await controller.bindOperation({
    tabId: 7,
    operationId: 'op-7',
    controllerId: 'controller-7',
    prompt: '[PASI_OPERATION op-7]\nReply with NETWORK_PATCH_OK_2026',
    completionMarkers: ['NETWORK_PATCH_OK_2026'],
    chatUrl: 'https://chatgpt.com/c/test'
  });

  await controller.handlePaused(
    {tabId: 7},
    'Fetch.requestPaused',
    {
      requestId: 'req-7',
      request: {
        url: 'https://chatgpt.com/backend-api/f/conversation',
        method: 'POST',
        postData: JSON.stringify({messages: [{content: '[PASI_OPERATION op-7]\nReply with NETWORK_PATCH_OK_2026'}]})
      }
    }
  );
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(events[0].eventType, 'STARTED');
  assert.equal(events[0].operationId, 'op-7');
  assert.equal(events[0].requestId, 'req-7');
  assert.equal(events[0].telemetry.promptMatched, true);
});

test('response stream is captured before browser continuation and stale snapshots are replaced by the current message id', async () => {
  const debuggerApi = fakeDebugger();
  const events = [];
  const controller = source.createController({debuggerApi, onEvent: (event) => events.push(event)});
  controller.install();
  await controller.bindOperation({
    tabId: 8,
    operationId: 'op-8',
    controllerId: 'controller-8',
    prompt: 'current prompt',
    completionMarkers: ['NETWORK_PATCH_OK_2026']
  });

  await controller.handlePaused(
    {tabId: 8},
    'Fetch.requestPaused',
    {
      requestId: 'req-8',
      request: {
        url: 'https://chatgpt.com/backend-api/conversation',
        method: 'POST',
        postData: JSON.stringify({prompt: 'current prompt'})
      }
    }
  );
  await new Promise((resolve) => setImmediate(resolve));

  await controller.handlePaused(
    {tabId: 8},
    'Fetch.requestPaused',
    {
      requestId: 'req-8',
      responseStatusCode: 200,
      responseStatusText: 'OK',
      responseHeaders: [{name: 'content-type', value: 'text/event-stream'}],
      request: {
        url: 'https://chatgpt.com/backend-api/conversation',
        method: 'POST'
      }
    }
  );
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(events.at(-1).eventType, 'COMPLETED');
  assert.equal(events.at(-1).operationId, 'op-8');
  assert.equal(events.at(-1).requestId, 'req-8');
  assert.equal(events.at(-1).assistantMessageId, 'new');
  assert.equal(events.at(-1).responseText, 'NETWORK_PATCH_OK_2026');
  assert.ok(debuggerApi.commands.some((command) => command.method === 'Fetch.takeResponseBodyAsStream'));
  assert.ok(debuggerApi.commands.some((command) => command.method === 'Fetch.fulfillRequest'));
});

test('unmatched generation requests are never assigned to a task', async () => {
  const debuggerApi = fakeDebugger();
  const events = [];
  const controller = source.createController({debuggerApi, onEvent: (event) => events.push(event)});
  controller.install();
  await controller.bindOperation({
    tabId: 9,
    operationId: 'op-9',
    controllerId: 'controller-9',
    prompt: 'expected prompt',
    completionMarkers: ['OK']
  });

  await controller.handlePaused(
    {tabId: 9},
    'Fetch.requestPaused',
    {
      requestId: 'req-wrong',
      request: {
        url: 'https://chatgpt.com/backend-api/conversation',
        method: 'POST',
        postData: JSON.stringify({prompt: 'different prompt'})
      }
    }
  );
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(events, []);
  assert.ok(debuggerApi.commands.some((command) => command.method === 'Fetch.continueRequest'));
});

test('classifies provider failures before response-body correlation', async () => {
  const debuggerApi = fakeDebugger();
  const events = [];
  const controller = source.createController({debuggerApi, onEvent: (event) => events.push(event)});
  controller.install();
  await controller.bindOperation({
    tabId: 10,
    operationId: 'op-10',
    controllerId: 'controller-10',
    prompt: 'expected prompt'
  });

  await controller.handlePaused(
    {tabId: 10},
    'Fetch.requestPaused',
    {
      requestId: 'req-10',
      request: {
        url: 'https://chatgpt.com/backend-api/conversation',
        method: 'POST',
        postData: JSON.stringify({prompt: 'expected prompt'})
      }
    }
  );
  await new Promise((resolve) => setImmediate(resolve));
  await controller.handlePaused(
    {tabId: 10},
    'Fetch.requestPaused',
    {
      requestId: 'req-10',
      responseStatusCode: 429,
      request: {url: 'https://chatgpt.com/backend-api/conversation', method: 'POST'}
    }
  );
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(events.at(-1).eventType, 'INTERRUPTED');
  assert.equal(events.at(-1).reason, 'USAGE_LIMIT_REACHED');
});

test('network-health exposes the request-to-task map and controller fence', async () => {
  const debuggerApi = fakeDebugger();
  const controller = source.createController({debuggerApi});
  controller.install();
  await controller.bindOperation({
    tabId: 11,
    operationId: 'op-11',
    controllerId: 'controller-11',
    prompt: 'prompt'
  });
  const health = controller.health();
  assert.equal(health.attachedTabs[0].operationId, 'op-11');
  assert.equal(health.attachedTabs[0].controllerId, 'controller-11');
  assert.equal(health.activeRequests.length, 0);
});
