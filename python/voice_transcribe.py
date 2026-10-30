"""Transcribe an actual WAV locally; stdout is a single JSON result."""
import argparse
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("HF_HOME", str(ROOT / ".runtime" / "huggingface"))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", nargs="?")
    parser.add_argument("--prepare", action="store_true")
    args = parser.parse_args()
    from faster_whisper import WhisperModel

    started = time.monotonic()
    model = WhisperModel("base", device="cpu", compute_type="int8", cpu_threads=4,
                         download_root=str(ROOT / ".runtime" / "whisper"),
                         local_files_only=not args.prepare)
    if args.prepare:
        print(json.dumps({"ready": True, "model": "Whisper base", "device": "cpu"}))
        return
    if not args.audio or not Path(args.audio).is_file():
        raise ValueError("An existing audio file is required")
    # No source text/initial prompt is passed: decode only the generated audio.
    segments, info = model.transcribe(args.audio, language="ko", beam_size=5,
                                      condition_on_previous_text=False, vad_filter=True,
                                      vad_parameters={"min_silence_duration_ms": 500})
    if info.duration > 61:
        raise ValueError("Recordings must be at most 60 seconds")
    text = " ".join(segment.text.strip() for segment in segments).strip()
    if not text:
        raise ValueError("No speech was recognized")
    print(json.dumps({"text": text, "language": info.language,
                      "durationMs": round(info.duration * 1000),
                      "elapsedMs": round((time.monotonic() - started) * 1000)}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
