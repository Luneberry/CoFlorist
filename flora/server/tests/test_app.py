"""서버 본체 시험: 기기처럼 접속해 소리를 보내고 답을 받는다 (가짜 AI 사용)."""
import json
import math
import struct

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from duo_server import opus
from duo_server.app import create_app
from duo_server.pipeline import Pipeline, Reply
from duo_server.providers.fake import FakeLlm, FakeStt, FakeTts, tone

TOKEN = "test-token"
HEADERS = {
    "Authorization": "Bearer " + TOKEN,
    "Protocol-Version": "1",
    "Device-Id": "aa:bb:cc:dd:ee:ff",
    "Client-Id": "11111111-2222-3333-4444-555555555555",
}
HELLO = {
    "type": "hello",
    "version": 1,
    "transport": "websocket",
    "audio_params": {"format": "opus", "sample_rate": 16000, "channels": 1, "frame_duration": 60},
}


def speech_packets(seconds: float = 0.6) -> list[bytes]:
    count = int(16000 * seconds)
    pcm = b"".join(struct.pack("<h", int(9000 * math.sin(2 * math.pi * 300 * i / 16000))) for i in range(count))
    encoder = opus.Encoder(16000)
    return [encoder.encode(frame) for frame in opus.split_frames(pcm, 16000, 60)]


@pytest.fixture
def parts(tmp_path):
    stt = FakeStt("오늘 회의 몇 시야?")
    llm = FakeLlm(Reply("오후 2시예요!", "happy"))
    tts = FakeTts(chunks=[tone(0.3), tone(0.3)])
    metrics = tmp_path / "turns.jsonl"
    app = create_app(
        pipeline=Pipeline(stt=stt, llm=llm, tts=tts),
        device_token=TOKEN,
        metrics_path=metrics,
        pace_audio=False,
    )
    return TestClient(app), stt, llm, tts, metrics


def talk(ws, packets):
    ws.send_text(json.dumps(HELLO))
    hello = json.loads(ws.receive_text())
    ws.send_text(json.dumps({"session_id": hello["session_id"], "type": "listen", "state": "start", "mode": "manual"}))
    for packet in packets:
        ws.send_bytes(packet)
    ws.send_text(json.dumps({"session_id": hello["session_id"], "type": "listen", "state": "stop"}))
    messages, audio = [], []
    while True:
        frame = ws.receive()
        if frame.get("bytes") is not None:
            audio.append(frame["bytes"])
            continue
        message = json.loads(frame["text"])
        messages.append(message)
        if message["type"] == "tts" and message["state"] == "stop":
            return hello, messages, audio


# ---------------------------------------------------------------- 접속 안내

def test_ota_tells_device_where_to_connect(parts):
    client = parts[0]
    response = client.post(
        "/xiaozhi/ota/",
        json={"application": {"version": "2.0.5"}, "board": {"type": "duo-wha103"}},
        headers={"Host": "172.20.10.2:8765", "Device-Id": "aa:bb:cc:dd:ee:ff"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["websocket"] == {"url": "ws://172.20.10.2:8765/xiaozhi/v1/", "token": TOKEN}
    assert body["firmware"] == {"version": "2.0.5", "url": ""}
    assert "activation" not in body


def test_ota_also_answers_get_and_missing_body(parts):
    client = parts[0]
    response = client.get("/xiaozhi/ota/", headers={"Host": "10.0.0.5:8765"})
    assert response.status_code == 200
    assert response.json()["websocket"]["url"] == "ws://10.0.0.5:8765/xiaozhi/v1/"
    assert "firmware" not in response.json()


def test_health_lists_selected_providers(parts):
    body = parts[0].get("/health").json()
    assert body == {"ok": True, "stt": "fake-stt", "llm": "fake-llm", "tts": "fake-tts", "registrations": 0}


# ---------------------------------------------------------------- 접속 암호

def test_websocket_without_token_is_refused(parts):
    client = parts[0]
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/xiaozhi/v1/") as ws:
            ws.receive_text()


def test_websocket_with_wrong_token_is_refused(parts):
    client = parts[0]
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/xiaozhi/v1/", headers={**HEADERS, "Authorization": "Bearer nope"}) as ws:
            ws.receive_text()


# ---------------------------------------------------------------- 대화 한 번

def test_full_turn_message_order(parts):
    client = parts[0]
    with client.websocket_connect("/xiaozhi/v1/", headers=HEADERS) as ws:
        hello, messages, audio = talk(ws, speech_packets())

    assert hello["type"] == "hello" and hello["transport"] == "websocket"
    assert hello["audio_params"]["sample_rate"] == 24000
    kinds = [(m["type"], m.get("state") or m.get("emotion") or m.get("text")) for m in messages]
    assert kinds == [
        ("stt", "오늘 회의 몇 시야?"),
        ("llm", "thinking"),
        ("llm", "happy"),
        ("tts", "start"),
        ("tts", "sentence_start"),
        ("tts", "stop"),
    ]
    assert messages[4]["text"] == "오후 2시예요!"
    assert all(m["session_id"] == hello["session_id"] for m in messages)


def test_reply_audio_is_opus_at_24k_with_right_length(parts):
    client = parts[0]
    with client.websocket_connect("/xiaozhi/v1/", headers=HEADERS) as ws:
        _, _, audio = talk(ws, speech_packets())
    assert len(audio) == 10  # 0.6초 = 60ms 조각 10개
    decoder = opus.Decoder(24000)
    pcm = b"".join(decoder.decode(packet, 60) for packet in audio)
    assert len(pcm) == 24000 * 2 * 6 // 10


def test_stt_receives_decoded_speech(parts):
    client, stt = parts[0], parts[1]
    seen = {}

    async def spy(pcm, sample_rate):
        seen["bytes"] = len(pcm)
        seen["rate"] = sample_rate
        return "네"

    stt.transcribe = spy
    with client.websocket_connect("/xiaozhi/v1/", headers=HEADERS) as ws:
        talk(ws, speech_packets(0.6))
    assert seen == {"bytes": 16000 * 2 * 6 // 10, "rate": 16000}


def test_two_turns_on_one_connection(parts):
    client, _, llm = parts[0], parts[1], parts[2]
    with client.websocket_connect("/xiaozhi/v1/", headers=HEADERS) as ws:
        hello, _, _ = talk(ws, speech_packets())
        ws.send_text(json.dumps({"type": "listen", "state": "start", "mode": "manual"}))
        for packet in speech_packets():
            ws.send_bytes(packet)
        ws.send_text(json.dumps({"type": "listen", "state": "stop"}))
        kinds = []
        while True:
            frame = ws.receive()
            if frame.get("text"):
                message = json.loads(frame["text"])
                kinds.append(message["type"])
                if message["type"] == "tts" and message["state"] == "stop":
                    break
    assert kinds[0] == "stt"
    assert llm.calls == 2
    assert len(llm.seen_history[1]) == 2  # 앞 대화를 기억함


# ---------------------------------------------------------------- 실패

def test_failure_sends_confused_face_and_closes_the_turn(tmp_path):
    app = create_app(
        pipeline=Pipeline(stt=FakeStt(error=RuntimeError("down")), llm=FakeLlm(), tts=FakeTts()),
        device_token=TOKEN,
        metrics_path=tmp_path / "turns.jsonl",
        pace_audio=False,
    )
    with TestClient(app).websocket_connect("/xiaozhi/v1/", headers=HEADERS) as ws:
        _, messages, audio = talk(ws, speech_packets())
    assert audio == []
    assert [(m["type"], m.get("state") or m.get("emotion")) for m in messages] == [
        ("llm", "confused"),
        ("tts", "start"),
        ("tts", "stop"),
    ]


def test_broken_audio_packet_is_skipped(parts):
    client = parts[0]
    packets = speech_packets()
    packets.insert(3, b"\xff\xff\xff")
    with client.websocket_connect("/xiaozhi/v1/", headers=HEADERS) as ws:
        _, messages, _ = talk(ws, packets)
    assert messages[0]["type"] == "stt"


def test_listen_before_hello_closes_connection(parts):
    client = parts[0]
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/xiaozhi/v1/", headers=HEADERS) as ws:
            ws.send_text(json.dumps({"type": "listen", "state": "start"}))
            ws.receive_text()


# ---------------------------------------------------------------- 받은 소리 저장 (마이크 확인용)

def test_received_audio_is_saved_only_when_asked(tmp_path):
    import wave

    folder = tmp_path / "audio"
    app = create_app(
        pipeline=Pipeline(stt=FakeStt("네"), llm=FakeLlm(), tts=FakeTts()),
        device_token=TOKEN,
        save_audio_dir=folder,
        pace_audio=False,
    )
    with TestClient(app).websocket_connect("/xiaozhi/v1/", headers=HEADERS) as ws:
        talk(ws, speech_packets(0.6))
    (saved,) = list(folder.glob("*-in.wav"))
    with wave.open(str(saved), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16000)
        assert w.getnframes() == 9600


def test_audio_is_not_saved_by_default(parts, tmp_path):
    client = parts[0]
    with client.websocket_connect("/xiaozhi/v1/", headers=HEADERS) as ws:
        talk(ws, speech_packets())
    assert list(tmp_path.rglob("*.wav")) == []


# ---------------------------------------------------------------- 기록

def test_each_turn_is_logged_as_one_line(parts):
    client, metrics = parts[0], parts[4]
    with client.websocket_connect("/xiaozhi/v1/", headers=HEADERS) as ws:
        talk(ws, speech_packets())
    lines = metrics.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["ok"] is True
    assert row["stt_name"] == "fake-stt" and row["llm_name"] == "fake-llm" and row["tts_name"] == "fake-tts"
    assert row["transcript"] == "오늘 회의 몇 시야?"
    assert row["input_ms"] == 600
    assert row["audio_ms"] == 600
    assert row["device_id"] == "aa:bb:cc:dd:ee:ff"
    assert "time" in row
    for key in ("stt_ms", "llm_ms", "tts_first_audio_ms", "total_ms"):
        assert isinstance(row[key], int)


# ---------------------------------------------------------------- 기기 등록 (인터넷 공개용)

from duo_server.registry import Registry  # noqa: E402

INVITE = "duo-2026"


@pytest.fixture
def reg_parts(tmp_path):
    registry = Registry(tmp_path / "data" / "reg.json")
    made = []

    def factory(registration):
        made.append(registration)
        return Pipeline(
            stt=FakeStt("안녕 듀오"),
            llm=FakeLlm(Reply("반가워요, %s님!" % registration.name, "happy")),
            tts=FakeTts(chunks=[tone(0.3)]),
        )

    app = create_app(
        registry=registry,
        invite_code=INVITE,
        pipeline_factory=factory,
        legacy_routes=False,
        metrics_path=tmp_path / "turns.jsonl",
        pace_audio=False,
    )
    return TestClient(app), registry, made


def register(client, **fields):
    data = {"name": "민수", "invite": INVITE, "upstage_key": "SECRET-up", "gemini_key": ""}
    data.update(fields)
    return client.post("/register", data=data)


def test_legacy_routes_are_off_in_registry_mode(reg_parts):
    client, _, _ = reg_parts
    assert client.get("/xiaozhi/ota/").status_code == 404
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/xiaozhi/v1/", headers=HEADERS):
            pass


def test_register_form_is_served(reg_parts):
    client, _, _ = reg_parts
    page = client.get("/register")
    assert page.status_code == 200
    assert "업스테이지" in page.text and 'name="invite"' in page.text


def test_register_is_off_without_invite_code(tmp_path):
    app = create_app(registry=Registry(tmp_path / "reg.json"), invite_code="", legacy_routes=False)
    client = TestClient(app)
    assert client.get("/register").status_code == 404
    assert client.post("/register", data={"invite": "", "upstage_key": "SECRET-up"}).status_code == 404


def test_register_rejects_wrong_invite(reg_parts):
    client, registry, _ = reg_parts
    assert register(client, invite="wrong").status_code == 403
    assert registry.all() == []


def test_register_requires_upstage_key(reg_parts):
    client, registry, _ = reg_parts
    assert register(client, upstage_key="  ").status_code == 400
    assert registry.all() == []


def test_register_without_gemini_key_is_allowed_but_voice_is_warned(tmp_path):
    """글 대화만 하려는 사람은 제미나이 키 없이 등록한다. 음성 기기를 붙이려면 필요하다고 알린다."""
    app = create_app(
        registry=Registry(tmp_path / "reg.json"), invite_code=INVITE, legacy_routes=False, gemini_key_missing=True,
        pipeline_factory=lambda reg: Pipeline(stt=FakeStt(), llm=FakeLlm(), tts=FakeTts()),
    )
    client = TestClient(app)
    assert "음성 기기를 붙이려면" in client.get("/register").text
    page = register(client)
    assert page.status_code == 200 and "음성은 아직" in page.text
    page = register(client, gemini_key="SECRET-gem")
    assert page.status_code == 200 and "음성은 아직" not in page.text


def test_register_returns_device_url_and_stores_keys(reg_parts):
    client, registry, _ = reg_parts
    page = register(client, gemini_key="SECRET-gem")
    assert page.status_code == 200
    (reg,) = registry.all()
    assert reg.name == "민수" and reg.upstage_key == "SECRET-up" and reg.gemini_key == "SECRET-gem"
    assert "http://testserver/d/%s/xiaozhi/ota/" % reg.token in page.text
    assert reg.access_token not in page.text  # 기기 암호는 접속 안내 때만 준다
    assert "SECRET-up" not in page.text


def test_register_url_uses_forwarded_host_and_https(reg_parts):
    client, registry, _ = reg_parts
    page = client.post(
        "/register",
        data={"name": "민수", "invite": INVITE, "upstage_key": "SECRET-up", "gemini_key": ""},
        headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "duo.example.ts.net"},
    )
    (reg,) = registry.all()
    assert "https://duo.example.ts.net/d/%s/xiaozhi/ota/" % reg.token in page.text


def test_token_ota_gives_private_websocket_url(reg_parts):
    client, registry, _ = reg_parts
    reg = registry.create("민수", "SECRET-up")
    body = client.post("/d/%s/xiaozhi/ota/" % reg.token, json={"application": {"version": "2.0.5"}},
                       headers={"Device-Id": "aa:bb:cc:dd:ee:01"}).json()
    assert body["websocket"]["url"] == "ws://testserver/d/%s/xiaozhi/v1/" % reg.token
    assert body["websocket"]["token"] == reg.access_token
    assert body["firmware"]["version"] == "2.0.5"
    assert reg.devices == ["aa:bb:cc:dd:ee:01"]

    secure = client.get("/d/%s/xiaozhi/ota" % reg.token, headers={"X-Forwarded-Proto": "https"}).json()
    assert secure["websocket"]["url"] == "wss://testserver/d/%s/xiaozhi/v1/" % reg.token


def test_unknown_token_is_404(reg_parts):
    client, _, _ = reg_parts
    assert client.get("/d/nope/xiaozhi/ota/").status_code == 404
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/d/nope/xiaozhi/v1/", headers=HEADERS):
            pass


def test_token_websocket_turn_uses_registrants_pipeline_once(reg_parts):
    client, registry, made = reg_parts
    reg = registry.create("민수", "SECRET-up")
    headers = {**HEADERS, "Authorization": "Bearer " + reg.access_token}
    for _ in range(2):
        with client.websocket_connect("/d/%s/xiaozhi/v1/" % reg.token, headers=headers) as ws:
            _, messages, audio = talk(ws, speech_packets())
    texts = [m["text"] for m in messages if m["type"] == "tts" and m["state"] == "sentence_start"]
    assert texts == ["반가워요, 민수님!"]
    assert audio
    assert [r.token for r in made] == [reg.token]  # 두 번 접속해도 진행기는 한 번만 만든다


def test_token_websocket_with_wrong_access_token_is_refused(reg_parts):
    client, registry, made = reg_parts
    reg = registry.create("민수", "SECRET-up")
    headers = {**HEADERS, "Authorization": "Bearer wrong"}
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/d/%s/xiaozhi/v1/" % reg.token, headers=headers):
            pass
    assert made == []


def test_health_counts_registrations(reg_parts):
    client, registry, _ = reg_parts
    registry.create("a", "SECRET-1")
    body = client.get("/health").json()
    assert body["ok"] is True and body["registrations"] == 1


def test_register_without_upstage_key_when_server_has_one(tmp_path):
    app = create_app(
        registry=Registry(tmp_path / "reg.json"), invite_code=INVITE, legacy_routes=False,
        upstage_key_required=False, pipeline_factory=lambda reg: Pipeline(stt=FakeStt(), llm=FakeLlm(), tts=FakeTts()),
    )
    client = TestClient(app)
    page = client.get("/register")
    assert "필수" not in page.text.split("업스테이지")[1].split("</label>")[0]
    assert register(client, upstage_key="").status_code == 200


def test_register_asks_openai_key_when_server_has_none(tmp_path):
    registry = Registry(tmp_path / "reg.json")
    app = create_app(
        registry=registry, invite_code=INVITE, legacy_routes=False, openai_key_required=True, upstage_key_required=False,
        pipeline_factory=lambda reg: Pipeline(stt=FakeStt(), llm=FakeLlm(), tts=FakeTts()),
    )
    client = TestClient(app)
    form = client.get("/register").text
    assert 'name="openai_key"' in form and "OpenAI" in form
    assert register(client, upstage_key="").status_code == 400
    page = register(client, upstage_key="", openai_key="SECRET-oa")
    assert page.status_code == 200
    (reg,) = registry.all()
    assert reg.openai_key == "SECRET-oa" and "SECRET-oa" not in page.text


def test_openai_key_is_optional_by_default(reg_parts):
    client, registry, _ = reg_parts
    assert 'name="openai_key"' in client.get("/register").text
    assert register(client).status_code == 200
    assert registry.all()[0].openai_key == ""


# ---------------------------------------------------------------- 글로 묻고 답하기 (소리 없이)

def chat_parts(tmp_path, llm=None):
    stt, tts = FakeStt("들리면 안 되는 말"), FakeTts()
    llm = llm or FakeLlm(Reply("반가워요!", "happy"))
    registry = Registry(tmp_path / "reg.json")
    app = create_app(
        registry=registry, invite_code=INVITE, legacy_routes=False, metrics_path=tmp_path / "turns.jsonl",
        pipeline_factory=lambda reg: Pipeline(stt=stt, llm=llm, tts=tts),
    )
    return TestClient(app), registry, stt, llm, tts, tmp_path / "turns.jsonl"


def test_chat_page_is_served_for_known_token_only(tmp_path):
    client, registry, *_ = chat_parts(tmp_path)
    reg = registry.create("민수", "")
    page = client.get("/d/%s/chat" % reg.token)
    assert page.status_code == 200
    assert "민수" in page.text and 'id="question"' in page.text and "글로 묻고" in page.text
    assert client.get("/d/nope/chat").status_code == 404
    assert client.post("/d/nope/chat", json={"text": "안녕"}).status_code == 404


def test_chat_answers_by_text_without_touching_stt_or_tts(tmp_path):
    client, registry, stt, llm, tts, turns = chat_parts(tmp_path)
    reg = registry.create("민수", "")
    body = client.post("/d/%s/chat" % reg.token, json={"text": "안녕?"}).json()
    assert body["ok"] is True and body["reply"] == "반가워요!" and body["emotion"] == "happy"
    assert isinstance(body["llm_ms"], int)
    assert llm.calls == 1 and stt.calls == 0 and tts.calls == 0
    (row,) = [json.loads(line) for line in turns.read_text().splitlines()]
    assert row["device_id"] == "chat:민수" and row["reply_text"] == "반가워요!" and row["transcript"] == "안녕?"


def test_chat_returns_flower_recommendation_for_the_robot(tmp_path):
    llm = FakeLlm(Reply("흰 백합이 좋겠어요.", "neutral", "백합", "white"))
    client, registry, _, _, _, turns = chat_parts(tmp_path, llm=llm)
    reg = registry.create("민수", "")
    body = client.post("/d/%s/chat" % reg.token, json={"text": "위로가 필요해"}).json()
    assert body["ok"] is True and body["flower"] == "백합" and body["flower_color"] == "white"
    (row,) = [json.loads(line) for line in turns.read_text().splitlines()]
    assert row["flower"] == "백합" and row["flower_color"] == "white"


def read_feed(client, path, origin=None):
    headers = {"Origin": origin} if origin else {}
    response = client.get(path + "?once=1", headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    return response, [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def test_chat_turn_is_published_to_the_dashboard_feed(tmp_path):
    llm = FakeLlm(Reply("흰 백합이 좋겠어요.", "sad", "백합", "white"))
    client, registry, *_ = chat_parts(tmp_path, llm=llm)
    reg = registry.create("민수", "")
    assert client.post("/d/%s/chat" % reg.token, json={"text": "위로가 필요해"}).status_code == 200
    _, events = read_feed(client, "/d/%s/events" % reg.token)
    assert [(e["type"], e.get("state") or e.get("role")) for e in events] == [
        ("message", "user"), ("state", "thinking"), ("message", "assistant"), ("state", "idle"),
    ]
    assert events[0]["text"] == "위로가 필요해" and "flower" not in events[0]
    assistant = events[2]
    assert assistant["text"] == "흰 백합이 좋겠어요." and assistant["flower"] == "백합"
    assert assistant["flower_color"] == "white" and assistant["emotion"] == "sad" and assistant["stimulus_hz"] == 60
    assert len({e["id"] for e in events}) == 4
    assert client.get("/d/nope/events?once=1").status_code == 404


def test_feed_allows_only_the_dashboard_origin(tmp_path):
    client, registry, *_ = chat_parts(tmp_path)
    reg = registry.create("민수", "")
    response, _ = read_feed(client, "/d/%s/events" % reg.token, origin="http://localhost:4317")
    assert response.headers["access-control-allow-origin"] == "http://localhost:4317"
    assert client.get("/d/%s/events?once=1" % reg.token, headers={"Origin": "http://evil.example"}).status_code == 403


def test_failed_chat_turn_marks_the_feed_as_error(tmp_path):
    client, registry, *_ = chat_parts(tmp_path, llm=FakeLlm(error=RuntimeError("down")))
    reg = registry.create("민수", "")
    assert client.post("/d/%s/chat" % reg.token, json={"text": "안녕"}).status_code == 502
    _, events = read_feed(client, "/d/%s/events" % reg.token)
    assert [(e["type"], e.get("state") or e.get("role")) for e in events] == [
        ("message", "user"), ("state", "thinking"), ("state", "error"),
    ]


def test_chat_keeps_colours_from_the_configured_palette(tmp_path):
    llm = FakeLlm(Reply("분홍 장미 어때요?", "loving", "장미", "pink"))
    stt, tts = FakeStt("x"), FakeTts()
    registry = Registry(tmp_path / "reg.json")
    app = create_app(registry=registry, invite_code=INVITE, legacy_routes=False, metrics_path=tmp_path / "t.jsonl",
                     pipeline_factory=lambda reg: Pipeline(stt=stt, llm=llm, tts=tts), flower_colors=("white", "pink"))
    reg = registry.create("민수", "")
    body = TestClient(app).post("/d/%s/chat" % reg.token, json={"text": "고백할 꽃"}).json()
    assert body["flower_color"] == "pink"


def test_chat_remembers_previous_turns_per_registration(tmp_path):
    client, registry, _, llm, _, _ = chat_parts(tmp_path)
    reg = registry.create("민수", "")
    other = registry.create("영희", "")
    client.post("/d/%s/chat" % reg.token, json={"text": "첫 질문"})
    client.post("/d/%s/chat" % reg.token, json={"text": "둘째 질문"})
    client.post("/d/%s/chat" % other.token, json={"text": "다른 사람"})
    assert llm.seen_history[0] == []
    assert llm.seen_history[1] == [{"role": "user", "content": "첫 질문"}, {"role": "assistant", "content": "반가워요!"}]
    assert llm.seen_history[2] == []


def test_chat_reports_answer_failure_plainly(tmp_path):
    client, registry, *_ = chat_parts(tmp_path, llm=FakeLlm(error=RuntimeError("답변 서비스 오류 401: bad key")))
    reg = registry.create("민수", "")
    response = client.post("/d/%s/chat" % reg.token, json={"text": "안녕?"})
    assert response.status_code == 502
    assert response.json()["ok"] is False and "401" in response.json()["error"]


def test_chat_rejects_empty_question(tmp_path):
    client, registry, _, llm, _, _ = chat_parts(tmp_path)
    reg = registry.create("민수", "")
    assert client.post("/d/%s/chat" % reg.token, json={"text": "   "}).status_code == 400
    assert client.post("/d/%s/chat" % reg.token, content=b"not json").status_code == 400
    assert llm.calls == 0


def test_register_done_page_links_the_chat_page(reg_parts):
    client, registry, _ = reg_parts
    page = register(client)
    (reg,) = registry.all()
    assert "http://testserver/d/%s/chat" % reg.token in page.text


# ---------------------------------------------------------------- 등록 화면 키 칸 끄기 (모두 서버 키 사용)

def test_register_can_hide_key_fields_and_use_server_keys(tmp_path):
    """키 칸을 끄면 이름과 초대 코드만 받고, 넘어온 키 값은 저장하지 않는다 (모두 서버 .env의 키로 처리)."""
    registry = Registry(tmp_path / "reg.json")
    app = create_app(
        registry=registry, invite_code=INVITE, legacy_routes=False, ask_keys=False, upstage_key_required=False,
        pipeline_factory=lambda reg: Pipeline(stt=FakeStt(), llm=FakeLlm(), tts=FakeTts()),
    )
    client = TestClient(app)
    form = client.get("/register").text
    assert 'name="name"' in form and 'name="invite"' in form
    for field in ("upstage_key", "openai_key", "gemini_key"):
        assert 'name="%s"' % field not in form
    assert "API 키" not in form

    page = client.post("/register", data={"name": "민수", "invite": INVITE})
    assert page.status_code == 200 and "음성은 아직" not in page.text
    (reg,) = registry.all()
    assert reg.name == "민수" and (reg.upstage_key, reg.gemini_key, reg.openai_key) == ("", "", "")
    assert "http://testserver/d/%s/xiaozhi/ota/" % reg.token in page.text

    page = client.post("/register", data={
        "name": "영희", "invite": INVITE, "upstage_key": "SECRET-up", "gemini_key": "SECRET-gem", "openai_key": "SECRET-oa",
    })
    assert page.status_code == 200
    second = registry.all()[1]
    assert (second.upstage_key, second.gemini_key, second.openai_key) == ("", "", "")


def test_hiding_key_fields_needs_server_answer_key(tmp_path):
    with pytest.raises(ValueError):
        create_app(
            registry=Registry(tmp_path / "reg.json"), invite_code=INVITE, legacy_routes=False, ask_keys=False,
            upstage_key_required=True, pipeline_factory=lambda reg: Pipeline(stt=FakeStt(), llm=FakeLlm(), tts=FakeTts()),
        )


# ---------------------------------------------------------------- 접속 직후 기기 볼륨 맞추기

def volume_app(tmp_path, device_volume):
    return TestClient(create_app(
        pipeline=Pipeline(stt=FakeStt("안녕"), llm=FakeLlm(Reply("네", "happy")), tts=FakeTts(chunks=[tone(0.3)])),
        device_token=TOKEN, metrics_path=tmp_path / "turns.jsonl", pace_audio=False, device_volume=device_volume,
    ))


def test_server_sets_device_volume_right_after_hello(tmp_path):
    client = volume_app(tmp_path, 50)
    with client.websocket_connect("/xiaozhi/v1/", headers=HEADERS) as ws:
        ws.send_text(json.dumps({**HELLO, "features": {"mcp": True}}))
        hello = json.loads(ws.receive_text())
        mcp = json.loads(ws.receive_text())
    assert hello["type"] == "hello"
    assert mcp["type"] == "mcp" and mcp["session_id"] == hello["session_id"]
    assert mcp["payload"]["method"] == "tools/call"
    assert mcp["payload"]["params"] == {"name": "self.audio_speaker.set_volume", "arguments": {"volume": 50}}


def test_no_volume_message_without_setting_or_without_mcp_feature(tmp_path):
    client = volume_app(tmp_path, None)
    with client.websocket_connect("/xiaozhi/v1/", headers=HEADERS) as ws:
        ws.send_text(json.dumps({**HELLO, "features": {"mcp": True}}))
        json.loads(ws.receive_text())
        ws.send_text(json.dumps({"type": "listen", "state": "start", "mode": "manual"}))
        for packet in speech_packets():
            ws.send_bytes(packet)
        ws.send_text(json.dumps({"type": "listen", "state": "stop"}))
        first = json.loads(ws.receive_text())
    assert first["type"] != "mcp"

    client = volume_app(tmp_path, 50)
    with client.websocket_connect("/xiaozhi/v1/", headers=HEADERS) as ws:
        ws.send_text(json.dumps(HELLO))  # 옛 펌웨어: features 없음 → 보내지 않는다
        json.loads(ws.receive_text())
        ws.send_text(json.dumps({"type": "listen", "state": "start", "mode": "manual"}))
        for packet in speech_packets():
            ws.send_bytes(packet)
        ws.send_text(json.dumps({"type": "listen", "state": "stop"}))
        first = json.loads(ws.receive_text())
    assert first["type"] != "mcp"
