"""Opus 소리 압축 묶기·풀기.

기기 펌웨어는 소리를 Opus로만 주고받는다. libopus를 ctypes로 직접 부른다.
소리는 16비트, 모노, little-endian PCM만 다룬다.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
from collections.abc import Iterator

OPUS_APPLICATION_VOIP = 2048
MAX_PACKET_BYTES = 4000
BYTES_PER_SAMPLE = 2

_LIBRARY_CANDIDATES = (
    os.environ.get("DUO_OPUS_LIBRARY", ""),
    "/opt/homebrew/lib/libopus.dylib",
    "/usr/local/lib/libopus.dylib",
    ctypes.util.find_library("opus") or "",
)


class OpusError(RuntimeError):
    """libopus가 오류를 돌려줬거나 라이브러리를 찾지 못함."""


def _load_library() -> ctypes.CDLL:
    for path in _LIBRARY_CANDIDATES:
        if not path:
            continue
        try:
            return ctypes.CDLL(path)
        except OSError:
            continue
    raise OpusError("libopus를 찾지 못했습니다. `brew install opus`로 설치하세요.")


_lib = _load_library()
_lib.opus_strerror.restype = ctypes.c_char_p
_lib.opus_strerror.argtypes = [ctypes.c_int]
_lib.opus_encoder_create.restype = ctypes.c_void_p
_lib.opus_encoder_create.argtypes = [ctypes.c_int32, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
_lib.opus_encoder_destroy.restype = None
_lib.opus_encoder_destroy.argtypes = [ctypes.c_void_p]
_lib.opus_encode.restype = ctypes.c_int32
_lib.opus_encode.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int32]
_lib.opus_decoder_create.restype = ctypes.c_void_p
_lib.opus_decoder_create.argtypes = [ctypes.c_int32, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
_lib.opus_decoder_destroy.restype = None
_lib.opus_decoder_destroy.argtypes = [ctypes.c_void_p]
_lib.opus_decode.restype = ctypes.c_int
_lib.opus_decode.argtypes = [
    ctypes.c_void_p,
    ctypes.c_char_p,
    ctypes.c_int32,
    ctypes.c_char_p,
    ctypes.c_int,
    ctypes.c_int,
]


def _message(code: int) -> str:
    return _lib.opus_strerror(code).decode("utf-8", "replace")


def frame_samples(sample_rate: int, frame_ms: int) -> int:
    return sample_rate * frame_ms // 1000


def frame_bytes(sample_rate: int, frame_ms: int) -> int:
    return frame_samples(sample_rate, frame_ms) * BYTES_PER_SAMPLE


def split_frames(pcm: bytes, sample_rate: int, frame_ms: int) -> Iterator[bytes]:
    """소리를 같은 길이 조각으로 나눈다. 마지막 조각의 빈 자리는 무음으로 채운다."""
    size = frame_bytes(sample_rate, frame_ms)
    for start in range(0, len(pcm), size):
        frame = pcm[start:start + size]
        if len(frame) < size:
            frame = frame + b"\x00" * (size - len(frame))
        yield frame


class Encoder:
    def __init__(self, sample_rate: int, frame_ms: int = 60) -> None:
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        error = ctypes.c_int(0)
        self._handle = _lib.opus_encoder_create(sample_rate, 1, OPUS_APPLICATION_VOIP, ctypes.byref(error))
        if error.value != 0 or not self._handle:
            raise OpusError("압축기 생성 실패: %s" % _message(error.value))
        self._out = ctypes.create_string_buffer(MAX_PACKET_BYTES)

    def encode(self, frame: bytes) -> bytes:
        expected = frame_bytes(self.sample_rate, self.frame_ms)
        if len(frame) != expected:
            raise ValueError("조각 길이가 %d바이트여야 하는데 %d바이트임" % (expected, len(frame)))
        written = _lib.opus_encode(
            self._handle, frame, frame_samples(self.sample_rate, self.frame_ms), self._out, MAX_PACKET_BYTES
        )
        if written < 0:
            raise OpusError("압축 실패: %s" % _message(written))
        return self._out.raw[:written]

    def __del__(self) -> None:
        handle = getattr(self, "_handle", None)
        if handle:
            _lib.opus_encoder_destroy(handle)
            self._handle = None


class Decoder:
    def __init__(self, sample_rate: int) -> None:
        self.sample_rate = sample_rate
        error = ctypes.c_int(0)
        self._handle = _lib.opus_decoder_create(sample_rate, 1, ctypes.byref(error))
        if error.value != 0 or not self._handle:
            raise OpusError("풀기 도구 생성 실패: %s" % _message(error.value))

    def decode(self, packet: bytes, frame_ms: int = 60) -> bytes:
        if not packet:
            raise OpusError("빈 조각은 풀 수 없음")
        samples = frame_samples(self.sample_rate, frame_ms)
        out = ctypes.create_string_buffer(samples * BYTES_PER_SAMPLE)
        decoded = _lib.opus_decode(self._handle, packet, len(packet), out, samples, 0)
        if decoded < 0:
            raise OpusError("풀기 실패: %s" % _message(decoded))
        return out.raw[:decoded * BYTES_PER_SAMPLE]

    def __del__(self) -> None:
        handle = getattr(self, "_handle", None)
        if handle:
            _lib.opus_decoder_destroy(handle)
            self._handle = None
