from __future__ import annotations

import json
import subprocess
from pathlib import Path


CONTROLLER = Path(__file__).resolve().parents[1] / "extensions" / "pasi-chatgpt" / "src" / "cdp-network-controller.js"


def run_node(script: str) -> None:
    result = subprocess.run(
        ["node", "--input-type=commonjs", "-e", script],
        cwd=CONTROLLER.parents[3],
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"node test failed (exit {result.returncode})\\nSTDOUT:\\n{result.stdout}\\nSTDERR:\\n{result.stderr}"
        )


def controller_loader() -> str:
    path = json.dumps(str(CONTROLLER))
    return f"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const sourcePath = {path};
const sourceText = fs.readFileSync(sourcePath, 'utf8');
const sandbox = {{
  module: {{exports: {{}}}},
  exports: {{}},
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
}};
vm.runInNewContext(sourceText, sandbox, {{filename: sourcePath}});
const cdp = sandbox.module.exports;
"""


def test_done_marker_requires_authoritative_assistant_completion_signal() -> None:
    run_node(
        controller_loader()
        + r"""
const completeState = {
  responseText: '',
  assistantMessageId: null,
  terminal: null,
  streamComplete: false,
  doneMarkerSeen: false,
  assistantCompletionVerified: false,
  assistantCompletionStatus: null,
  assistantCompletionSource: null
};
cdp.parseSseText(
  'data: ' + JSON.stringify({
    message: {
      id: 'assistant-1',
      author: {role: 'assistant'},
      content: {parts: ['complete response']},
      status: 'finished_successfully'
    }
  }) + '\n' +
  'data: [DONE]\n',
  completeState
);
assert.equal(completeState.doneMarkerSeen, true);
assert.equal(completeState.assistantCompletionVerified, true);
assert.equal(cdp.authoritativeStreamComplete(completeState), true);

const ambiguousState = {
  responseText: '',
  assistantMessageId: null,
  terminal: null,
  streamComplete: false,
  doneMarkerSeen: false,
  assistantCompletionVerified: false,
  assistantCompletionStatus: null,
  assistantCompletionSource: null
};
cdp.parseSseText(
  'data: ' + JSON.stringify({
    message: {
      id: 'assistant-2',
      author: {role: 'assistant'},
      content: {parts: ['partial response']},
      status: 'in_progress'
    }
  }) + '\n' +
  'data: [DONE]\n',
  ambiguousState
);
assert.equal(ambiguousState.doneMarkerSeen, true);
assert.equal(ambiguousState.assistantCompletionVerified, false);
assert.equal(cdp.authoritativeStreamComplete(ambiguousState), false);
"""
    )


def test_generation_request_is_correlated_once_per_operation() -> None:
    run_node(
        controller_loader()
        + r"""
const events = [];
const debuggerApi = {
  attach(_target, _version, callback) { callback(); },
  detach(_target, callback) { callback(); },
  sendCommand(_target, method, _params, callback) {
    if (method === 'Accessibility.enable' || method === 'Fetch.enable') callback({});
    else callback({});
  },
  onEvent: {addListener() {}},
  onDetach: {addListener() {}}
};

const controller = cdp.createController({
  debuggerApi,
  onEvent: (event) => events.push(event)
});

(async () => {
  await controller.bindOperation({
    tabId: 7,
    operationId: 'op-1',
    controllerId: 'cdp-tab:7',
    prompt: 'exact prompt',
    completionMarkers: [],
    chatUrl: 'https://chatgpt.com/c/example'
  });

  const request = (requestId) => ({
    requestId,
    request: {
      method: 'POST',
      url: 'https://chatgpt.com/backend-api/conversation',
      postData: JSON.stringify({messages: [{content: {parts: ['exact prompt']}}]})
    }
  });

  await controller.handlePaused({tabId: 7}, 'Fetch.requestPaused', request('req-1'));
  await controller.handlePaused({tabId: 7}, 'Fetch.requestPaused', request('req-2'));

  const starts = events.filter((event) => event.eventType === 'STARTED');
  assert.equal(starts.length, 1);
  assert.equal(starts[0].requestId, 'req-1');
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
"""
    )
