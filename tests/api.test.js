import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createServer } from 'node:net';
import { request as httpRequest } from 'node:http';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { once } from 'node:events';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

async function availablePort() {
  const listener = createServer();
  listener.listen(0, '127.0.0.1');
  await once(listener, 'listening');
  const port = listener.address().port;
  await new Promise((resolve, reject) => listener.close(error => error ? reject(error) : resolve()));
  return port;
}

test('real localhost API preserves local credentials and operator-only transitions', { timeout: 25000 }, async (t) => {
  const runtime = await mkdtemp(path.join(tmpdir(), 'coflorist-api-test-'));
  const port = await availablePort();
  const base = 'http://127.0.0.1:' + port;
  const child = spawn(process.execPath, [path.join(ROOT, 'server/app.js')], {
    cwd: ROOT,
    env: { ...process.env, PORT: String(port), COFLORIST_RUNTIME_DIR: runtime, NEBIUS_API_KEY: '', NEBIUS_MODEL: 'nvidia/nemotron-3-super-120b-a12b' },
    windowsHide: true,
    shell: false,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let output = '';
  child.stdout.on('data', data => { output += data.toString(); });
  child.stderr.on('data', data => { output += data.toString(); });
  const childExit = once(child, 'exit');
  const request = async (url, body, headers = {}) => {
    const result = await fetch(base + url, {
      method: body === undefined ? 'GET' : 'POST',
      headers: { ...(body === undefined ? {} : { 'Content-Type': 'application/json' }), ...headers },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      signal: AbortSignal.timeout(4000),
    });
    const text = await result.text();
    return { status: result.status, headers: result.headers, text, data: JSON.parse(text) };
  };
  try {
    let started = false;
    let startupFailure = '';
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

    await t.test('health, state and credential-free calls report real local state', async () => {
      const health = await request('/api/health');
      assert.deepEqual(health.data, { ok: true, name: 'CoFlorist' });
      const state = await request('/api/state');
      assert.equal(state.data.settings.apiConfigured, false);
      assert.equal(state.data.session.phase, 'idle');
      assert.deepEqual(state.data.session.messages, []);
      assert.equal(state.headers.get('cache-control'), 'no-store');
      const connect = await request('/api/connect', {});
      assert.equal(connect.status, 400);
      assert.equal(typeof connect.data.error, 'string');
      const models = await request('/api/models');
      assert.equal(models.status, 400);
      const chat = await request('/api/chat', { text: '그 사람에게 꽃을 선물하고 싶어.' });
      assert.equal(chat.status, 400);
      assert.deepEqual((await request('/api/state')).data.session.messages, []);
    });

    await t.test('invalid requests are rejected without inventing model or robot progress', async () => {
      const before = (await request('/api/state')).data.session;
      for (const text of [23, {}, null, '', 'x'.repeat(2001)]) {
        assert.equal((await request('/api/chat', { text })).status, 400);
      }
      assert.equal((await request('/api/action', { action: 'start' })).status, 409);
      assert.equal((await request('/api/action', { action: 'release' })).status, 409);
      assert.equal((await request('/api/action', { action: 'invent_action' })).status, 400);
      assert.deepEqual((await request('/api/state')).data.session, before);
    });

    await t.test('pause takes the local immediate path without requiring a provider key', async () => {
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
      assert.equal(resumed.data.session.phase, 'idle');
      assert.equal(resumed.data.session.paused, false);
      const reset = await request('/api/action', { action: 'reset' });
      assert.equal(reset.data.session.phase, 'idle');
      assert.equal(reset.data.session.messages.length, 0);
      assert.ok(reset.data.session.revision > resumed.data.session.revision);
    });

    await t.test('external flower queues once and never starts an operator task', async () => {
      const body = { eventId: 'assistant-1', color: 'white' };
      const first = await request('/api/external/flower', body);
      assert.equal(first.status, 200);
      assert.equal(first.data.session.phase, 'planning');
      assert.equal(first.data.session.placements.length, 1);
      assert.equal(first.data.session.placements[0].status, 'pending');
      assert.equal(first.data.session.activeTask, null);
      const revision = first.data.session.revision;
      const replay = await request('/api/external/flower', body);
      assert.equal(replay.data.session.revision, revision);
      assert.equal(replay.data.session.placements.length, 1);
      assert.equal((await request('/api/external/flower', { eventId: 'bad', color: 'green' })).status, 400);
      const reset = await request('/api/action', { action: 'reset' });
      assert.equal(reset.data.session.placements.length, 0);
    });

    await t.test('settings are validated atomically', async () => {
      const before = (await request('/api/state')).data.settings;
      for (const invalid of [
        { cameraUrl: 'file:///private' }, { cameraMode: 'shell' }, { model: 'other/vendor' },
        { recipientName: 'will not be saved', voiceEnabled: 'yes' }, { apiKey: 'invalid\nheader' },
      ]) {
        assert.equal((await request('/api/settings', invalid)).status, 400);
        assert.deepEqual((await request('/api/state')).data.settings, before);
      }
    });

    await t.test('a local test key is never returned, exported, logged or persisted', async () => {
      // This value is never sent to the external provider; it exists only to detect accidental disclosure.
      const localOnlyKey = 'local-only-test-key-' + port;
      const saved = await request('/api/settings', { apiKey: localOnlyKey, recipientName: '테스트 수령자', historyIsDemo: true });
      assert.equal(saved.status, 200);
      assert.equal(saved.data.settings.apiConfigured, true);
      for (const response of [saved, await request('/api/state'), await request('/api/export')]) {
        assert.equal(response.text.includes(localOnlyKey), false);
        assert.equal(Object.hasOwn(response.data.settings, 'apiKey'), false);
      }
      assert.equal(output.includes(localOnlyKey), false);
      const persisted = await readFile(path.join(runtime, 'coflorist.sqlite'));
      assert.equal(persisted.includes(Buffer.from(localOnlyKey)), false);
      const cleared = await request('/api/settings', { clearApiKey: true });
      assert.equal(cleared.data.settings.apiConfigured, false);
    });

    await t.test('foreign origins and hosts cannot change local settings', async () => {
      const before = (await request('/api/state')).data.settings;
      const foreign = await request('/api/settings', { recipientName: 'not saved' }, { Origin: 'https://example.com' });
      assert.equal(foreign.status, 403);
      const crossSite = await request('/api/settings', { recipientName: 'not saved' }, { 'Sec-Fetch-Site': 'cross-site' });
      assert.equal(crossSite.status, 403);
      // Native fetch normalizes Host, so use HTTP directly to exercise DNS-rebinding protection.
      const wrongHostStatus = await new Promise((resolve, reject) => {
        const req = httpRequest(base + '/api/state', { headers: { Host: 'example.com' } }, response => {
          response.resume();
          response.on('end', () => resolve(response.statusCode));
        });
        req.on('error', reject);
        req.setTimeout(4000, () => req.destroy(new Error('Host validation request timed out.')));
        req.end();
      });
      assert.equal(wrongHostStatus, 403);
      assert.deepEqual((await request('/api/state')).data.settings, before);
    });
  } finally {
    if (child.exitCode === null) child.kill('SIGTERM');
    await childExit;
    // Delete only the exact temporary test folder created above, never the application's runtime directory.
    if (path.dirname(runtime) !== path.resolve(tmpdir()) || !path.basename(runtime).startsWith('coflorist-api-test-')) {
      throw new Error('Refusing cleanup outside the isolated test directory.');
    }
    await rm(runtime, { recursive: true, force: true });
  }
});
