"""소리 압축(Opus) 묶기·풀기 시험."""
import math
import struct

import pytest

from duo_server import opus


def sine(sample_rate: int, seconds: float, hz: float = 440.0) -> bytes:
    count = int(sample_rate * seconds)
    return b"".join(
        struct.pack("<h", int(12000 * math.sin(2 * math.pi * hz * i / sample_rate))) for i in range(count)
    )


def rms(pcm: bytes) -> float:
    values = struct.unpack("<%dh" % (len(pcm) // 2), pcm)
    return math.sqrt(sum(v * v for v in values) / max(1, len(values)))


def test_frame_bytes():
    assert opus.frame_bytes(16000, 60) == 960 * 2
    assert opus.frame_bytes(24000, 60) == 1440 * 2


def test_split_frames_pads_last_frame_with_silence():
    pcm = b"\x01\x00" * 1000  # 1000표본, 한 조각은 960표본
    frames = list(opus.split_frames(pcm, 16000, 60))
    assert len(frames) == 2
    assert all(len(f) == 1920 for f in frames)
    assert frames[1][80:] == b"\x00" * (1920 - 80)


def test_split_frames_of_empty_audio_is_empty():
    assert list(opus.split_frames(b"", 16000, 60)) == []


def test_roundtrip_keeps_length_and_loudness():
    pcm = sine(16000, 0.6)
    encoder = opus.Encoder(16000)
    decoder = opus.Decoder(16000)
    packets = [encoder.encode(frame) for frame in opus.split_frames(pcm, 16000, 60)]
    assert len(packets) == 10
    assert all(0 < len(p) < 1920 for p in packets), "압축한 조각은 원본보다 작아야 함"

    restored = b"".join(decoder.decode(p, 60) for p in packets)
    assert len(restored) == len(pcm)
    assert rms(restored) == pytest.approx(rms(pcm), rel=0.25)


def test_roundtrip_at_24k():
    pcm = sine(24000, 0.3)
    encoder = opus.Encoder(24000)
    decoder = opus.Decoder(24000)
    restored = b"".join(decoder.decode(encoder.encode(f), 60) for f in opus.split_frames(pcm, 24000, 60))
    assert len(restored) == len(pcm)


def test_encoder_rejects_wrong_frame_size():
    encoder = opus.Encoder(16000)
    with pytest.raises(ValueError):
        encoder.encode(b"\x00" * 100)


def test_decoder_reports_broken_packet():
    decoder = opus.Decoder(16000)
    with pytest.raises(opus.OpusError):
        decoder.decode(b"", 60)
