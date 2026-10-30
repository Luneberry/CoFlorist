import test from 'node:test';
import assert from 'node:assert/strict';
import { inspectWav } from '../server/local-speech.js';

function chunk(name, payload) {
  const header = Buffer.alloc(8);
  header.write(name); header.writeUInt32LE(payload.length, 4);
  return Buffer.concat([header, payload, ...(payload.length % 2 ? [Buffer.alloc(1)] : [])]);
}

function wav(dataBytes = 72000) {
  const fmt = Buffer.alloc(16);
  fmt.writeUInt16LE(1, 0); fmt.writeUInt16LE(1, 2); fmt.writeUInt32LE(24000, 4);
  fmt.writeUInt32LE(48000, 8); fmt.writeUInt16LE(2, 12); fmt.writeUInt16LE(16, 14);
  const body = Buffer.concat([chunk('fmt ', fmt), chunk('JUNK', Buffer.from('abc')), chunk('data', Buffer.alloc(dataBytes))]);
  const header = Buffer.alloc(12);
  header.write('RIFF'); header.writeUInt32LE(body.length + 4, 4); header.write('WAVE', 8);
  return Buffer.concat([header, body]);
}

test('WAV inspection measures real PCM duration and skips padded auxiliary chunks', () => {
  const bytes = wav();
  const result = inspectWav(bytes);
  assert.equal(result.durationMs, 1500);
  assert.equal(result.sampleRate, 24000);
  assert.equal(result.channels, 1);
  assert.equal(result.bitsPerSample, 16);
  assert.equal(result.encoding, 1);
  assert.throws(() => inspectWav(bytes.subarray(0, bytes.length - 7)), /완전하지/);
  assert.throws(() => inspectWav(Buffer.alloc(60)), /WAV/);
  assert.throws(() => inspectWav(wav(0)), /비어/);
});
