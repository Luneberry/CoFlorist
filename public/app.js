const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const colorNames = { white: '흰 꽃', red: '빨간 꽃', yellow: '노란 꽃', purple: '보라 꽃' };
const positionNames = { center: '가운데', left: '왼쪽', right: '오른쪽' };
const phaseNames = { idle: '대화 대기', planning: '꽃 선택', placing: '꽃 배치 중', paused: '잠시 대기', retry: '다시 잡기', approved: '작품 확인 완료', lifting: '작품 들어 올리기', handover: '전달 중', awaiting_receipt: '수령 확인 대기', completed: '전달 완료' };
const brainNames = { idle: '신경망 대기', starting: '불러오는 중', ready: '실제 회로 연결', running: '신경 활동 계산 중', error: '연결 확인 필요' };
let state = null;
let initialized = false;
let messagesSignature = '';
let graphSignature = '';
let toastTimer;
let cameraStream = null;
let cameraGeneration = 0;
let listening = false;
let speaking = false;
let speechGeneration = 0;
let replyAudio = null;
let playingMessageId = null;
let playbackStatus = 'idle';
let microphoneStream = null;
let recorder = null;
let micOpening = false;
let micGeneration = 0;
let recordingLimitTimer = null;
let transcribing = false;
let transcriptionController = null;
let connectionChanging = false;
const microphoneSupported = Boolean(navigator.mediaDevices?.getUserMedia && window.MediaRecorder);
const stageNames = { brain: '초파리 신경망 계산 중', responding: '신경망 결과로 답변을 만들고 있어요', synthesizing: '답변을 목소리로 바꾸고 있어요' };
let chatInFlight = false;
let chatGeneration = 0;
let actionInFlight = false;
let eventSource = null;
let fallbackTimer = null;
let spokenIds = new Set();
let localView = location.hash === '#settings' ? 'settings' : 'main';

function toast(message, error = false) {
  const el = $('#toast');
  el.textContent = message;
  el.classList.toggle('error', error);
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, error ? 7000 : 3500);
}

async function api(path, body) {
  const response = await fetch(path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: body === undefined ? { Accept: 'application/json' } : { 'Content-Type': 'application/json', Accept: 'application/json' },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  let result;
  try { result = await response.json(); } catch { throw new Error('서버 응답을 확인할 수 없습니다.'); }
  if (!response.ok) throw new Error(result.error || '요청을 처리하지 못했습니다.');
  if (result.session && result.settings) render(result);
  return result;
}

function navigate(page) {
  localView = page === 'settings' ? 'settings' : 'main';
  $$('.page').forEach(el => { el.hidden = el.id !== `page-${localView}`; });
  $$('.nav-button').forEach(button => {
    const active = button.dataset.page === localView;
    button.classList.toggle('active', active);
    if (active) button.setAttribute('aria-current', 'page'); else button.removeAttribute('aria-current');
  });
  history.replaceState(null, '', `#${localView}`);
}

function setBadge(selector, text, status) {
  const el = $(selector);
  el.textContent = text;
  el.classList.toggle('ready', status === 'ready');
  el.classList.toggle('error', status === 'error');
}

function render(next) {
  const first = !initialized;
  const previous = state;
  state = next;
  const session = state.session;
  const settings = state.settings;
  const connection = state.connection || {};
  const enabled = settings.connectionEnabled !== false;
  const connectionReady = enabled && connection.status === 'ready';
  $('#system-dot').className = `status-dot${connectionReady ? ' ready' : connection.status === 'error' ? ' error' : ''}`;
  $('#system-label').textContent = !enabled ? '대화 연결 꺼짐' : connectionReady ? 'Nebius 연결됨' : settings.apiConfigured ? '대화 준비됨' : '서버 연결 설정 필요';
  $('#phase-label').textContent = stageNames[session.processingStage] || (session.busy ? '답변을 준비하고 있어요' : phaseNames[session.phase] || session.phase);
  $('#phase-dot').className = `status-dot${session.busy ? ' busy' : session.phase === 'completed' ? ' ready' : ''}`;
  $('#connection-toggle').setAttribute('aria-checked', String(enabled));
  $('#connection-toggle').textContent = enabled ? 'ON' : 'OFF';
  $('#connection-toggle').disabled = connectionChanging;
  $('#connection-feedback').textContent = !enabled ? '대화 연결이 꺼져 있어요.' : settings.apiConfigured ? '저장된 연결 설정을 사용해요.' : '서버의 .env에 API 키를 설정해 주세요.';
  $('#voice-toggle').setAttribute('aria-pressed', String(Boolean(settings.voiceEnabled)));
  $('#voice-enabled').checked = Boolean(settings.voiceEnabled);
  $('#voice-toggle').title = settings.voiceEnabled ? '음성 응답 끄기' : '음성 응답 켜기';
  $('#demo-memory-badge').hidden = !settings.historyIsDemo;
  if (((!settings.voiceEnabled && previous?.settings?.voiceEnabled !== false) || (!enabled && previous?.settings?.connectionEnabled !== false)) && replyAudio) stopSpeaking();
  if (!enabled && (listening || micOpening || transcribing)) cancelRecording();
  if (previous && previous.session.revision !== session.revision && (session.paused || !session.messages?.length)) {
    stopSpeaking(); cancelRecording();
  }
  if (first) {
    for (const message of session.messages || []) spokenIds.add(message.id);
    populateSettings(settings);
  }
  renderMessages();
  renderPlacements();
  renderProgress();
  renderOperator();
  renderBrain();
  updateInputControls();
  const latestAssistant = [...(session.messages || [])].reverse().find(message => message.role === 'assistant');
  if (!first && latestAssistant?.audio && !spokenIds.has(latestAssistant.id)) {
    spokenIds.add(latestAssistant.id);
    if (settings.voiceEnabled && enabled && !listening && !micOpening && !transcribing) void playReply(latestAssistant);
  }
  initialized = true;
}

function populateSettings(settings) {
  $('#recipient-name').value = settings.recipientName || '';
  $('#recipient-history').value = settings.recipientHistory || '';
  $('#history-demo').checked = Boolean(settings.historyIsDemo);
  $('#camera-mode').value = ['device', 'url', 'off'].includes(settings.cameraMode) ? settings.cameraMode : 'device';
  $('#camera-url').value = settings.cameraUrl || '';
  $('#python-executable').value = settings.pythonExecutable || '';
  $('#data-path').value = settings.dataPath || '';
  if (settings.cameraDeviceId) {
    const option = new Option('저장된 카메라', settings.cameraDeviceId);
    $('#camera-device').add(option);
    $('#camera-device').value = settings.cameraDeviceId;
  }
  updateCameraFields();
}

function renderMessages() {
  const session = state.session;
  const signature = JSON.stringify([session.messages, session.busy, session.processingStage]);
  $('#quick-prompts').hidden = Boolean(session.messages?.length);
  if (signature === messagesSignature) return;
  messagesSignature = signature;
  const host = $('#messages');
  const nearBottom = host.scrollHeight - host.scrollTop - host.clientHeight < 100;
  host.replaceChildren();
  if (!session.messages?.length && !session.busy) {
    const empty = document.createElement('div'); empty.className = 'conversation-empty';
    const flower = document.createElement('div'); flower.className = 'empty-flower'; flower.textContent = '✳'; flower.setAttribute('aria-hidden', 'true');
    const text = document.createElement('p'); text.textContent = '메시지를 입력하거나 마이크로 말을 걸어주세요.';
    empty.append(flower, text); host.append(empty);
  }
  for (const item of session.messages || []) {
    const message = document.createElement('article'); message.className = `message ${item.role}`;
    const label = document.createElement('div'); label.className = 'message-label'; label.textContent = item.role === 'assistant' ? 'CoFlorist' : '나';
    const time = document.createElement('time'); time.dateTime = item.createdAt;
    const date = new Date(item.createdAt); time.textContent = Number.isFinite(date.getTime()) ? date.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', hour12: false }) : '';
    label.append(time);
    const body = document.createElement('p'); body.className = 'message-body'; body.textContent = item.text;
    message.append(label, body);
    if (item.role === 'assistant' && item.audio) {
      const controls = document.createElement('div'); controls.className = 'message-audio';
      const button = document.createElement('button'); button.type = 'button'; button.className = 'message-audio-button'; button.dataset.audioMessage = item.id;
      button.addEventListener('click', () => {
        if (playingMessageId === item.id && speaking) stopSpeaking();
        else void playReply(item, true);
      });
      const status = document.createElement('span'); status.dataset.audioStatus = item.id;
      controls.append(button, status); message.append(controls);
    } else if (item.role === 'assistant' && item.audioError) {
      const note = document.createElement('p'); note.className = 'message-audio-error'; note.textContent = `음성 생성 오류 · ${item.audioError}`; message.append(note);
    }
    host.append(message);
  }
  if (session.busy) {
    const pending = document.createElement('article'); pending.className = 'message assistant pending';
    const body = document.createElement('p'); body.className = 'message-body'; body.textContent = `${stageNames[session.processingStage] || '답변을 준비하고 있어요'} ···`; pending.append(body); host.append(pending);
  }
  updateAudioControls();
  if (nearBottom || !initialized || session.busy) host.scrollTop = host.scrollHeight;
}

function renderPlacements() {
  const session = state.session;
  const host = $('#placements'); host.replaceChildren();
  for (const placement of session.placements || []) {
    const chip = document.createElement('span'); chip.className = `placement-chip ${placement.status}`;
    const dot = document.createElement('i'); dot.className = `color-dot ${placement.color}`;
    const label = document.createElement('span'); label.textContent = `${positionNames[placement.position] || placement.position}${placement.status === 'completed' ? ' ✓' : ''}`;
    chip.title = `${colorNames[placement.color] || placement.color} · ${positionNames[placement.position] || placement.position} · ${placement.status === 'completed' ? '완료' : placement.status === 'active' ? '진행 중' : '대기'}`;
    chip.append(dot, label); host.append(chip);
  }
  if (!session.placements?.length) { const empty = document.createElement('span'); empty.className = 'muted'; empty.textContent = '선택 대기'; host.append(empty); }
  const complete = (session.placements || []).filter(item => item.status === 'completed').length;
  $('#placement-count').textContent = session.placements?.length ? `${complete} / ${session.placements.length}송이` : '0송이';
  const task = session.activeTask;
  $('#active-task').textContent = session.paused ? '잠시 기다리는 중' : task?.status === 'active' ? `${colorNames[task.color]} → ${positionNames[task.position]}` : session.phase === 'completed' ? '전달 완료' : ['lifting', 'handover', 'awaiting_receipt'].includes(session.phase) ? phaseNames[session.phase] : '작업 대기';
}

function renderProgress() {
  const session = state.session;
  const phase = session.paused ? session.resumePhase : session.phase;
  const allCompleted = session.placements?.length > 0 && session.placements.every(item => item.status === 'completed');
  const index = ['lifting', 'handover', 'awaiting_receipt', 'completed'].includes(phase) ? 3 : phase === 'approved' || allCompleted ? 2 : session.placements?.length ? 1 : 0;
  $$('.task-progress li').forEach((item, i) => { item.classList.toggle('current', i === index); item.classList.toggle('done', i < index || phase === 'completed'); });
  const pause = $('#pause-button');
  pause.dataset.action = session.paused ? 'resume' : 'pause';
  pause.innerHTML = session.paused ? '<svg viewBox="0 0 20 20" fill="currentColor"><path d="m6 3 10 7-10 7Z"/></svg>다시 시작' : '<svg viewBox="0 0 20 20" fill="currentColor"><rect x="5" y="4" width="3" height="12" rx="1"/><rect x="12" y="4" width="3" height="12" rx="1"/></svg>잠깐 기다려';
  pause.disabled = session.phase === 'completed';
}

function renderOperator() {
  const session = state.session;
  const host = $('#operator-actions'); host.replaceChildren();
  const add = (action, text, primary = false) => { const button = document.createElement('button'); button.type = 'button'; button.className = `button ${primary ? 'primary' : 'secondary'}`; button.textContent = text; button.dataset.action = action; button.disabled = actionInFlight; host.append(button); };
  let note = '꽃 선택 대기';
  if (session.paused) note = '대기 중 · 재개 후 작업 가능';
  else if (session.phase === 'placing' && session.activeTask?.status === 'active') { note = `${colorNames[session.activeTask.color] || '꽃'} · ${positionNames[session.activeTask.position] || ''} 배치`; add('complete', '배치 완료', true); add('fail', '파지 실패'); }
  else if (session.phase === 'retry') { note = '실패 기록을 반영해 다시 잡아요.'; add('retry', '다시 시도', true); }
  else if (session.phase === 'approved') { note = '사용자가 작품을 확인했어요.'; add('lift', '들어 올리기', true); }
  else if (session.phase === 'lifting') { note = '작품을 안전하게 들어 올렸나요?'; add('handover', '작품 전달', true); }
  else if (['handover', 'awaiting_receipt'].includes(session.phase)) {
    note = session.receiptConfirmed ? '수령 확인 완료 · 놓아도 좋아요.' : '받는 사람이 작품을 잡았는지 확인해 주세요.';
    add(session.receiptConfirmed ? 'release' : 'received', session.receiptConfirmed ? '그리퍼 해제' : '수령 확인', true);
  } else if (session.phase === 'completed') note = '전달 완료';
  else if (session.placements?.some(item => item.status === 'pending')) { note = '선택한 꽃의 배치를 시작해요.'; add('start', '다음 꽃 잡기', true); }
  else if (session.placements?.length) note = '대화로 작품을 확인해 주세요.';
  $('#operator-message').textContent = note;
  const event = session.events?.at(-1);
  $('#last-event').textContent = event ? `${new Date(event.createdAt).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', hour12: false })} · ${event.text}` : '진행 기록 없음';
}

function svgElement(tag, attributes) {
  const element = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, String(value));
  return element;
}

function renderBrain() {
  const brain = state.brain || {};
  $('.conversation-brain').dataset.status = brain.status || 'idle';
  updateBrainActivity();
  const status = brainNames[brain.status] || '신경망 대기';
  setBadge('#brain-status', status, brain.status);
  setBadge('#brain-settings-status', status, brain.status);
  $('#brain-test').disabled = ['starting', 'running'].includes(brain.status);
  $('#brain-start').disabled = ['starting', 'running'].includes(brain.status);
  const result = brain.lastResult;
  if (brain.error) feedback('#brain-feedback', brain.error, true);
  if (!result) {
    graphSignature = '';
    $('#brain-svg').replaceChildren();
    $('#brain-svg').setAttribute('aria-label', '신경 활동 결과 대기');
    $('#brain-empty').hidden = false;
    for (const id of ['#metric-neurons', '#metric-spikes', '#metric-time']) $(id).textContent = '—';
    $('#brain-detail').textContent = 'FlyWire 부분회로 · Brian2';
    $('#brain-result-details').textContent = '아직 계산 결과가 없습니다.';
    return;
  }
  const decisionLabels = { wait: '대기', propose: '행동 제안', suggest_retry: '재시도 제안', confirm: '확인' };
  const decision = decisionLabels[state.session.neuralDecision?.action];
  $('#brain-detail').textContent = `${result.neuronCount?.toLocaleString() || '—'} 뉴런 · 실제 부분회로${decision ? ` · ${decision}` : ''}`;
  const signature = JSON.stringify([result.dataHash, result.seed, result.spikeCount, result.wallTimeMs]);
  if (signature === graphSignature) return;
  graphSignature = signature;
  $('#brain-empty').hidden = true;
  $('#metric-neurons').textContent = Number.isFinite(result.activeNeurons) ? result.activeNeurons.toLocaleString() : '—';
  $('#metric-spikes').textContent = Number.isFinite(result.spikeCount) ? result.spikeCount.toLocaleString() : '—';
  $('#metric-time').textContent = Number.isFinite(result.wallTimeMs) ? `${(result.wallTimeMs / 1000).toFixed(2)}s` : '—';
  const svg = $('#brain-svg'); svg.replaceChildren();
  svg.setAttribute('aria-label', `실제 신경 활동: ${result.activeNeurons ?? '미확인'}개 활성 뉴런, ${result.spikeCount ?? '미확인'}회 신호. 원형 배치는 시각화용이며 해부학적 위치가 아닙니다.`);
  const nodes = (result.nodes || result.nodeRates || []).slice(0, 64);
  const coordinates = new Map();
  const maxRate = Math.max(1, ...nodes.map(node => Number(node.rateHz) || 0));
  nodes.forEach((node, index) => {
    const angle = index * Math.PI * (3 - Math.sqrt(5));
    const radius = 8 + 35 * Math.sqrt(index / Math.max(1, nodes.length - 1));
    coordinates.set(String(node.id), { x: 72 + Math.cos(angle) * radius * 1.25, y: 50 + Math.sin(angle) * radius });
  });
  for (const edge of (result.edges || []).slice(0, 180)) {
    const source = coordinates.get(String(edge.source)); const target = coordinates.get(String(edge.target));
    if (source && target) svg.append(svgElement('line', { class: 'neural-edge', x1: source.x, y1: source.y, x2: target.x, y2: target.y, stroke: Number(edge.weightMv) < 0 ? '#b9bda5' : '#b4c2a4', 'stroke-width': .45, opacity: .38 }));
  }
  nodes.forEach(node => {
    const point = coordinates.get(String(node.id));
    const rate = Number(node.rateHz) || 0;
    const circle = svgElement('circle', { class: rate > 0 ? 'neural-node active' : 'neural-node', style: `--node-delay:${-(nodes.indexOf(node) % 13) * .11}s`, cx: point.x, cy: point.y, r: 1.3 + Math.sqrt(rate / maxRate) * 1.8, fill: node.stimulated ? '#c3b477' : rate > 0 ? '#688854' : '#c7ceb9', opacity: rate > 0 ? .9 : .5 });
    const title = svgElement('title', {}); title.textContent = `뉴런 ${node.id} · ${rate.toFixed(1)} Hz${node.stimulated ? ' · 입력 자극' : ''}`; circle.append(title); svg.append(circle);
  });
  const traces = result.traces || [];
  if (traces.length) {
    const max = Math.max(1, ...traces.map(item => Number(item.spikes) || 0));
    svg.append(svgElement('line', { x1: 147, y1: 81, x2: 274, y2: 81, stroke: '#e1e6d6', 'stroke-width': 1 }));
    const points = traces.map((item, index) => `${148 + index * 125 / Math.max(1, traces.length - 1)},${80 - (Number(item.spikes) || 0) / max * 53}`).join(' ');
    svg.append(svgElement('polyline', { points, fill: 'none', stroke: '#8caa70', 'stroke-width': 1.4, 'stroke-linejoin': 'round' }));
    const label = svgElement('text', { x: 148, y: 96, fill: '#a7b197', 'font-size': 6 }); label.textContent = `0 — ${result.durationMs ?? result.simulationTimeMs} ms`; svg.append(label);
  }
  const { nodes: _nodes, nodeRates: _nodeRates, edges: _edges, traces: _traces, spikes: _spikes, ...summary } = result;
  $('#brain-result-details').textContent = JSON.stringify(summary, null, 2);
  feedback('#brain-feedback', '실제 신경 계산 완료');
}

function updateBrainActivity() {
  const stage = state?.session?.processingStage;
  const running = state?.brain?.status === 'running' || stage === 'brain';
  const phase = listening || micOpening ? 'listening' : transcribing ? 'transcribing' : running ? 'computing' : stage || (state?.session?.busy || chatInFlight ? 'responding' : speaking ? 'speaking' : 'idle');
  const card = $('.conversation-brain');
  card.dataset.interaction = phase === 'responding' || phase === 'synthesizing' ? 'thinking' : phase;
  const labels = { listening: '듣고 있어요', transcribing: '말씀을 텍스트로 바꾸고 있어요', computing: '초파리 신경망 계산 중', responding: '신경망 결과로 답변 생성 중', synthesizing: '한국어 음성 생성 중', speaking: '답변을 소리로 전하고 있어요' };
  $('#brain-activity').textContent = labels[phase] || '대화 대기';
  $('#brain-replay-note').textContent = phase === 'computing' ? '현재 입력으로 실제 부분회로 계산 중' : state?.brain?.lastResult ? '측정한 신경 활동을 답변 생성에 반영' : '텍스트·음성 입력 → 신경망 → 답변 → 음성';
}

function updateInputControls() {
  const enabled = Boolean(state?.settings?.apiConfigured) && state?.settings?.connectionEnabled !== false;
  const busy = chatInFlight || Boolean(state?.session?.busy);
  $('#send-button').disabled = !enabled || busy || listening || micOpening || transcribing;
  $('#chat-input').disabled = !enabled || listening || micOpening || transcribing;
  $('#chat-input').placeholder = !enabled ? '설정에서 대화 연결을 켜주세요' : '메시지를 입력하세요';
  $('#mic-button').disabled = !microphoneSupported || !enabled || busy || micOpening || transcribing;
  $('#mic-button').classList.toggle('listening', listening);
  $('#mic-button').setAttribute('aria-pressed', String(listening));
  $('#mic-button').setAttribute('aria-label', listening ? '말하기 완료' : '음성으로 말하기');
  $('#mic-button').title = listening ? '다시 누르면 말을 마치고 전송해요' : '음성으로 말하기';
  $$('#quick-prompts button').forEach(button => { button.disabled = !enabled || busy || listening || micOpening || transcribing; });
}

function feedback(selector, text, isError = false) { const el = $(selector); el.textContent = text; el.classList.toggle('error', isError); }

async function submitChat(text) {
  text = text.trim();
  if (/^(?:(?:잠깐|잠시)\s*)?(?:기다려|멈춰|정지|대기)(?:줘|주세요|해줘|해 주세요)?(?:[.!?]|$|\s)/.test(text)) {
    chatGeneration += 1;
    chatInFlight = false;
    stopSpeaking(); cancelRecording();
    $('#chat-input').value = ''; resizeInput();
    try { await api('/api/chat', { text }); } catch (error) { toast(error.message, true); }
    return;
  }
  if (!text || chatInFlight || state?.session?.busy || state?.settings?.connectionEnabled === false) return;
  if (text.length > 2000) { toast('메시지는 2,000자까지 입력할 수 있어요.', true); return; }
  stopSpeaking();
  if (listening || micOpening || transcribing) cancelRecording();
  const ownGeneration = ++chatGeneration;
  chatInFlight = true; updateInputControls(); updateBrainActivity();
  $('#voice-status').textContent = '';
  $('#chat-input').value = ''; resizeInput();
  try { await api('/api/chat', { text }); }
  catch (error) { if (ownGeneration === chatGeneration) { toast(error.message, true); if (!$('#chat-input').value) $('#chat-input').value = text; } }
  finally { if (ownGeneration === chatGeneration) { chatInFlight = false; updateInputControls(); resizeInput(); updateBrainActivity(); } }
}

async function action(name) {
  if (actionInFlight && !['pause', 'reset'].includes(name)) return;
  if (['pause', 'reset'].includes(name)) { chatGeneration += 1; chatInFlight = false; stopSpeaking(); cancelRecording(); }
  actionInFlight = true; if (state) renderOperator();
  try { await api('/api/action', { action: name }); }
  catch (error) { toast(error.message, true); }
  finally { actionInFlight = false; if (state) renderOperator(); }
}

async function saveSettings(data) { return api('/api/settings', data); }

function stopCamera() {
  cameraGeneration += 1;
  cameraStream?.getTracks().forEach(track => track.stop()); cameraStream = null;
  $('#camera-video').pause(); $('#camera-video').srcObject = null; $('#camera-video').removeAttribute('src'); $('#camera-video').load();
  $('#camera-remote').removeAttribute('src');
  $('#camera-video').hidden = true; $('#camera-remote').hidden = true; $('#camera-placeholder').hidden = false;
  $('#camera-source').textContent = '카메라 연결 전 · 참고 사진';
  $('#camera-connect').hidden = false;
  $('#disconnect-camera').disabled = true;
}

async function connectCamera(settings = state?.settings || {}) {
  stopCamera();
  const generation = cameraGeneration;
  if (settings.cameraMode === 'off') { feedback('#camera-feedback', '참고 사진 표시 중'); return; }
  feedback('#camera-feedback', '카메라 연결 중…');
  try {
    if (settings.cameraMode === 'url') {
      const url = new URL(settings.cameraUrl);
      if (!['http:', 'https:'].includes(url.protocol)) throw new Error('HTTP 또는 HTTPS 영상 주소를 입력해 주세요.');
      const isVideo = /\.(mp4|webm|ogg)(?:$|\?)/i.test(url.href);
      const media = isVideo ? $('#camera-video') : $('#camera-remote');
      await new Promise((resolve, reject) => {
        const timeout = setTimeout(() => done(new Error('영상 응답이 없습니다. 주소와 연결을 확인해 주세요.')), 12000);
        const success = () => done(); const failure = () => done(new Error('영상을 불러오지 못했습니다. 지원되는 영상 또는 MJPEG 주소를 확인해 주세요.'));
        const done = error => { clearTimeout(timeout); media.removeEventListener(isVideo ? 'loadeddata' : 'load', success); media.removeEventListener('error', failure); error ? reject(error) : resolve(); };
        media.addEventListener(isVideo ? 'loadeddata' : 'load', success, { once: true }); media.addEventListener('error', failure, { once: true }); media.src = url.href;
        if (isVideo) media.play().catch(failure);
      });
      if (generation !== cameraGeneration) return;
      media.hidden = false;
    } else {
      if (!navigator.mediaDevices?.getUserMedia) throw new Error('현재 브라우저에서 카메라를 사용할 수 없습니다. localhost에서 Chrome 또는 Edge로 열어주세요.');
      const stream = await navigator.mediaDevices.getUserMedia({ audio: false, video: { ...(settings.cameraDeviceId ? { deviceId: { exact: settings.cameraDeviceId } } : {}), width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 30, max: 30 } } });
      if (generation !== cameraGeneration) { stream.getTracks().forEach(track => track.stop()); return; }
      cameraStream = stream;
      $('#camera-video').srcObject = stream; await $('#camera-video').play(); $('#camera-video').hidden = false;
      stream.getVideoTracks()[0]?.addEventListener('ended', () => { if (cameraStream !== stream) return; stopCamera(); feedback('#camera-feedback', '카메라 연결 종료'); });
      await refreshCameras();
    }
    if (generation !== cameraGeneration) return;
    $('#camera-placeholder').hidden = true; $('#camera-connect').hidden = true;
    $('#disconnect-camera').disabled = false;
    $('#camera-source').textContent = '카메라 연결됨'; feedback('#camera-feedback', '카메라 연결됨');
  } catch (error) {
    if (generation !== cameraGeneration) return;
    stopCamera();
    const message = error.name === 'NotAllowedError' ? '카메라 권한을 허용해 주세요.' : error.name === 'NotFoundError' ? '연결된 카메라가 없습니다.' : error.name === 'NotReadableError' ? '다른 앱에서 카메라를 사용 중일 수 있어요. LeLab 화면 공유를 선택하거나 사용 중인 앱을 닫아주세요.' : error.message;
    feedback('#camera-feedback', message, true); toast(message, true);
  }
}

async function shareLeLabScreen() {
  if (!navigator.mediaDevices?.getDisplayMedia) {
    feedback('#camera-feedback', '이 브라우저는 화면 공유를 지원하지 않습니다. 직접 카메라 연결을 사용해 주세요.', true);
    return;
  }
  const generation = ++cameraGeneration;
  const previousFeedback = $('#camera-feedback').textContent;
  const previousError = $('#camera-feedback').classList.contains('error');
  const button = $('#share-lelab');
  button.disabled = true;
  let sharedStream = null;
  try {
    // Called from this button's click: keep the browser picker in the user gesture.
    sharedStream = await navigator.mediaDevices.getDisplayMedia({ video: { frameRate: { ideal: 30, max: 30 } }, audio: false });
    if (generation !== cameraGeneration) { sharedStream.getTracks().forEach(track => track.stop()); return; }
    stopCamera();
    cameraStream = sharedStream;
    $('#camera-video').srcObject = sharedStream;
    await $('#camera-video').play();
    if (cameraStream !== sharedStream) return;
    $('#camera-video').hidden = false;
    $('#camera-placeholder').hidden = true;
    $('#camera-connect').hidden = true;
    $('#disconnect-camera').disabled = false;
    $('#camera-source').textContent = '공유 화면';
    feedback('#camera-feedback', '화면 공유 중');
    sharedStream.getVideoTracks()[0]?.addEventListener('ended', () => {
      if (cameraStream !== sharedStream) return;
      stopCamera();
      feedback('#camera-feedback', '화면 공유 종료');
    });
  } catch (error) {
    if (sharedStream) {
      sharedStream.getTracks().forEach(track => track.stop());
      if (cameraStream === sharedStream) stopCamera();
    }
    if (['NotAllowedError', 'AbortError'].includes(error.name)) {
      feedback('#camera-feedback', previousFeedback, previousError);
      return;
    }
    feedback('#camera-feedback', error.name === 'NotReadableError' ? '선택한 화면을 읽지 못했어요. 공유할 탭이나 창을 다시 선택해 주세요.' : error.message, true);
  } finally { button.disabled = false; }
}

async function refreshCameras() {
  if (!navigator.mediaDevices?.enumerateDevices) return;
  try {
    const current = $('#camera-device').value || state?.settings?.cameraDeviceId || '';
    const devices = (await navigator.mediaDevices.enumerateDevices()).filter(item => item.kind === 'videoinput');
    const select = $('#camera-device'); select.replaceChildren(new Option('기본 카메라', ''));
    devices.forEach((device, i) => select.add(new Option(device.label || `카메라 ${i + 1}`, device.deviceId)));
    if ([...select.options].some(option => option.value === current)) select.value = current;
  } catch (error) { feedback('#camera-feedback', error.message, true); }
}

function updateCameraFields() { const mode = $('#camera-mode').value; $('#camera-device-label').hidden = mode !== 'device'; $('#camera-url-label').hidden = mode !== 'url'; }

function updateAudioControls() {
  const labels = { playing: '재생 중', ended: '재생 완료', blocked: '버튼을 눌러 들어주세요', error: '음성 재생 오류', paused: '재생 멈춤' };
  $$('[data-audio-message]').forEach(button => {
    const own = button.dataset.audioMessage === playingMessageId;
    button.textContent = own && speaking ? '■ 음성 멈추기' : own && playbackStatus === 'ended' ? '▶ 다시 듣기' : '▶ 음성 듣기';
    button.setAttribute('aria-label', own && speaking ? '답변 음성 멈추기' : '답변 음성 듣기');
  });
  $$('[data-audio-status]').forEach(element => {
    const item = state?.session?.messages?.find(message => message.id === element.dataset.audioStatus);
    const duration = Number.isFinite(item?.audio?.durationMs) ? `${(item.audio.durationMs / 1000).toFixed(1)}초` : '';
    element.textContent = element.dataset.audioStatus === playingMessageId ? labels[playbackStatus] || duration : duration;
  });
  $('#reply-voice-status').textContent = playbackStatus === 'playing' ? '노트북에서 답변 재생 중' : playbackStatus === 'blocked' ? '자동 재생이 제한됐어요. 답변 아래 음성 듣기를 눌러주세요.' : playbackStatus === 'ended' ? '답변 음성 재생 완료' : playbackStatus === 'error' ? '답변 아래 음성 듣기로 다시 시도해 주세요.' : '';
}

function stopSpeaking() {
  speechGeneration++;
  if (replyAudio) { replyAudio.pause(); replyAudio.removeAttribute('src'); replyAudio.load(); }
  replyAudio = null; speaking = false; playingMessageId = null; playbackStatus = 'idle';
  updateAudioControls(); updateBrainActivity();
}

async function playReply(message, force = false) {
  if (!message?.audio?.url || (!force && !state?.settings?.voiceEnabled)) return;
  let url;
  try { url = new URL(message.audio.url, location.origin); if (url.origin !== location.origin) throw new Error(); }
  catch { toast('음성 파일 주소를 확인할 수 없습니다.', true); return; }
  if (listening || micOpening || transcribing) cancelRecording();
  stopSpeaking();
  const generation = ++speechGeneration;
  const audio = new Audio(url.href); replyAudio = audio; playingMessageId = message.id || 'voice-test';
  audio.addEventListener('playing', () => { if (generation !== speechGeneration) return; speaking = true; playbackStatus = 'playing'; updateAudioControls(); updateBrainActivity(); });
  audio.addEventListener('ended', () => { if (generation !== speechGeneration) return; speaking = false; playbackStatus = 'ended'; updateAudioControls(); updateBrainActivity(); });
  audio.addEventListener('pause', () => { if (generation !== speechGeneration || audio.ended) return; speaking = false; playbackStatus = 'paused'; updateAudioControls(); updateBrainActivity(); });
  audio.addEventListener('error', () => { if (generation !== speechGeneration) return; speaking = false; playbackStatus = 'error'; updateAudioControls(); updateBrainActivity(); });
  try { await audio.play(); }
  catch (error) {
    if (generation !== speechGeneration) return;
    speaking = false; playbackStatus = error.name === 'NotAllowedError' ? 'blocked' : 'error';
    updateAudioControls(); updateBrainActivity();
  }
}

function cancelRecording() {
  micGeneration++; clearTimeout(recordingLimitTimer); recordingLimitTimer = null;
  transcriptionController?.abort(); transcriptionController = null;
  if (recorder?.state === 'recording') recorder.stop();
  recorder = null; microphoneStream?.getTracks().forEach(track => track.stop()); microphoneStream = null;
  listening = false; micOpening = false; transcribing = false;
  $('#voice-status').textContent = ''; updateInputControls(); updateBrainActivity();
}

async function finishRecording(own, capture, chunks, tooLarge) {
  if (own !== micGeneration) return;
  clearTimeout(recordingLimitTimer); recordingLimitTimer = null;
  listening = false; recorder = null;
  microphoneStream?.getTracks().forEach(track => track.stop()); microphoneStream = null;
  const blob = new Blob(chunks, { type: capture.mimeType || chunks[0]?.type || 'audio/webm' });
  if (tooLarge || blob.size > 8 * 1024 * 1024) { $('#voice-status').textContent = '녹음이 너무 커요. 60초 이내로 다시 말해 주세요.'; updateInputControls(); updateBrainActivity(); return; }
  if (!blob.size) { $('#voice-status').textContent = '녹음된 말소리가 없어요. 다시 말해 주세요.'; updateInputControls(); updateBrainActivity(); return; }
  transcribing = true; const controller = new AbortController(); transcriptionController = controller;
  $('#voice-status').textContent = '말씀을 텍스트로 바꾸고 있어요…'; updateInputControls(); updateBrainActivity();
  try {
    const response = await fetch('/api/transcribe', { method: 'POST', headers: { 'Content-Type': blob.type }, body: blob, signal: controller.signal });
    let result; try { result = await response.json(); } catch { throw new Error('음성 인식 서버의 응답을 확인할 수 없습니다.'); }
    if (!response.ok) throw new Error(result.error || '음성을 인식하지 못했어요. 다시 말해 주세요.');
    if (own !== micGeneration) return;
    const text = typeof result.text === 'string' ? result.text.trim() : '';
    if (!text) throw new Error('인식된 말소리가 없어요. 다시 말해 주세요.');
    if (text.length > 2000) throw new Error('인식된 문장이 너무 길어요. 짧게 다시 말해 주세요.');
    transcribing = false; transcriptionController = null;
    $('#voice-status').textContent = '말씀을 인식했어요.'; $('#chat-input').value = text; resizeInput(); updateInputControls();
    await submitChat(text);
  } catch (error) {
    if (own === micGeneration && error.name !== 'AbortError') { $('#voice-status').textContent = error.message; toast(error.message, true); }
  } finally {
    if (own === micGeneration) { transcribing = false; transcriptionController = null; updateInputControls(); updateBrainActivity(); }
  }
}

async function startRecording() {
  if (!microphoneSupported || micOpening || transcribing || chatInFlight || state?.session?.busy || state?.settings?.connectionEnabled === false) return;
  stopSpeaking(); cancelRecording();
  const own = ++micGeneration; micOpening = true; updateInputControls();
  $('#voice-status').textContent = '마이크를 연결하고 있어요…';
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true }, video: false });
    if (own !== micGeneration) { stream.getTracks().forEach(track => track.stop()); return; }
    microphoneStream = stream;
    const mimeType = ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/mp4'].find(type => MediaRecorder.isTypeSupported(type));
    const capture = new MediaRecorder(stream, mimeType ? { mimeType } : {}); recorder = capture;
    const chunks = []; let size = 0; let tooLarge = false;
    capture.addEventListener('dataavailable', event => {
      if (own !== micGeneration || !event.data.size) return;
      size += event.data.size;
      if (size > 8 * 1024 * 1024) { tooLarge = true; if (capture.state === 'recording') capture.stop(); }
      else chunks.push(event.data);
    });
    capture.addEventListener('stop', () => { void finishRecording(own, capture, chunks, tooLarge); });
    capture.addEventListener('error', () => { if (own === micGeneration) { cancelRecording(); $('#voice-status').textContent = '마이크 녹음에 실패했어요. 다시 시도해 주세요.'; } });
    capture.start(500); micOpening = false; listening = true;
    recordingLimitTimer = setTimeout(() => { if (own === micGeneration && capture.state === 'recording') capture.stop(); }, 60000);
    $('#voice-status').textContent = '듣고 있어요 · 다시 누르면 전송해요 (최대 60초)';
    updateInputControls(); updateBrainActivity();
  } catch (error) {
    if (own !== micGeneration) return;
    cancelRecording();
    const labels = { NotAllowedError: '마이크 권한을 허용해 주세요.', NotFoundError: '연결된 마이크를 찾지 못했어요.', NotReadableError: '다른 앱이 마이크를 사용 중인지 확인해 주세요.' };
    $('#voice-status').textContent = labels[error.name] || '마이크를 연결하지 못했어요. 다시 시도해 주세요.';
  }
}

function setupSpeech() {
  $('#speech-support').textContent = microphoneSupported ? '메인의 마이크를 누르고 말한 뒤 다시 누르면 전송해요. 최대 60초까지 녹음합니다.' : '이 브라우저는 마이크 녹음을 지원하지 않아요. 메시지 입력은 사용할 수 있어요.';
  $('#mic-button').addEventListener('click', () => {
    if (listening && recorder?.state === 'recording') { recorder.stop(); return; }
    void startRecording();
  });
}

function resizeInput() { const input = $('#chat-input'); input.style.height = '30px'; input.style.height = `${Math.min(100, input.scrollHeight)}px`; }

function startEvents() {
  eventSource = new EventSource('/api/events');
  eventSource.addEventListener('state', event => { try { render(JSON.parse(event.data)); } catch (error) { console.error('상태 표시 오류', error); } });
  eventSource.onopen = () => { if (fallbackTimer) clearInterval(fallbackTimer); fallbackTimer = null; };
  eventSource.onerror = () => { if (!fallbackTimer) fallbackTimer = setInterval(() => { api('/api/state').catch(() => { $('#system-label').textContent = '서버 연결 확인 필요'; $('#system-dot').className = 'status-dot error'; }); }, 5000); };
}

$$('.nav-button').forEach(button => button.addEventListener('click', () => navigate(button.dataset.page)));
$('.brand').addEventListener('click', event => { event.preventDefault(); navigate('main'); });
window.addEventListener('hashchange', () => navigate(location.hash.slice(1)));
$('#chat-form').addEventListener('submit', event => { event.preventDefault(); submitChat($('#chat-input').value); });
$('#chat-input').addEventListener('input', resizeInput);
$('#chat-input').addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); submitChat($('#chat-input').value); } });
$('#quick-prompts').addEventListener('click', event => { const button = event.target.closest('[data-prompt]'); if (button) submitChat(button.dataset.prompt); });
$('#operator-actions').addEventListener('click', event => { const button = event.target.closest('[data-action]'); if (button) action(button.dataset.action); });
$('#pause-button').addEventListener('click', event => action(event.currentTarget.dataset.action));

$('#connection-toggle').addEventListener('click', async () => {
  if (connectionChanging) return;
  const enabled = state?.settings?.connectionEnabled === false;
  if (!enabled) { chatGeneration++; chatInFlight = false; stopSpeaking(); cancelRecording(); }
  connectionChanging = true; $('#connection-toggle').disabled = true;
  try { await saveSettings({ connectionEnabled: enabled }); }
  catch (error) { toast(error.message, true); }
  finally { connectionChanging = false; if (state) render(state); }
});
$('#recipient-form').addEventListener('submit', async event => { event.preventDefault(); try { await saveSettings({ recipientName: $('#recipient-name').value, recipientHistory: $('#recipient-history').value, historyIsDemo: $('#history-demo').checked }); toast('이야기를 저장했어요.'); } catch (error) { toast(error.message, true); } });
$('#camera-mode').addEventListener('change', updateCameraFields);
$('#camera-form').addEventListener('submit', async event => { event.preventDefault(); try { await saveSettings({ cameraMode: $('#camera-mode').value, cameraUrl: $('#camera-url').value, cameraDeviceId: $('#camera-device').value }); await connectCamera(); } catch (error) { feedback('#camera-feedback', error.message, true); } });
$('#camera-connect').addEventListener('click', () => connectCamera({ ...state?.settings, cameraMode: state?.settings?.cameraMode === 'off' ? 'device' : state?.settings?.cameraMode }));
$('#share-lelab').addEventListener('click', shareLeLabScreen);
$('#disconnect-camera').addEventListener('click', () => { stopCamera(); feedback('#camera-feedback', '연결 해제됨'); });
$('#refresh-cameras').addEventListener('click', () => refreshCameras());
$('#voice-test').addEventListener('click', async () => {
  $('#voice-test').disabled = true;
  const generation = speechGeneration;
  try { const result = await api('/api/voice/test', {}); if (generation === speechGeneration) await playReply({ id: 'voice-test', audio: result.audio }, true); }
  catch (error) { toast(error.message, true); }
  finally { $('#voice-test').disabled = false; }
});
$('#voice-stop').addEventListener('click', stopSpeaking);
$('#voice-enabled').addEventListener('change', async event => { if (!event.target.checked) stopSpeaking(); try { await saveSettings({ voiceEnabled: event.target.checked }); } catch (error) { event.target.checked = !event.target.checked; toast(error.message, true); } });
$('#voice-toggle').addEventListener('click', async () => { const enabled = !state?.settings?.voiceEnabled; if (!enabled) stopSpeaking(); try { await saveSettings({ voiceEnabled: enabled }); } catch (error) { toast(error.message, true); } });
$('#brain-form').addEventListener('submit', async event => { event.preventDefault(); feedback('#brain-feedback', '신경망 준비 중…'); $('#brain-start').disabled = true; try { await saveSettings({ pythonExecutable: $('#python-executable').value, dataPath: $('#data-path').value }); await api('/api/brain/start', {}); feedback('#brain-feedback', '실제 FlyWire 부분회로 연결됨'); } catch (error) { feedback('#brain-feedback', error.message, true); } finally { $('#brain-start').disabled = false; } });
$('#brain-test').addEventListener('click', async () => { feedback('#brain-feedback', '실제 신경 활동 계산 중…'); $('#brain-test').disabled = true; try { await api('/api/brain/run', {}); feedback('#brain-feedback', '실제 신경 계산 완료'); } catch (error) { feedback('#brain-feedback', error.message, true); } finally { $('#brain-test').disabled = false; } });
$('#export-session').addEventListener('click', async () => { try { const result = await api('/api/export'); const url = URL.createObjectURL(new Blob([JSON.stringify(result, null, 2)], { type: 'application/json' })); const link = document.createElement('a'); link.href = url; link.download = `coflorist-${new Date().toISOString().slice(0, 10)}.json`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); } catch (error) { toast(error.message, true); } });
$('#reset-session').addEventListener('click', async () => { if (!window.confirm('현재 대화와 작업을 초기화할까요? 필요한 기록은 먼저 내려받아 주세요.')) return; await action('reset'); toast('새로운 시연을 시작할 준비가 됐어요.'); });
window.addEventListener('beforeunload', () => { stopSpeaking(); cancelRecording(); cameraStream?.getTracks().forEach(track => track.stop()); eventSource?.close(); clearInterval(fallbackTimer); });

navigate(localView);
setupSpeech();
if (!navigator.mediaDevices?.getDisplayMedia) {
  $('#share-lelab').disabled = true;
  $('#camera-share-help').textContent = '이 브라우저는 화면 공유를 지원하지 않습니다. 직접 카메라 연결을 사용해 주세요.';
}
try { await api('/api/state'); startEvents(); refreshCameras(); }
catch (error) { $('#system-label').textContent = '서버 연결 확인 필요'; $('#system-dot').className = 'status-dot error'; toast(error.message, true); startEvents(); }
