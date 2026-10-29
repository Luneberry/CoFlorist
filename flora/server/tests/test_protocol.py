"""기기와 주고받는 메시지 규칙 시험 (xiaozhi 2.0.5 기준)."""
import pytest

from duo_server import protocol
from duo_server.protocol import ProtocolError, Session, SessionState

HELLO = {
    "type": "hello",
    "version": 1,
    "features": {"mcp": True},
    "transport": "websocket",
    "audio_params": {"format": "opus", "sample_rate": 16000, "channels": 1, "frame_duration": 60},
}


def ready_session() -> Session:
    session = Session("s1")
    session.handle_text(HELLO)
    return session


# ---------------------------------------------------------------- 접속 안내

def test_ota_response_points_device_to_websocket():
    body = protocol.ota_response(
        device_version="2.0.5", websocket_url="ws://172.20.10.2:8765/xiaozhi/v1/", token="abc", now_ms=1000
    )
    assert body["websocket"] == {"url": "ws://172.20.10.2:8765/xiaozhi/v1/", "token": "abc"}
    assert body["server_time"]["timestamp"] == 1000


def test_ota_response_never_offers_newer_firmware():
    # 기기보다 높은 버전을 알려주면 기기가 펌웨어를 받아 가려 한다
    body = protocol.ota_response(device_version="2.0.5", websocket_url="ws://h/", token="t", now_ms=0)
    assert body["firmware"]["version"] == "2.0.5"
    assert body["firmware"]["url"] == ""
    assert "force" not in body["firmware"]


def test_ota_response_without_known_version_omits_firmware():
    body = protocol.ota_response(device_version=None, websocket_url="ws://h/", token="t", now_ms=0)
    assert "firmware" not in body


def test_ota_response_has_no_activation_or_mqtt():
    body = protocol.ota_response(device_version="2.0.5", websocket_url="ws://h/", token="t", now_ms=0)
    assert "activation" not in body
    assert "mqtt" not in body


def test_device_version_is_read_from_system_info():
    assert protocol.device_version_from_info({"application": {"version": "2.0.5"}}) == "2.0.5"
    assert protocol.device_version_from_info({}) is None
    assert protocol.device_version_from_info({"application": {"version": 7}}) is None
    assert protocol.device_version_from_info(None) is None


def test_websocket_url_uses_the_address_the_device_used():
    assert protocol.websocket_url_for_host("172.20.10.2:8765") == "ws://172.20.10.2:8765/xiaozhi/v1/"


# ---------------------------------------------------------------- 접속 암호

def test_token_check_accepts_bearer_form():
    assert protocol.token_matches("Bearer secret", "secret")
    assert protocol.token_matches("secret", "secret")


def test_token_check_rejects_wrong_or_missing():
    assert not protocol.token_matches("Bearer nope", "secret")
    assert not protocol.token_matches(None, "secret")
    assert not protocol.token_matches("", "secret")


def test_empty_expected_token_is_never_accepted():
    # 서버에 암호를 안 정해 둔 상태로 열리는 일을 막는다
    assert not protocol.token_matches("Bearer ", "")
    assert not protocol.token_matches(None, "")


# ---------------------------------------------------------------- 인사

def test_hello_reply_matches_firmware_expectation():
    session = Session("s1")
    event = session.handle_text(HELLO)
    assert event.kind == "hello"
    assert event.reply["type"] == "hello"
    assert event.reply["transport"] == "websocket"
    assert event.reply["session_id"] == "s1"
    assert event.reply["audio_params"] == {
        "format": "opus", "sample_rate": 24000, "channels": 1, "frame_duration": 60,
    }
    assert session.state is SessionState.READY
    assert session.input_sample_rate == 16000
    assert session.input_frame_ms == 60


def test_hello_with_other_transport_is_rejected():
    session = Session("s1")
    with pytest.raises(ProtocolError):
        session.handle_text({**HELLO, "transport": "udp"})


def test_hello_with_non_opus_audio_is_rejected():
    session = Session("s1")
    bad = {**HELLO, "audio_params": {**HELLO["audio_params"], "format": "pcm"}}
    with pytest.raises(ProtocolError):
        session.handle_text(bad)


def test_message_without_type_is_rejected():
    with pytest.raises(ProtocolError):
        Session("s1").handle_text({"state": "start"})


# ---------------------------------------------------------------- 듣기

def test_listen_before_hello_is_rejected():
    with pytest.raises(ProtocolError):
        Session("s1").handle_text({"type": "listen", "state": "start", "mode": "manual"})


def test_audio_is_kept_only_while_listening():
    session = ready_session()
    session.handle_audio(b"early")  # 듣기 전에 온 소리는 버린다
    session.handle_text({"type": "listen", "state": "start", "mode": "manual"})
    assert session.state is SessionState.LISTENING
    session.handle_audio(b"one")
    session.handle_audio(b"two")
    event = session.handle_text({"type": "listen", "state": "stop"})
    assert event.kind == "turn"
    assert event.packets == [b"one", b"two"]
    assert session.state is SessionState.BUSY
    session.handle_audio(b"late")  # 처리 중에 온 소리도 버린다
    assert session.dropped_packets == 2


def test_new_listen_clears_previous_audio():
    session = ready_session()
    session.handle_text({"type": "listen", "state": "start", "mode": "manual"})
    session.handle_audio(b"old")
    session.handle_text({"type": "listen", "state": "start", "mode": "manual"})
    session.handle_audio(b"new")
    event = session.handle_text({"type": "listen", "state": "stop"})
    assert event.packets == [b"new"]


def test_stop_without_start_is_ignored():
    session = ready_session()
    event = session.handle_text({"type": "listen", "state": "stop"})
    assert event.kind == "ignored"
    assert session.state is SessionState.READY


def test_audio_longer_than_limit_is_cut():
    session = ready_session()
    session.handle_text({"type": "listen", "state": "start", "mode": "manual"})
    limit = protocol.MAX_LISTEN_MS // 60
    for _ in range(limit + 5):
        session.handle_audio(b"x")
    event = session.handle_text({"type": "listen", "state": "stop"})
    assert len(event.packets) == limit


def test_finish_turn_returns_to_ready():
    session = ready_session()
    session.handle_text({"type": "listen", "state": "start", "mode": "manual"})
    session.handle_text({"type": "listen", "state": "stop"})
    session.finish_turn()
    assert session.state is SessionState.READY


def test_abort_returns_to_ready():
    session = ready_session()
    session.handle_text({"type": "listen", "state": "start", "mode": "manual"})
    event = session.handle_text({"type": "abort", "reason": "x"})
    assert event.kind == "abort"
    assert session.state is SessionState.READY


def test_unknown_messages_are_ignored():
    session = ready_session()
    assert session.handle_text({"type": "iot", "payload": {}}).kind == "ignored"  # mcp는 이제 따로 처리한다
    assert session.handle_text({"type": "listen", "state": "detect", "text": "hi"}).kind == "ignored"


# ---------------------------------------------------------------- 서버가 보내는 메시지

def test_server_messages_carry_session_id():
    session = ready_session()
    assert session.stt("안녕") == {"session_id": "s1", "type": "stt", "text": "안녕"}
    assert session.emotion("happy") == {"session_id": "s1", "type": "llm", "emotion": "happy"}
    assert session.tts("start") == {"session_id": "s1", "type": "tts", "state": "start"}
    assert session.tts("stop") == {"session_id": "s1", "type": "tts", "state": "stop"}
    assert session.tts("sentence_start", "네!") == {
        "session_id": "s1", "type": "tts", "state": "sentence_start", "text": "네!",
    }


def test_unknown_emotion_becomes_neutral():
    assert protocol.normalize_emotion("happy") == "happy"
    assert protocol.normalize_emotion("look_around") == "look_around"  # 표정 이름도 그대로 받음
    assert protocol.normalize_emotion("ecstatic") == "neutral"
    assert protocol.normalize_emotion("") == "neutral"
    assert protocol.normalize_emotion(None) == "neutral"
    assert protocol.normalize_emotion("gear") == "neutral"  # 알림 아이콘 이름은 감정이 아님


# ---------------------------------------------------------------- 기기 볼륨 (MCP 도구 호출)

def test_hello_records_device_features():
    session = Session("s1")
    session.handle_text(HELLO)  # 이 파일의 HELLO에는 features: {"mcp": true}가 들어 있다
    assert session.features == {"mcp": True}
    plain = Session("s2")
    plain.handle_text({k: v for k, v in HELLO.items() if k != "features"})  # 옛 펌웨어
    assert plain.features == {}


def test_mcp_tool_call_message_shape():
    session = Session("s1")
    session.handle_text(HELLO)
    message = session.mcp_call(7, "self.audio_speaker.set_volume", {"volume": 50})
    assert message["session_id"] == "s1" and message["type"] == "mcp"
    assert message["payload"] == {
        "jsonrpc": "2.0", "id": 7, "method": "tools/call",
        "params": {"name": "self.audio_speaker.set_volume", "arguments": {"volume": 50}},
    }


def test_mcp_reply_from_device_is_reported_not_ignored():
    session = Session("s1")
    session.handle_text(HELLO)
    event = session.handle_text({"session_id": "s1", "type": "mcp", "payload": {"jsonrpc": "2.0", "id": 7, "result": True}})
    assert event.kind == "mcp" and event.payload == {"jsonrpc": "2.0", "id": 7, "result": True}
