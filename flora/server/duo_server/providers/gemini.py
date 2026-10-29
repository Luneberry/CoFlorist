"""Gemini 음성: 음성→글, 글→음성.

호출 형식은 공식 문서 예제를 따른다 (2026-09-29 확인).
- https://ai.google.dev/gemini-api/docs/transcribe
- https://ai.google.dev/gemini-api/docs/audio (소리를 파일로 올리지 않고 바로 넣는 형식)
- https://ai.google.dev/gemini-api/docs/speech-generation (스트리밍)
"""
from __future__ import annotations

import base64
import io
import wave
from collections.abc import AsyncIterator
from typing import Any

# 감정 → 말투 지시. 문서 예제가 영어 문구를 쓰므로 영어로 적는다.
STYLES = {
    "neutral": "warm and friendly, like a small helpful robot",
    "happy": "cheerful and friendly",
    "sad": "gentle and a little sad",
    "angry": "firm and slightly annoyed",
    "surprised": "surprised and excited",
    "loving": "affectionate and sweet",
    "winking": "playful and teasing",
    "sleepy": "slow and sleepy",
    "confused": "hesitant and unsure",
    "thinking": "calm and thoughtful",
}
_SAME_STYLE = {
    "laughing": "happy", "funny": "winking", "crying": "sad", "embarrassed": "confused",
    "shocked": "surprised", "cool": "winking", "relaxed": "neutral", "delicious": "happy",
    "kissy": "loving", "confident": "happy", "silly": "winking",
    # 표정 이름으로 온 경우
    "basic": "neutral", "blink": "neutral", "joy": "happy", "wink": "winking",
    "look_around": "thinking", "love": "loving", "dizzy": "confused",
}


def style_for_emotion(emotion: str) -> str:
    return STYLES.get(_SAME_STYLE.get(emotion, emotion), STYLES["neutral"])


def pcm_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buffer.getvalue()


class GeminiStt:
    def __init__(self, client: Any, model: str = "gemini-3.5-transcribe", language: str = "ko-KR") -> None:
        self.name = "gemini:%s" % model
        self._client = client
        self._model = model
        self._language = language

    async def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        wav = pcm_to_wav(pcm, sample_rate)
        interaction = await self._client.aio.interactions.create(
            model=self._model,
            input=[
                {
                    "type": "audio",
                    "data": base64.b64encode(wav).decode("ascii"),
                    "mime_type": "audio/wav",
                }
            ],
            generation_config={"transcription_config": {"language_codes": [self._language]}},
        )
        return getattr(interaction, "output_text", None) or ""


class GeminiTts:
    """24kHz 16비트 모노 PCM을 조각으로 내놓는다."""

    def __init__(self, client: Any, model: str = "gemini-3.8-flash-tts", voice: str = "Kore") -> None:
        self.name = "gemini:%s" % model
        self._client = client
        self._model = model
        self._voice = voice

    async def synthesize(self, text: str, emotion: str) -> AsyncIterator[bytes]:
        stream = await self._client.aio.interactions.create(
            model=self._model,
            input=[
                {
                    "type": "user_input",
                    "content": [
                        {
                            "type": "text",
                            "text": text,
                            "annotations": [{"type": "speech_metadata", "style": style_for_emotion(emotion)}],
                        }
                    ],
                }
            ],
            response_format={"type": "audio"},
            generation_config={"speech_config": [{"voice": self._voice}]},
            stream=True,
        )
        async for event in stream:
            if getattr(event, "event_type", None) != "step.delta":
                continue
            delta = getattr(event, "delta", None)
            if getattr(delta, "type", None) == "audio" and getattr(delta, "data", None):
                yield base64.b64decode(delta.data)
