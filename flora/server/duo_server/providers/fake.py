"""시험과 키 없는 실행에 쓰는 가짜 AI."""
from __future__ import annotations

import asyncio
import math
import struct
from collections.abc import AsyncIterator

from ..pipeline import Reply


class FakeStt:
    name = "fake-stt"

    def __init__(self, text: str = "안녕 듀오", *, error: Exception | None = None, delay: float = 0.0):
        self.text = text
        self.error = error
        self.delay = delay
        self.calls = 0

    async def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.text


class FakeLlm:
    name = "fake-llm"

    def __init__(self, reply: Reply | None = None, *, error: Exception | None = None, delay: float = 0.0):
        self.answer = reply or Reply("안녕하세요! 저는 듀오예요.", "happy")
        self.error = error
        self.delay = delay
        self.calls = 0
        self.seen_history: list[list[dict]] = []

    async def reply(self, user_text: str, history: list[dict]) -> Reply:
        self.calls += 1
        self.seen_history.append(history)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.answer


def tone(seconds: float, hz: float = 660.0, sample_rate: int = 24000) -> bytes:
    """짧은 '삐' 소리. 가짜 음성 합성이 말 대신 내보낸다."""
    count = int(sample_rate * seconds)
    return b"".join(
        struct.pack("<h", int(8000 * math.sin(2 * math.pi * hz * i / sample_rate))) for i in range(count)
    )


class FakeTts:
    name = "fake-tts"

    def __init__(self, chunks: list[bytes] | None = None, *, error: Exception | None = None, delay: float = 0.0):
        self.chunks = chunks if chunks is not None else [tone(0.3), tone(0.3, 880.0)]
        self.error = error
        self.delay = delay
        self.calls = 0

    async def synthesize(self, text: str, emotion: str) -> AsyncIterator[bytes]:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        for chunk in self.chunks:
            yield chunk
