"""기기와 주고받는 메시지 규칙 (xiaozhi 펌웨어 2.0.5).

입출력 없이 규칙만 담는다. 근거: 펌웨어 소스의 docs/websocket.md, main/ota.cc,
main/protocols/websocket_protocol.cc.
"""
from __future__ import annotations

import hmac
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

WEBSOCKET_PATH = "/xiaozhi/v1/"
OTA_PATH = "/xiaozhi/ota/"

OUTPUT_SAMPLE_RATE = 24000
FRAME_MS = 60
MAX_LISTEN_MS = 30_000
EXTENDED_LISTEN_MS = 60_000

# 기기 펌웨어가 아는 감정 이름 21개
FIRMWARE_EMOTIONS = (
    "neutral", "happy", "laughing", "funny", "sad", "angry", "crying", "loving",
    "embarrassed", "surprised", "shocked", "thinking", "winking", "cool", "relaxed",
    "delicious", "kissy", "confident", "sleepy", "silly", "confused",
)
# 기기에 넣은 표정 이름 12개 (code/tools/faces/convert_faces.py의 FACE_KEYS와 같음)
FACE_NAMES = (
    "basic", "blink", "joy", "sleepy", "angry", "surprised", "sad", "confused",
    "wink", "look_around", "love", "dizzy",
)
ALLOWED_EMOTIONS = frozenset(FIRMWARE_EMOTIONS) | frozenset(FACE_NAMES)


class ProtocolError(ValueError):
    """기기가 규칙에 맞지 않는 메시지를 보냄."""


def normalize_emotion(name: str | None) -> str:
    return name if name in ALLOWED_EMOTIONS else "neutral"


def token_matches(header: str | None, expected: str) -> bool:
    """접속 암호 확인. 기기는 `Bearer <암호>` 꼴로 보낸다."""
    if not expected or not header:
        return False
    given = header[7:] if header.startswith("Bearer ") else header
    return hmac.compare_digest(given.encode(), expected.encode())


def device_version_from_info(info: Any) -> str | None:
    if not isinstance(info, dict):
        return None
    application = info.get("application")
    if not isinstance(application, dict):
        return None
    version = application.get("version")
    return version if isinstance(version, str) and version else None


def websocket_url_for_host(host: str, secure: bool = False, path: str = WEBSOCKET_PATH) -> str:
    """음성 연결 주소. 인터넷 공개 주소(https)로 들어왔으면 wss로 알려 준다."""
    return "%s://%s%s" % ("wss" if secure else "ws", host, path)


def device_ota_path(token: str) -> str:
    """등록한 사람마다 다른 접속 안내 주소 (/d/<비밀>/xiaozhi/ota/)."""
    return "/d/%s%s" % (token, OTA_PATH)


def device_websocket_path(token: str) -> str:
    return "/d/%s%s" % (token, WEBSOCKET_PATH)


def device_chat_path(token: str) -> str:
    """소리 없이 글로 묻고 답하는 페이지 (등록한 사람만 아는 주소)."""
    return "/d/%s/chat" % token


def device_feed_path(token: str) -> str:
    """대시보드가 구독하는 대화 이벤트 스트림 (SSE)."""
    return "/d/%s/events" % token


def ota_response(
    device_version: str | None,
    websocket_url: str,
    token: str,
    now_ms: int,
    timezone_offset_min: int = 540,
) -> dict:
    """기기의 첫 요청에 대한 답. 음성 접속 주소와 암호를 알려준다."""
    body: dict[str, Any] = {
        "server_time": {"timestamp": now_ms, "timezone_offset": timezone_offset_min},
        "websocket": {"url": websocket_url, "token": token},
    }
    if device_version:
        # 기기와 같은 버전을 적어 자동 펌웨어 교체를 막는다
        body["firmware"] = {"version": device_version, "url": ""}
    return body


class SessionState(Enum):
    NEW = "new"
    READY = "ready"
    LISTENING = "listening"
    BUSY = "busy"


@dataclass
class Event:
    kind: str  # hello | listen_start | turn | abort | mcp | ignored
    reply: dict | None = None
    packets: list[bytes] = field(default_factory=list)
    payload: Any = None  # mcp: 기기가 보낸 JSON-RPC 응답


class Session:
    """기기 한 대와의 대화 상태."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.state = SessionState.NEW
        self.input_sample_rate = 16000
        self.input_frame_ms = FRAME_MS
        self.max_listen_ms = MAX_LISTEN_MS
        self.dropped_packets = 0
        self.features: dict = {}  # 기기가 hello에 적어 보낸 기능 (예: {"mcp": true})
        self._packets: list[bytes] = []

    # ------------------------------------------------------------ 기기 → 서버

    def handle_text(self, message: dict) -> Event:
        kind = message.get("type") if isinstance(message, dict) else None
        if not isinstance(kind, str):
            raise ProtocolError("type이 없는 메시지")

        if kind == "hello":
            return self._on_hello(message)
        if self.state is SessionState.NEW:
            raise ProtocolError("hello 전에 온 메시지: %s" % kind)

        if kind == "listen":
            return self._on_listen(message)
        if kind == "abort":
            self._packets = []
            self.state = SessionState.READY
            return Event("abort")
        if kind == "mcp":
            return Event("mcp", payload=message.get("payload"))
        return Event("ignored")

    def handle_audio(self, packet: bytes) -> None:
        if self.state is not SessionState.LISTENING:
            self.dropped_packets += 1
            return
        if len(self._packets) >= self.max_listen_ms // self.input_frame_ms:
            self.dropped_packets += 1
            return
        self._packets.append(packet)

    def finish_turn(self) -> None:
        if self.state is SessionState.BUSY:
            self.state = SessionState.READY

    def _on_hello(self, message: dict) -> Event:
        if message.get("transport") != "websocket":
            raise ProtocolError("지원하지 않는 전송 방식: %r" % message.get("transport"))
        params = message.get("audio_params") or {}
        if params.get("format", "opus") != "opus":
            raise ProtocolError("지원하지 않는 소리 형식: %r" % params.get("format"))
        self.input_sample_rate = int(params.get("sample_rate", 16000))
        self.input_frame_ms = int(params.get("frame_duration", FRAME_MS))
        if self.input_frame_ms not in (20, 40, 60):
            raise ProtocolError("지원하지 않는 소리 프레임 길이")
        features = message.get("features")
        self.features = features if isinstance(features, dict) else {}
        # Opt-in keeps the existing 30-second limit for all older firmware.
        extended = self.features.get("listen_60s") is True
        self.max_listen_ms = EXTENDED_LISTEN_MS if extended else MAX_LISTEN_MS
        self.state = SessionState.READY
        return Event(
            "hello",
            reply={
                "type": "hello",
                "transport": "websocket",
                "session_id": self.session_id,
                **({"max_listen_ms": self.max_listen_ms} if extended else {}),
                "audio_params": {
                    "format": "opus",
                    "sample_rate": OUTPUT_SAMPLE_RATE,
                    "channels": 1,
                    "frame_duration": FRAME_MS,
                },
            },
        )

    def _on_listen(self, message: dict) -> Event:
        state = message.get("state")
        if state == "start":
            self._packets = []
            self.state = SessionState.LISTENING
            return Event("listen_start")
        if state == "stop":
            if self.state is not SessionState.LISTENING:
                return Event("ignored")
            packets, self._packets = self._packets, []
            self.state = SessionState.BUSY
            return Event("turn", packets=packets)
        return Event("ignored")

    # ------------------------------------------------------------ 서버 → 기기

    def _message(self, kind: str, **fields: Any) -> dict:
        return {"session_id": self.session_id, "type": kind, **fields}

    def stt(self, text: str) -> dict:
        return self._message("stt", text=text)

    def mcp_call(self, call_id: int, tool: str, arguments: dict) -> dict:
        """기기 안의 MCP 도구를 부른다 (예: self.audio_speaker.set_volume). 기기는 type=mcp로 결과를 돌려준다."""
        return self._message("mcp", payload={
            "jsonrpc": "2.0", "id": call_id, "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        })

    def emotion(self, name: str) -> dict:
        return self._message("llm", emotion=normalize_emotion(name))

    def tts(self, state: str, text: str | None = None) -> dict:
        if text is None:
            return self._message("tts", state=state)
        return self._message("tts", state=state, text=text)
