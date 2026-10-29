"""대화 한 번(음성→글 → 답변 → 글→음성)의 진행 시험. 가짜 AI를 끼워 쓴다."""
import asyncio

import pytest

from duo_server.pipeline import Pipeline, Reply, TurnEvents
from duo_server.providers.fake import FakeLlm, FakeStt, FakeTts

ONE_SECOND = b"\x01\x00" * 16000


class Recorder(TurnEvents):
    def __init__(self):
        self.log = []
        self.audio = b""

    async def on_transcript(self, text):
        self.log.append(("stt", text))

    async def on_thinking(self):
        self.log.append(("thinking",))

    async def on_reply(self, reply):
        self.log.append(("reply", reply.text, reply.emotion))

    async def on_audio(self, pcm, sample_rate):
        self.log.append(("audio", sample_rate))
        self.audio += pcm

    async def on_error(self, stage, message):
        self.log.append(("error", stage))


def make(stt=None, llm=None, tts=None, **kwargs):
    return Pipeline(
        stt=stt or FakeStt("오늘 회의 몇 시야?"),
        llm=llm or FakeLlm(Reply("오후 2시예요!", "happy")),
        tts=tts or FakeTts(chunks=[b"\x02\x00" * 2400, b"\x03\x00" * 2400]),
        **kwargs,
    )


async def test_happy_path_order_and_result():
    events = Recorder()
    result = await make().run_turn(ONE_SECOND, 16000, events)

    assert [e[0] for e in events.log] == ["stt", "thinking", "reply", "audio", "audio"]
    assert events.log[0] == ("stt", "오늘 회의 몇 시야?")
    assert events.log[2] == ("reply", "오후 2시예요!", "happy")
    assert len(events.audio) == 4800 * 2
    assert result.ok
    assert result.transcript == "오늘 회의 몇 시야?"
    assert result.reply_text == "오후 2시예요!"
    assert result.emotion == "happy"
    assert result.audio_ms == 200  # 24kHz에서 4800표본


async def test_timings_are_recorded_per_stage():
    ticks = iter([0.0, 0.5, 0.5, 1.7, 1.7, 2.0, 2.4])
    result = await make(clock=lambda: next(ticks)).run_turn(ONE_SECOND, 16000, Recorder())
    assert result.stt_ms == 500
    assert result.llm_ms == 1200
    assert result.tts_first_audio_ms == 300
    assert result.total_ms == 2400


async def test_names_of_providers_are_in_result():
    result = await make().run_turn(ONE_SECOND, 16000, Recorder())
    assert (result.stt_name, result.llm_name, result.tts_name) == ("fake-stt", "fake-llm", "fake-tts")


async def test_too_short_audio_skips_ai_calls():
    stt = FakeStt("무시")
    events = Recorder()
    result = await make(stt=stt).run_turn(b"\x00\x00" * 1600, 16000, events)  # 0.1초
    assert not result.ok
    assert result.failed_stage == "input"
    assert stt.calls == 0
    assert events.log == [("error", "input")]


async def test_empty_transcript_stops_before_llm():
    llm = FakeLlm(Reply("x", "neutral"))
    events = Recorder()
    result = await make(stt=FakeStt("   "), llm=llm).run_turn(ONE_SECOND, 16000, events)
    assert result.failed_stage == "stt"
    assert llm.calls == 0
    assert events.log == [("error", "stt")]


async def test_stt_failure_is_reported():
    events = Recorder()
    result = await make(stt=FakeStt(error=RuntimeError("boom"))).run_turn(ONE_SECOND, 16000, events)
    assert result.failed_stage == "stt"
    assert "boom" in result.error
    assert events.log == [("error", "stt")]


async def test_llm_failure_is_reported_after_transcript():
    events = Recorder()
    result = await make(llm=FakeLlm(error=RuntimeError("down"))).run_turn(ONE_SECOND, 16000, events)
    assert result.failed_stage == "llm"
    assert result.transcript == "오늘 회의 몇 시야?"
    assert [e[0] for e in events.log] == ["stt", "thinking", "error"]


async def test_tts_failure_is_reported_after_reply():
    events = Recorder()
    result = await make(tts=FakeTts(error=RuntimeError("no voice"))).run_turn(ONE_SECOND, 16000, events)
    assert result.failed_stage == "tts"
    assert result.reply_text == "오후 2시예요!"
    assert [e[0] for e in events.log] == ["stt", "thinking", "reply", "error"]


async def test_slow_stage_times_out():
    slow = FakeLlm(Reply("늦음", "neutral"), delay=0.2)
    result = await make(llm=slow, llm_timeout=0.05).run_turn(ONE_SECOND, 16000, Recorder())
    assert result.failed_stage == "llm"
    assert "시간" in result.error


async def test_unknown_emotion_from_llm_becomes_neutral():
    events = Recorder()
    result = await make(llm=FakeLlm(Reply("음", "ecstatic"))).run_turn(ONE_SECOND, 16000, events)
    assert result.emotion == "neutral"
    assert ("reply", "음", "neutral") in events.log


async def test_history_is_passed_to_llm_and_grows():
    llm = FakeLlm(Reply("네", "neutral"))
    pipeline = make(llm=llm)
    history = []
    await pipeline.run_turn(ONE_SECOND, 16000, Recorder(), history=history)
    await pipeline.run_turn(ONE_SECOND, 16000, Recorder(), history=history)
    assert history == [
        {"role": "user", "content": "오늘 회의 몇 시야?"},
        {"role": "assistant", "content": "네"},
        {"role": "user", "content": "오늘 회의 몇 시야?"},
        {"role": "assistant", "content": "네"},
    ]
    assert len(llm.seen_history[1]) == 2  # 두 번째 호출은 앞 대화 2줄을 받음


async def test_history_is_trimmed():
    pipeline = make(max_history=4)
    history = []
    for _ in range(5):
        await pipeline.run_turn(ONE_SECOND, 16000, Recorder(), history=history)
    assert len(history) == 4


async def test_flower_recommendation_is_carried_into_result():
    llm = FakeLlm(Reply("빨간 장미 어때요?", "loving", "장미", "red"))
    result = await make(llm=llm).run_turn(ONE_SECOND, 16000, Recorder())
    assert result.ok and result.flower == "장미" and result.flower_color == "red"
    assert result.as_dict()["flower_color"] == "red"


async def test_pipeline_keeps_colours_from_the_configured_palette():
    llm = FakeLlm(Reply("분홍 장미 어때요?", "loving", "장미", "pink"))
    kept = await make(llm=llm, flower_colors=("white", "pink")).run_turn(ONE_SECOND, 16000, Recorder())
    dropped = await make(llm=FakeLlm(Reply("분홍 장미 어때요?", "loving", "장미", "pink"))).run_turn(ONE_SECOND, 16000, Recorder())
    assert kept.flower_color == "pink" and dropped.flower_color == ""
