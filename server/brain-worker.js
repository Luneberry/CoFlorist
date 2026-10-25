import { spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { mkdirSync } from 'node:fs';
import { createInterface } from 'node:readline';

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
export const DEFAULT_PYTHON = join(projectRoot, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
export const DEFAULT_BRAIN_DATA = join(projectRoot, 'data/flywire/subset.json');

export class BrainWorker {
  constructor({ pythonExecutable = DEFAULT_PYTHON, dataPath = DEFAULT_BRAIN_DATA, onChange = () => {}, timeoutMs = 20000 } = {}) {
    this.pythonExecutable = pythonExecutable || DEFAULT_PYTHON;
    this.dataPath = dataPath || DEFAULT_BRAIN_DATA;
    this.onChange = onChange;
    this.timeoutMs = timeoutMs;
    this.child = null;
    this.startPromise = null;
    this.startResolve = null;
    this.startReject = null;
    this.startTimer = null;
    this.pending = [];
    this.active = null;
    this.stderr = '';
    this.generation = 0;
    this.state = { status: 'idle', mode: 'unconfigured', lastResult: null, error: null };
  }

  snapshot() { return structuredClone(this.state); }
  changed(patch) {
    Object.assign(this.state, patch);
    try { this.onChange(this.snapshot()); } catch { /* UI observers cannot break the worker. */ }
  }

  start() {
    if (this.child && ['ready', 'running'].includes(this.state.status)) return Promise.resolve(this.snapshot());
    if (this.startPromise) return this.startPromise;
    this.changed({ status: 'starting', error: null });
    const taskTemp = join(projectRoot, '.work-tmp');
    mkdirSync(taskTemp, { recursive: true });
    this.startPromise = new Promise((resolveStart, rejectStart) => {
      this.startResolve = resolveStart;
      this.startReject = rejectStart;
    });
    const promise = this.startPromise;
    try {
      const child = spawn(this.pythonExecutable, ['-u', join(projectRoot, 'python/brain_worker.py'), '--data', this.dataPath], {
        cwd: projectRoot, shell: false, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'],
        env: { ...process.env, PYTHONUTF8: '1', PYTHONUNBUFFERED: '1', TEMP: taskTemp, TMP: taskTemp,
          COFLORIST_BRIAN_CACHE: join(taskTemp, 'brian-cpu') },
      });
      this.child = child;
      this.stderr = '';
      const lines = createInterface({ input: child.stdout, crlfDelay: Infinity });
      lines.on('line', (line) => {
        if (this.child !== child) return;
        try { this.receive(JSON.parse(line)); }
        catch (error) { this.fail(new Error(`Invalid brain worker protocol: ${error.message}`)); }
      });
      child.stderr.on('data', (chunk) => { this.stderr = (this.stderr + chunk.toString()).slice(-4000); });
      child.stdin.on('error', (error) => { if (this.child === child) this.fail(error); });
      child.on('error', (error) => { if (this.child === child) this.fail(error); });
      child.on('close', (code) => {
        lines.close();
        if (this.child === child) this.fail(new Error(`Brain worker exited (${code}). ${this.stderr.trim()}`));
      });
      this.startTimer = setTimeout(() => this.fail(new Error('Brain worker startup timed out. Check Python dependencies and the data path.')), 30000);
    } catch (error) { this.fail(error); }
    return promise;
  }

  receive(message) {
    if (message.type === 'fatal') { this.fail(new Error(message.error)); return; }
    if (message.type === 'ready') {
      clearTimeout(this.startTimer);
      const resolveStart = this.startResolve;
      this.startPromise = this.startResolve = this.startReject = null;
      this.changed({ status: 'ready', mode: 'flywire', error: null, metadata: message });
      resolveStart?.(this.snapshot());
      this.drain();
      return;
    }
    if (!this.active || message.requestId !== this.active.id) return;
    if (message.type === 'started') return;
    if (!['result', 'error'].includes(message.type)) throw new Error(`Unknown message type ${message.type}`);
    const active = this.active;
    clearTimeout(active.timer);
    this.active = null;
    if (message.type === 'error') {
      this.changed({ status: 'ready', error: message.error });
      active.reject(new Error(message.error));
    } else {
      this.changed({ status: 'ready', lastResult: message.result, error: null });
      active.resolve(message.result);
    }
    this.drain();
  }

  async run(payload = {}) {
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) throw new Error('Brain payload must be an object.');
    const generation = this.generation;
    await this.start();
    if (generation !== this.generation || !this.child) throw new Error('Brain worker stopped before the job could start.');
    if (this.pending.length >= 8) throw new Error('Brain worker queue is full.');
    return new Promise((resolveJob, rejectJob) => {
      const job = { id: randomUUID(), payload, resolve: resolveJob, reject: rejectJob, timer: null };
      job.timer = setTimeout(() => {
        if (this.active === job) this.fail(new Error('Brain calculation timed out. Worker was stopped.'));
        else {
          this.pending = this.pending.filter((item) => item !== job);
          job.reject(new Error('Brain job expired while waiting in the queue.'));
        }
      }, this.timeoutMs);
      this.pending.push(job);
      this.drain();
    });
  }

  drain() {
    if (this.active || !this.child || this.state.status !== 'ready' || !this.pending.length) return;
    this.active = this.pending.shift();
    this.changed({ status: 'running', error: null });
    this.child.stdin.write(`${JSON.stringify({ protocolVersion: 1, op: 'simulate', requestId: this.active.id, payload: this.active.payload })}\n`);
  }

  clear(error) {
    this.generation += 1;
    clearTimeout(this.startTimer);
    this.startReject?.(error);
    this.startPromise = this.startResolve = this.startReject = null;
    for (const job of [this.active, ...this.pending].filter(Boolean)) {
      clearTimeout(job.timer);
      job.reject(error);
    }
    this.active = null;
    this.pending = [];
    const child = this.child;
    this.child = null;
    if (child) { child.stdin.destroy(); child.kill(); }
  }

  fail(error) {
    this.clear(error);
    this.changed({ status: 'error', error: error.message });
  }

  stop() {
    this.clear(new Error('Brain worker stopped.'));
    this.changed({ status: 'idle', error: null });
    return this.snapshot();
  }
}
