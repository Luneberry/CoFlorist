"""설정 읽기 시험."""
import pytest

from duo_server.config import ConfigError, load_settings

BASE = {"DUO_DEVICE_TOKEN": "tok"}
ALL_KEYS = {
    **BASE,
    "GEMINI_API_KEY": "g",
    "NEBIUS_API_KEY": "n",
    "UPSTAGE_API_KEY": "u",
    "NVIDIA_API_KEY": "v",
}


def test_defaults_are_case_one_with_gemini_voice():
    s = load_settings(ALL_KEYS)
    assert (s.stt, s.llm, s.tts) == ("gemini", "nemotron", "gemini")
    assert s.port == 8765
    assert s.language == "ko-KR"


def test_case_two_selects_solar():
    s = load_settings({**ALL_KEYS, "DUO_CASE": "2"})
    assert (s.stt, s.llm, s.tts) == ("gemini", "solar", "gemini")


def test_explicit_choice_beats_case():
    s = load_settings({**ALL_KEYS, "DUO_CASE": "2", "DUO_LLM": "nemotron", "DUO_STT": "nvidia"})
    assert (s.stt, s.llm) == ("nvidia", "nemotron")


def test_model_ids_come_from_documents_and_can_be_overridden():
    s = load_settings(ALL_KEYS)
    assert s.gemini_stt_model == "gemini-3.5-transcribe"
    assert s.gemini_tts_model == "gemini-3.8-flash-tts"
    assert s.nebius_base_url == "https://api.tokenfactory.nebius.com/v1/"
    assert s.upstage_base_url == "https://api.upstage.ai/v1"
    assert s.solar_model == "solar-mini4"
    s = load_settings({**ALL_KEYS, "SOLAR_MODEL": "solar-pro4", "NEMOTRON_MODEL": "nvidia/nemotron-3-super-120b-a12b"})
    assert s.solar_model == "solar-pro4"
    assert s.nemotron_model == "nvidia/nemotron-3-super-120b-a12b"


def test_missing_token_is_an_error():
    with pytest.raises(ConfigError) as info:
        load_settings({"DUO_STT": "fake", "DUO_LLM": "fake", "DUO_TTS": "fake"})
    assert "DUO_DEVICE_TOKEN" in str(info.value)


def test_missing_keys_are_listed_by_name_only():
    with pytest.raises(ConfigError) as info:
        load_settings({**BASE, "DUO_CASE": "2", "GEMINI_API_KEY": "secret-value"})
    message = str(info.value)
    assert "UPSTAGE_API_KEY" in message
    assert "NEBIUS_API_KEY" not in message  # 고르지 않은 제공자의 키는 요구하지 않음
    assert "secret-value" not in message


def test_all_fake_needs_no_keys():
    s = load_settings({**BASE, "DUO_STT": "fake", "DUO_LLM": "fake", "DUO_TTS": "fake"})
    assert (s.stt, s.llm, s.tts) == ("fake", "fake", "fake")


def test_unknown_choice_is_an_error():
    with pytest.raises(ConfigError):
        load_settings({**ALL_KEYS, "DUO_LLM": "gpt"})
    with pytest.raises(ConfigError):
        load_settings({**ALL_KEYS, "DUO_CASE": "3"})


def test_settings_repr_hides_secrets():
    secrets = {
        "DUO_DEVICE_TOKEN": "SECRET-DEVICE",
        "GEMINI_API_KEY": "SECRET-GEMINI",
        "NEBIUS_API_KEY": "SECRET-NEBIUS",
        "UPSTAGE_API_KEY": "SECRET-UPSTAGE",
        "NVIDIA_API_KEY": "SECRET-NVIDIA",
    }
    text = repr(load_settings(secrets))
    assert "SECRET" not in text
    assert "nemotron" in text  # 비밀이 아닌 값은 보인다


# ---------------------------------------------------------------- 등록 방식 (인터넷 공개용)

from duo_server.config import build_pipeline, build_registrant_factory  # noqa: E402
from duo_server.registry import Registration  # noqa: E402

REGISTRY_MODE = {"DUO_INVITE_CODE": "duo-2026", "DUO_CASE": "2"}


def test_registry_mode_needs_no_device_token_or_upstage_key():
    s = load_settings({**REGISTRY_MODE, "GEMINI_API_KEY": "SECRET-gem"})
    assert s.legacy_routes is False
    assert s.invite_code == "duo-2026"
    assert s.llm == "solar"
    assert s.registry_path == "data/registrations.json"
    assert s.gemini_key_missing is False


def test_registry_mode_without_server_gemini_key_marks_it_missing():
    s = load_settings(REGISTRY_MODE)
    assert s.gemini_key_missing is True


def test_legacy_routes_keep_requiring_keys():
    with pytest.raises(ConfigError) as exc:
        load_settings({**REGISTRY_MODE, "DUO_LEGACY_ROUTES": "1", "GEMINI_API_KEY": "SECRET-gem"})
    assert "DUO_DEVICE_TOKEN" in str(exc.value) and "UPSTAGE_API_KEY" in str(exc.value)


def test_public_url_is_read():
    s = load_settings({**REGISTRY_MODE, "GEMINI_API_KEY": "SECRET-gem", "DUO_PUBLIC_URL": "https://duo.example/"})
    assert s.public_url == "https://duo.example"


def test_registrant_factory_uses_registrants_keys():
    s = load_settings({**REGISTRY_MODE, "DUO_STT": "fake", "DUO_TTS": "fake"})
    reg = Registration(token="t", access_token="a", name="민수", upstage_key="SECRET-reg", gemini_key="")
    pipeline = build_registrant_factory(s)(reg)
    assert pipeline.llm.name == "solar:solar-mini4"
    assert pipeline.llm._api_key == "SECRET-reg"


def test_build_pipeline_key_override():
    s = load_settings({**REGISTRY_MODE, "DUO_STT": "fake", "DUO_TTS": "fake", "UPSTAGE_API_KEY": "SECRET-server"})
    assert build_pipeline(s).llm._api_key == "SECRET-server"
    assert build_pipeline(s, upstage_key="SECRET-reg").llm._api_key == "SECRET-reg"


# ---------------------------------------------------------------- 답변 모델을 OpenAI(코덱스 키)로

def test_openai_answer_model_uses_server_key():
    s = load_settings({**REGISTRY_MODE, "DUO_LLM": "openai", "OPENAI_API_KEY": "SECRET-oa", "DUO_STT": "fake", "DUO_TTS": "fake"})
    assert s.llm == "openai"
    assert s.upstage_key_required is False  # 솔라를 안 쓰니 등록하는 사람이 업스테이지 키를 낼 필요 없음
    pipeline = build_pipeline(s)
    assert pipeline.llm.name == "openai:" + s.openai_model
    assert pipeline.llm._api_key == "SECRET-oa"
    assert pipeline.llm._url == "https://api.openai.com/v1/chat/completions"


def test_openai_without_server_key_is_asked_from_registrants():
    s = load_settings({**REGISTRY_MODE, "DUO_LLM": "openai", "DUO_STT": "fake", "DUO_TTS": "fake"})
    assert s.openai_key_required is True
    with_key = load_settings({**REGISTRY_MODE, "DUO_LLM": "openai", "OPENAI_API_KEY": "SECRET-oa", "DUO_STT": "fake", "DUO_TTS": "fake"})
    assert with_key.openai_key_required is False


def test_openai_key_is_still_required_on_the_shared_address():
    with pytest.raises(ConfigError) as exc:
        load_settings({**BASE, "DUO_LLM": "openai", "DUO_STT": "fake", "DUO_TTS": "fake"})
    assert "OPENAI_API_KEY" in str(exc.value)


def test_registrant_openai_key_overrides_server_key():
    s = load_settings({**REGISTRY_MODE, "DUO_LLM": "openai", "OPENAI_API_KEY": "SECRET-server", "DUO_STT": "fake", "DUO_TTS": "fake"})
    assert build_pipeline(s, openai_key="SECRET-reg").llm._api_key == "SECRET-reg"
    reg = Registration(token="t", access_token="a", name="민수", openai_key="SECRET-reg")
    assert build_registrant_factory(s)(reg).llm._api_key == "SECRET-reg"


def test_pipeline_builds_without_gemini_key_and_voice_fails_with_a_clear_message():
    """글 대화만 쓰는 사람은 제미나이 키 없이도 등록할 수 있어야 한다. 음성은 키가 없다고 분명히 알린다."""
    import asyncio

    s = load_settings({**REGISTRY_MODE, "DUO_LLM": "fake"})
    pipeline = build_pipeline(s)
    assert pipeline.llm.name == "fake-llm"
    with pytest.raises(RuntimeError) as exc:
        asyncio.run(pipeline.stt.transcribe(b"\x00" * 3200, 16000))
    assert "제미나이" in str(exc.value)

    async def speak():
        async for _ in pipeline.tts.synthesize("안녕", "happy"):
            pass

    with pytest.raises(RuntimeError) as exc:
        asyncio.run(speak())
    assert "제미나이" in str(exc.value)


def test_solar_with_server_key_makes_registrant_key_optional():
    s = load_settings({**REGISTRY_MODE, "UPSTAGE_API_KEY": "SECRET-server", "GEMINI_API_KEY": "SECRET-gem"})
    assert s.upstage_key_required is False
    assert load_settings({**REGISTRY_MODE, "GEMINI_API_KEY": "SECRET-gem"}).upstage_key_required is True


def test_register_key_fields_can_be_turned_off_when_server_has_the_keys():
    env = {"DUO_INVITE_CODE": "duo", "DUO_LLM": "openai", "OPENAI_API_KEY": "sk", "GEMINI_API_KEY": "g"}
    assert load_settings(env).register_ask_keys is True
    s = load_settings({**env, "DUO_REGISTER_ASK_KEYS": "0"})
    assert s.register_ask_keys is False
    assert s.openai_key_required is False and s.gemini_key_missing is False


def test_turning_key_fields_off_needs_the_answer_model_key_on_the_server():
    with pytest.raises(ConfigError) as exc:
        load_settings({"DUO_INVITE_CODE": "duo", "DUO_LLM": "openai", "GEMINI_API_KEY": "g", "DUO_REGISTER_ASK_KEYS": "0"})
    assert "OPENAI_API_KEY" in str(exc.value) and "DUO_REGISTER_ASK_KEYS" in str(exc.value)


def test_openai_answer_model_gets_the_openai_request_profile():
    s = load_settings({**REGISTRY_MODE, "DUO_LLM": "openai", "OPENAI_API_KEY": "SECRET-oa", "DUO_STT": "fake", "DUO_TTS": "fake"})
    assert build_pipeline(s).llm._profile == "openai"
    solar = load_settings({**REGISTRY_MODE, "DUO_CASE": "2", "UPSTAGE_API_KEY": "u", "DUO_STT": "fake", "DUO_TTS": "fake"})
    assert build_pipeline(solar).llm._profile == "generic"


def test_device_volume_setting_is_optional_and_bounded():
    assert load_settings({**ALL_KEYS}).device_volume is None
    assert load_settings({**ALL_KEYS, "DUO_DEVICE_VOLUME": "50"}).device_volume == 50
    assert load_settings({**ALL_KEYS, "DUO_DEVICE_VOLUME": ""}).device_volume is None
    for bad in ("150", "-1", "loud"):
        with pytest.raises(ConfigError):
            load_settings({**ALL_KEYS, "DUO_DEVICE_VOLUME": bad})


def test_flower_colors_come_from_env_with_a_dashboard_matching_default():
    from duo_server.config import load_settings
    base = {"DUO_STT": "fake", "DUO_LLM": "fake", "DUO_TTS": "fake", "DUO_DEVICE_TOKEN": "t"}
    assert load_settings(base).flower_colors == ("white", "red", "yellow", "purple")
    assert load_settings({**base, "DUO_FLOWER_COLORS": " White, pink ,, "}).flower_colors == ("white", "pink")
    assert load_settings({**base, "DUO_FLOWER_COLORS": " , "}).flower_colors == ("white", "red", "yellow", "purple")
