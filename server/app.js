import express from 'express';
import { DatabaseSync } from 'node:sqlite';
import { mkdirSync, existsSync } from 'node:fs';
import { writeFile, unlink } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createSession, applyIntent, applyAction, addMessage } from './session.js';
import { BrainWorker, DEFAULT_PYTHON, DEFAULT_BRAIN_DATA } from './brain-worker.js';
import { DEFAULT_MODEL, chat } from './nebius.js';
import { computeDialogueBrain, encodeDialogue } from './dialogue-pipeline.js';
import { synthesizeKorean } from './local-speech.js';
import { transcribeAudio } from './transcription.js';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = Number(process.env.PORT || 4317);
const runtime = process.env.COFLORIST_RUNTIME_DIR || path.join(ROOT, '.runtime');
mkdirSync(runtime, { recursive:true });
const db = new DatabaseSync(path.join(runtime, 'coflorist.sqlite'));
db.exec('CREATE TABLE IF NOT EXISTS records (name TEXT PRIMARY KEY, value TEXT NOT NULL); CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL)');
const saveRecord = db.prepare('INSERT INTO records(name,value) VALUES (?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value');
const readRecord = name => { try {return JSON.parse(db.prepare('SELECT value FROM records WHERE name=?').get(name)?.value || 'null');}catch{return null;} };
const audit = (kind,data) => db.prepare('INSERT INTO audit(at,kind,data) VALUES (?,?,?)').run(new Date().toISOString(),kind,JSON.stringify(data));
let settings = { recipientName:'', recipientHistory:'', historyIsDemo:false, voiceEnabled:true, connectionEnabled:true,
  cameraMode:'device',cameraUrl:'',cameraDeviceId:'',pythonExecutable:process.env.PYTHON_EXECUTABLE || DEFAULT_PYTHON,dataPath:DEFAULT_BRAIN_DATA,...(readRecord('settings') || {}),
  model:process.env.NEBIUS_MODEL || DEFAULT_MODEL };
const apiKey = process.env.NEBIUS_API_KEY?.trim() || '';
const speechDirectory = path.join(runtime, 'speech');
const inputDirectory = path.join(runtime, 'voice-input');
mkdirSync(speechDirectory, { recursive: true });
mkdirSync(inputDirectory, { recursive: true });
let session = createSession();
session.processingStage = null;
let connection = {status:!settings.connectionEnabled ? 'disabled' : apiKey ? 'configured':'unconfigured',model:settings.model,lastError:null,latencyMs:null};
let controller = null;
let transcriptionController = null;
let voiceTestController = null;
let chatSequence = 0;
let brainEpoch = 0;
const clients = new Set();
const brain = new BrainWorker({pythonExecutable:settings.pythonExecutable,dataPath:settings.dataPath,onChange:()=>broadcast()});
function snapshot(){return {settings:{...settings,apiConfigured:Boolean(apiKey)},session,brain:brain.snapshot(),connection};}
function broadcast(){const payload='event: state\ndata: '+JSON.stringify(snapshot())+'\n\n';for(const client of clients)client.write(payload);}
function persist(){saveRecord.run('settings',JSON.stringify(settings));saveRecord.run('lastSession',JSON.stringify(session));broadcast();}
function cancelChat(){
  controller?.abort();controller=null;transcriptionController?.abort();voiceTestController?.abort();
  if(session.processingStage==='brain')brain.stop();
  chatSequence++;session.busy=false;session.processingStage=null;
}
function error(message,status=400){return Object.assign(new Error(message),{status});}
function requireConnection(){
  if(!settings.connectionEnabled)throw error('대화 연결을 켜 주세요.',409);
  if(!apiKey)throw error('.env의 NEBIUS_API_KEY를 설정한 뒤 서버를 다시 실행해 주세요.');
}
async function makeAudio(text, signal) {
  const filename = randomUUID() + '.wav';
  const result = await synthesizeKorean(text, path.join(speechDirectory, filename), { signal });
  return { url:'/api/speech/'+filename, durationMs:result.durationMs, voice:result.voice };
}
async function attachAudio(message, own, id) {
  if(!settings.voiceEnabled)return;
  session.processingStage='synthesizing';persist();
  try {
    const audio=await makeAudio(message.text,own.signal);
    if(id!==chatSequence||own.signal.aborted)throw error('이전 음성 응답을 취소했습니다.',409);
    message.audio=audio;
  } catch(e) {
    if(id!==chatSequence||own.signal.aborted)throw error('이전 음성 응답을 취소했습니다.',409);
    message.audioError=e.message;
  }
}
function runBrain(intent) {
  const epoch=++brainEpoch; const revision=session.revision;
  const stimulusHz = intent==='pause' ? 0 : intent==='retry' ? 120 : intent==='gift' ? 80 : 100;
  brain.run({stimulusHz,durationMs:200,seed:42,intent}).then(result=>{
    if(epoch!==brainEpoch || revision!==session.revision)return;
    session.neuralDecision={action:session.paused ? 'wait' : result.downstreamRateHz>0 ? (session.phase==='retry'?'suggest_retry':'propose'):'confirm',
      downstreamRateHz:result.downstreamRateHz,computedAt:new Date().toISOString(),scope:'induced-subset',basis:'engineered_mapping'};
    audit('neural_result',{intent,spikeCount:result.spikeCount,downstreamRateHz:result.downstreamRateHz,dataHash:result.dataHash,scope:result.scope});
    persist();
  }).catch(()=>broadcast());
}
export const app=express();
app.disable('x-powered-by');
app.use((req,res,next)=>{
  const allowedHosts=new Set(['localhost:'+PORT,'127.0.0.1:'+PORT,'[::1]:'+PORT]);
  if(!allowedHosts.has(req.headers.host))return res.status(403).json({error:'로컬 주소로 접속해 주세요.'});
  const origin=req.headers.origin;
  if(origin && !['http://localhost:'+PORT,'http://127.0.0.1:'+PORT].includes(origin))return res.status(403).json({error:'허용되지 않은 요청입니다.'});
  if(req.headers['sec-fetch-site']==='cross-site' && req.method!=='GET')return res.status(403).json({error:'허용되지 않은 요청입니다.'});
  res.setHeader('X-Content-Type-Options','nosniff');
  res.setHeader('Referrer-Policy','no-referrer');
  res.setHeader('Cache-Control',req.path.startsWith('/api/')?'no-store':'no-cache');
  next();
});
app.use(express.json({limit:'32kb'}));
app.get('/api/health',(_req,res)=>res.json({ok:true,name:'CoFlorist'}));
app.get('/api/state',(_req,res)=>res.json(snapshot()));
app.get('/api/speech/:filename',(req,res)=>{
  const filename=req.params.filename;
  if(!/^[\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12}\.wav$/.test(filename))throw error('음성 파일을 찾을 수 없습니다.',404);
  const file=path.join(speechDirectory,filename);
  if(!existsSync(file))throw error('음성 파일을 찾을 수 없습니다.',404);
  res.type('audio/wav').sendFile(file, { dotfiles: 'allow' });
});
app.post('/api/voice/test',async(_req,res)=>{
  if(voiceTestController)throw error('음성 테스트를 기다려 주세요.',409);
  const own=new AbortController();voiceTestController=own;
  try {res.json({audio:await makeAudio('안녕하세요. 원하는 꽃을 함께 골라볼까요?',own.signal)});}
  finally {if(voiceTestController===own)voiceTestController=null;}
});
app.post('/api/transcribe',express.raw({type:()=>true,limit:'8mb'}),async(req,res)=>{
  requireConnection();
  if(transcriptionController||session.busy)throw error('현재 대화 처리를 기다려 주세요.',409);
  const mime=(req.headers['content-type']||'').split(';')[0].trim().toLowerCase();
  const extension={'audio/webm':'webm','audio/ogg':'ogg','audio/mp4':'m4a','audio/wav':'wav','audio/x-wav':'wav','audio/mpeg':'mp3'}[mime];
  if(!extension||!Buffer.isBuffer(req.body)||req.body.length<44)throw error('녹음한 음성 파일을 보내 주세요.');
  const file=path.join(inputDirectory,randomUUID()+'.'+extension);
  const own=new AbortController();transcriptionController=own;
  const disconnected=()=>{if(!res.writableEnded)own.abort();};res.on('close',disconnected);
  try {
    await writeFile(file,req.body);
    const result=await transcribeAudio(file,{signal:own.signal});
    if(own.signal.aborted)throw error('음성 입력을 취소했습니다.',409);
    res.json({text:result.text});
  } finally {
    res.off('close',disconnected);
    if(transcriptionController===own)transcriptionController=null;
    await unlink(file).catch(()=>{});
  }
});
app.get('/api/events',(req,res)=>{
  res.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-cache','Connection':'keep-alive'});
  clients.add(res);res.write('event: state\ndata: '+JSON.stringify(snapshot())+'\n\n');
  const heartbeat=setInterval(()=>res.write(': keepalive\n\n'),20000);
  req.on('close',()=>{clearInterval(heartbeat);clients.delete(res);});
});
app.post('/api/settings',(req,res)=>{
  const b=req.body;if(!b || typeof b!=='object')throw error('설정 값을 확인해 주세요.');
  if(['apiKey','clearApiKey','model'].some(field=>field in b))throw error('API 키와 모델은 .env에서 설정합니다.');
  const next={...settings};
  for(const [field,max] of Object.entries({recipientName:100,recipientHistory:12000,cameraMode:20,cameraUrl:2000,cameraDeviceId:300,pythonExecutable:1000,dataPath:1000})){
    if(field in b){if(typeof b[field]!=='string'||b[field].length>max)throw error('설정 값을 확인해 주세요: '+field);next[field]=b[field].trim();}
  }
  if(!['device','url','off'].includes(next.cameraMode))throw error('카메라 모드를 확인해 주세요.');
  if(next.cameraUrl){let u;try{u=new URL(next.cameraUrl);}catch{throw error('카메라 URL을 확인해 주세요.');}if(!['http:','https:'].includes(u.protocol))throw error('HTTP 영상 주소를 입력해 주세요.');}
  for(const field of ['historyIsDemo','voiceEnabled','connectionEnabled'])if(field in b){if(typeof b[field]!=='boolean')throw error('설정 값을 확인해 주세요.');next[field]=b[field];}
  const changedBrain=next.pythonExecutable!==settings.pythonExecutable||next.dataPath!==settings.dataPath;
  if(changedBrain&&brain.snapshot().status==='running')throw error('신경 계산 후 경로를 변경해 주세요.',409);
  cancelChat();
  const connectionChanged=settings.connectionEnabled!==next.connectionEnabled;
  settings=next;
  if(changedBrain){brain.stop();brain.pythonExecutable=settings.pythonExecutable;brain.dataPath=settings.dataPath;brain.changed({lastResult:null,metadata:null,mode:'unconfigured'});}
  if(connectionChanged)connection={status:!settings.connectionEnabled?'disabled':apiKey?'configured':'unconfigured',model:settings.model,lastError:null,latencyMs:null};
  persist();res.json(snapshot());
});
app.post('/api/chat',async(req,res)=>{
  const rawText=req.body?.text;if(typeof rawText!=='string')throw error('1~2,000자로 말해 주세요.');
  const text=rawText.trim();if(!text||text.length>2000)throw error('1~2,000자로 말해 주세요.');
  if(encodeDialogue(text).intent==='pause'){
    const candidate=structuredClone(session);addMessage(candidate,'user',text);applyAction(candidate,'pause');
    const reply='기다릴게요.';addMessage(candidate,'assistant',reply);
    cancelChat();brainEpoch++;session=candidate;session.busy=false;session.processingStage=null;persist();
    if(settings.connectionEnabled&&apiKey&&settings.voiceEnabled){
      const id=chatSequence;const own=new AbortController();controller=own;
      try {await attachAudio(session.messages.at(-1),own,id);session.processingStage=null;persist();}
      finally {if(controller===own)controller=null;}
    }
    res.json({reply,...snapshot()});return;
  }
  if(session.busy)throw error('현재 답변을 기다려 주세요. 대기는 바로 요청할 수 있습니다.',409);
  requireConnection();
  brainEpoch++;
  const id=++chatSequence;controller=new AbortController();const own=controller;
  addMessage(session,'user',text);session.busy=true;session.processingStage='brain';session.lastError=null;const revision=session.revision;persist();
  const current=()=>{
    if(id!==chatSequence||own.signal.aborted)throw error('상태가 바뀌어 이전 응답을 취소했습니다.',409);
  };
  try{
    const neural=await computeDialogueBrain({text,brain,seed:session.messages.filter(m=>m.role==='user').length,signal:own.signal});
    current();
    if(revision!==session.revision)throw error('상태가 바뀌어 이전 계산을 취소했습니다.',409);
    session.neuralDecision=neural.readout;
    audit('dialogue_neural_result',{...neural.readout,dataHash:neural.result.dataHash,input:neural.input});
    session.processingStage='responding';persist();
    const result=await chat(apiKey,settings.model,session,settings,own.signal);
    current();
    if(revision!==session.revision)throw error('상태가 바뀌어 이전 응답을 취소했습니다.',409);
    const candidate=structuredClone(session);
    if(candidate.paused && result.intent==='arrange')applyAction(candidate,'resume');
    applyIntent(candidate,result);addMessage(candidate,'assistant',result.reply);
    session=candidate;
    const assistant=session.messages.at(-1);assistant.neural=neural.readout;
    connection={status:'ready',model:result.model,lastError:null,latencyMs:result.latencyMs};
    audit('model_response',{model:result.model,intent:result.intent,latencyMs:result.latencyMs,usage:result.usage});
    persist();
    await attachAudio(assistant,own,id);current();
    session.busy=false;session.processingStage=null;persist();
    res.json({reply:result.reply,...snapshot()});
  }catch(e){if(id===chatSequence){
    if(session.processingStage==='responding'&&e.status!==409){connection.status='error';connection.lastError=e.message;}
    session.busy=false;session.processingStage=null;session.lastError=e.message;persist();
  }throw e;}
  finally{if(controller===own)controller=null;}
});
app.post('/api/action',(req,res)=>{
  const action=req.body?.action;
  const candidate=structuredClone(session);
  applyAction(candidate,action);
  cancelChat();brainEpoch++;session=candidate;session.busy=false;session.processingStage=null;
  if(action==='release')addMessage(session,'assistant','꽃다발을 전달했어요.');
  audit('operator_action',{action,phase:session.phase,revision:session.revision});
  persist();
  if(['retry','fail'].includes(action))runBrain('retry');
  res.json(snapshot());
});
app.post('/api/brain/start',async(_req,res)=>{await brain.start();res.json(snapshot());});
app.post('/api/brain/run',async(req,res)=>{
  const result=await brain.run(req.body||{});audit('neural_test',{spikeCount:result.spikeCount,dataHash:result.dataHash,wallTimeMs:result.wallTimeMs});res.json({result,...snapshot()});
});
app.get('/api/export',(_req,res)=>{
  res.setHeader('Content-Disposition','attachment; filename="coflorist-session.json"');
  res.json({exportedAt:new Date().toISOString(),...snapshot(),audit:db.prepare('SELECT * FROM audit ORDER BY id DESC LIMIT 200').all().map(x=>({...x,data:JSON.parse(x.data)}))});
});
app.use('/api',(_req,res)=>res.status(404).json({error:'사용하지 않는 API입니다.'}));
app.get('/voice-demo.html',(_req,res)=>res.redirect('/'));
app.use(express.static(path.join(ROOT,'public')));
app.use((err,_req,res,_next)=>{
  if(res.headersSent)return;
  const status=err.status||400;
  res.status(status).json({error:status===413?'입력 내용이 너무 깁니다.':err.message||'요청을 처리하지 못했습니다.'});
});
export const server=app.listen(PORT,'127.0.0.1',()=>console.log('CoFlorist Local: http://localhost:'+PORT));
function close(){cancelChat();brain.stop();for(const c of clients)c.end();server.close(()=>{db.close();process.exit(0);});}
process.on('SIGINT',close);process.on('SIGTERM',close);

