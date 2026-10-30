// This is an application-defined encoder/readout around the measured FlyWire
// circuit. The circuit produces spikes, not Korean text or semantic intent.
const invalid = message => Object.assign(new Error(message), { status: 400 });
const cancelled = () => Object.assign(new Error('상태가 바뀌어 이전 대화를 취소했습니다.'), { status: 409 });

export function encodeDialogue(text, { seed = 42 } = {}) {
  if (typeof text !== 'string' || !text.trim() || text.trim().length > 2000) throw invalid('1~2,000자로 말해 주세요.');
  if (!Number.isInteger(seed) || seed < 0 || seed > 2147483647) throw invalid('신경 계산 시드가 올바르지 않습니다.');
  const normalized = text.trim().replace(/\s+/g, ' ');
  let intent = 'chat', stimulusHz = 60;
  if (/^(?:(?:잠깐|잠시)\s*)?(?:기다려|멈춰|정지|대기)(?:줘|주세요|해줘|해 주세요)?(?:[.!?]|$|\s)/.test(normalized)) {
    intent = 'pause'; stimulusHz = 0;
  } else if (/재시도|다시\s*(?:잡|해|놓|시도)/.test(normalized)) {
    intent = 'retry'; stimulusHz = 120;
  } else if (/천천히|잘\s*모르|고민|망설|어떻게\s*할지/.test(normalized)) {
    intent = 'hesitant'; stimulusHz = 40;
  } else if (/놓아|놓으|배치|옮겨|꽂아|가운데.*송이|오른쪽.*송이|왼쪽.*송이/.test(normalized)) {
    intent = 'arrange'; stimulusHz = 100;
  } else if (/선물|추천|꽃다발/.test(normalized)) {
    intent = 'gift'; stimulusHz = 80;
  }
  return { stimulusHz, durationMs: 200, seed, intent };
}

export function brainReadout(result, input) {
  if (!result || result.mode !== 'flywire' || result.computeMode !== 'live' || result.scope !== 'induced-subset') {
    throw invalid('실제 FlyWire 부분회로 계산 결과가 필요합니다.');
  }
  for (const field of ['downstreamRateHz', 'activeNeurons', 'spikeCount']) {
    if (!Number.isFinite(result[field]) || result[field] < 0) throw invalid('신경 계산 결과를 확인할 수 없습니다.');
  }
  if (result.stimulusHz !== input.stimulusHz || result.seed !== input.seed || result.durationMs !== input.durationMs) {
    throw invalid('현재 발화와 신경 계산 결과가 일치하지 않습니다.');
  }
  const action = input.intent === 'pause' || input.stimulusHz === 0 ? 'wait'
    : result.downstreamRateHz === 0 || input.intent === 'hesitant' ? 'confirm' : 'propose';
  return {
    action,
    responseStyle: action === 'wait' ? 'pause' : action === 'confirm' ? 'brief_confirmation' : 'concise_suggestion',
    stimulusHz: input.stimulusHz,
    inputIntent: input.intent,
    seed: input.seed,
    durationMs: input.durationMs,
    downstreamRateHz: result.downstreamRateHz,
    activeNeurons: result.activeNeurons,
    spikeCount: result.spikeCount,
    computedAt: new Date().toISOString(),
    scope: result.scope,
    dataHash: result.dataHash,
    basis: 'engineered_mapping',
  };
}

export async function computeDialogueBrain({ text, brain, seed = 42, signal }) {
  if (signal?.aborted) throw cancelled();
  const input = encodeDialogue(text, { seed });
  // A failed calculation must stop the dialogue; never replace it with preset
  // metrics or produce an answer before the requested trial has completed.
  const result = await brain.run(input);
  if (signal?.aborted) throw cancelled();
  return { input, result, readout: brainReadout(result, input) };
}
