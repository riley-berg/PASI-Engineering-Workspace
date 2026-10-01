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
      if (method === 'Accessibility.enable' || method === 'Accessibility.disable' || method === 'DOM.focus') return callback({});
      if (method === 'Accessibility.getFullAXTree') {
        return callback({
          nodes: [{
            nodeId: 'ax-composer',
            backendDOMNodeId: 42,
            role: {type: 'role', value: 'textbox'},
            name: {type: 'computedString', value: 'Message'},
            value: {type: 'string', value: ''},
            ignored: false,
            properties: [
              {name: 'editable', value: {type: 'boolean', value: true}},
              {name: 'multiline', value: {type: 'boolean', value: true}},
              {name: 'focused', value: {type: 'boolean', value: false}}
            ]
          }]
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

test('CDP reasoning control verifies an existing Thinking state and can enable it', async () => {
  const debuggerApi = fakeDebugger();
  const originalSendCommand = debuggerApi.sendCommand.bind(debuggerApi);
  let treeCalls = 0;
  debuggerApi.sendCommand = function(_debuggee, method, params, callback) {
    if (method === 'Accessibility.getFullAXTree') {
      this.commands.push({method, params});
      treeCalls += 1;
      const selected = treeCalls >= 2;
      return callback({
        nodes: [{
          nodeId: 'ax-thinking',
          backendDOMNodeId: 91,
          role: {type: 'role', value: 'button'},
          name: {type: 'computedString', value: 'Thinking'},
          ignored: false,
          properties: [
            {name: 'selected', value: {type: 'boolean', value: selected}},
            {name: 'focused', value: {type: 'boolean', value: false}}
          ]
        }]
      });
    }
    return originalSendCommand(_debuggee, method, params, callback);
  };

  const controller = source.createController({debuggerApi});
  controller.install();
  const result = await controller.ensureReasoningMode(15, 'thinking');

  assert.equal(result.enabled, true);
  assert.equal(result.control, 'Thinking');
  assert.ok(debuggerApi.commands.some((command) =>
    command.method === 'DOM.focus' && command.params.backendNodeId === 91
  ));
  assert.ok(debuggerApi.commands.some((command) =>
    command.method === 'Input.dispatchKeyEvent' && command.params.key === 'Enter'
  ));
  assert.equal(debuggerApi.commands.some((command) => command.method === 'Runtime.evaluate'), false);
});

test('CDP GitHub attachment uses accessibility controls and native input', async () => {
  const debuggerApi = fakeDebugger();
  const originalSendCommand = debuggerApi.sendCommand.bind(debuggerApi);
  let treeCalls = 0;
  debuggerApi.sendCommand = function(_debuggee, method, params, callback) {
    if (method === 'Accessibility.getFullAXTree') {
      this.commands.push({method, params});
      treeCalls += 1;
      const node = (backendDOMNodeId, role, name, properties = []) => ({
        nodeId: 'ax-' + backendDOMNodeId,
        backendDOMNodeId,
        role: {type: 'role', value: role},
        name: {type: 'computedString', value: name},
        ignored: false,
        properties
      });
      if (treeCalls === 1) {
        return callback({nodes: [node(101, 'button', 'Add files and more')]});
      }
      if (treeCalls === 2) {
        return callback({nodes: [node(102, 'button', 'GitHub')]});
      }
      if (treeCalls === 3) {
        return callback({nodes: [node(
          103,
          'textbox',
          'Repository',
          [{name: 'editable', value: {type: 'boolean', value: true}}]
        )]});
      }
      return callback({nodes: [node(104, 'option', 'th3-st0v3/PASI-Engineering-Workspace')]});
    }
    return originalSendCommand(_debuggee, method, params, callback);
  };

  const controller = source.createController({debuggerApi});
  controller.install();
  const result = await controller.ensureGithubRepository(
    16,
    'th3-st0v3/PASI-Engineering-Workspace'
  );

  assert.equal(result.attached, true);
  assert.equal(result.repository, 'th3-st0v3/PASI-Engineering-Workspace');
  assert.equal(
    debuggerApi.commands.some((command) =>
      command.method === 'Input.insertText' &&
      command.params.text === 'th3-st0v3/PASI-Engineering-Workspace'
    ),
    true
  );
  assert.equal(debuggerApi.commands.some((command) => command.method === 'Runtime.evaluate'), false);
});

test('CDP submit operation uses native input and never clicks a DOM send control', async () => {
  const debuggerApi = fakeDebugger();
  const controller = source.createController({debuggerApi});
  controller.install();
  await controller.bindOperation({
    tabId: 6,
    operationId: 'op-6',
    controllerId: 'controller-6',
    prompt: '[PASI_OPERATION op-6]\nReply with NETWORK_PATCH_OK_2026',
    completionMarkers: ['NETWORK_PATCH_OK_2026']
  });

  const result = await controller.submitOperation(6, 'op-6', 'controller-6');
  assert.equal(result.submitted, true);
  assert.equal(result.submissionMethod, 'cdp_input');
  assert.equal(result.targetKind, 'accessibility_textbox');

  assert.ok(debuggerApi.commands.some((command) => command.method === 'Accessibility.getFullAXTree'));
  assert.ok(debuggerApi.commands.some((command) => command.method === 'DOM.focus' && command.params.backendNodeId === 42));
  assert.equal(debuggerApi.commands.some((command) => command.method === 'Runtime.evaluate'), false);

  const insertIndex = debuggerApi.commands.findIndex((command) => command.method === 'Input.insertText');
  assert.ok(insertIndex >= 0);
  assert.equal(debuggerApi.commands[insertIndex].params.text, '[PASI_OPERATION op-6]\nReply with NETWORK_PATCH_OK_2026');

  const keyEvents = debuggerApi.commands.filter((command) => command.method === 'Input.dispatchKeyEvent');
  assert.deepEqual(keyEvents.map((command) => command.params.type), ['keyDown', 'keyUp']);
  assert.ok(!debuggerApi.commands.some((command) => command.method === 'Runtime.evaluate' && /button/i.test(command.params.expression) && /click/i.test(command.params.expression)));
});

test('CDP submit rejects an unacknowledged send instead of treating a red-box submission as accepted', async () => {
  const debuggerApi = fakeDebugger();
  const originalSendCommand = debuggerApi.sendCommand.bind(debuggerApi);
  const prompt = '[PASI_OPERATION op-send-failed]';

  debuggerApi.sendCommand = function(_debuggee, method, params, callback) {
    if (method === 'Accessibility.getFullAXTree') {
      this.commands.push({method, params});
      return callback({
        nodes: [{
          nodeId: 'ax-composer',
          backendDOMNodeId: 99,
          role: {type: 'role', value: 'textbox'},
          name: {type: 'computedString', value: 'Message'},
          value: {type: 'string', value: prompt},
          ignored: false,
          properties: [
            {name: 'editable', value: {type: 'boolean', value: true}},
            {name: 'multiline', value: {type: 'boolean', value: true}},
            {name: 'focused', value: {type: 'boolean', value: true}}
          ]
        }]
      });
    }
    return originalSendCommand(_debuggee, method, params, callback);
  };

  const controller = source.createController({debuggerApi, now: () => Date.now()});
  controller.install();
  await controller.bindOperation({
    tabId: 17,
    operationId: 'op-send-failed',
    controllerId: 'controller-17',
    prompt,
    completionMarkers: ['SEND_FAILED_OK']
  });

  await assert.rejects(
    controller.submitOperation(17, 'op-send-failed', 'controller-17'),
    /prompt submission not acknowledged/
  );
});

test('CDP submit accepts editable composer AX nodes exposed with newer semantic roles', async () => {
  const debuggerApi = fakeDebugger();
  const originalSendCommand = debuggerApi.sendCommand.bind(debuggerApi);
  debuggerApi.sendCommand = function(_debuggee, method, params, callback) {
    if (method === 'Accessibility.getFullAXTree') {
      this.commands.push({method, params});
      return callback({
        nodes: [{
          nodeId: 'ax-composer',
          backendDOMNodeId: 77,
          role: {type: 'role', value: 'generic'},
          name: {type: 'computedString', value: 'Message'},
          value: {type: 'string', value: ''},
          ignored: false,
          properties: [
            {name: 'editable', value: {type: 'boolean', value: true}},
            {name: 'multiline', value: {type: 'boolean', value: true}},
            {name: 'focused', value: {type: 'boolean', value: false}}
          ]
        }]
      });
    }
    return originalSendCommand(_debuggee, method, params, callback);
  };

  const controller = source.createController({debuggerApi});
  controller.install();
  await controller.bindOperation({
    tabId: 12,
    operationId: 'op-12',
    controllerId: 'controller-12',
    prompt: '[PASI_OPERATION op-12] Reply with ROLE_OK_2026',
    completionMarkers: ['ROLE_OK_2026']
  });

  const result = await controller.submitOperation(12, 'op-12', 'controller-12');
  assert.equal(result.submitted, true);
  assert.equal(result.targetKind, 'accessibility_textbox');
  assert.ok(debuggerApi.commands.some((command) => command.method === 'DOM.focus' && command.params.backendNodeId === 77));
});

test('CDP submit waits for the accessibility composer to appear after debugger attach', async () => {
  const debuggerApi = fakeDebugger();
  const originalSendCommand = debuggerApi.sendCommand.bind(debuggerApi);
  let treeCalls = 0;
  debuggerApi.sendCommand = function(_debuggee, method, params, callback) {
    if (method === 'Accessibility.getFullAXTree') {
      this.commands.push({method, params});
      treeCalls += 1;
      if (treeCalls < 3) return callback({nodes: []});
      return callback({
        nodes: [{
          nodeId: 'ax-composer',
          backendDOMNodeId: 88,
          role: {type: 'role', value: 'textbox'},
          name: {type: 'computedString', value: 'Message'},
          value: {type: 'string', value: ''},
          ignored: false,
          properties: [
            {name: 'editable', value: {type: 'boolean', value: true}},
            {name: 'multiline', value: {type: 'boolean', value: true}},
            {name: 'focused', value: {type: 'boolean', value: false}}
          ]
        }]
      });
    }
    return originalSendCommand(_debuggee, method, params, callback);
  };

  const controller = source.createController({debuggerApi});
  controller.install();
  await controller.bindOperation({
    tabId: 13,
    operationId: 'op-13',
    controllerId: 'controller-13',
    prompt: '[PASI_OPERATION op-13] Reply with DELAYED_COMPOSER_OK_2026',
    completionMarkers: ['DELAYED_COMPOSER_OK_2026']
  });

  const result = await controller.submitOperation(13, 'op-13', 'controller-13');
  assert.equal(result.submitted, true);
  // The composer becomes available on the third AX-tree read; the fourth\n  // read is the post-submit acknowledgement check introduced to reject\n  // unacknowledged ChatGPT red-box submissions.\n  assert.equal(treeCalls, 4);
});

test('CDP submit operation refuses to overwrite unrelated editable text', async () => {
  const debuggerApi = fakeDebugger();
  const controller = source.createController({debuggerApi});
  controller.install();
  await controller.bindOperation({
    tabId: 5,
    operationId: 'op-5',
    controllerId: 'controller-5',
    prompt: '[PASI_OPERATION op-5]\\nReply with NETWORK_PATCH_OK_2026',
    completionMarkers: ['NETWORK_PATCH_OK_2026']
  });

  debuggerApi.sendCommand = function(_debuggee, method, params, callback) {
    this.commands.push({method, params});
    if (method === 'Accessibility.getFullAXTree') {
      return callback({
        nodes: [{
          nodeId: 'ax-composer',
          backendDOMNodeId: 42,
          role: {type: 'role', value: 'textbox'},
          name: {type: 'computedString', value: 'Message'},
          value: {type: 'string', value: 'user draft'},
          ignored: false,
          properties: [
            {name: 'editable', value: {type: 'boolean', value: true}},
            {name: 'multiline', value: {type: 'boolean', value: true}},
            {name: 'focused', value: {type: 'boolean', value: true}}
          ]
        }]
      });
    }
    if (method === 'Accessibility.enable' || method === 'Accessibility.disable' || method === 'DOM.focus') return callback({});
    if (method === 'Input.insertText' || method === 'Input.dispatchKeyEvent') return callback({});
    if (method === 'Fetch.enable') return callback({});
    callback({});
  };

  await assert.rejects(
    controller.submitOperation(5, 'op-5', 'controller-5'),
    /unrelated draft text/
  );
  assert.equal(debuggerApi.commands.some((command) => command.method === 'Input.insertText'), false);
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

test('CDP recovery can interrupt the active generation request without a page reload', async () => {
  const debuggerApi = fakeDebugger();
  const events = [];
  const controller = source.createController({debuggerApi, onEvent: (event) => events.push(event)});
  controller.install();
  await controller.bindOperation({
    tabId: 14,
    operationId: 'op-14',
    controllerId: 'controller-14',
    prompt: 'interrupt me'
  });

  await controller.handlePaused(
    {tabId: 14},
    'Fetch.requestPaused',
    {
      requestId: 'req-14',
      request: {
        url: 'https://chatgpt.com/backend-api/conversation',
        method: 'POST',
        postData: JSON.stringify({prompt: 'interrupt me'})
      }
    }
  );

  const result = await controller.interruptOperation(
    14,
    'op-14',
    'controller-14'
  );

  assert.equal(result.interrupted, 1);
  assert.equal(events.at(-1).eventType, 'FAILED');
  assert.equal(events.at(-1).reason, 'NETWORK_STREAM_DISCONNECTED');
  assert.equal(events.at(-1).classification, 'retryable_transport_failure');
  assert.equal(debuggerApi.commands.some((command) =>
    command.method === 'Fetch.failRequest' &&
    command.params.requestId === 'req-14'
  ), true);
  assert.equal(controller.health().activeRequests.length, 0);
});

test('detaching a tab consumes stale-tab runtime errors without rejecting cleanup', async () => {
  const debuggerApi = fakeDebugger();
  const originalDetach = debuggerApi.detach;
  const sandboxChrome = {runtime: {lastError: null}};
  sandbox.chrome = sandboxChrome;
  debuggerApi.detach = function(debuggee, callback) {
    sandboxChrome.runtime.lastError = {message: 'No tab with given id 1779180802'};
    originalDetach.call(this, debuggee, callback);
    sandboxChrome.runtime.lastError = null;
  };

  const controller = source.createController({debuggerApi});
  controller.install();
  await controller.bindOperation({
    tabId: 18,
    operationId: 'op-18',
    controllerId: 'controller-18',
    prompt: 'cleanup'
  });

  await assert.doesNotReject(() => controller.detachTab(18));
  assert.equal(controller.health().attachedTabs.length, 0);
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
