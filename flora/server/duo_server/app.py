"""서버 본체: 기기의 접속 안내 요청과 음성 연결을 받는다.

두 가지 입구가 있다.
- 공용 주소 /xiaozhi/ota/ + /xiaozhi/v1/ : 암호 하나를 모든 기기가 같이 쓴다. 같은 와이파이 안에서만 쓸 것.
- 등록 방식 /register → /d/<비밀>/xiaozhi/ota/ : 인터넷에 열 때. 등록한 사람마다 주소·암호·API 키가 다르다.
"""
from __future__ import annotations

import asyncio
import hmac
import html
import json
import logging
import time
import uuid
import wave
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from starlette.websockets import WebSocketDisconnect

from . import opus, protocol
from .feed import ConversationFeed, stimulus_hz_for
from .metrics import MetricsWriter
from .pipeline import Pipeline, Reply, TurnEvents, TurnResult, normalize_flower_color
from .protocol import ProtocolError, Session, normalize_emotion
from .registry import Registration, Registry

log = logging.getLogger("duo")

# 기기는 받은 소리를 약 2.4초까지만 쌓아 둔다. 실제 재생 속도에 맞춰 보내되 조금 앞서 보낸다.
PACE_LEAD_FRAMES = 5
FAIL_FACE = "confused"
CHAT_TIMEOUT = 25.0  # 글 대화에서 답변을 기다리는 최대 시간(초)
CHAT_MAX_HISTORY = 12  # 글 대화에서 기억하는 이전 말 수


class DeviceEvents(TurnEvents):
    """대화 진행 상황을 기기가 아는 메시지로 바꿔 보낸다."""

    def __init__(self, ws: WebSocket, session: Session, pace_audio: bool, fail_hold_seconds: float,
                 feed: ConversationFeed | None = None) -> None:
        self._ws = ws
        self._session = session
        self._feed = feed
        self._pace = pace_audio
        self._fail_hold = fail_hold_seconds
        self._encoder: opus.Encoder | None = None
        self._pending = bytearray()
        self._frames_sent = 0
        self._audio_started_at = 0.0
        self._speaking = False

    async def _send(self, message: dict) -> None:
        await self._ws.send_text(json.dumps(message, ensure_ascii=False))

    async def on_transcript(self, text: str) -> None:
        await self._send(self._session.stt(text))
        if self._feed:
            self._feed.message("user", text)

    async def on_thinking(self) -> None:
        await self._send(self._session.emotion("thinking"))
        if self._feed:
            self._feed.state("thinking")

    async def on_reply(self, reply: Reply) -> None:
        await self._send(self._session.emotion(reply.emotion))
        await self._send(self._session.tts("start"))
        await self._send(self._session.tts("sentence_start", reply.text))
        self._speaking = True
        if self._feed:
            publish_reply(self._feed, reply)
            self._feed.state("speaking")

    async def on_audio(self, pcm: bytes, sample_rate: int) -> None:
        if self._encoder is None:
            self._encoder = opus.Encoder(sample_rate, protocol.FRAME_MS)
        self._pending += pcm
        size = opus.frame_bytes(sample_rate, protocol.FRAME_MS)
        while len(self._pending) >= size:
            frame = bytes(self._pending[:size])
            del self._pending[:size]
            await self._send_frame(frame)

    async def _send_frame(self, frame: bytes) -> None:
        assert self._encoder is not None
        if self._pace:
            now = time.monotonic()
            if self._frames_sent == 0:
                self._audio_started_at = now
            due = self._audio_started_at + max(0, self._frames_sent - PACE_LEAD_FRAMES) * protocol.FRAME_MS / 1000
            if due > now:
                await asyncio.sleep(due - now)
        await self._ws.send_bytes(self._encoder.encode(frame))
        self._frames_sent += 1

    async def finish(self, result: TurnResult) -> None:
        if self._encoder is not None and self._pending:
            size = opus.frame_bytes(self._encoder.sample_rate, protocol.FRAME_MS)
            frame = bytes(self._pending) + b"\x00" * (size - len(self._pending))
            self._pending.clear()
            await self._send_frame(frame)

        if not result.ok and not self._speaking:
            # 실패를 표정으로 알린다. 말하기 시작·끝을 보내야 기기가 기다림 상태로 돌아간다.
            await self._send(self._session.emotion(FAIL_FACE))
            await self._send(self._session.tts("start"))
            if self._fail_hold > 0:
                await asyncio.sleep(self._fail_hold)
        await self._send(self._session.tts("stop"))
        if self._feed:
            self._feed.state("idle" if result.ok else "error")


# ---------------------------------------------------------------- 등록 페이지 글

REGISTER_FORM = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>DUO 기기 등록</title>
<style>
body{font-family:-apple-system,"Apple SD Gothic Neo","Noto Sans KR",sans-serif;max-width:560px;margin:40px auto;padding:0 16px;line-height:1.6;word-break:keep-all}
label{display:block;margin-top:18px;font-weight:600}
input{width:100%%;padding:10px;font-size:16px;border:1px solid #bbb;border-radius:8px;box-sizing:border-box}
button{margin-top:24px;padding:12px 20px;font-size:16px;border:0;border-radius:8px;background:#222;color:#fff}
small{color:#666}
.err{background:#fde8e8;border-radius:8px;padding:10px 14px;color:#8a1c1c}
</style></head><body>
<h1>DUO 기기 등록</h1>
%(intro)s
%(error)s
<form method="post" action="/register">
<label>이름 (누구의 기기인지)<input name="name" maxlength="40" required></label>
<label>초대 코드<input name="invite" required autocomplete="off"></label>
%(upstage_field)s
%(openai_field)s
%(gemini_field)s
<button type="submit">접속 주소 받기</button>
</form>
</body></html>"""

REGISTER_DONE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>DUO 접속 주소</title>
<style>
body{font-family:-apple-system,"Apple SD Gothic Neo","Noto Sans KR",sans-serif;max-width:560px;margin:40px auto;padding:0 16px;line-height:1.6;word-break:keep-all}
code{display:block;background:#f3f3f3;padding:12px;border-radius:8px;font-size:15px;word-break:break-all;user-select:all}
ol li{margin:8px 0}
.warn{background:#fff4d6;border-radius:8px;padding:10px 14px;color:#6b4e00}
</style></head><body>
<h1>%(name)s님의 접속 주소</h1>
<p>아래 두 주소는 본인만 알고 있어야 해요. 이 페이지를 닫으면 다시 볼 수 없으니 복사해 두세요.</p>
<h2>1. 기기 없이 글로 먼저 시험하기</h2>
<p>소리 없이 글로 묻고 답하는 페이지예요. 서버와 답변 모델이 제대로 붙었는지 여기서 먼저 확인해 보세요.</p>
<code>%(chat_url)s</code>
%(voice_note)s
<h2>2. 기기에 넣는 접속 주소</h2>
<code>%(url)s</code>
<h3>기기에 넣는 방법</h3>
<ol>
<li>기기를 켜고 설정용 와이파이(이름이 <b>xiaozhi-…</b> 또는 <b>DUO-…</b>)에 휴대폰으로 접속</li>
<li>브라우저에서 <b>192.168.4.1</b> 열기</li>
<li>집·휴대폰 와이파이 이름과 비밀번호 입력</li>
<li><b>고급(Advanced)</b> 탭의 <b>Custom OTA URL</b> 칸에 위 주소를 붙여넣기</li>
<li>저장하면 기기가 다시 켜지며 이 서버에 붙어요. 버튼을 누른 채 말하면 답해요.</li>
</ol>
</body></html>"""


REGISTER_INTRO_KEYS = """<p>기기를 이 서버에 붙이려면 초대 코드와 본인의 API 키를 넣어 주세요. 키는 이 서버 안에서만 쓰이고 화면에 다시 보여 주지 않아요. 답변 모델 키는 <b>(필수)</b>라고 적힌 것 하나만 넣으면 돼요.</p>"""
REGISTER_INTRO_NO_KEYS = """<p>기기를 이 서버에 붙이려면 이름과 초대 코드만 넣어 주세요. 답변과 음성은 이 서버에 준비된 모델로 처리돼요.</p>"""
GEMINI_FIELD = """<label>제미나이(Gemini) API 키 %(gemini_note)s<input name="gemini_key" autocomplete="off">
<small>aistudio.google.com 에서 발급. 음성 인식·음성 합성에 쓰여요.</small></label>"""
UPSTAGE_FIELD_REQUIRED = """<label>업스테이지(Upstage) API 키 (필수)<input name="upstage_key" required autocomplete="off">
<small>console.upstage.ai 에서 발급. 답변(솔라 모델)에 쓰여요.</small></label>"""
UPSTAGE_FIELD_OPTIONAL = """<label>업스테이지(Upstage) API 키 (선택, 비우면 서버의 답변 모델을 씀)<input name="upstage_key" autocomplete="off"></label>"""
OPENAI_FIELD_REQUIRED = """<label>OpenAI API 키 (필수)<input name="openai_key" required autocomplete="off">
<small>platform.openai.com 에서 발급한 sk-로 시작하는 키. 답변에 쓰여요. 코덱스·ChatGPT 로그인으로는 안 돼요.</small></label>"""
OPENAI_FIELD_OPTIONAL = """<label>OpenAI API 키 (선택, 비우면 서버의 답변 모델을 씀)<input name="openai_key" autocomplete="off"></label>"""
GEMINI_NOTE_MISSING = "(음성 기기를 붙이려면 필수, 글 대화만 하면 비워도 됨)"
GEMINI_NOTE_OPTIONAL = "(선택, 비우면 서버의 키를 씀)"
VOICE_NOTE_MISSING = """<p class="warn">제미나이 키를 넣지 않아서 <b>음성은 아직 안 돼요.</b> 글 대화 페이지는 쓸 수 있어요. 음성 기기를 붙이려면 제미나이 키를 넣고 다시 등록해 주세요.</p>"""

CHAT_PAGE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>DUO 글로 묻고 답하기</title>
<style>
body{font-family:-apple-system,"Apple SD Gothic Neo","Noto Sans KR",sans-serif;max-width:560px;margin:24px auto;padding:0 16px;line-height:1.6;word-break:keep-all}
#log{min-height:200px;margin:16px 0}
.msg{margin:8px 0;padding:10px 14px;border-radius:12px;white-space:pre-wrap}
.me{background:#e8f0fe;margin-left:40px}
.bot{background:#f3f3f3;margin-right:40px}
.err{background:#fde8e8;color:#8a1c1c;margin-right:40px}
form{display:flex;gap:8px}
input{flex:1;padding:12px;font-size:16px;border:1px solid #bbb;border-radius:8px}
button{padding:12px 18px;font-size:16px;border:0;border-radius:8px;background:#222;color:#fff}
small{color:#666}
</style></head><body>
<h1>%(name)s님 · 글로 묻고 답하기</h1>
<p><small>소리 없이 글로만 묻고 답해요. 답변 모델: %(llm)s</small></p>
<div id="log"></div>
<form id="f"><input id="question" autocomplete="off" placeholder="질문을 적고 Enter" required><button type="submit">보내기</button></form>
<script>
var log=document.getElementById("log"),q=document.getElementById("question");
function add(who,text,cls){var d=document.createElement("div");d.className="msg "+cls;d.textContent=who+" "+text;log.appendChild(d);d.scrollIntoView();}
document.getElementById("f").addEventListener("submit",async function(e){
  e.preventDefault();var text=q.value.trim();if(!text)return;add("나:",text,"me");q.value="";
  try{
    var r=await fetch(location.pathname,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({text:text})});
    var b=await r.json();
    if(b.ok){add("듀오:",b.reply+"  ("+b.llm_ms+"ms, "+b.emotion+")","bot");}else{add("오류:",b.error,"err");}
  }catch(err){add("오류:","서버에 닿지 못했어요: "+err,"err");}
  q.focus();
});
</script></body></html>"""


def _form_page(
    gemini_key_missing: bool,
    error: str = "",
    upstage_key_required: bool = True,
    openai_key_required: bool = False,
    ask_keys: bool = True,
) -> str:
    """등록 화면. ask_keys=False면 키 칸을 모두 숨기고 이름·초대 코드만 받는다 (모두 서버 키 사용)."""
    if not ask_keys:
        upstage_field = openai_field = gemini_field = ""
    else:
        upstage_field = UPSTAGE_FIELD_REQUIRED if upstage_key_required else UPSTAGE_FIELD_OPTIONAL
        openai_field = OPENAI_FIELD_REQUIRED if openai_key_required else OPENAI_FIELD_OPTIONAL
        gemini_field = GEMINI_FIELD % {"gemini_note": GEMINI_NOTE_MISSING if gemini_key_missing else GEMINI_NOTE_OPTIONAL}
    return REGISTER_FORM % {
        "error": '<p class="err">%s</p>' % html.escape(error) if error else "",
        "intro": REGISTER_INTRO_KEYS if ask_keys else REGISTER_INTRO_NO_KEYS,
        "upstage_field": upstage_field,
        "openai_field": openai_field,
        "gemini_field": gemini_field,
    }


def publish_reply(feed: ConversationFeed, reply: Reply) -> None:
    """플로라의 답을 대시보드·로봇용 필드와 함께 피드에 올린다."""
    feed.message(
        "assistant", reply.text, flower=reply.flower, flower_color=reply.flower_color, emotion=reply.emotion,
        stimulus_hz=stimulus_hz_for(reply.emotion, reply.flower_color),
    )


def feed_response(feed: ConversationFeed, request: Request, allowed_origins: tuple[str, ...]):
    origin = request.headers.get("origin")
    if origin and origin not in allowed_origins:
        return JSONResponse({"error": "허용되지 않은 출처"}, status_code=403)
    once = request.query_params.get("once") in ("1", "true")
    last_id = request.headers.get("last-event-id") or request.query_params.get("last_event_id")
    headers = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no", "Vary": "Origin"}
    if origin:
        headers["Access-Control-Allow-Origin"] = origin
    return StreamingResponse(feed.stream(last_id, once=once), media_type="text/event-stream; charset=utf-8", headers=headers)


def create_app(
    pipeline: Pipeline | None = None,
    device_token: str = "",
    metrics_path: str | Path | None = None,
    pace_audio: bool = True,
    fail_hold_seconds: float | None = None,
    save_audio_dir: str | Path | None = None,
    *,
    legacy_routes: bool = True,
    registry: Registry | None = None,
    invite_code: str = "",
    pipeline_factory: Callable[[Registration], Pipeline] | None = None,
    gemini_key_missing: bool = False,
    upstage_key_required: bool = True,
    openai_key_required: bool = False,
    ask_keys: bool = True,
    public_url: str = "",
    device_volume: int | None = None,
    feed_allowed_origins: tuple[str, ...] = ("http://localhost:4317", "http://127.0.0.1:4317"),
    flower_colors: tuple[str, ...] = ("white", "red", "yellow", "purple"),
) -> FastAPI:
    if legacy_routes:
        if pipeline is None:
            raise ValueError("공용 주소를 켜려면 대화 진행기(pipeline)가 필요합니다")
        if not device_token:
            raise ValueError("접속 암호(DUO_DEVICE_TOKEN)가 비어 있습니다")
    if registry is not None and pipeline_factory is None and invite_code:
        raise ValueError("등록 방식에는 등록한 사람의 키로 진행기를 만드는 pipeline_factory가 필요합니다")
    if registry is not None and not ask_keys and (upstage_key_required or openai_key_required):
        raise ValueError("등록 화면의 키 칸을 끄려면 서버에 답변 모델 키가 있어야 합니다")
    if fail_hold_seconds is None:
        fail_hold_seconds = 2.0 if pace_audio else 0.0

    app = FastAPI(title="DUO server")
    writer = MetricsWriter(metrics_path) if metrics_path else None
    audio_dir = Path(save_audio_dir) if save_audio_dir else None
    pipelines: dict[str, Pipeline] = {}  # 등록 비밀 → 그 사람의 키로 만든 진행기
    chat_histories: dict[str, list[dict]] = {}  # 등록 비밀 → 글 대화의 이전 말
    feeds: dict[str, ConversationFeed] = {}  # 등록 비밀("" = 옛 공용 입구) → 대시보드 피드

    def feed_for(key: str) -> ConversationFeed:
        return feeds.setdefault(key, ConversationFeed())
    public_url = public_url.rstrip("/")

    # ------------------------------------------------------------ 도우미

    def public_base(request: Request | WebSocket) -> str:
        """바깥에서 보이는 주소의 앞부분 (https://host). 중계기(Tailscale 등) 뒤에 있으면 그 헤더를 따른다."""
        if public_url:
            return public_url
        proto = request.headers.get("x-forwarded-proto") or request.url.scheme
        host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
        return "%s://%s" % (proto, host)

    def websocket_url(request: Request, path: str) -> str:
        base = public_base(request)
        secure = base.startswith("https://")
        host = base.split("://", 1)[1] if "://" in base else base
        return protocol.websocket_url_for_host(host, secure=secure, path=path)

    def save_input(pcm: bytes, sample_rate: int, session_id: str) -> None:
        """기기 마이크로 들어온 소리를 파일로 남긴다 (마이크 확인용, 기본은 꺼짐)."""
        assert audio_dir is not None
        audio_dir.mkdir(parents=True, exist_ok=True)
        name = "%s-%s-in.wav" % (datetime.now().strftime("%Y%m%d-%H%M%S-%f"), session_id)
        with wave.open(str(audio_dir / name), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes(pcm)

    def pipeline_for(registration: Registration) -> Pipeline:
        assert pipeline_factory is not None
        found = pipelines.get(registration.token)
        if found is None:
            found = pipelines[registration.token] = pipeline_factory(registration)
        return found

    async def ota_body(request: Request, ws_path: str, token: str) -> dict:
        info = None
        try:
            raw = await request.body()
            info = json.loads(raw) if raw else None
        except ValueError:
            info = None
        version = protocol.device_version_from_info(info)
        url = websocket_url(request, ws_path)
        log.info("접속 안내 요청: device=%s version=%s → %s", request.headers.get("device-id", ""), version, url)
        return protocol.ota_response(
            device_version=version,
            websocket_url=url,
            token=token,
            now_ms=int(time.time() * 1000),
        )

    # ------------------------------------------------------------ 상태

    @app.get("/health")
    async def health() -> dict:
        return {
            "ok": True,
            "stt": pipeline.stt.name if pipeline else "",
            "llm": pipeline.llm.name if pipeline else "",
            "tts": pipeline.tts.name if pipeline else "",
            "registrations": len(registry.all()) if registry else 0,
        }

    # ------------------------------------------------------------ 공용 주소 (같은 와이파이용)

    if legacy_routes:
        assert pipeline is not None

        async def ota(request: Request) -> dict:
            return await ota_body(request, protocol.WEBSOCKET_PATH, device_token)

        async def voice(ws: WebSocket) -> None:
            if not protocol.token_matches(ws.headers.get("authorization"), device_token):
                await ws.close(code=1008)
                return
            await run_connection(ws, pipeline, feed=feed_for(""))

        async def events_legacy(request: Request):
            return feed_response(feed_for(""), request, feed_allowed_origins)

        app.add_api_route("/events", events_legacy, methods=["GET"])

        for path in (protocol.OTA_PATH, protocol.OTA_PATH.rstrip("/")):
            app.add_api_route(path, ota, methods=["GET", "POST"])
        for path in (protocol.WEBSOCKET_PATH, protocol.WEBSOCKET_PATH.rstrip("/")):
            app.add_api_websocket_route(path, voice)

    # ------------------------------------------------------------ 등록 방식 (인터넷 공개용)

    if registry is not None:

        async def register_form(request: Request):
            if not invite_code:
                return JSONResponse({"error": "등록이 꺼져 있습니다"}, status_code=404)
            return HTMLResponse(_form_page(gemini_key_missing, "", upstage_key_required, openai_key_required, ask_keys))

        async def register_submit(request: Request):
            if not invite_code:
                return JSONResponse({"error": "등록이 꺼져 있습니다"}, status_code=404)
            fields = parse_qs((await request.body()).decode("utf-8", "replace"), keep_blank_values=True)

            def value(name: str) -> str:
                return (fields.get(name) or [""])[0].strip()

            def again(message: str, status: int):
                return HTMLResponse(
                    _form_page(gemini_key_missing, message, upstage_key_required, openai_key_required, ask_keys),
                    status_code=status,
                )

            if not hmac.compare_digest(value("invite").encode(), invite_code.encode()):
                return again("초대 코드가 맞지 않아요.", 403)
            if not ask_keys:
                # 키 칸을 껐으면 넘어온 키 값은 버리고 모두 서버의 키로 처리한다
                reg = registry.create(value("name"), "", "", "")
            else:
                if upstage_key_required and not value("upstage_key"):
                    return again("업스테이지 API 키를 넣어 주세요.", 400)
                if openai_key_required and not value("openai_key"):
                    return again("OpenAI API 키를 넣어 주세요.", 400)
                reg = registry.create(value("name"), value("upstage_key"), value("gemini_key"), value("openai_key"))
            base = public_base(request)
            log.info("새 등록: %s (등록된 사람 %d명, 제미나이 키 %s)", reg.name, len(registry.all()),
                     "있음" if reg.gemini_key else "없음")
            return HTMLResponse(REGISTER_DONE % {
                "name": html.escape(reg.name),
                "url": html.escape(base + protocol.device_ota_path(reg.token)),
                "chat_url": html.escape(base + protocol.device_chat_path(reg.token)),
                "voice_note": VOICE_NOTE_MISSING if gemini_key_missing and not reg.gemini_key else "",
            })

        async def ota_for(request: Request, token: str):
            reg = registry.get(token)
            if reg is None:
                return JSONResponse({"error": "모르는 주소"}, status_code=404)
            registry.note_device(token, request.headers.get("device-id", ""))
            return await ota_body(request, protocol.device_websocket_path(token), reg.access_token)

        async def voice_for(ws: WebSocket, token: str) -> None:
            reg = registry.get(token)
            if reg is None or not protocol.token_matches(ws.headers.get("authorization"), reg.access_token):
                await ws.close(code=1008)
                return
            await run_connection(ws, pipeline_for(reg), owner=reg.name, feed=feed_for(reg.token))

        async def events_for(request: Request, token: str):
            reg = registry.get(token)
            if reg is None:
                return JSONResponse({"error": "모르는 주소"}, status_code=404)
            return feed_response(feed_for(reg.token), request, feed_allowed_origins)

        async def chat_page(request: Request, token: str):
            reg = registry.get(token)
            if reg is None:
                return JSONResponse({"error": "모르는 주소"}, status_code=404)
            return HTMLResponse(CHAT_PAGE % {
                "name": html.escape(reg.name),
                "llm": html.escape(pipeline_for(reg).llm.name),
            })

        async def chat_submit(request: Request, token: str):
            """소리 없이 글로 묻고 답한다. 음성 인식·음성 합성은 전혀 부르지 않는다."""
            reg = registry.get(token)
            if reg is None:
                return JSONResponse({"ok": False, "error": "모르는 주소"}, status_code=404)
            try:
                data = json.loads(await request.body())
            except ValueError:
                data = None
            text = data.get("text") if isinstance(data, dict) else ""
            text = text.strip() if isinstance(text, str) else ""
            if not text:
                return JSONResponse({"ok": False, "error": "질문이 비어 있어요"}, status_code=400)

            turn_pipeline = pipeline_for(reg)
            history = chat_histories.setdefault(reg.token, [])
            feed = feed_for(reg.token)
            feed.message("user", text)
            feed.state("thinking")
            result = TurnResult(transcript=text, llm_name=turn_pipeline.llm.name)
            started = time.monotonic()
            error = ""
            reply = None
            try:
                reply = await asyncio.wait_for(turn_pipeline.llm.reply(text, list(history)), CHAT_TIMEOUT)
            except asyncio.TimeoutError:
                error = "답변 생성이 시간 안에 끝나지 않음"
            except Exception as exc:
                error = "답변 생성 실패: %s" % exc
            result.llm_ms = result.total_ms = round((time.monotonic() - started) * 1000)
            if reply is not None:
                reply = Reply((reply.text or "").strip(), normalize_emotion(reply.emotion),
                              (reply.flower or "").strip(), normalize_flower_color(reply.flower_color, flower_colors))
                if not reply.text:
                    error = "답변이 비어 있음"
            if error:
                result.failed_stage, result.error = "llm", error
                log.warning("글 대화 실패 (%s, %dms): %s", reg.name, result.llm_ms, error)
                if writer:
                    writer.write(result, "chat:" + reg.name)
                feed.state("error")
                return JSONResponse({"ok": False, "error": error}, status_code=502)

            assert reply is not None
            result.ok, result.reply_text, result.emotion = True, reply.text, reply.emotion
            result.flower, result.flower_color = reply.flower, reply.flower_color
            history.append({"role": "user", "content": text})
            history.append({"role": "assistant", "content": reply.text})
            del history[: max(0, len(history) - CHAT_MAX_HISTORY)]
            log.info("글 대화 (%s): 질문 %d자 → 답 %d자, %dms", reg.name, len(text), len(reply.text), result.llm_ms)
            if writer:
                writer.write(result, "chat:" + reg.name)
            publish_reply(feed, reply)
            feed.state("idle")
            return {"ok": True, "reply": reply.text, "emotion": reply.emotion,
                    "flower": reply.flower, "flower_color": reply.flower_color,
                    "llm_ms": result.llm_ms, "llm": turn_pipeline.llm.name}

        app.add_api_route("/register", register_form, methods=["GET"])
        app.add_api_route("/register", register_submit, methods=["POST"])
        app.add_api_route(protocol.device_chat_path("{token}"), chat_page, methods=["GET"])
        app.add_api_route(protocol.device_chat_path("{token}"), chat_submit, methods=["POST"])
        app.add_api_route(protocol.device_feed_path("{token}"), events_for, methods=["GET"])
        for path in (protocol.device_ota_path("{token}"), protocol.device_ota_path("{token}").rstrip("/")):
            app.add_api_route(path, ota_for, methods=["GET", "POST"])
        for path in (protocol.device_websocket_path("{token}"), protocol.device_websocket_path("{token}").rstrip("/")):
            app.add_api_websocket_route(path, voice_for)

    # ------------------------------------------------------------ 음성 연결 (두 입구 공통)

    async def run_turn(ws: WebSocket, session: Session, packets: list[bytes], history: list[dict],
                       device_id: str, turn_pipeline: Pipeline, feed: ConversationFeed | None = None) -> None:
        decoder = opus.Decoder(session.input_sample_rate)
        pcm = bytearray()
        broken = 0
        for packet in packets:
            try:
                pcm += decoder.decode(packet, session.input_frame_ms)
            except opus.OpusError:
                broken += 1
        if broken:
            log.warning("풀지 못한 소리 조각 %d개를 건너뜀", broken)

        if audio_dir is not None and pcm:
            save_input(bytes(pcm), session.input_sample_rate, session.session_id)

        events = DeviceEvents(ws, session, pace_audio, fail_hold_seconds, feed=feed)
        result = await turn_pipeline.run_turn(bytes(pcm), session.input_sample_rate, events, history)
        await events.finish(result)
        session.finish_turn()
        log.info(
            "대화 끝: ok=%s stage=%s stt=%dms llm=%dms tts첫소리=%dms 전체=%dms",
            result.ok, result.failed_stage, result.stt_ms, result.llm_ms, result.tts_first_audio_ms, result.total_ms,
        )
        if writer:
            writer.write(result, device_id)

    async def run_connection(ws: WebSocket, conn_pipeline: Pipeline, owner: str = "",
                             feed: ConversationFeed | None = None) -> None:
        await ws.accept()
        device_id = ws.headers.get("device-id", "")
        session = Session(uuid.uuid4().hex[:12])
        history: list[dict] = []
        log.info("기기 접속: %s%s", device_id, " (%s)" % owner if owner else "")
        try:
            while True:
                frame = await ws.receive()
                if frame["type"] == "websocket.disconnect":
                    break
                if frame.get("bytes") is not None:
                    session.handle_audio(frame["bytes"])
                    continue
                try:
                    message = json.loads(frame.get("text") or "")
                except ValueError:
                    continue
                try:
                    event = session.handle_text(message)
                except ProtocolError as exc:
                    log.warning("규칙에 맞지 않는 메시지: %s", exc)
                    await ws.close(code=1002)
                    break
                if event.kind == "hello":
                    await ws.send_text(json.dumps(event.reply, ensure_ascii=False))
                    if device_volume is not None and session.features.get("mcp"):
                        # 기기 안의 볼륨 설정을 바꾼다 (기기가 저장하므로 한 번 되면 유지됨)
                        call = session.mcp_call(1, "self.audio_speaker.set_volume", {"volume": device_volume})
                        await ws.send_text(json.dumps(call, ensure_ascii=False))
                        log.info("기기 볼륨 %d 요청: %s", device_volume, device_id)
                elif event.kind == "mcp":
                    payload = event.payload if isinstance(event.payload, dict) else {}
                    if "error" in payload:
                        log.warning("기기 MCP 오류 (%s): %s", device_id, json.dumps(payload.get("error"), ensure_ascii=False)[:200])
                    else:
                        log.info("기기 MCP 응답 (%s): %s", device_id, json.dumps(payload.get("result"), ensure_ascii=False)[:200])
                elif event.kind == "listen_start":
                    if feed:
                        feed.state("listening")
                elif event.kind == "turn":
                    await run_turn(ws, session, event.packets, history, device_id, conn_pipeline, feed)
        except WebSocketDisconnect:
            pass
        log.info("기기 접속 종료: %s", device_id)

    return app
