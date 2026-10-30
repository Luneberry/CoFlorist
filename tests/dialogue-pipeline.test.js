import test from 'node:test';
import assert from 'node:assert/strict';
import { encodeDialogue, brainReadout, computeDialogueBrain } from '../server/dialogue-pipeline.js';
import { chat, DEFAULT_MODEL } from '../server/nebius.js';
import { createSession, addMessage } from '../server/session.js';

function resultFor(input, overrides = {}) {
  return { mode: 'flywire', computeMode: 'live', scope: 'induced-subset',
    stimulusHz: input.stimulusHz, seed: input.seed, durationMs: input.durationMs,
    downstreamRateHz: 18.5, activeNeurons: 82, spikeCount: 431,
    dataHash: 'test-circuit-hash', ...overrides };
}

test('dialogue encoding is reproducible and gives explicit pause/retry commands priority', () => {
  const cases = [
    ['잠깐 기다려. 다시 잡지 마.', 'pause', 0],
    ['다시 잡아줘.', 'retry', 120],
    ['천천히 해줘. 어떤 꽃이 좋을지 고민이야.', 'hesitant', 40],
    ['선물할 흰 꽃 한 송이를 가운데 놓아줘.', 'arrange', 100],
    ['안녕, 꽃 선물하고 싶은데 추천해줘. 잔잔한 향기를 좋아하고 겨울에 어울리는 꽃이면 좋겠어.', 'gift', 80],
    ['안녕!', 'chat', 60],
  ];
  for (const [text, intent, stimulusHz] of cases) {
    const encoded = encodeDialogue(text, { seed: 7 });
    assert.deepEqual(encoded, { intent, stimulusHz, durationMs: 200, seed: 7 });
    assert.deepEqual(encodeDialogue(text, { seed: 7 }), encoded);
  }
  for (const text of ['', ' ', null, 3, 'x'.repeat(2001)]) assert.throws(() => encodeDialogue(text));
  for (const seed of [-1, 1.5, NaN, 2147483648]) assert.throws(() => encodeDialogue('안녕', { seed }));
});

test('measured propagation changes the readout and stale or fabricated results are rejected', () => {
  const input = encodeDialogue('겨울 꽃을 추천해줘.');
  const measured = resultFor(input);
  const positive = brainReadout(measured, input);
  assert.equal(positive.action, 'propose');
  assert.equal(positive.responseStyle, 'concise_suggestion');
  assert.equal(positive.basis, 'engineered_mapping');
  assert.equal(positive.activeNeurons, measured.activeNeurons);
  assert.equal(positive.spikeCount, measured.spikeCount);
  assert.equal(brainReadout(resultFor(input, { downstreamRateHz: 0 }), input).action, 'confirm');
  const paused = encodeDialogue('잠깐 기다려.');
  assert.equal(brainReadout(resultFor(paused, { downstreamRateHz: 0, activeNeurons: 0, spikeCount: 0 }), paused).action, 'wait');
  for (const bad of [
    { mode: 'simulated' }, { computeMode: 'cached' }, { scope: 'full-brain' },
    { downstreamRateHz: NaN }, { activeNeurons: -1 }, { spikeCount: '431' },
    { seed: 1 }, { stimulusHz: 120 }, { durationMs: 300 },
  ]) assert.throws(() => brainReadout(resultFor(input, bad), input));
});

test('brain computation must finish before its readout can be consumed; worker failures are propagated', async () => {
  let resolveBrain;
  let receivedInput;
  let finished = false;
  const brain = { run(input) { receivedInput = input; return new Promise(resolve => { resolveBrain = resolve; }); } };
  const pending = computeDialogueBrain({ text: '꽃을 추천해줘.', brain, seed: 8 }).then(value => { finished = true; return value; });
  await Promise.resolve();
  assert.equal(finished, false);
  assert.equal(receivedInput.seed, 8);
  assert.equal(receivedInput.stimulusHz, 80);
  resolveBrain(resultFor(receivedInput));
  const value = await pending;
  assert.equal(value.readout.spikeCount, 431);
  assert.deepEqual(value.input, receivedInput);
  const failure = new Error('Actual circuit unavailable');
  await assert.rejects(computeDialogueBrain({ text: '안녕', brain: { run: async () => { throw failure; } } }), failure);
});

test('cancellation before or during a circuit trial prevents use of its result', async () => {
  const before = new AbortController(); before.abort();
  let calls = 0;
  await assert.rejects(computeDialogueBrain({ text: '꽃 추천', signal: before.signal,
    brain: { run: async input => { calls++; return resultFor(input); } } }), { status: 409 });
  assert.equal(calls, 0);
  const during = new AbortController();
  let finish;
  let input;
  const pending = computeDialogueBrain({ text: '꽃 추천', signal: during.signal,
    brain: { run: value => { input = value; return new Promise(resolve => { finish = resolve; }); } } });
  during.abort();
  finish(resultFor(input));
  await assert.rejects(pending, { status: 409 });
});

test('Nebius receives the current actual circuit readout together with the user text', async () => {
  const session = createSession();
  const text = '잔잔한 향기가 나는 겨울 꽃을 추천해줘.';
  addMessage(session, 'user', text);
  const measured = await computeDialogueBrain({ text, seed: 9, brain: { run: async input => resultFor(input) } });
  session.neuralDecision = measured.readout;
  const originalFetch = globalThis.fetch;
  let submitted;
  globalThis.fetch = async (_url, options) => {
    submitted = JSON.parse(options.body);
    return Response.json({ model: DEFAULT_MODEL, choices: [{ message: { content: JSON.stringify({ reply: '겨울 분위기의 흰 꽃에 향이 약한 장미를 조금 섞어 보세요.', intent: 'gift', placements: [] }) } }] });
  };
  try {
    const response = await chat('test-only-key', DEFAULT_MODEL, session, {});
    assert.equal(response.intent, 'gift');
    assert.deepEqual(submitted.messages.at(-1), { role: 'user', content: text });
    const system = submitted.messages[0].content;
    assert.equal(system.includes('초파리 신경망은 문장이나 의미를 생성하지 않는다'), true);
    const appState = JSON.parse(system.split('\n현재 앱 상태: ')[1]);
    assert.deepEqual(appState.neuralDecision, measured.readout);
    assert.equal(appState.neuralDecision.seed, 9);
    assert.equal(submitted.response_format.type, 'json_object');
    assert.equal(JSON.stringify(response).includes('test-only-key'), false);
  } finally { globalThis.fetch = originalFetch; }
});
