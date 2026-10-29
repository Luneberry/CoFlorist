"""A minute is one turn; older firmware keeps its existing 30-second limit."""
import json

import pytest
from fastapi.testclient import TestClient

from duo_server import opus
from duo_server.app import create_app
from duo_server.pipeline import Pipeline, Reply
from duo_server.protocol import ProtocolError, Session
from duo_server.providers.fake import FakeLlm, FakeStt, FakeTts, tone

HELLO = {
    "type": "hello", "transport": "websocket",
    "features": {"listen_60s": True},
    "audio_params": {"format": "opus", "sample_rate": 16000, "channels": 1, "frame_duration": 60},
}


@pytest.mark.parametrize("feature,frames", [(True, 1000), (False, 500), (None, 500), ("true", 500), (1, 500)])
def test_duration_is_explicit_bounded_opt_in(feature, frames):
    session = Session("test")
    hello = session.handle_text({**HELLO, "features": {"listen_60s": feature}})
    assert hello.reply.get("max_listen_ms", 30000) == frames * 60
    session.handle_text({"type": "listen", "state": "start"})
    packets = [str(i).encode() for i in range(frames + 1)]
    for packet in packets:
        session.handle_audio(packet)
    event = session.handle_text({"type": "listen", "state": "stop"})
    assert event.packets == packets[:frames]
    assert session.dropped_packets == 1
    assert session.handle_text({"type": "listen", "state": "stop"}).kind == "ignored"


@pytest.mark.parametrize("duration", [0, -1, 61])
def test_invalid_frame_duration_is_rejected(duration):
    with pytest.raises(ProtocolError):
        Session("test").handle_text({**HELLO, "audio_params": {**HELLO["audio_params"], "frame_duration": duration}})


def test_full_minute_reaches_stt_once_and_returns_one_response(tmp_path):
    class CountStt(FakeStt):
        async def transcribe(self, pcm, sample_rate):
            assert len(pcm) == 16000 * 2 * 60
            assert sample_rate == 16000
            return await super().transcribe(pcm, sample_rate)

    stt = CountStt("one minute completed")
    llm = FakeLlm(Reply("one response", "happy"))
    tts = FakeTts(chunks=[tone(0.12)])
    metrics = tmp_path / "turns.jsonl"
    app = create_app(Pipeline(stt, llm, tts), "test-token", metrics_path=metrics, pace_audio=False)
    encoder = opus.Encoder(16000)
    packet = encoder.encode(bytes(960 * 2))
    with TestClient(app).websocket_connect("/xiaozhi/v1/", headers={"Authorization": "Bearer test-token"}) as ws:
        ws.send_json(HELLO)
        assert ws.receive_json()["max_listen_ms"] == 60000
        ws.send_json({"type": "listen", "state": "start", "mode": "manual"})
        for _ in range(1000):
            ws.send_bytes(packet)
        assert stt.calls == llm.calls == tts.calls == 0
        ws.send_json({"type": "listen", "state": "stop"})
        starts = stops = audio = 0
        while True:
            frame = ws.receive()
            if frame.get("bytes") is not None:
                audio += 1
                continue
            message = json.loads(frame["text"])
            if message.get("type") == "tts":
                starts += message.get("state") == "start"
                stops += message.get("state") == "stop"
                if stops:
                    break
    assert starts == stops == stt.calls == llm.calls == tts.calls == 1
    assert audio > 0
    rows = [json.loads(line) for line in metrics.read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["input_ms"] == 60000 and rows[0]["ok"]
