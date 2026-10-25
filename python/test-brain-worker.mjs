import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { BrainWorker, DEFAULT_BRAIN_DATA } from '../server/brain-worker.js';

const brain = new BrainWorker();
try {
  const started = await brain.start();
  assert.equal(started.status, 'ready');
  assert.equal(started.mode, 'flywire');
  const baseline = await brain.run({ stimulusHz: 0, durationMs: 100, seed: 42 });
  const active = await brain.run({ stimulusHz: 100, durationMs: 200, seed: 42 });
  const repeat = await brain.run({ stimulusHz: 100, durationMs: 200, seed: 42 });
  assert.equal(baseline.spikeCount, 0, 'Unstimulated isolated LIF circuit starts at rest.');
  assert(active.spikeCount > 0, 'Real stimulated circuit must produce measured spikes.');
  assert(active.neuronCount >= 128 && active.neuronCount <= 512);
  assert(active.connectionCount > 0);
  assert.equal(active.spikeCount, repeat.spikeCount, 'Same seed produces the same spike count.');
  assert.deepEqual(active.nodes, repeat.nodes, 'Same seed produces the same node rates.');
  assert(active.nodes.every((node) => typeof node.id === 'string'));
  const { stimulusIds } = JSON.parse(readFileSync(DEFAULT_BRAIN_DATA, 'utf8'));
  const silenced = await brain.run({ stimulusHz: 100, durationMs: 200, seed: 42, silenceIds: stimulusIds });
  assert.equal(silenced.downstreamRateHz, 0, 'Silencing every stimulus output removes downstream propagation.');
  assert(silenced.spikeCount > 0, 'Directly stimulated sensory neurons still spike.');
  const invalid = brain.run({ stimulusHz: 999 });
  await assert.rejects(invalid, /stimulusHz/);
  assert.equal(brain.snapshot().status, 'ready', 'Validation errors do not kill healthy worker.');
  console.log(JSON.stringify({ passed: true, baselineSpikes: baseline.spikeCount,
    activeSpikes: active.spikeCount, activeNeurons: active.activeNeurons,
    silencedDownstreamRateHz: silenced.downstreamRateHz,
    downstreamRateHz: active.downstreamRateHz, neuronCount: active.neuronCount,
    connectionCount: active.connectionCount, initialRunMs: active.wallTimeMs,
    repeatRunMs: repeat.wallTimeMs, datasetVersion: active.datasetVersion,
    dataHash: active.dataHash, sourceCommit: active.sourceCommit }, null, 2));
  const pending = brain.run();
  brain.stop();
  await assert.rejects(pending, /stopped/);
  assert.equal(brain.snapshot().status, 'idle');
} finally {
  brain.stop();
}
