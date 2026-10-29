"""대화 한 번의 진행: 음성→글 → 답변 → 글→음성.

각 단계는 약속(Protocol)만 지키면 바꿔 끼울 수 있다. 단계마다 걸린 시간을 잰다.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict, dataclass
from typing import Protocol

from .protocol import normalize_emotion

MIN_AUDIO_MS = 300
TTS_SAMPLE_RATE = 24000


# 로봇이 집을 수 있는 꽃 색. 답변 모델은 이 네 가지 중에서만 고른다 (CoFlorist 대시보드의 색 목록과 같음)
FLOWER_COLORS = ("white", "red", "yellow", "purple")


def normalize_flower_color(value, colors=FLOWER_COLORS) -> str:
    """목록에 있는 색이면 그대로, 아니면 빈 문자열 (로봇이 모르는 색은 넘기지 않는다). 목록은 설정(DUO_FLOWER_COLORS)으로 바꾼다."""
    if isinstance(value, str) and value.strip().lower() in tuple(colors):
        return value.strip().lower()
    return ""


_COLOR_WORDS = (
    ("white", ("흰", "하얀", "하얗", "백색")),
    ("red", ("빨간", "빨갛", "붉은", "붉", "적색")),
    ("yellow", ("노란", "노랗", "황색")),
    ("purple", ("보라", "보랏", "자주", "자줏")),
)


def infer_flower_color(text: str) -> str:
    """말로 한 추천("노란 튤립")에서 색을 건진다. 여러 색이 있으면 먼저 나온 것. 없으면 빈 문자열."""
    found = []
    for color, words in _COLOR_WORDS:
        positions = [text.find(w) for w in words if w in (text or "")]
        if positions:
            found.append((min(positions), color))
    return min(found)[1] if found else ""


@dataclass
class Reply:
    text: str
    emotion: str = "neutral"
    flower: str = ""  # 추천한 꽃 이름 (없으면 빈 문자열)
    flower_color: str = ""  # FLOWER_COLORS 중 하나 (없으면 빈 문자열)


class SttProvider(Protocol):
    name: str

    async def transcribe(self, pcm: bytes, sample_rate: int) -> str: ...


class LlmProvider(Protocol):
    name: str

    async def reply(self, user_text: str, history: list[dict]) -> Reply: ...


class TtsProvider(Protocol):
    name: str

    def synthesize(self, text: str, emotion: str) -> AsyncIterator[bytes]:
        """24kHz 16비트 모노 PCM 조각을 차례로 내놓는다."""
        ...


class TurnEvents:
    """진행 상황을 받는 쪽. 필요한 것만 덮어 쓴다."""

    async def on_transcript(self, text: str) -> None: ...

    async def on_thinking(self) -> None: ...

    async def on_reply(self, reply: Reply) -> None: ...

    async def on_audio(self, pcm: bytes, sample_rate: int) -> None: ...

    async def on_error(self, stage: str, message: str) -> None: ...


@dataclass
class TurnResult:
    ok: bool = False
    failed_stage: str = ""
    error: str = ""
    transcript: str = ""
    reply_text: str = ""
    emotion: str = ""
    flower: str = ""
    flower_color: str = ""
    input_ms: int = 0
    audio_ms: int = 0
    stt_ms: int = 0
    llm_ms: int = 0
    tts_first_audio_ms: int = 0
    total_ms: int = 0
    stt_name: str = ""
    llm_name: str = ""
    tts_name: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _ms(seconds: float) -> int:
    return round(seconds * 1000)


class Pipeline:
    def __init__(
        self,
        stt: SttProvider,
        llm: LlmProvider,
        tts: TtsProvider,
        *,
        clock: Callable[[], float] = time.monotonic,
        stt_timeout: float = 15.0,
        llm_timeout: float = 20.0,
        tts_timeout: float = 20.0,
        max_history: int = 12,
        flower_colors=FLOWER_COLORS,
    ) -> None:
        self.stt = stt
        self.llm = llm
        self.tts = tts
        self._clock = clock
        self._stt_timeout = stt_timeout
        self._llm_timeout = llm_timeout
        self._tts_timeout = tts_timeout
        self._max_history = max_history
        self._flower_colors = tuple(flower_colors)

    async def run_turn(
        self,
        pcm: bytes,
        sample_rate: int,
        events: TurnEvents,
        history: list[dict] | None = None,
    ) -> TurnResult:
        result = TurnResult(stt_name=self.stt.name, llm_name=self.llm.name, tts_name=self.tts.name)
        result.input_ms = len(pcm) // 2 * 1000 // sample_rate
        history = history if history is not None else []

        async def fail(stage: str, message: str) -> TurnResult:
            result.failed_stage = stage
            result.error = message
            await events.on_error(stage, message)
            return result

        if result.input_ms < MIN_AUDIO_MS:
            return await fail("input", "소리가 너무 짧음 (%dms)" % result.input_ms)

        started = self._clock()

        # 1) 음성 → 글
        try:
            transcript = await asyncio.wait_for(self.stt.transcribe(pcm, sample_rate), self._stt_timeout)
        except asyncio.TimeoutError:
            return await fail("stt", "음성 인식이 시간 안에 끝나지 않음")
        except Exception as exc:  # 어떤 제공자 오류든 기기에는 실패로만 알린다
            return await fail("stt", "음성 인식 실패: %s" % exc)
        stt_done = self._clock()
        result.stt_ms = _ms(stt_done - started)
        transcript = (transcript or "").strip()
        if not transcript:
            return await fail("stt", "알아들은 말이 없음")
        result.transcript = transcript
        await events.on_transcript(transcript)
        await events.on_thinking()

        # 2) 답변
        llm_started = self._clock()
        try:
            reply = await asyncio.wait_for(self.llm.reply(transcript, list(history)), self._llm_timeout)
        except asyncio.TimeoutError:
            return await fail("llm", "답변 생성이 시간 안에 끝나지 않음")
        except Exception as exc:
            return await fail("llm", "답변 생성 실패: %s" % exc)
        result.llm_ms = _ms(self._clock() - llm_started)
        reply = Reply((reply.text or "").strip(), normalize_emotion(reply.emotion),
                      (reply.flower or "").strip(), normalize_flower_color(reply.flower_color, self._flower_colors))
        if not reply.text:
            return await fail("llm", "답변이 비어 있음")
        result.reply_text = reply.text
        result.emotion = reply.emotion
        result.flower, result.flower_color = reply.flower, reply.flower_color
        await events.on_reply(reply)

        # 3) 글 → 음성
        tts_started = self._clock()
        samples = 0
        try:
            async with asyncio.timeout(self._tts_timeout):
                async for chunk in self.tts.synthesize(reply.text, reply.emotion):
                    if not chunk:
                        continue
                    if samples == 0:
                        result.tts_first_audio_ms = _ms(self._clock() - tts_started)
                    samples += len(chunk) // 2
                    await events.on_audio(chunk, TTS_SAMPLE_RATE)
        except TimeoutError:
            return await fail("tts", "음성 합성이 시간 안에 끝나지 않음")
        except Exception as exc:
            return await fail("tts", "음성 합성 실패: %s" % exc)
        if samples == 0:
            return await fail("tts", "합성된 소리가 없음")

        result.audio_ms = samples * 1000 // TTS_SAMPLE_RATE
        result.total_ms = _ms(self._clock() - started)
        result.ok = True

        history.append({"role": "user", "content": transcript})
        history.append({"role": "assistant", "content": reply.text})
        del history[: max(0, len(history) - self._max_history)]
        return result
