import test from 'node:test';
import assert from 'node:assert/strict';
import { DEFAULT_MODEL, validateModel, parseResponse, listModels, testConnection, chat } from '../server/nebius.js';
import { createSession, applyIntent } from '../server/session.js';

const response = (overrides = {}) => JSON.stringify({
  reply: '흰 꽃 한 송이를 가운데에 놓는 작업을 준비할게요.',
  intent: 'arrange',
  placements: [{ color: 'white', position: 'center', quantity: 1 }],
  shortExplanation: true,
  ...overrides,
});

test('parser accepts structured responses and strips provider reasoning and fences', () => {
  const plain = parseResponse(response());
  assert.equal(plain.intent, 'arrange');
  assert.equal(plain.placements[0].quantity, 1);
  assert.equal(plain.shortExplanation, true);
  assert.deepEqual(parseResponse('<think>not a visible reply</think>\n```json\n' + response() + '\n```'), plain);
  assert.equal(parseResponse(response({ reply: '  기다릴게요. ', intent: 'pause', placements: [] })).reply, '기다릴게요.');
  assert.equal(parseResponse(response({ shortExplanation: false })).shortExplanation, false);
});

test('parser rejects broken JSON, invented actions, empty replies and invalid placements', () => {
  for (const content of [null, undefined, {}, '', 'not JSON', '{', 'null', '[]', 'Here is JSON: ' + response(), '```json\n{}\n```']) {
    assert.throws(() => parseResponse(content));
  }
  for (const overrides of [
    { intent: 'execute_robot' }, { intent: 'completed' }, { reply: '' }, { reply: ' ' }, { reply: 'x'.repeat(1201) },
    { placements: 'white' }, { placements: Array.from({ length: 13 }, () => ({ color: 'red', position: 'right', quantity: 1 })) },
    { placements: [{ color: 'blue', position: 'center', quantity: 1 }] },
    { placements: [{ color: 'white', position: 'behind_person', quantity: 1 }] },
    ...[0, -1, 0.5, '1', null, 13].map(quantity => ({ placements: [{ color: 'white', position: 'center', quantity }] })),
  ]) assert.throws(() => parseResponse(response(overrides)));
});

test('model validation allows Nemotron and excludes other providers and missing IDs', () => {
  assert.equal(validateModel(DEFAULT_MODEL), DEFAULT_MODEL);
  assert.equal(validateModel('nvidia/Nemotron-Another-Valid-Variant'), 'nvidia/Nemotron-Another-Valid-Variant');
  for (const model of [null, '', 'nemotron', 'openai/nemotron', 'nvidia/llama', 'nvidia/nemotron bad', 'nvidia/' + 'x'.repeat(180) + 'nemotron']) {
    assert.throws(() => validateModel(model));
  }
});

test('missing credentials fail locally and cannot fabricate a successful model connection', async () => {
  await assert.rejects(listModels(''), { status: 400 });
  await assert.rejects(testConnection('', DEFAULT_MODEL), { status: 400 });
  await assert.rejects(chat('', DEFAULT_MODEL, createSession(), {}), { status: 400 });
});

test('parsed plans still pass the state gate: a compound over-limit plan cannot mutate the session', () => {
  const session = createSession();
  const parsed = parseResponse(response({ placements: [
    { color: 'white', position: 'center', quantity: 8 },
    { color: 'red', position: 'right', quantity: 5 },
  ] }));
  const before = structuredClone(session);
  assert.throws(() => applyIntent(session, parsed), { code: 'INVALID_INPUT' });
  assert.deepEqual(session, before);
});

test('a model approval cannot invent physical placement or delivery success', () => {
  const session = createSession();
  const parsed = parseResponse(response({ intent: 'approve', placements: [] }));
  assert.throws(() => applyIntent(session, parsed), { code: 'INVALID_TRANSITION' });
  assert.equal(session.phase, 'idle');
  assert.equal(session.placements.length, 0);
});
