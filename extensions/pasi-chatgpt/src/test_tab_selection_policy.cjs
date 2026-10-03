const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');

const sourcePath = require.resolve('./tab-selection-policy.js');
const sourceText = fs.readFileSync(sourcePath, 'utf8');
const sandbox = {globalThis: {}};
vm.runInNewContext(sourceText, sandbox, {filename: sourcePath});
const policy = sandbox.globalThis.PASI_TAB_SELECTION_POLICY;

const chatTab = (id, overrides = {}) => ({
  id,
  windowId: 1,
  active: false,
  pinned: false,
  status: 'complete',
  url: 'https://chatgpt.com/c/test-' + id,
  ...overrides,
});

test('safe tab policy prefers an existing inactive tab over the active tab by default', () => {
  const result = policy.selectReusableTab([
    chatTab(1, {active: true}),
    chatTab(2, {active: false}),
  ]);

  assert.equal(result.selected_tab_id, 2);
  assert.equal(result.reused_existing, true);
  assert.equal(result.reason, 'inactive_reuse');
});

test('safe tab policy never reuses a tab with active PASI work', () => {
  const result = policy.selectReusableTab([
    chatTab(1, {active: true}),
    chatTab(2, {active: false}),
  ], {
    busyTabIds: [2],
  });

  assert.equal(result.selected_tab_id, 1);
  assert.equal(result.candidates.find((item) => item.tab_id === 2).eligible, false);
  assert.equal(
    result.candidates.find((item) => item.tab_id === 2).reasons[0],
    'active_pasi_work',
  );
});

test('safe tab policy excludes pinned tabs unless explicitly permitted', () => {
  const result = policy.selectReusableTab([
    chatTab(1, {pinned: true}),
    chatTab(2, {active: false}),
  ]);

  assert.equal(result.selected_tab_id, 2);
  assert.equal(result.candidates.find((item) => item.tab_id === 1).eligible, false);

  const permitted = policy.selectReusableTab([
    chatTab(1, {pinned: true}),
  ], {allowPinned: true});
  assert.equal(permitted.selected_tab_id, 1);
});

test('safe tab policy honors an explicit preferred tab', () => {
  const result = policy.selectReusableTab([
    chatTab(1, {active: true}),
    chatTab(2, {active: false}),
    chatTab(3, {active: false}),
  ], {
    preferredTabId: 1,
  });

  assert.equal(result.selected_tab_id, 1);
  assert.equal(result.reason, 'preferred_tab');
});

test('safe tab policy falls back to the active ChatGPT tab when it is the only safe tab', () => {
  const result = policy.selectReusableTab([
    chatTab(7, {active: true}),
    {id: 8, active: false, status: 'complete', url: 'https://github.com/example/repo'},
  ]);

  assert.equal(result.selected_tab_id, 7);
  assert.equal(result.reused_existing, true);
});

test('safe tab policy reports when no existing tab is safe to reuse', () => {
  const result = policy.selectReusableTab([
    chatTab(1, {active: true}),
    chatTab(2, {pinned: true, active: false}),
    {id: 3, active: false, status: 'complete', url: 'https://example.com/'},
  ], {
    busyTabIds: [1],
  });

  assert.equal(result.selected_tab_id, null);
  assert.equal(result.reused_existing, false);
  assert.equal(result.reason, 'no_safe_existing_tab');
});
