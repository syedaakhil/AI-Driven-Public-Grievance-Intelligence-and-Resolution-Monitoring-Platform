// Button states of the Agent Work "Collect data" button (static/js/agent-work.js).
// Run by mlops.tests.ButtonStateJsTests, or directly: node --test mlops/tests_js/agent-work.test.js
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const { buttonState, badgeFor } = require(path.join(__dirname, '..', '..', 'static', 'js', 'agent-work.js'));

test('default: no job yet', () => {
  assert.deepEqual(buttonState(null), { state: 'idle', label: 'Collect data', disabled: false, busy: false });
});

test('loading while the request is in flight', () => {
  const view = buttonState(null, { requesting: true });
  assert.equal(view.state, 'loading');
  assert.equal(view.disabled, true);
  assert.equal(view.busy, true);
});

test('loading while the job is queued or running', () => {
  assert.equal(buttonState({ status: 'queued' }).label, 'Starting…');
  const running = buttonState({ status: 'running' });
  assert.equal(running.state, 'loading');
  assert.equal(running.label, 'Collecting…');
  assert.equal(running.disabled, true);
});

test('success after the job finished', () => {
  const view = buttonState({ status: 'succeeded' });
  assert.equal(view.state, 'success');
  assert.equal(view.label, 'Collect again');
  assert.equal(view.disabled, false);
});

test('error when the job failed or the request failed', () => {
  assert.equal(buttonState({ status: 'failed' }).state, 'error');
  const view = buttonState({ status: 'succeeded' }, { requestError: true });
  assert.equal(view.state, 'error');
  assert.equal(view.label, 'Try again');
  assert.equal(view.disabled, false);
});

test('disabled wins over everything (e.g. the session ended)', () => {
  const view = buttonState({ status: 'running' }, { disabled: true, requesting: true });
  assert.equal(view.state, 'disabled');
  assert.equal(view.disabled, true);
  assert.equal(view.busy, false);
});

test('badges', () => {
  assert.equal(badgeFor(null).cls, 'badge--plain');
  assert.equal(badgeFor({ is_active: true, status: 'running' }).cls, 'badge--medium');
  assert.equal(badgeFor({ is_active: false, status: 'failed' }).cls, 'badge--high');
  assert.equal(badgeFor({ is_active: false, status: 'succeeded' }).cls, 'badge--resolved');
});
