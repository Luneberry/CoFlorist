import test from 'node:test';
import assert from 'node:assert/strict';
import { createFeedState, applyFeedEvent } from '../public/conversation-feed.js';

test('external conversation displays text and states without duplicating reconnect events', () => {
  const state = createFeedState();
  const event = { id: 'm1', type: 'message', role: 'user', text: '꽃을 선물하고 싶어.' };
  assert.equal(applyFeedEvent(state, event), true);
  assert.equal(applyFeedEvent(state, event), false);
  assert.equal(state.messages.length, 1);
  applyFeedEvent(state, { id: 's1', type: 'state', state: 'thinking' });
  assert.equal(state.phase, 'thinking');
  applyFeedEvent(state, { id: 'm2', type: 'message', role: 'assistant', text: '<script>text only</script>' });
  assert.equal(state.messages[1].text, '<script>text only</script>');
  assert.equal(state.messages[1].role, 'assistant');
});

test('invalid events cannot change displayed state or run operator actions', () => {
  const state = createFeedState();
  for (const event of [null, {}, { id: 'bad', type: 'action', action: 'release' },
    { id: 'bad', type: 'state', state: 'completed' },
    { id: 'bad', type: 'message', role: 'system', text: 'x' },
    { id: 'bad', type: 'message', role: 'user', text: 'x'.repeat(2001) }]) {
    assert.throws(() => applyFeedEvent(state, event));
    assert.deepEqual(state, createFeedState());
  }
});
