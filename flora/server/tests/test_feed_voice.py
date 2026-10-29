"""기기 음성 턴도 대시보드 피드에 같은 순서로 올라간다 (옛 공용 입구 /events)."""
import json

from fastapi.testclient import TestClient

from duo_server import opus
from duo_server.app import create_app
from duo_server.pipeline import Pipeline, Reply
from duo_server.providers.fake import FakeLlm, FakeStt, FakeTts, tone

HELLO = {
    "type": "hello", "transport": "websocket", "features": {},
    "audio_params": {"format": "opus", "sample_rate": 16000, "channels": 1, "frame_duration": 60},
}


def test_voice_turn_publishes_listening_user_thinking_assistant_speaking_idle(tmp_path):
    llm = FakeLlm(Reply("노란 튤립을 추천해요.", "happy", "튤립", "yellow"))
    app = create_app(Pipeline(FakeStt("축하해 줘"), llm, FakeTts(chunks=[tone(0.12)])), "test-token",
                     metrics_path=tmp_path / "turns.jsonl", pace_audio=False)
    client = TestClient(app)
    packet = opus.Encoder(16000).encode(bytes(960 * 2))
    with client.websocket_connect("/xiaozhi/v1/", headers={"Authorization": "Bearer test-token"}) as ws:
        ws.send_json(HELLO)
        ws.receive_json()
        ws.send_json({"type": "listen", "state": "start", "mode": "manual"})
        for _ in range(20):
            ws.send_bytes(packet)
        ws.send_json({"type": "listen", "state": "stop"})
        while True:
            frame = ws.receive()
            if frame.get("bytes") is not None:
                continue
            message = json.loads(frame["text"])
            if message.get("type") == "tts" and message.get("state") == "stop":
                break
    response = client.get("/events?once=1")
    assert response.status_code == 200
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert [(e["type"], e.get("state") or e.get("role")) for e in events] == [
        ("state", "listening"), ("message", "user"), ("state", "thinking"),
        ("message", "assistant"), ("state", "speaking"), ("state", "idle"),
    ]
    assert events[1]["text"] == "축하해 줘"
    assert events[3]["flower_color"] == "yellow" and events[3]["stimulus_hz"] == 120
