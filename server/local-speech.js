import { spawn } from 'node:child_process';
import { mkdir, readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const SCRIPT = fileURLToPath(new URL('../scripts/synthesize-korean.ps1', import.meta.url));

export function inspectWav(bytes) {
  if (bytes.length < 44 || bytes.toString('ascii', 0, 4) !== 'RIFF' || bytes.toString('ascii', 8, 12) !== 'WAVE') {
    throw new Error('올바른 WAV 음성 파일이 아닙니다.');
  }
  let format;
  let dataBytes;
  for (let offset = 12; offset + 8 <= bytes.length;) {
    const kind = bytes.toString('ascii', offset, offset + 4);
    const length = bytes.readUInt32LE(offset + 4);
    const start = offset + 8;
    if (start + length > bytes.length) throw new Error('WAV 파일이 완전하지 않습니다.');
    if (kind === 'fmt ' && length >= 16) {
      format = {
        encoding: bytes.readUInt16LE(start),
        channels: bytes.readUInt16LE(start + 2),
        sampleRate: bytes.readUInt32LE(start + 4),
        byteRate: bytes.readUInt32LE(start + 8),
        bitsPerSample: bytes.readUInt16LE(start + 14),
      };
    }
    if (kind === 'data') dataBytes = length;
    offset = start + length + (length % 2);
  }
  if (!format || !format.byteRate || !dataBytes) throw new Error('WAV 음성 데이터가 비어 있습니다.');
  return { ...format, dataBytes, durationMs: Math.round(dataBytes / format.byteRate * 1000) };
}

/** outputPath must be chosen by the server, never taken from an HTTP request. */
export async function synthesizeKorean(text, outputPath, { signal } = {}) {
  if (typeof text !== 'string' || !text.trim() || text.length > 5000) throw new Error('읽을 한국어 문장을 입력해 주세요.');
  if (!path.isAbsolute(outputPath) || path.extname(outputPath).toLowerCase() !== '.wav') throw new Error('절대 WAV 출력 경로가 필요합니다.');
  if (process.platform !== 'win32') throw new Error('이 음성 생성기는 Windows 한국어 음성을 사용합니다.');
  signal?.throwIfAborted();
  await mkdir(path.dirname(outputPath), { recursive: true });

  const metadata = await new Promise((resolve, reject) => {
    const child = spawn('powershell.exe', ['-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', SCRIPT], {
      windowsHide: true,
      shell: false,
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    let settled = false;
    let timer;
    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      signal?.removeEventListener('abort', abort);
      if (error) { child.kill(); reject(error); } else resolve(value);
    };
    const abort = () => finish(signal.reason || new Error('음성 생성을 취소했습니다.'));
    signal?.addEventListener('abort', abort, { once: true });
    timer = setTimeout(() => finish(new Error('한국어 음성 생성 시간이 초과됐습니다.')), 45000);
    child.stdout.setEncoding('utf8');
    child.stderr.setEncoding('utf8');
    child.stdout.on('data', chunk => {
      stdout += chunk;
      if (stdout.length > 65536) finish(new Error('음성 생성기의 응답이 너무 큽니다.'));
    });
    child.stderr.on('data', chunk => { stderr = (stderr + chunk).slice(-8192); });
    child.on('error', () => finish(new Error('Windows 음성 생성기를 실행하지 못했습니다.')));
    child.stdin.on('error', () => finish(new Error('음성 생성기로 문장을 전달하지 못했습니다.')));
    child.on('close', code => {
      if (code !== 0) return finish(new Error('한국어 음성을 생성하지 못했습니다: ' + (stderr.trim().slice(0, 500) || `종료 코드 ${code}`)));
      try { finish(null, JSON.parse(stdout.trim())); } catch { finish(new Error('음성 생성기의 결과를 확인할 수 없습니다.')); }
    });
    if (signal?.aborted) abort();
    else child.stdin.end(JSON.stringify({ text, outputPath }), 'utf8');
  });
  signal?.throwIfAborted();
  const bytes = await readFile(outputPath);
  return { ...metadata, ...inspectWav(bytes), path: outputPath, bytes: bytes.length };
}
