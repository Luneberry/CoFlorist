import test from 'node:test';
import assert from 'node:assert/strict';
import { createSession, applyIntent, applyAction, addMessage } from '../server/session.js';

const plan = (session, placements = [{ color: 'white', position: 'center', quantity: 1 }]) => applyIntent(session, { intent: 'arrange', placements });

function arrangedSession() {
  const session = createSession();
  plan(session);
  applyAction(session, 'start');
  applyAction(session, 'complete');
  return session;
}

function approvedSession() {
  const session = arrangedSession();
  applyIntent(session, { intent: 'approve' });
  return session;
}

function rejectsWithoutMutation(session, operation, code = 'INVALID_TRANSITION') {
  const before = structuredClone(session);
  assert.throws(operation, { code });
  assert.deepEqual(session, before);
}

test('human speaks first; messages and reset preserve monotonic revision', () => {
  const session = createSession();
  assert.equal(session.phase, 'idle');
  assert.deepEqual(session.messages, []);
  assert.equal(addMessage(session, 'user', '  그 사람에게 꽃을 선물하고 싶어.  '), session);
  assert.equal(session.messages[0].text, '그 사람에게 꽃을 선물하고 싶어.');
  assert.equal(session.revision, 1);
  rejectsWithoutMutation(session, () => addMessage(session, 'system', 'x'), 'INVALID_INPUT');
  rejectsWithoutMutation(session, () => addMessage(session, 'user', ' '), 'INVALID_INPUT');
  const beforeReset = session.revision;
  const sameReference = applyAction(session, 'reset');
  assert.equal(sameReference, session);
  assert.equal(session.revision, beforeReset + 1);
  assert.equal(session.phase, 'idle');
  assert.equal(session.receiptConfirmed, false);
  assert.deepEqual(session.messages, []);
  assert.deepEqual(session.failures, []);
});

test('pause invalidates earlier revisions and prevents new or completed physical actions until resume', () => {
  const session = createSession();
  plan(session);
  applyAction(session, 'start');
  const taskId = session.activeTask.id;
  const revisionWhenAsyncPlanStarted = session.revision;
  session.busy = true;
  applyAction(session, 'pause');
  assert.ok(session.revision > revisionWhenAsyncPlanStarted);
  assert.equal(session.busy, false);
  assert.equal(session.phase, 'paused');
  assert.equal(session.activeTask.id, taskId);
  for (const action of ['start', 'complete', 'fail', 'retry', 'lift', 'handover', 'received', 'release']) {
    rejectsWithoutMutation(session, () => applyAction(session, action), 'PAUSED');
  }
  rejectsWithoutMutation(session, () => plan(session), 'PAUSED');
  applyAction(session, 'resume');
  assert.equal(session.phase, 'placing');
  assert.equal(session.activeTask.id, taskId);
  assert.equal(session.placements[0].status, 'active');
  applyAction(session, 'complete');
  assert.equal(session.placements[0].status, 'completed');
  assert.notEqual(session.phase, 'completed');
});

test('only complete operator-confirmed layouts can be approved and approval is not delivery completion', () => {
  const session = createSession();
  rejectsWithoutMutation(session, () => applyIntent(session, { intent: 'approve' }));
  plan(session, [{ color: 'white', position: 'center', quantity: 2 }]);
  rejectsWithoutMutation(session, () => applyIntent(session, { intent: 'approve' }));
  applyAction(session, 'start');
  rejectsWithoutMutation(session, () => applyIntent(session, { intent: 'approve' }));
  applyAction(session, 'complete');
  assert.equal(session.placements.filter((item) => item.status === 'pending').length, 1);
  rejectsWithoutMutation(session, () => applyIntent(session, { intent: 'approve' }));
  applyAction(session, 'start');
  applyAction(session, 'complete');
  applyIntent(session, { intent: 'approve' });
  assert.equal(session.phase, 'approved');
  rejectsWithoutMutation(session, () => applyIntent(session, { intent: 'approve' }));
});

test('handoff requires lift, handover, receipt, and release in order with no duplicates', () => {
  const session = approvedSession();
  for (const action of ['handover', 'received', 'release']) rejectsWithoutMutation(session, () => applyAction(session, action));
  applyAction(session, 'lift');
  rejectsWithoutMutation(session, () => applyAction(session, 'lift'));
  rejectsWithoutMutation(session, () => applyAction(session, 'received'));
  rejectsWithoutMutation(session, () => applyAction(session, 'release'));
  applyAction(session, 'handover');
  assert.equal(session.phase, 'awaiting_receipt');
  rejectsWithoutMutation(session, () => applyAction(session, 'handover'));
  rejectsWithoutMutation(session, () => applyAction(session, 'release'));
  applyAction(session, 'received');
  assert.equal(session.receiptConfirmed, true);
  assert.notEqual(session.phase, 'completed');
  rejectsWithoutMutation(session, () => applyAction(session, 'received'));
  applyAction(session, 'pause');
  rejectsWithoutMutation(session, () => applyAction(session, 'release'), 'PAUSED');
  applyAction(session, 'resume');
  applyAction(session, 'release');
  assert.equal(session.phase, 'completed');
  for (const action of ['start', 'lift', 'handover', 'received', 'release']) rejectsWithoutMutation(session, () => applyAction(session, action));
});

test('failure memory changes retry methods and cannot falsely complete failed work', () => {
  const session = createSession();
  plan(session);
  applyAction(session, 'start');
  assert.equal(session.activeTask.method, 'standard');
  const taskId = session.activeTask.id;
  applyAction(session, 'fail');
  assert.equal(session.failures.length, 1);
  assert.equal(session.failures[0].actor, 'operator');
  assert.equal(session.phase, 'retry');
  assert.equal(session.placements[0].status, 'pending');
  rejectsWithoutMutation(session, () => applyAction(session, 'complete'));
  rejectsWithoutMutation(session, () => applyAction(session, 'start'));
  applyAction(session, 'retry');
  assert.equal(session.activeTask.id, taskId);
  assert.equal(session.activeTask.method, 'alternate');
  applyAction(session, 'fail');
  applyAction(session, 'retry');
  assert.equal(session.activeTask.method, 'adjusted');
  applyAction(session, 'fail');
  rejectsWithoutMutation(session, () => applyAction(session, 'retry'), 'RETRY_EXHAUSTED');
  assert.equal(session.failures.length, 3);
});

test('changes preserve completed placements and replace only pending work', () => {
  const session = createSession();
  plan(session, [{ color: 'white', position: 'center', quantity: 2 }]);
  applyAction(session, 'start');
  const completedId = session.activeTask.id;
  rejectsWithoutMutation(session, () => applyIntent(session, { intent: 'change', placements: [{ color: 'red', position: 'right', quantity: 1 }] }));
  applyAction(session, 'complete');
  applyIntent(session, { intent: 'change', placements: [{ color: 'red', position: 'right', quantity: 1 }], shortExplanation: true });
  assert.equal(session.placements.length, 2);
  assert.equal(session.placements[0].id, completedId);
  assert.equal(session.placements[0].status, 'completed');
  assert.equal(session.placements[1].color, 'red');
  assert.equal(session.placements[1].status, 'pending');
  assert.equal(session.preferences.shortExplanation, true);
  applyIntent(session, { intent: 'chat' });
  assert.equal(session.preferences.shortExplanation, true);
  applyIntent(session, { intent: 'change', placements: [] });
  assert.equal(session.placements.length, 1);
  applyIntent(session, { intent: 'approve' });
  assert.equal(session.phase, 'approved');
});

test('valid counts expand into individually confirmed flowers; invalid plans are atomic', () => {
  const session = createSession();
  plan(session, [{ color: 'red', position: 'right', quantity: 3 }]);
  assert.equal(session.placements.length, 3);
  assert.equal(new Set(session.placements.map((item) => item.id)).size, 3);
  assert.ok(session.placements.every((item) => item.quantity === 1 && item.status === 'pending'));
  for (const quantity of [0, -1, 1.5, '2', null, 13, Infinity, NaN]) {
    rejectsWithoutMutation(session, () => plan(session, [{ color: 'white', position: 'center', quantity }]), 'INVALID_INPUT');
  }
  for (const placements of [[], null, [{ color: 'blue', position: 'center', quantity: 1 }], [{ color: 'red', position: 'top', quantity: 1 }], [{ color: 'red', position: 'center', quantity: 8 }, { color: 'white', position: 'left', quantity: 5 }]]) {
    rejectsWithoutMutation(session, () => plan(session, placements), 'INVALID_INPUT');
  }
  rejectsWithoutMutation(session, () => applyIntent(session, { intent: 'change', placements: [], shortExplanation: 'yes' }), 'INVALID_INPUT');
  rejectsWithoutMutation(session, () => applyAction(session, 'fly'), 'INVALID_INPUT');
});

test('completed flowers count toward the twelve-flower limit and failed-plan replacement retains memory', () => {
  const session = arrangedSession();
  rejectsWithoutMutation(session, () => plan(session, [{ color: 'red', position: 'right', quantity: 12 }]), 'INVALID_INPUT');
  plan(session, [{ color: 'red', position: 'right', quantity: 1 }]);
  applyAction(session, 'start');
  applyAction(session, 'fail');
  applyIntent(session, { intent: 'change', placements: [{ color: 'red', position: 'right', quantity: 1 }] });
  assert.equal(session.activeTask, null);
  assert.equal(session.failures.length, 1);
  applyAction(session, 'start');
  assert.equal(session.activeTask.method, 'alternate');
});
