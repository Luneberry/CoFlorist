"""가짜 기기: 실제 기기처럼 서버에 접속해 녹음 파일을 보내고 답을 받는다.

사용:
  uv run python -m duo_server.fake_device --server http://127.0.0.1:8765 --wav 질문.wav --out 답.wav

--wav를 주지 않으면 1초짜리 '삐' 소리를 보낸다 (흐름 확인용).
녹음 파일은 16kHz, 16비트, 모노 WAV여야 한다.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import wave

import httpx
import websockets

from . import opus
from .providers.fake import tone

DEVICE_ID = "fa:ke:de:vi:ce:01"
CLIENT_ID = "00000000-0000-4000-8000-00000000fake"
FRAME_MS = 60


def read_wav(path: str) -> bytes:
    with wave.open(path, "rb") as w:
        if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (1, 2, 16000):
            raise SystemExit(
                "녹음 파일은 16kHz 16비트 모노여야 합니다 (지금: %d채널 %d비트 %dHz)"
                % (w.getnchannels(), w.getsampwidth() * 8, w.getframerate())
            )
        return w.readframes(w.getnframes())


def write_wav(path: str, pcm: bytes, sample_rate: int) -> None:
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)


async def run(server: str, wav: str | None, out: str | None, realtime: bool) -> int:
    pcm = read_wav(wav) if wav else tone(1.0, 300.0, 16000)

    async with httpx.AsyncClient(timeout=10.0) as http:
        response = await http.post(
            server.rstrip("/") + "/xiaozhi/ota/",
            json={"application": {"version": "2.0.5"}, "board": {"type": "fake-device"}},
            headers={"Device-Id": DEVICE_ID, "Client-Id": CLIENT_ID},
        )
        response.raise_for_status()
        guide = response.json()
    url = guide["websocket"]["url"]
    print("접속 안내 받음: %s" % url)

    headers = {
        "Authorization": "Bearer " + guide["websocket"]["token"],
        "Protocol-Version": "1",
        "Device-Id": DEVICE_ID,
        "Client-Id": CLIENT_ID,
    }
    async with websockets.connect(url, additional_headers=headers, max_size=None) as ws:
        await ws.send(json.dumps({
            "type": "hello",
            "version": 1,
            "transport": "websocket",
            "audio_params": {"format": "opus", "sample_rate": 16000, "channels": 1, "frame_duration": FRAME_MS},
        }))
        hello = json.loads(await ws.recv())
        out_rate = hello["audio_params"]["sample_rate"]
        print("인사 완료: session=%s, 답변 소리 %dHz" % (hello["session_id"], out_rate))

        await ws.send(json.dumps({"session_id": hello["session_id"], "type": "listen", "state": "start", "mode": "manual"}))
        encoder = opus.Encoder(16000, FRAME_MS)
        sent = 0
        for frame in opus.split_frames(pcm, 16000, FRAME_MS):
            await ws.send(encoder.encode(frame))
            sent += 1
            if realtime:
                await asyncio.sleep(FRAME_MS / 1000)
        await ws.send(json.dumps({"session_id": hello["session_id"], "type": "listen", "state": "stop"}))
        stopped_at = time.monotonic()
        print("소리 %d조각(%.1f초) 보냄" % (sent, sent * FRAME_MS / 1000))

        decoder = opus.Decoder(out_rate)
        reply_pcm = bytearray()
        first_audio_at = None
        while True:
            message = await ws.recv()
            if isinstance(message, bytes):
                if first_audio_at is None:
                    first_audio_at = time.monotonic()
                reply_pcm += decoder.decode(message, FRAME_MS)
                continue
            data = json.loads(message)
            shown = {k: v for k, v in data.items() if k != "session_id"}
            print("  받음 %5.2fs  %s" % (time.monotonic() - stopped_at, json.dumps(shown, ensure_ascii=False)))
            if data.get("type") == "tts" and data.get("state") == "stop":
                break

    seconds = len(reply_pcm) / 2 / out_rate
    if first_audio_at is not None:
        print("말 끝난 뒤 첫 소리까지 %.2f초, 답변 소리 %.2f초" % (first_audio_at - stopped_at, seconds))
    else:
        print("답변 소리 없음")
    if out and reply_pcm:
        write_wav(out, bytes(reply_pcm), out_rate)
        print("답변 소리 저장: %s" % out)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="DUO 가짜 기기")
    parser.add_argument("--server", default="http://127.0.0.1:8765")
    parser.add_argument("--wav", help="보낼 녹음 파일 (16kHz 16비트 모노)")
    parser.add_argument("--out", help="받은 답변 소리를 저장할 파일")
    parser.add_argument("--fast", action="store_true", help="실제 속도로 기다리지 않고 한꺼번에 보냄")
    args = parser.parse_args()
    return asyncio.run(run(args.server, args.wav, args.out, realtime=not args.fast))


if __name__ == "__main__":
    sys.exit(main())
