"""설정 읽기. 값은 환경 변수(.env 파일)에서 온다.

두 가지 경우
  DUO_CASE=1  네모트론 + 제미나이 (기본)
  DUO_CASE=2  솔라 + 제미나이
음성 인식만 바꾸려면 DUO_STT=nvidia (Nemotron 음성 인식)

인터넷 공개(등록 방식)
  DUO_INVITE_CODE를 적으면 등록 방식이 켜진다. 기기를 붙이려는 사람이 /register 에서
  초대 코드와 자기 API 키를 넣고 자기만의 접속 주소를 받는다. 이때 옛 공용 주소
  (/xiaozhi/ota/)는 꺼진다. 같은 와이파이에서만 쓰는 옛 방식을 유지하려면 DUO_LEGACY_ROUTES=1.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field

STT_CHOICES = ("gemini", "nvidia", "fake")
LLM_CHOICES = ("nemotron", "solar", "openai", "fake")
TTS_CHOICES = ("gemini", "fake")
CASES = {"1": "nemotron", "2": "solar"}

REQUIRED_KEY = {
    ("stt", "gemini"): "GEMINI_API_KEY",
    ("stt", "nvidia"): "NVIDIA_API_KEY",
    ("llm", "nemotron"): "NEBIUS_API_KEY",
    ("llm", "solar"): "UPSTAGE_API_KEY",
    ("llm", "openai"): "OPENAI_API_KEY",
    ("tts", "gemini"): "GEMINI_API_KEY",
}


class ConfigError(ValueError):
    pass


@dataclass
class Settings:
    stt: str
    llm: str
    tts: str
    device_token: str = field(repr=False)
    language: str = "ko-KR"
    host: str = "0.0.0.0"
    port: int = 8765
    device_volume: int | None = None  # 비우면 기기 볼륨을 건드리지 않음. 0~100이면 기기가 붙을 때마다 그 값으로 맞춤
    metrics_path: str = "logs/turns.jsonl"
    flower_colors: tuple = ("white", "red", "yellow", "purple")  # 로봇이 집을 수 있는 꽃 색 (DUO_FLOWER_COLORS, 쉼표 구분)
    save_audio_dir: str = ""  # 비어 있으면 받은 소리를 저장하지 않음

    # 인터넷 공개(등록 방식)
    invite_code: str = field(default="", repr=False)  # 비어 있으면 등록 페이지가 꺼짐
    registry_path: str = "data/registrations.json"
    legacy_routes: bool = True  # 공용 암호 하나로 /xiaozhi/ota/ 를 여는 옛 방식
    public_url: str = ""  # 비어 있으면 요청의 Host 헤더로 주소를 만든다
    gemini_key_missing: bool = False  # 서버에 제미나이 키가 없음 → 음성 기기를 붙이려는 사람이 등록 때 내야 함
    upstage_key_required: bool = True  # 답변이 솔라이고 서버에 업스테이지 키가 없을 때만 등록하는 사람이 냄
    openai_key_required: bool = False  # 답변이 OpenAI이고 서버에 키가 없을 때만 등록하는 사람이 냄
    register_ask_keys: bool = True  # 등록 화면에 키 칸을 보일지. 끄면(DUO_REGISTER_ASK_KEYS=0) 이름·초대 코드만 받고 모두 서버 키를 씀

    gemini_api_key: str = field(default="", repr=False)
    gemini_stt_model: str = "gemini-3.5-transcribe"
    gemini_tts_model: str = "gemini-3.8-flash-tts"
    gemini_tts_voice: str = "Kore"

    nebius_api_key: str = field(default="", repr=False)
    nebius_base_url: str = "https://api.tokenfactory.nebius.com/v1/"
    nemotron_model: str = "nvidia/Nemotron-3_5-Lightning"

    upstage_api_key: str = field(default="", repr=False)
    upstage_base_url: str = "https://api.upstage.ai/v1"
    solar_model: str = "solar-mini4"

    openai_api_key: str = field(default="", repr=False)
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-5.6"  # 2026-10-14 공식 모델 페이지에서 확인한 이름

    nvidia_api_key: str = field(default="", repr=False)
    nvidia_asr_server: str = "grpc.nvcf.nvidia.com:443"
    nvidia_asr_function_id: str = "bb0837de-8c7b-481f-9ec8-ef5663e9c1fa"

    llm_extra_body: dict = field(default_factory=dict)


def _choice(env: Mapping[str, str], name: str, choices: tuple[str, ...], default: str) -> str:
    value = (env.get(name) or default).strip().lower()
    if value not in choices:
        raise ConfigError("%s 값은 %s 중 하나여야 합니다 (지금: %r)" % (name, ", ".join(choices), value))
    return value


def load_settings(env: Mapping[str, str]) -> Settings:
    case = (env.get("DUO_CASE") or "1").strip()
    if case not in CASES:
        raise ConfigError("DUO_CASE 값은 1 또는 2여야 합니다 (지금: %r)" % case)

    stt = _choice(env, "DUO_STT", STT_CHOICES, "gemini")
    llm = _choice(env, "DUO_LLM", LLM_CHOICES, CASES[case])
    tts = _choice(env, "DUO_TTS", TTS_CHOICES, "gemini")

    invite_code = (env.get("DUO_INVITE_CODE") or "").strip()
    legacy_raw = (env.get("DUO_LEGACY_ROUTES") or "").strip().lower()
    legacy_routes = legacy_raw in ("1", "true", "yes", "on") if legacy_raw else not invite_code
    # 등록 방식에서는 답변 모델 키(업스테이지·OpenAI)를 등록하는 사람이 낼 수 있고, 제미나이 키도 서버에 없으면 그 사람이 낸다
    provided_by_registrant = () if legacy_routes else ("UPSTAGE_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY")

    missing = []
    if legacy_routes and not (env.get("DUO_DEVICE_TOKEN") or "").strip():
        missing.append("DUO_DEVICE_TOKEN")
    for kind, chosen in (("stt", stt), ("llm", llm), ("tts", tts)):
        key = REQUIRED_KEY.get((kind, chosen))
        if key and key not in provided_by_registrant and not (env.get(key) or "").strip() and key not in missing:
            missing.append(key)
    if missing:
        raise ConfigError("다음 값이 비어 있습니다: %s (.env 파일에 넣어 주세요)" % ", ".join(missing))

    extra_raw = (env.get("DUO_LLM_EXTRA_BODY") or "").strip()
    try:
        extra = json.loads(extra_raw) if extra_raw else {}
    except ValueError as exc:
        raise ConfigError("DUO_LLM_EXTRA_BODY는 JSON이어야 합니다") from exc
    if not isinstance(extra, dict):
        raise ConfigError("DUO_LLM_EXTRA_BODY는 JSON 객체여야 합니다")

    def text(name: str, default: str) -> str:
        return (env.get(name) or default).strip()

    upstage_key_required = llm == "solar" and not text("UPSTAGE_API_KEY", "")
    openai_key_required = llm == "openai" and not text("OPENAI_API_KEY", "")
    ask_raw = (env.get("DUO_REGISTER_ASK_KEYS") or "").strip().lower()
    register_ask_keys = ask_raw not in ("0", "false", "no", "off")
    if not register_ask_keys and not legacy_routes and (upstage_key_required or openai_key_required):
        raise ConfigError(
            "등록 화면의 키 칸을 끄려면(DUO_REGISTER_ASK_KEYS=0) 서버 .env에 답변 모델 키가 있어야 합니다: %s"
            % ("UPSTAGE_API_KEY" if upstage_key_required else "OPENAI_API_KEY")
        )

    volume_raw = text("DUO_DEVICE_VOLUME", "")
    device_volume = None
    if volume_raw:
        if not volume_raw.isdigit() or not 0 <= int(volume_raw) <= 100:
            raise ConfigError("DUO_DEVICE_VOLUME 값은 0~100 사이 정수여야 합니다 (지금: %r)" % volume_raw)
        device_volume = int(volume_raw)

    defaults = Settings(stt="fake", llm="fake", tts="fake", device_token="-")
    return Settings(
        stt=stt,
        llm=llm,
        tts=tts,
        device_token=(env.get("DUO_DEVICE_TOKEN") or "").strip(),
        language=text("DUO_LANGUAGE", defaults.language),
        host=text("DUO_HOST", defaults.host),
        port=int(text("DUO_PORT", str(defaults.port))),
        device_volume=device_volume,
        metrics_path=text("DUO_METRICS_PATH", defaults.metrics_path),
        flower_colors=tuple(c.strip().lower() for c in text("DUO_FLOWER_COLORS", "").split(",") if c.strip()) or defaults.flower_colors,
        save_audio_dir=text("DUO_SAVE_AUDIO_DIR", ""),
        invite_code=invite_code,
        registry_path=text("DUO_REGISTRY_PATH", defaults.registry_path),
        legacy_routes=legacy_routes,
        public_url=text("DUO_PUBLIC_URL", "").rstrip("/"),
        gemini_key_missing=(not legacy_routes) and "gemini" in (stt, tts) and not text("GEMINI_API_KEY", ""),
        upstage_key_required=upstage_key_required,
        openai_key_required=openai_key_required,
        register_ask_keys=register_ask_keys,
        openai_api_key=text("OPENAI_API_KEY", ""),
        openai_base_url=text("OPENAI_BASE_URL", defaults.openai_base_url),
        openai_model=text("OPENAI_MODEL", defaults.openai_model),
        gemini_api_key=text("GEMINI_API_KEY", ""),
        gemini_stt_model=text("GEMINI_STT_MODEL", defaults.gemini_stt_model),
        gemini_tts_model=text("GEMINI_TTS_MODEL", defaults.gemini_tts_model),
        gemini_tts_voice=text("GEMINI_TTS_VOICE", defaults.gemini_tts_voice),
        nebius_api_key=text("NEBIUS_API_KEY", ""),
        nebius_base_url=text("NEBIUS_BASE_URL", defaults.nebius_base_url),
        nemotron_model=text("NEMOTRON_MODEL", defaults.nemotron_model),
        upstage_api_key=text("UPSTAGE_API_KEY", ""),
        upstage_base_url=text("UPSTAGE_BASE_URL", defaults.upstage_base_url),
        solar_model=text("SOLAR_MODEL", defaults.solar_model),
        nvidia_api_key=text("NVIDIA_API_KEY", ""),
        nvidia_asr_server=text("NVIDIA_ASR_SERVER", defaults.nvidia_asr_server),
        nvidia_asr_function_id=text("NVIDIA_ASR_FUNCTION_ID", defaults.nvidia_asr_function_id),
        llm_extra_body=extra,
    )


GEMINI_KEY_MISSING = "제미나이 키가 없어요. 음성을 쓰려면 등록 화면에서 제미나이 키를 넣거나 서버 .env에 GEMINI_API_KEY를 넣어 주세요"


class _MissingGeminiStt:
    """제미나이 키 없이 등록한 사람의 음성 인식 자리. 글 대화는 되고, 음성은 이유를 알려 주며 실패한다."""

    name = "gemini-stt(키 없음)"

    async def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        raise RuntimeError(GEMINI_KEY_MISSING)


class _MissingGeminiTts:
    name = "gemini-tts(키 없음)"

    async def synthesize(self, text: str, emotion: str):
        raise RuntimeError(GEMINI_KEY_MISSING)
        yield b""  # 비동기 생성기로 만들기 위한 줄 (실행되지 않음)


def build_pipeline(
    settings: Settings,
    *,
    upstage_key: str | None = None,
    gemini_key: str | None = None,
    openai_key: str | None = None,
):
    """설정에 맞는 제공자를 만들어 대화 진행기로 묶는다.

    upstage_key, gemini_key, openai_key를 주면 설정 파일의 키 대신 그 키를 쓴다 (등록한 사람의 키).
    제미나이 키가 아무 데도 없으면 음성 단계는 '키 없음' 자리로 채워 글 대화만 되게 한다.
    """
    from .pipeline import Pipeline
    from .providers import fake

    gemini_client = None
    gemini_api_key = gemini_key or settings.gemini_api_key
    upstage_api_key = upstage_key or settings.upstage_api_key
    openai_api_key = openai_key or settings.openai_api_key

    def gemini():
        nonlocal gemini_client
        if gemini_client is None:
            from google import genai

            gemini_client = genai.Client(api_key=gemini_api_key)
        return gemini_client

    if settings.stt == "gemini" and not gemini_api_key:
        stt = _MissingGeminiStt()
    elif settings.stt == "gemini":
        from .providers.gemini import GeminiStt

        stt = GeminiStt(gemini(), settings.gemini_stt_model, settings.language)
    elif settings.stt == "nvidia":
        from .providers.nvidia_asr import NvidiaAsr, build_service

        service = build_service(settings.nvidia_api_key, settings.nvidia_asr_server, settings.nvidia_asr_function_id)
        stt = NvidiaAsr(service, settings.language)
    else:
        stt = fake.FakeStt()

    if settings.llm in ("nemotron", "solar", "openai"):
        from .providers.openai_chat import OpenAiChatLlm

        if settings.llm == "nemotron":
            base_url, key, model = settings.nebius_base_url, settings.nebius_api_key, settings.nemotron_model
        elif settings.llm == "openai":
            base_url, key, model = settings.openai_base_url, openai_api_key, settings.openai_model
        else:
            base_url, key, model = settings.upstage_base_url, upstage_api_key, settings.solar_model
        llm = OpenAiChatLlm(
            settings.llm, base_url, key, model, extra_body=settings.llm_extra_body,
            profile="openai" if settings.llm == "openai" else "generic", flower_colors=settings.flower_colors,
        )
    else:
        llm = fake.FakeLlm()

    if settings.tts == "gemini" and not gemini_api_key:
        tts = _MissingGeminiTts()
    elif settings.tts == "gemini":
        from .providers.gemini import GeminiTts

        tts = GeminiTts(gemini(), settings.gemini_tts_model, settings.gemini_tts_voice)
    else:
        tts = fake.FakeTts()

    return Pipeline(stt=stt, llm=llm, tts=tts, flower_colors=settings.flower_colors)


def build_registrant_factory(settings: Settings):
    """등록한 사람마다 그 사람의 키로 대화 진행기를 만드는 함수를 돌려준다."""

    def factory(registration):
        return build_pipeline(
            settings,
            upstage_key=registration.upstage_key or None,
            gemini_key=registration.gemini_key or None,
            openai_key=getattr(registration, "openai_key", "") or None,
        )

    return factory
