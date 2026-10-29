import test from 'node:test';
import assert from 'node:assert/strict';
import { createFeedState, applyFeedEvent, brainBadge } from '../public/conversation-feed.js';

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

test('assistant stimulus and flower survive replay deduplication with stable turn seeds', () => {
  const state = createFeedState();
  const first = { id: 'a1', type: 'message', role: 'assistant', text: '백합을 준비할게요.', flower: '백합', flower_color: 'white', emotion: 'calm', stimulus_hz: 60 };
  const second = { id: 'a2', type: 'message', role: 'assistant', text: '장미도 좋아요.', flower: '장미', flower_color: 'red', emotion: 'happy', stimulus_hz: 120 };
  assert.equal(applyFeedEvent(state, first), true);
  assert.equal(applyFeedEvent(state, first), false);
  assert.equal(applyFeedEvent(state, second), true);
  assert.deepEqual(state.messages.map(item => [item.turn, item.stimulusHz, item.flowerColor]), [[1, 60, 'white'], [2, 120, 'red']]);
  assert.equal(state.turnCount, 2);
  assert.equal(brainBadge(0, 1, 5), 'wait');
  assert.equal(brainBadge(3, 1, 7), 'reach');
  assert.equal(brainBadge(7, 1, 7), 'offer');
});

test('invalid events cannot change displayed state or run operator actions', () => {
  const state = createFeedState();
  for (const event of [null, {}, { id: 'bad', type: 'action', action: 'release' },
    { id: 'bad', type: 'state', state: 'completed' },
    { id: 'bad', type: 'message', role: 'system', text: 'x' },
    { id: 'bad', type: 'message', role: 'user', text: 'x'.repeat(2001) },
    { id: 'bad', type: 'message', role: 'assistant', text: 'x', stimulus_hz: 251 },
    { id: 'bad', type: 'message', role: 'assistant', text: 'x', flower_color: 'green' }]) {
    assert.throws(() => applyFeedEvent(state, event));
    assert.deepEqual(state, createFeedState());
  }
});
