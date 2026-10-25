/**
 * CoFlorist 외부 대화 연결 예시 (Node.js / Express).
 * 기존 대화 서버에 createConversationStream(app)을 붙이고,
 * STT 확정 / LLM 시작 / TTS 시작·종료 시 publish(event)를 호출하세요.
 * 대시보드 설정 → 외부 대화에 http://이서버IP:4320/events 입력.
 * 오디오와 Nebius API 키는 대시보드로 보내지 않습니다.
 *
 * 단독 서버: node examples/conversation-stream.mjs
 * 로컬 UI 확인용 가상 대화: node examples/conversation-stream.mjs --demo
 * 기본 바인딩은 127.0.0.1. 다른 기기 연결 시 HOST에 서버의 LAN IP 설정.
 * 브라우저 출처는 ALLOWED_ORIGIN으로 명시 (기본 http://localhost:4317).
 * 이 파일은 상대가 쓸 참고 구현입니다. 로봇 명령을 처리하지 않습니다.
 */
import express from 'express';
import { randomUUID } from 'node:crypto';
import { pathToFileURL } from 'node:url';

export function createConversationStream(app, { allowedOrigin = 'http://localhost:4317', onClient = () => {} } = {}) {
  const clients = new Set();
  const history = [];
  const format = event => `id: ${event.id}\ndata: ${JSON.stringify(event)}\n\n`;
  const publish = event => {
    if (!event || typeof event.id !== 'string' || !/^[a-zA-Z0-9_-]{1,160}$/.test(event.id)) throw new Error('id: 매 이벤트마다 고유한 UUID 또는 영숫자 ID');
    if (event.type === 'state') {
      if (!['idle', 'listening', 'thinking', 'speaking', 'error'].includes(event.state)) throw new Error('잘못된 state');
    } else if (event.type === 'message') {
      if (!['user', 'assistant'].includes(event.role) || typeof event.text !== 'string' || !event.text.trim() || event.text.length > 2000) throw new Error('잘못된 message');
    } else throw new Error('type: state 또는 message');
    if (history.some(item => item.id === event.id)) return;
    history.push(event);
    if (history.length > 500) history.shift();
    for (const client of clients) client.write(format(event));
  };
  app.get('/events', (req, res) => {
    const allowed = new Set([allowedOrigin, 'http://localhost:4317', 'http://127.0.0.1:4317']);
    if (req.headers.origin && !allowed.has(req.headers.origin)) return res.sendStatus(403);
    res.set({ 'Content-Type': 'text/event-stream; charset=utf-8', 'Cache-Control': 'no-cache, no-transform',
      Connection: 'keep-alive', 'X-Accel-Buffering': 'no', Vary: 'Origin' });
    if (req.headers.origin) res.set('Access-Control-Allow-Origin', req.headers.origin);
    res.flushHeaders();
    clients.add(res);
    const last = req.headers['last-event-id'];
    const index = last ? history.findIndex(event => event.id === last) : -1;
    for (const event of history.slice(index + 1)) res.write(format(event));
    const timer = setInterval(() => res.write(': keepalive\n\n'), 15000);
    req.on('close', () => { clearInterval(timer); clients.delete(res); });
    onClient(publish);
  });
  return publish;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const app = express();
  let demoStarted = false;
  createConversationStream(app, {
    allowedOrigin: process.env.ALLOWED_ORIGIN || 'http://localhost:4317',
    onClient: publish => {
      if (!process.argv.includes('--demo') || demoStarted) return;
      demoStarted = true;
      const send = event => publish({ id: randomUUID(), ...event });
      send({ type: 'state', state: 'listening' });
      setTimeout(() => send({ type: 'message', role: 'user', text: '[연결 예시] 그 사람에게 꽃을 선물하고 싶어.' }), 1500);
      setTimeout(() => send({ type: 'state', state: 'thinking' }), 2000);
      setTimeout(() => {
        send({ type: 'message', role: 'assistant', text: '[연결 예시] 흰 꽃과 빨간 꽃을 함께 준비할까요?' });
        send({ type: 'state', state: 'speaking' });
      }, 14000);
      setTimeout(() => send({ type: 'state', state: 'idle' }), 32000);
    },
  });
  const port = Number(process.env.STREAM_PORT || 4320);
  const host = process.env.HOST || '127.0.0.1';
  app.listen(port, host, () => console.log(`Conversation SSE: http://${host}:${port}/events`));
}
