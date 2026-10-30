import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createServer } from 'node:net';
import { request as httpRequest } from 'node:http';
import { mkdir, mkdtemp, readFile, writeFile, rm } from 'node:fs/promises';
import { once } from 'node:events';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const MODEL = 'nvidia/nemotron-3-super-120b-a12b';

async function availablePort() {
  const listener = createServer();
  listener.listen(0, '127.0.0.1');
  await once(listener, 'listening');
  const port = listener.address().port;
  await new Promise((resolve, reject) => listener.close(error => error ? reject(error) : resolve()));
  return port;
}

async function startServer(t, key = '') {
  // Production audio is under .runtime: exercise a dotted parent directory.
  const runtime = await mkdtemp(path.join(tmpdir(), '.coflorist-api-test-'));
  const port = await availablePort();
  const base = 'http://127.0.0.1:' + port;
  const child = spawn(process.execPath, [path.join(ROOT, 'server/app.js')], {
    cwd: ROOT,
    env: { ...process.env, PORT: String(port), COFLORIST_RUNTIME_DIR: runtime, NEBIUS_API_KEY: key, NEBIUS_MODEL: MODEL },
    windowsHide: true, shell: false, stdio: ['ignore', 'pipe', 'pipe'],
  });
  let output = '';
  child.stdout.on('data', data => { output += data.toString(); });
  child.stderr.on('data', data => { output += data.toString(); });
  const childExit = once(child, 'exit');
  t.after(async () => {
    if (child.exitCode === null) child.kill('SIGTERM');
    await childExit;
    if (path.dirname(runtime) !== path.resolve(tmpdir()) || !path.basename(runtime).startsWith('.coflorist-api-test-')) {
      throw new Error('Refusing cleanup outside the isolated test directory.');
    }
    await rm(runtime, { recursive: true, force: true });
  });
  const request = async (url, body, headers = {}) => {
    const result = await fetch(base + url, {
      method: body === undefined ? 'GET' : 'POST',
      headers: { ...(body === undefined ? {} : { 'Content-Type': 'application/json' }), ...headers },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      signal: AbortSignal.timeout(4000),
    });
    const text = await result.text();
    let data;
    try { data = JSON.parse(text); } catch { data = null; }
    return { status: result.status, headers: result.headers, text, data };
  };
  let started = false, startupFailure = '';
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (child.exitCode !== null) throw new Error('Test server exited before startup: ' + output);
    try {
      const health = await request('/api/health');
      started = health.status === 200;
      startupFailure = 'HTTP ' + health.status + ': ' + health.text;
    } catch (error) { startupFailure = error.message + (error.cause?.code ? ' (' + error.cause.code + ')' : ''); }
    if (started) break;
    await delay(50);
  }
  assert.equal(started, true, 'Test server must start on its isolated localhost port. ' + startupFailure + '\n' + output);
  return { runtime, base, request, output: () => output };
}

test('main conversation API preserves environment credentials and operator-only transitions', { timeout: 25000 }, async t => {
  const { runtime, base, request } = await startServer(t);

  await t.test('health and missing-key calls report real state without fabricating progress', async () => {
    assert.deepEqual((await request('/api/health')).data, { ok: true, name: 'CoFlorist' });
    const state = await request('/api/state');
    assert.equal(state.data.settings.apiConfigured, false);
    assert.equal(state.data.settings.connectionEnabled, true);
    assert.equal(state.data.settings.model, MODEL);
    assert.equal(state.data.session.phase, 'idle');
    assert.deepEqual(state.data.session.messages, []);
    assert.equal(state.headers.get('cache-control'), 'no-store');
    assert.equal((await request('/api/chat', { text: '그 사람에게 꽃을 선물하고 싶어.' })).status, 400);
    assert.deepEqual((await request('/api/state')).data.session.messages, []);
    const transcribe = await fetch(base + '/api/transcribe', {
      method: 'POST', headers: { 'Content-Type': 'audio/webm' }, body: Buffer.alloc(100), signal: AbortSignal.timeout(4000),
    });
    assert.equal(transcribe.status, 400);
  });

  await t.test('separate demo and external-connection routes are no longer exposed', async () => {
    for (const [url, body] of [
      ['/api/models'], ['/api/connect', {}], ['/api/external/flower', { eventId: 'x', color: 'white' }],
      ['/api/voice-demo'], ['/api/voice-demo/run', {}],
    ]) assert.equal((await request(url, body)).status, 404, url);
    const oldPage = await fetch(base + '/voice-demo.html', { redirect: 'manual', signal: AbortSignal.timeout(4000) });
    assert.equal(oldPage.status, 302);
    assert.equal(oldPage.headers.get('location'), '/');
  });

  await t.test('invalid requests cannot invent model replies or robot progress', async () => {
    const before = (await request('/api/state')).data.session;
    for (const text of [23, {}, null, '', 'x'.repeat(2001)]) assert.equal((await request('/api/chat', { text })).status, 400);
    assert.equal((await request('/api/action', { action: 'start' })).status, 409);
    assert.equal((await request('/api/action', { action: 'release' })).status, 409);
    assert.equal((await request('/api/action', { action: 'invent_action' })).status, 400);
    assert.deepEqual((await request('/api/state')).data.session, before);
  });

  await t.test('validated WAV files in hidden runtime directories support playback and byte ranges', async () => {
    const filename = '11111111-1111-4111-8111-111111111111.wav';
    const audioDirectory = path.join(runtime, 'speech');
    await mkdir(audioDirectory, { recursive: true });
    const wav = Buffer.alloc(364);
    wav.write('RIFF', 0); wav.writeUInt32LE(wav.length - 8, 4); wav.write('WAVE', 8);
    wav.write('fmt ', 12); wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20);
    wav.writeUInt16LE(1, 22); wav.writeUInt32LE(16000, 24); wav.writeUInt32LE(32000, 28);
    wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34);
    wav.write('data', 36); wav.writeUInt32LE(wav.length - 44, 40);
    await writeFile(path.join(audioDirectory, filename), wav);
    const audioUrl = base + '/api/speech/' + filename;
    const full = await fetch(audioUrl, { signal: AbortSignal.timeout(4000) });
    assert.equal(full.status, 200);
    assert.match(full.headers.get('content-type'), /^audio\/wav\b/);
    assert.equal(full.headers.get('cache-control'), 'no-store');
    assert.deepEqual(Buffer.from(await full.arrayBuffer()), wav);
    const partial = await fetch(audioUrl, { headers: { Range: 'bytes=0-43' }, signal: AbortSignal.timeout(4000) });
    assert.equal(partial.status, 206);
    assert.equal(partial.headers.get('content-range'), `bytes 0-43/${wav.length}`);
    assert.deepEqual(Buffer.from(await partial.arrayBuffer()), wav.subarray(0, 44));
    for (const filename of ['secret.json', '..%2fcoflorist.sqlite', '..%5ccoflorist.sqlite',
      '11111111-1111-4111-8111-111111111111.wav:secret', '00000000-0000-0000-0000-000000000000.wav']) {
      assert.equal((await request('/api/speech/' + filename)).status, 404, filename);
    }
  });

  await t.test('pause is immediate and local even without a model key', async () => {
    const paused = await request('/api/chat', { text: '잠깐 기다려. 배치를 볼게.' });
    assert.equal(paused.status, 200);
    assert.equal(paused.data.reply, '기다릴게요.');
    assert.equal(paused.data.session.paused, true);
    assert.equal(paused.data.session.phase, 'paused');
    assert.equal(paused.data.session.messages.length, 2);
    const before = paused.data.session;
    assert.equal((await request('/api/action', { action: 'start' })).status, 409);
    assert.deepEqual((await request('/api/state')).data.session, before);
    const resumed = await request('/api/action', { action: 'resume' });
    assert.equal(resumed.status, 200);
    assert.equal(resumed.data.session.paused, false);
    const reset = await request('/api/action', { action: 'reset' });
    assert.equal(reset.data.session.phase, 'idle');
    assert.equal(reset.data.session.messages.length, 0);
    assert.ok(reset.data.session.revision > resumed.data.session.revision);
  });

  await t.test('settings validate atomically and cannot replace environment-only credentials', async () => {
    const before = (await request('/api/state')).data.settings;
    for (const invalid of [
      { cameraUrl: 'file:///private' }, { cameraMode: 'shell' }, { model: MODEL },
      { recipientName: 'not saved', voiceEnabled: 'yes' }, { apiKey: 'any-key' },
      { clearApiKey: true }, { connectionEnabled: 'yes' },
    ]) {
      assert.equal((await request('/api/settings', invalid)).status, 400);
      assert.deepEqual((await request('/api/state')).data.settings, before);
    }
  });

  await t.test('foreign origins and hosts cannot change local settings', async () => {
    const before = (await request('/api/state')).data.settings;
    assert.equal((await request('/api/settings', { recipientName: 'not saved' }, { Origin: 'https://example.com' })).status, 403);
    assert.equal((await request('/api/settings', { recipientName: 'not saved' }, { 'Sec-Fetch-Site': 'cross-site' })).status, 403);
    const wrongHostStatus = await new Promise((resolve, reject) => {
      const req = httpRequest(base + '/api/state', { headers: { Host: 'example.com' } }, response => {
        response.resume(); response.on('end', () => resolve(response.statusCode));
      });
      req.on('error', reject);
      req.setTimeout(4000, () => req.destroy(new Error('Host validation timed out.')));
      req.end();
    });
    assert.equal(wrongHostStatus, 403);
    assert.deepEqual((await request('/api/state')).data.settings, before);
  });
});

test('environment key stays private and connection off blocks text and microphone processing', { timeout: 15000 }, async t => {
  // Never sent to a provider: exercise local state and the disabled gate only.
  const key = 'local-only-environment-test-secret';
  const { runtime, base, request, output } = await startServer(t, key);
  const initial = await request('/api/state');
  assert.equal(initial.data.settings.apiConfigured, true);
  assert.equal(initial.data.settings.connectionEnabled, true);
  const saved = await request('/api/settings', { recipientName: '테스트 수령자', connectionEnabled: false });
  assert.equal(saved.status, 200);
  assert.equal(saved.data.connection.status, 'disabled');
  assert.equal(saved.data.settings.apiConfigured, true);
  assert.equal(saved.data.settings.connectionEnabled, false);
  const before = (await request('/api/state')).data.session;
  assert.equal((await request('/api/chat', { text: '꽃을 추천해 줘.' })).status, 409);
  const transcribe = await fetch(base + '/api/transcribe', {
    method: 'POST', headers: { 'Content-Type': 'audio/webm' }, body: Buffer.alloc(100), signal: AbortSignal.timeout(4000),
  });
  assert.equal(transcribe.status, 409);
  assert.deepEqual((await request('/api/state')).data.session, before);
  const reenabled = await request('/api/settings', { connectionEnabled: true });
  assert.equal(reenabled.status, 200);
  assert.equal(reenabled.data.settings.connectionEnabled, true);
  assert.equal(reenabled.data.settings.model, MODEL);
  const beforeInvalidAudio = (await request('/api/state')).data.session;
  for (const [contentType, body] of [
    ['application/json', JSON.stringify({ text: 'This must not bypass audio recognition.' })],
    ['text/plain', 'This is not a recording.'.repeat(4)],
    ['audio/webm', Buffer.alloc(12)],
  ]) {
    const rejected = await fetch(base + '/api/transcribe', {
      method: 'POST', headers: { 'Content-Type': contentType }, body, signal: AbortSignal.timeout(4000),
    });
    assert.equal(rejected.status, 400, contentType);
  }
  const oversized = await fetch(base + '/api/transcribe', {
    method: 'POST', headers: { 'Content-Type': 'audio/webm' }, body: Buffer.alloc(8 * 1024 * 1024 + 1),
    signal: AbortSignal.timeout(4000),
  });
  assert.equal(oversized.status, 413);
  assert.deepEqual((await request('/api/state')).data.session, beforeInvalidAudio);
  for (const response of [initial, saved, reenabled, await request('/api/export')]) {
    assert.equal(response.text.includes(key), false);
    assert.equal(Object.hasOwn(response.data.settings, 'apiKey'), false);
  }
  assert.equal(output().includes(key), false);
  assert.equal((await readFile(path.join(runtime, 'coflorist.sqlite'))).includes(Buffer.from(key)), false);
});
