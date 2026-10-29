"""NVIDIA 음성 인식 (Nemotron ASR Streaming).

NVIDIA가 돌려주는 호스팅 주소로 gRPC 호출을 한다. 한국어(ko-KR)는 NVIDIA 지원표에
"Transcription-ready"로 올라 있다 (docs.nvidia.com, 2026-09-29 확인).
호스팅 주소와 function-id는 서드파티 문서(Pipecat)에서 온 값이라 키로 직접 확인해야 한다.
"""
from __future__ import annotations

import asyncio
from typing import Any

CHUNK_BYTES = 3200  # 16kHz 기준 0.1초

DEFAULT_SERVER = "grpc.nvcf.nvidia.com:443"
DEFAULT_FUNCTION_ID = "bb0837de-8c7b-481f-9ec8-ef5663e9c1fa"


def build_service(api_key: str, server: str = DEFAULT_SERVER, function_id: str = DEFAULT_FUNCTION_ID) -> Any:
    import riva.client

    auth = riva.client.Auth(
        uri=server,
        use_ssl=True,
        metadata_args=[["function-id", function_id], ["authorization", "Bearer " + api_key]],
    )
    return riva.client.ASRService(auth)


class NvidiaAsr:
    def __init__(self, service: Any, language: str = "ko-KR", model_name: str = "nemotron-asr-streaming") -> None:
        self.name = "nvidia:%s" % model_name
        self._service = service
        self._language = language

    async def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        return await asyncio.to_thread(self._recognize, pcm, sample_rate)

    def _recognize(self, pcm: bytes, sample_rate: int) -> str:
        import riva.client

        config = riva.client.StreamingRecognitionConfig(
            config=riva.client.RecognitionConfig(
                encoding=riva.client.AudioEncoding.LINEAR_PCM,
                language_code=self._language,
                sample_rate_hertz=sample_rate,
                audio_channel_count=1,
                max_alternatives=1,
                enable_automatic_punctuation=True,
            ),
            interim_results=False,
        )
        chunks = (pcm[i:i + CHUNK_BYTES] for i in range(0, len(pcm), CHUNK_BYTES))
        parts: list[str] = []
        for response in self._service.streaming_response_generator(audio_chunks=chunks, streaming_config=config):
            for result in response.results:
                if result.is_final and result.alternatives:
                    parts.append(result.alternatives[0].transcript.strip())
        return " ".join(part for part in parts if part)
