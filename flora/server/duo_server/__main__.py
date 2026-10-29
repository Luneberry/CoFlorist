"""서버 실행: uv run python -m duo_server"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from .app import create_app
from .config import ConfigError, build_pipeline, build_registrant_factory, load_settings
from .registry import Registry


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
    load_dotenv(Path.cwd() / ".env")
    try:
        settings = load_settings(os.environ)
    except ConfigError as exc:
        print("설정 오류: %s" % exc, file=sys.stderr)
        return 2

    log = logging.getLogger("duo")
    registry = Registry(settings.registry_path) if settings.invite_code else None
    pipeline = build_pipeline(settings) if settings.legacy_routes else None

    if pipeline is not None:
        log.info("공용 주소(/xiaozhi/ota/) 켜짐 — 음성→글: %s | 답변: %s | 글→음성: %s",
                 pipeline.stt.name, pipeline.llm.name, pipeline.tts.name)
    if registry is not None:
        log.info("등록 방식 켜짐 — 등록 페이지 /register, 장부 %s, 등록된 사람 %d명 (음성→글: %s, 답변: %s, 글→음성: %s)",
                 settings.registry_path, len(registry.all()), settings.stt, settings.llm, settings.tts)
        if not settings.register_ask_keys:
            log.info("등록 화면의 키 칸 꺼짐 (DUO_REGISTER_ASK_KEYS=0) — 등록하는 사람은 이름·초대 코드만 넣고 모두 서버 키를 씀")
    if settings.device_volume is not None:
        log.info("기기 볼륨: 붙을 때마다 %d으로 맞춤 (DUO_DEVICE_VOLUME)", settings.device_volume)
    if pipeline is None and registry is None:
        print("설정 오류: 등록 방식(DUO_INVITE_CODE)도 공용 주소(DUO_LEGACY_ROUTES=1)도 켜져 있지 않습니다", file=sys.stderr)
        return 2

    app = create_app(
        pipeline,
        settings.device_token,
        metrics_path=settings.metrics_path,
        save_audio_dir=settings.save_audio_dir or None,
        legacy_routes=settings.legacy_routes,
        registry=registry,
        invite_code=settings.invite_code,
        pipeline_factory=build_registrant_factory(settings) if registry is not None else None,
        gemini_key_missing=settings.gemini_key_missing,
        upstage_key_required=settings.upstage_key_required,
        openai_key_required=settings.openai_key_required,
        ask_keys=settings.register_ask_keys,
        public_url=settings.public_url,
        device_volume=settings.device_volume,
        flower_colors=settings.flower_colors,
    )
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="warning", proxy_headers=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
