export function createFeedState() {
  return { phase: 'idle', messages: [], seen: [], error: '' };
}

// The stream carries display events only: never robot commands or model prompts.
export function applyFeedEvent(state, event, now = new Date().toISOString()) {
  if (!event || typeof event !== 'object' || Array.isArray(event)) throw new Error('JSON 이벤트가 필요합니다.');
  if (typeof event.id !== 'string' || !event.id.trim() || event.id.length > 160) throw new Error('고유 이벤트 id가 필요합니다.');
  if (state.seen.includes(event.id)) return false;
  if (event.type === 'state') {
    if (!['idle', 'listening', 'thinking', 'speaking', 'error'].includes(event.state)) throw new Error('대화 상태를 확인해 주세요.');
  } else if (event.type === 'message') {
    if (!['user', 'assistant'].includes(event.role) || typeof event.text !== 'string' || !event.text.trim() || event.text.length > 2000) throw new Error('role과 1~2,000자 text가 필요합니다.');
  } else throw new Error('지원 이벤트는 state, message입니다.');
  state.seen.push(event.id);
  if (state.seen.length > 500) state.seen.shift();
  state.error = '';
  if (event.type === 'state') {
    state.phase = event.state;
    if (event.state === 'error') state.error = '외부 대화 장치 확인 필요';
  } else {
    state.messages.push({ id: event.id, role: event.role, text: event.text.trim(), createdAt: now });
    if (state.messages.length > 200) state.messages.shift();
  }
  return true;
}
