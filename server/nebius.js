import { readFileSync } from 'node:fs';
const BASE = 'https://api.tokenfactory.nebius.com/v1';
export const DEFAULT_MODEL = 'nvidia/nemotron-3-super-120b-a12b';
export function validateModel(model) {
  if (typeof model !== 'string' || model.length > 180 || !/^nvidia\/[^\s]*nemotron[^\s]*$/i.test(model)) throw new Error('NVIDIA Nemotron 모델 ID를 입력해 주세요.');
  return model;
}
async function request(path, key, body, signal) {
  if (!key) throw Object.assign(new Error('설정에서 Nebius API 키를 연결해 주세요.'), { status: 400 });
  const timeout = AbortSignal.timeout(60000);
  let res;
  try {
    res = await fetch(BASE + path, { method: body ? 'POST' : 'GET',
      headers: { Authorization: 'Bearer ' + key, 'Content-Type': 'application/json' },
      ...(body ? { body: JSON.stringify(body) } : {}), signal: signal ? AbortSignal.any([signal, timeout]) : timeout });
  } catch (error) {
    if (signal?.aborted) throw Object.assign(new Error('이전 요청을 취소했습니다.'), { status: 409 });
    throw Object.assign(new Error(error.name === 'TimeoutError' ? 'Nebius 응답 시간이 초과됐습니다.' : 'Nebius에 연결할 수 없습니다. 인터넷 연결을 확인해 주세요.'), { status: 502 });
  }
  if (!res.ok) {
    const messages = { 401: 'API 키를 확인해 주세요.', 403: '모델 접근 권한을 확인해 주세요.', 404: '사용 가능한 모델 ID를 확인해 주세요.', 429: '호출 한도 또는 크레딧을 확인해 주세요.' };
    throw Object.assign(new Error('Nebius ' + res.status + ': ' + (messages[res.status] || '요청 처리에 실패했습니다. 모델 설정을 확인해 주세요.')), { status: 502 });
  }
  return res.json();
}
export async function listModels(key) {
  const result = await request('/models', key);
  return (result.data || []).map(x => x.id).filter(x => /^nvidia\//i.test(x) && /nemotron/i.test(x));
}
export async function testConnection(key, model, signal) {
  validateModel(model); const started = Date.now();
  const result = await request('/chat/completions', key, { model,
    messages: [{ role: 'system', content: '한국어로 한 문장만 답하세요. 추론 과정은 출력하지 마세요.' }, { role: 'user', content: '꽃 작업 준비가 되었다고 짧게 말해줘.' }],
    max_tokens: 256, temperature: 0.1, chat_template_kwargs: { enable_thinking: false } }, signal);
  const reply = result.choices?.[0]?.message?.content?.trim();
  if (!reply) throw new Error('모델의 응답이 비어 있습니다. 다른 Nemotron 모델을 선택해 주세요.');
  return { reply, model: result.model || model, latencyMs: Date.now() - started, usage: result.usage || null };
}
export function parseResponse(content) {
  if (typeof content !== 'string') throw new Error('모델 응답이 비어 있습니다.');
  const clean = content.replace(/<think>[\s\S]*?<\/think>/g, '').replace(/^\s*```(?:json)?\s*/i, '').replace(/\s*```\s*$/, '').trim();
  let value; try { value = JSON.parse(clean); } catch { throw new Error('모델 응답 형식이 올바르지 않습니다. 다시 말해 주세요.'); }
  const intents = ['gift','arrange','pause','resume','approve','change','chat'];
  if (!value || typeof value.reply !== 'string' || !value.reply.trim() || value.reply.length > 1200 || !intents.includes(value.intent)) throw new Error('모델 응답의 작업 형식을 확인할 수 없습니다.');
  const placements = value.placements || [];
  if (!Array.isArray(placements) || placements.length > 12) throw new Error('꽃 배치 수량을 확인해 주세요.');
  for (const p of placements) if (!p || !['white','red','yellow','purple'].includes(p.color) || !['center','left','right'].includes(p.position) || !Number.isInteger(p.quantity) || p.quantity < 1 || p.quantity > 12) throw new Error('꽃 색상·위치·수량을 다시 확인해 주세요.');
  return { reply: value.reply.trim(), intent: value.intent, placements, ...(typeof value.shortExplanation === 'boolean' ? {shortExplanation:value.shortExplanation}: {}) };
}
export async function chat(key, model, session, settings, signal) {
  validateModel(model);
  const instructions=readFileSync(new URL('./system-prompt.txt', import.meta.url),'utf8');
  const prompt=instructions+'\n현재 앱 상태: '+JSON.stringify({recipientName:settings.recipientName,recipientHistory:settings.recipientHistory,historyIsDemo:settings.historyIsDemo,phase:session.phase,paused:session.paused,placements:session.placements,activeTask:session.activeTask,failures:session.failures.slice(-3),shortExplanation:session.preferences?.shortExplanation??true,neuralDecision:session.neuralDecision || null});
  const started = Date.now();
  const result = await request('/chat/completions', key, { model, messages: [
    {role:'system',content:prompt}, ...session.messages.slice(-16).map(m=>({role:m.role,content:m.text}))
  ], temperature: 0.1, max_tokens: 1000, response_format: { type:'json_object' }, chat_template_kwargs:{enable_thinking:false}}, signal);
  return { ...parseResponse(result.choices?.[0]?.message?.content), latencyMs:Date.now()-started, model:result.model || model, usage:result.usage || null };
}

