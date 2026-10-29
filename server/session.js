import { randomUUID } from 'node:crypto';

const COLORS = new Set(['white', 'red', 'yellow', 'purple']);
const POSITIONS = new Set(['center', 'left', 'right']);
const INTENTS = new Set(['gift', 'arrange', 'pause', 'resume', 'approve', 'change', 'chat']);
const METHODS = ['standard', 'alternate', 'adjusted'];
const MAX_FLOWERS = 12;

function reject(message, code = 'INVALID_TRANSITION') {
  const error = new Error(message);
  error.code = code;
  error.status = code === 'INVALID_INPUT' ? 400 : 409;
  throw error;
}

function now() {
  return new Date().toISOString();
}

function event(session, type, text, details = {}) {
  session.events.push({ id: randomUUID(), type, text, createdAt: now(), ...details });
}

function changed(session) {
  session.revision += 1;
  return session;
}

function requireRunning(session) {
  if (session.paused) reject('대기 중에는 작업을 진행할 수 없습니다. 먼저 재개해 주세요.', 'PAUSED');
}

function requirePhase(session, phases, message) {
  if (!phases.includes(session.phase)) reject(message);
}

function activePlacement(session) {
  const task = session.activeTask;
  if (!task || task.status !== 'active') reject('진행 중인 꽃 작업이 없습니다.');
  const placement = session.placements.find((item) => item.id === task.id);
  if (!placement || placement.status !== 'active') reject('현재 작업과 꽃 배치 상태가 일치하지 않습니다.');
  return placement;
}

function nextMethod(session, placement) {
  const failed = new Set(session.failures
    .filter((failure) => failure.color === placement.color && failure.position === placement.position)
    .map((failure) => failure.method));
  const method = METHODS.find((candidate) => !failed.has(candidate));
  if (!method) reject('등록된 파지 방법을 모두 시도했습니다. 운영자의 도움이 필요합니다.', 'RETRY_EXHAUSTED');
  return method;
}

function normalizePlacements(input, completedCount, allowEmpty) {
  if (!Array.isArray(input) || (!allowEmpty && input.length === 0)) {
    reject('꽃 배치 목록을 입력해 주세요.', 'INVALID_INPUT');
  }
  if (input.length > MAX_FLOWERS) reject('꽃은 최대 12송이까지 배치할 수 있습니다.', 'INVALID_INPUT');
  let count = completedCount;
  const result = [];
  for (const item of input) {
    if (!item || !COLORS.has(item.color) || !POSITIONS.has(item.position)) {
      reject('지원하는 꽃 색상과 배치 위치를 선택해 주세요.', 'INVALID_INPUT');
    }
    if (!Number.isInteger(item.quantity) || item.quantity < 1 || item.quantity > MAX_FLOWERS) {
      reject('꽃 수량은 1부터 12까지의 정수여야 합니다.', 'INVALID_INPUT');
    }
    count += item.quantity;
    if (count > MAX_FLOWERS) reject('완료된 꽃을 포함해 최대 12송이까지 배치할 수 있습니다.', 'INVALID_INPUT');
    for (let index = 0; index < item.quantity; index += 1) {
      result.push({ id: randomUUID(), color: item.color, position: item.position, quantity: 1, status: 'pending' });
    }
  }
  return result;
}

export function createSession() {
  return {
    phase: 'idle',
    paused: false,
    messages: [],
    placements: [],
    activeTask: null,
    events: [],
    failures: [],
    preferences: { shortExplanation: false },
    receiptConfirmed: false,
    resumePhase: null,
    revision: 0,
    busy: false,
  };
}

export function addMessage(session, role, text) {
  if (!['user', 'assistant'].includes(role) || typeof text !== 'string' || !text.trim() || text.length > 12000) {
    reject('대화 역할과 비어 있지 않은 메시지를 입력해 주세요.', 'INVALID_INPUT');
  }
  session.messages.push({ id: randomUUID(), role, text: text.trim(), createdAt: now() });
  return changed(session);
}

// External dialogue suggests a flower only. Operator actions still control every physical step.
export function queueExternalFlower(session, input) {
  if (!input || typeof input.eventId !== 'string' || !input.eventId.trim() || input.eventId.length > 160 || !COLORS.has(input.color)) {
    reject('외부 꽃 제안의 id와 색상을 확인해 주세요.', 'INVALID_INPUT');
  }
  const existing = session.placements.find(item => item.sourceEventId === input.eventId);
  if (existing) return session;
  if (session.placements.length >= MAX_FLOWERS) reject('꽃은 최대 12송이까지 배치할 수 있습니다.');
  if (['approved', 'lifting', 'handover', 'awaiting_receipt', 'completed'].includes(session.phase)) {
    reject('이미 확정한 작품에는 꽃을 추가할 수 없습니다.');
  }
  const positions = ['center', 'left', 'right'];
  const placement = {
    id: randomUUID(), color: input.color, position: positions[session.placements.length % positions.length],
    quantity: 1, status: 'pending', sourceEventId: input.eventId,
  };
  session.placements.push(placement);
  if (session.phase === 'idle') session.phase = 'planning';
  if (session.paused && session.resumePhase === 'idle') session.resumePhase = 'planning';
  event(session, 'external_flower.suggested', '외부 대화에서 꽃 제안을 받았습니다. 운영자 확인을 기다립니다.', {
    taskId: placement.id, sourceEventId: input.eventId, color: input.color,
  });
  return changed(session);
}

export function applyIntent(session, modelResult) {
  if (!modelResult || !INTENTS.has(modelResult.intent)) reject('지원하지 않는 대화 의도입니다.', 'INVALID_INPUT');
  if (modelResult.shortExplanation !== undefined && typeof modelResult.shortExplanation !== 'boolean') {
    reject('설명 방식 설정은 참 또는 거짓이어야 합니다.', 'INVALID_INPUT');
  }
  const intent = modelResult.intent;
  if (intent === 'pause' || intent === 'resume') return applyAction(session, intent);

  // Validate completely before making changes: failed model output must be atomic.
  let replacements;
  if (intent === 'arrange' || intent === 'change') {
    requireRunning(session);
    requirePhase(session, ['idle', 'planning', 'placing', 'retry'], '승인한 꽃다발의 작업 계획은 변경할 수 없습니다.');
    if (session.activeTask?.status === 'active') reject('현재 꽃 작업을 완료하거나 실패로 확인한 뒤 계획을 변경해 주세요.');
    const completed = session.placements.filter((item) => item.status === 'completed');
    replacements = [...completed, ...normalizePlacements(modelResult.placements, completed.length, intent === 'change')];
  }
  if (intent === 'approve') {
    requireRunning(session);
    requirePhase(session, ['planning', 'placing'], '현재 단계에서는 꽃 배치를 승인할 수 없습니다.');
    if (session.activeTask || session.placements.length === 0 || !session.placements.every((item) => item.status === 'completed')) {
      reject('모든 꽃의 배치를 운영자가 완료 확인한 후 승인할 수 있습니다.');
    }
  }

  if (typeof modelResult.shortExplanation === 'boolean') {
    const previous = session.preferences.shortExplanation;
    session.preferences.shortExplanation = modelResult.shortExplanation;
    if (previous !== modelResult.shortExplanation) {
      event(session, 'preference.updated', modelResult.shortExplanation ? '설명을 짧게 안내합니다.' : '설명을 자세히 안내합니다.', { shortExplanation: modelResult.shortExplanation });
    }
  }
  if (replacements) {
    session.placements = replacements;
    session.activeTask = null;
    session.phase = 'planning';
    session.receiptConfirmed = false;
    event(session, 'plan.updated', '완료한 꽃 배치를 유지하고 남은 작업 계획을 갱신했습니다.', {
      pendingCount: replacements.filter((item) => item.status === 'pending').length,
      completedCount: replacements.filter((item) => item.status === 'completed').length,
    });
  } else if (intent === 'approve') {
    session.phase = 'approved';
    event(session, 'arrangement.approved', '사용자가 배치를 승인했습니다. 꽃다발 전달은 아직 완료되지 않았습니다.');
  } else if (intent === 'gift') {
    if (session.phase === 'idle') session.phase = 'planning';
    event(session, 'gift.requested', '꽃 선물에 대한 대화를 시작했습니다.');
  } else {
    event(session, 'conversation.updated', '사용자의 요청을 확인했습니다.');
  }
  return changed(session);
}

export function applyAction(session, action) {
  if (typeof action !== 'string') reject('올바른 작업 이름을 입력해 주세요.', 'INVALID_INPUT');

  if (action === 'reset') {
    const revision = session.revision + 1;
    for (const key of Object.keys(session)) delete session[key];
    Object.assign(session, createSession(), { revision });
    event(session, 'session.reset', '새 세션을 준비했습니다. 사용자의 첫 말을 기다립니다.');
    return session;
  }

  if (action === 'pause') {
    if (session.paused) return session;
    if (session.phase === 'completed') reject('이미 완료된 세션입니다.');
    session.resumePhase = session.phase;
    session.phase = 'paused';
    session.paused = true;
    session.busy = false;
    event(session, 'session.paused', '대기 요청을 운영자에게 전달했습니다. 자동으로 다음 동작을 시작하지 않습니다.');
    return changed(session);
  }

  if (action === 'resume') {
    if (!session.paused || !session.resumePhase) reject('대기 중인 작업이 없습니다.');
    session.phase = session.resumePhase;
    session.resumePhase = null;
    session.paused = false;
    event(session, 'session.resumed', '대기 전 작업 단계로 돌아왔습니다.');
    return changed(session);
  }

  const actions = new Set(['start', 'complete', 'fail', 'retry', 'lift', 'handover', 'received', 'release']);
  if (!actions.has(action)) reject('지원하지 않는 작업입니다.', 'INVALID_INPUT');
  requireRunning(session);

  if (action === 'start') {
    requirePhase(session, ['planning', 'placing'], '현재 단계에서는 꽃 작업을 시작할 수 없습니다.');
    if (session.activeTask) reject('진행 중인 작업을 먼저 확인해 주세요.');
    const placement = session.placements.find((item) => item.status === 'pending');
    if (!placement) reject('시작할 꽃 작업이 없습니다.');
    const method = nextMethod(session, placement);
    placement.status = 'active';
    session.activeTask = { id: placement.id, color: placement.color, position: placement.position, quantity: 1, method, status: 'active' };
    session.phase = 'placing';
    event(session, 'task.started', '운영자가 꽃 작업을 시작했습니다.', { taskId: placement.id, method, actor: 'operator' });
  } else if (action === 'complete') {
    requirePhase(session, ['placing'], '진행 중인 꽃 배치만 완료할 수 있습니다.');
    const placement = activePlacement(session);
    placement.status = 'completed';
    event(session, 'task.completed', '운영자가 실제 꽃 배치를 완료 확인했습니다.', { taskId: placement.id, actor: 'operator' });
    session.activeTask = null;
  } else if (action === 'fail') {
    requirePhase(session, ['placing'], '진행 중인 꽃 작업만 실패로 기록할 수 있습니다.');
    const placement = activePlacement(session);
    const task = session.activeTask;
    const failure = { id: randomUUID(), taskId: task.id, color: task.color, position: task.position, method: task.method, reason: 'operator_reported_grasp_failure', actor: 'operator', createdAt: now() };
    session.failures.push(failure);
    placement.status = 'pending';
    task.status = 'failed';
    session.phase = 'retry';
    event(session, 'task.failed', '운영자가 파지 실패를 기록했습니다.', { failureId: failure.id, taskId: task.id, method: task.method, actor: 'operator' });
  } else if (action === 'retry') {
    requirePhase(session, ['retry'], '재시도할 실패 작업이 없습니다.');
    if (!session.activeTask || session.activeTask.status !== 'failed') reject('재시도할 실패 작업이 없습니다.');
    const task = session.activeTask;
    const placement = session.placements.find((item) => item.id === task.id && item.status === 'pending');
    if (!placement) reject('재시도할 꽃 배치를 찾을 수 없습니다.');
    const method = nextMethod(session, placement);
    const previousMethod = task.method;
    task.method = method;
    task.status = 'active';
    placement.status = 'active';
    session.phase = 'placing';
    event(session, 'task.retried', '이전 실패 방법을 제외한 등록된 파지 방법으로 재시도합니다.', { taskId: task.id, previousMethod, method, actor: 'operator' });
  } else if (action === 'lift') {
    requirePhase(session, ['approved'], '꽃 배치를 승인한 후 꽃다발을 들어 올릴 수 있습니다.');
    session.phase = 'lifting';
    event(session, 'bouquet.lifting', '운영자가 완성된 꽃다발을 들어 올립니다.', { actor: 'operator' });
  } else if (action === 'handover') {
    requirePhase(session, ['lifting'], '꽃다발을 들어 올린 뒤 전달할 수 있습니다.');
    session.phase = 'awaiting_receipt';
    session.receiptConfirmed = false;
    event(session, 'bouquet.handover', '운영자가 꽃다발을 건넵니다. 실제 수령 확인을 기다립니다.', { actor: 'operator' });
  } else if (action === 'received') {
    requirePhase(session, ['awaiting_receipt'], '꽃다발을 건네는 단계에서만 수령을 확인할 수 있습니다.');
    if (session.receiptConfirmed) reject('이미 수령을 확인했습니다. 그리퍼를 놓은 뒤 해제 완료를 확인해 주세요.');
    session.receiptConfirmed = true;
    event(session, 'bouquet.received', '운영자가 사람이 꽃다발을 받았음을 확인했습니다. 그리퍼 해제를 기다립니다.', { actor: 'operator' });
  } else if (action === 'release') {
    requirePhase(session, ['awaiting_receipt'], '수령 확인 후에만 그리퍼 해제를 완료할 수 있습니다.');
    if (!session.receiptConfirmed) reject('사람이 꽃다발을 받았는지 먼저 확인해 주세요.');
    session.phase = 'completed';
    event(session, 'bouquet.released', '운영자가 그리퍼 해제를 확인했습니다. 꽃다발 전달을 완료했습니다.', { actor: 'operator' });
  }
  return changed(session);
}
