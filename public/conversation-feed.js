export function createFeedState() {
  return { phase: 'idle', messages: [], seen: [], error: '', turnCount: 0, samples: [] };
}

const COLORS = new Set(['', 'white', 'red', 'yellow', 'purple']);

// These are application display rules; the circuit does not infer intent or emotion.
export function brainBadge(downstreamRateHz, mn9RateHz, { mn9ReachHz, downstreamOfferHz, fallbackReachHz }) {
  if (Number.isFinite(mn9RateHz) && mn9RateHz >= mn9ReachHz) return 'reach';
  if (!Number.isFinite(mn9RateHz) && Number.isFinite(downstreamRateHz) && downstreamRateHz >= fallbackReachHz) return 'reach';
  return Number.isFinite(downstreamRateHz) && downstreamRateHz >= downstreamOfferHz ? 'offer' : 'wait';
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
    if (event.role === 'assistant') {
      if (event.stimulus_hz !== undefined && (!Number.isInteger(event.stimulus_hz) || event.stimulus_hz < 0 || event.stimulus_hz > 250)) throw new Error('stimulus_hz는 0~250 정수여야 합니다.');
      if (event.flower !== undefined && (typeof event.flower !== 'string' || event.flower.length > 100)) throw new Error('꽃 이름을 확인해 주세요.');
      if (event.emotion !== undefined && (typeof event.emotion !== 'string' || event.emotion.length > 40)) throw new Error('감정 값을 확인해 주세요.');
    }
  } else throw new Error('지원 이벤트는 state, message입니다.');
  state.seen.push(event.id);
  if (state.seen.length > 500) state.seen.shift();
  state.error = '';
  if (event.type === 'state') {
    state.phase = event.state;
    if (event.state === 'error') state.error = '외부 대화 장치 확인 필요';
  } else {
    const message = { id: event.id, role: event.role, text: event.text.trim(), createdAt: now };
    if (event.role === 'assistant') {
      message.flower = event.flower || '';
      message.flowerColor = COLORS.has(event.flower_color) ? event.flower_color : '';
      message.emotion = event.emotion || '';
      if (event.stimulus_hz !== undefined) {
        message.stimulusHz = event.stimulus_hz;
        message.turn = ++state.turnCount;
        message.analysisStatus = 'pending';
      }
    }
    state.messages.push(message);
    if (state.messages.length > 200) state.messages.shift();
  }
  return true;
}
