import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
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
  const thresholds = { mn9ReachHz: 15, downstreamOfferHz: 1, fallbackReachHz: 7 };
  assert.equal(brainBadge(0.5, 0, thresholds), 'wait');
  assert.equal(brainBadge(4.4, 0, thresholds), 'offer');
  assert.equal(brainBadge(5.9, 10, thresholds), 'offer');
  assert.equal(brainBadge(8, 20, thresholds), 'reach');
  assert.equal(brainBadge(8, null, thresholds), 'reach');
  assert.equal(brainBadge(3, null, thresholds), 'offer');
});

test('invalid events cannot change displayed state or run operator actions', () => {
  const state = createFeedState();
  for (const event of [null, {}, { id: 'bad', type: 'action', action: 'release' },
    { id: 'bad', type: 'state', state: 'completed' },
    { id: 'bad', type: 'message', role: 'system', text: 'x' },
    { id: 'bad', type: 'message', role: 'user', text: 'x'.repeat(2001) },
    { id: 'bad', type: 'message', role: 'assistant', text: 'x', stimulus_hz: 251 }]) {
    assert.throws(() => applyFeedEvent(state, event));
    assert.deepEqual(state, createFeedState());
  }
});

test('unsupported flower colours keep the reply and circuit input but cannot queue a flower', () => {
  const state = createFeedState();
  for (const [id, flower_color] of [['a1', 'green'], ['a2', 42]]) {
    assert.equal(applyFeedEvent(state, { id, type: 'message', role: 'assistant', text: '꽃 얘기예요.', flower: '장미', flower_color, stimulus_hz: 60 }), true);
  }
  assert.equal(state.turnCount, 2);
  assert.deepEqual(state.messages.map(message => [message.text, message.flowerColor, message.stimulusHz]), [
    ['꽃 얘기예요.', '', 60], ['꽃 얘기예요.', '', 60],
  ]);
});

test('pluggable Flora sample follows the feed contract with three circuit turns', () => {
  const { events } = JSON.parse(readFileSync(new URL('../examples/flora-feed-sample.json', import.meta.url), 'utf8'));
  const state = createFeedState();
  assert.equal(new Set(events.map(event => event.id)).size, events.length);
  for (const event of events) assert.equal(applyFeedEvent(state, event), true);
  assert.equal(state.phase, 'error');
  assert.deepEqual(state.messages.filter(message => message.role === 'assistant').map(message => message.stimulusHz), [20, 60, 120]);
  assert.equal(state.turnCount, 3);
  for (const event of events) assert.equal(applyFeedEvent(state, event), false);
  assert.equal(state.turnCount, 3);
});
