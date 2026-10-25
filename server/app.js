import express from 'express';
import { DatabaseSync } from 'node:sqlite';
import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createSession, applyIntent, applyAction, addMessage } from './session.js';
import { BrainWorker, DEFAULT_PYTHON, DEFAULT_BRAIN_DATA } from './brain-worker.js';
import { DEFAULT_MODEL, validateModel, testConnection, listModels, chat } from './nebius.js';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = Number(process.env.PORT || 4317);
const runtime = process.env.COFLORIST_RUNTIME_DIR || path.join(ROOT, '.runtime');
mkdirSync(runtime, { recursive:true });
const db = new DatabaseSync(path.join(runtime, 'coflorist.sqlite'));
db.exec('CREATE TABLE IF NOT EXISTS records (name TEXT PRIMARY KEY, value TEXT NOT NULL); CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL)');
const saveRecord = db.prepare('INSERT INTO records(name,value) VALUES (?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value');
const readRecord = name => { try {return JSON.parse(db.prepare('SELECT value FROM records WHERE name=?').get(name)?.value || 'null');}catch{return null;} };
const audit = (kind,data) => db.prepare('INSERT INTO audit(at,kind,data) VALUES (?,?,?)').run(new Date().toISOString(),kind,JSON.stringify(data));
let settings = { model:process.env.NEBIUS_MODEL || DEFAULT_MODEL, recipientName:'', recipientHistory:'', historyIsDemo:false, voiceEnabled:true,
  cameraMode:'device',cameraUrl:'',cameraDeviceId:'',pythonExecutable:process.env.PYTHON_EXECUTABLE || DEFAULT_PYTHON,dataPath:DEFAULT_BRAIN_DATA,...(readRecord('settings') || {}) };
let apiKey = process.env.NEBIUS_API_KEY?.trim() || '';
let session = createSession();
let connection = {status:apiKey ? 'configured':'unconfigured',model:settings.model,lastError:null,latencyMs:null};
let controller = null;
let connectController = null;
let chatSequence = 0;
let brainEpoch = 0;
const clients = new Set();
const brain = new BrainWorker({pythonExecutable:settings.pythonExecutable,dataPath:settings.dataPath,onChange:()=>broadcast()});
function snapshot(){return {settings:{...settings,apiConfigured:Boolean(apiKey)},session,brain:brain.snapshot(),connection};}
function broadcast(){const payload='event: state\ndata: '+JSON.stringify(snapshot())+'\n\n';for(const client of clients)client.write(payload);}
function persist(){saveRecord.run('settings',JSON.stringify(settings));saveRecord.run('lastSession',JSON.stringify(session));broadcast();}
function cancelChat(){controller?.abort();controller=null;chatSequence++;session.busy=false;}
function error(message,status=400){return Object.assign(new Error(message),{status});}
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
app.get('/api/events',(req,res)=>{
  res.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-cache','Connection':'keep-alive'});
  clients.add(res);res.write('event: state\ndata: '+JSON.stringify(snapshot())+'\n\n');
  const heartbeat=setInterval(()=>res.write(': keepalive\n\n'),20000);
  req.on('close',()=>{clearInterval(heartbeat);clients.delete(res);});
});
app.post('/api/settings',(req,res)=>{
  const b=req.body;if(!b || typeof b!=='object')throw error('설정 값을 확인해 주세요.');
  const next={...settings};
  for(const [field,max] of Object.entries({model:180,recipientName:100,recipientHistory:12000,cameraMode:20,cameraUrl:2000,cameraDeviceId:300,pythonExecutable:1000,dataPath:1000})){
    if(field in b){if(typeof b[field]!=='string'||b[field].length>max)throw error('설정 값을 확인해 주세요: '+field);next[field]=b[field].trim();}
  }
  validateModel(next.model);
  if(!['device','url','off'].includes(next.cameraMode))throw error('카메라 모드를 확인해 주세요.');
  if(next.cameraUrl){let u;try{u=new URL(next.cameraUrl);}catch{throw error('카메라 URL을 확인해 주세요.');}if(!['http:','https:'].includes(u.protocol))throw error('HTTP 영상 주소를 입력해 주세요.');}
  for(const field of ['historyIsDemo','voiceEnabled'])if(field in b){if(typeof b[field]!=='boolean')throw error('설정 값을 확인해 주세요.');next[field]=b[field];}
  if('apiKey' in b && (typeof b.apiKey!=='string'||b.apiKey.length>4096||/[\r\n]/.test(b.apiKey)))throw error('API 키 형식을 확인해 주세요.');
  const changedBrain=next.pythonExecutable!==settings.pythonExecutable||next.dataPath!==settings.dataPath;
  if(changedBrain&&brain.snapshot().status==='running')throw error('신경 계산 후 경로를 변경해 주세요.',409);
  cancelChat();connectController?.abort();connectController=null;
  const credentialsChanged=Boolean(b.apiKey?.trim()) || b.clearApiKey===true || settings.model!==next.model;
  if(b.apiKey?.trim())apiKey=b.apiKey.trim();
  if(b.clearApiKey===true)apiKey='';
  settings=next;
  if(changedBrain){brain.stop();brain.pythonExecutable=settings.pythonExecutable;brain.dataPath=settings.dataPath;brain.changed({lastResult:null,metadata:null,mode:'unconfigured'});}
  if(credentialsChanged)connection={status:apiKey?'configured':'unconfigured',model:settings.model,lastError:null,latencyMs:null};
  persist();res.json(snapshot());
});
app.get('/api/models',async(_req,res)=>res.json({models:await listModels(apiKey)}));
app.post('/api/connect',async(_req,res)=>{
  connectController?.abort();const own=new AbortController();connectController=own;
  try{const result=await testConnection(apiKey,settings.model,own.signal);if(own.signal.aborted)throw error('연결 요청이 취소됐습니다.',409);
    connection={status:'ready',model:result.model,lastError:null,latencyMs:result.latencyMs};
    audit('nebius_connection',{model:result.model,latencyMs:result.latencyMs,usage:result.usage});persist();res.json({ok:true,reply:result.reply,...snapshot()});
  }catch(e){if(!own.signal.aborted){connection.status='error';connection.lastError=e.message;broadcast();}throw e;}
  finally{if(connectController===own)connectController=null;}
});
app.post('/api/chat',async(req,res)=>{
  const rawText=req.body?.text;if(typeof rawText!=='string')throw error('1~2,000자로 말해 주세요.');
  const text=rawText.trim();if(!text||text.length>2000)throw error('1~2,000자로 말해 주세요.');
  if(/^(잠깐\s*)?(기다려|멈춰|대기|정지)(\s|[.!?]|$)/.test(text)||/^잠깐 기다려/.test(text)){
    const candidate=structuredClone(session);addMessage(candidate,'user',text);applyAction(candidate,'pause');
    const reply='기다릴게요.';addMessage(candidate,'assistant',reply);
    cancelChat();brainEpoch++;session=candidate;session.busy=false;persist();res.json({reply,...snapshot()});return;
  }
  if(session.busy)throw error('현재 답변을 기다려 주세요. 대기는 바로 요청할 수 있습니다.',409);
  if(!apiKey)throw error('설정에서 Nebius API 키를 연결해 주세요.');
  const id=++chatSequence;controller=new AbortController();const own=controller;
  addMessage(session,'user',text);session.busy=true;const revision=session.revision;persist();
  try{
    const result=await chat(apiKey,settings.model,session,settings,own.signal);
    if(id!==chatSequence||revision!==session.revision||own.signal.aborted)throw error('상태가 바뀌어 이전 응답을 취소했습니다.',409);
    const candidate=structuredClone(session);
    if(candidate.paused && result.intent==='arrange')applyAction(candidate,'resume');
    applyIntent(candidate,result);addMessage(candidate,'assistant',result.reply);candidate.busy=false;
    session=candidate;
    connection={status:'ready',model:result.model,lastError:null,latencyMs:result.latencyMs};
    audit('model_response',{model:result.model,intent:result.intent,latencyMs:result.latencyMs,usage:result.usage});
    persist();runBrain(result.intent);res.json({reply:result.reply,...snapshot()});
  }catch(e){if(id===chatSequence){session.busy=false;if(e.status!==409){connection.lastError=e.message;}persist();}throw e;}
  finally{if(controller===own)controller=null;}
});
app.post('/api/action',(req,res)=>{
  const action=req.body?.action;
  const candidate=structuredClone(session);
  applyAction(candidate,action);
  cancelChat();brainEpoch++;session=candidate;session.busy=false;
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
app.use(express.static(path.join(ROOT,'public')));
app.use((err,_req,res,_next)=>{
  if(res.headersSent)return;
  const status=err.status||400;
  res.status(status).json({error:status===413?'입력 내용이 너무 깁니다.':err.message||'요청을 처리하지 못했습니다.'});
});
export const server=app.listen(PORT,'127.0.0.1',()=>console.log('CoFlorist Local: http://localhost:'+PORT));
function close(){cancelChat();connectController?.abort();brain.stop();for(const c of clients)c.end();server.close(()=>{db.close();process.exit(0);});}
process.on('SIGINT',close);process.on('SIGTERM',close);

