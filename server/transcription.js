import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const failure = (message, status = 400) => Object.assign(new Error(message), { status });

export function transcribeAudio(audioPath, { signal } = {}) {
  signal?.throwIfAborted();
  const python = process.env.VOICE_PYTHON_EXECUTABLE || path.join(ROOT, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  return new Promise((resolve, reject) => {
    const child = spawn(python, [path.join(ROOT, 'python/voice_transcribe.py'), audioPath], {
      cwd: ROOT, windowsHide: true, shell: false,
      env: { ...process.env, PYTHONIOENCODING: 'utf-8' }, stdio: ['ignore', 'pipe', 'pipe'],
    });
    let output = '', diagnostic = '', settled = false, timer;
    const finish = (error, result) => {
      if (settled) return;
      settled = true; clearTimeout(timer); signal?.removeEventListener('abort', abort);
      if (error) { child.kill(); reject(error); } else resolve(result);
    };
    const abort = () => finish(failure('음성 입력을 취소했습니다.', 409));
    signal?.addEventListener('abort', abort, { once: true });
    timer = setTimeout(() => finish(failure('음성 인식 시간이 초과됐습니다. 다시 말해 주세요.', 504)), 120000);
    child.stdout.setEncoding('utf8');
    child.stdout.on('data', chunk => {
      output += chunk;
      if (output.length > 65536) finish(failure('음성 인식 결과가 너무 큽니다.', 502));
    });
    child.stderr.setEncoding('utf8');
    child.stderr.on('data', chunk => { diagnostic = (diagnostic + chunk).slice(-4096); });
    child.on('error', () => finish(failure('음성 인식용 Python을 실행할 수 없습니다. 로컬 음성 설치를 확인해 주세요.', 503)));
    child.on('close', code => {
      if (code !== 0) {
        if (diagnostic.includes('No speech was recognized')) return finish(failure('말소리를 인식하지 못했어요. 마이크 가까이에서 다시 말해 주세요.'));
        if (diagnostic.includes('Recordings must be at most 60 seconds')) return finish(failure('60초 이내로 녹음해 주세요.'));
        return finish(failure('음성 인식에 실패했습니다. 다시 말하거나 npm run setup:voice로 인식 모델을 확인해 주세요.', 503));
      }
      try {
        const result = JSON.parse(output.trim());
        if (typeof result.text !== 'string' || !result.text.trim() || result.text.length > 2000) throw new Error();
        finish(null, result);
      } catch { finish(failure('음성 인식 결과를 읽을 수 없습니다.', 502)); }
    });
    if (signal?.aborted) abort();
  });
}
