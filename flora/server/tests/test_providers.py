"""실제 AI 제공자 시험. 네트워크 없이 요청 형식과 응답 해석만 확인한다."""
import base64
import io
import json
import types
import wave

import httpx
import pytest

from duo_server.pipeline import Reply
from duo_server.providers.gemini import GeminiStt, GeminiTts, pcm_to_wav, style_for_emotion
from duo_server.providers.nvidia_asr import NvidiaAsr
from duo_server.providers.openai_chat import SYSTEM_PROMPT, OpenAiChatLlm, parse_reply, system_prompt

PCM = b"\x01\x00" * 16000


# ---------------------------------------------------------------- 답변 해석

def test_parse_plain_json():
    assert parse_reply('{"text": "안녕하세요!", "emotion": "happy"}') == Reply("안녕하세요!", "happy")


def test_parse_json_inside_code_fence_or_extra_text():
    assert parse_reply('```json\n{"text": "네", "emotion": "sad"}\n```') == Reply("네", "sad")
    assert parse_reply('답변: {"text": "네", "emotion": "sad"} 끝') == Reply("네", "sad")


def test_parse_drops_thinking_block():
    content = '<think>사용자가 인사함. {"x": 1}</think>\n{"text": "반가워요", "emotion": "happy"}'
    assert parse_reply(content) == Reply("반가워요", "happy")


def test_parse_non_json_falls_back_to_plain_text():
    assert parse_reply("그냥 글로 답했어요") == Reply("그냥 글로 답했어요", "neutral")


def test_parse_json_without_text_falls_back():
    assert parse_reply('{"emotion": "happy"}') == Reply("", "happy")
    assert parse_reply("") == Reply("", "neutral")


# ---------------------------------------------------------------- OpenAI 호환 답변 (Nemotron, Solar)

def chat_transport(seen: dict, content: str = '{"text": "오후 2시예요", "emotion": "happy"}', status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "bad key"}})
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content}}]})

    return httpx.MockTransport(handler)


async def test_chat_request_shape():
    seen = {}
    llm = OpenAiChatLlm(
        name="nemotron",
        base_url="https://api.tokenfactory.nebius.com/v1/",
        api_key="KEY",
        model="nvidia/Nemotron-3_5-Lightning",
        client=httpx.AsyncClient(transport=chat_transport(seen)),
    )
    history = [{"role": "user", "content": "안녕"}, {"role": "assistant", "content": "안녕하세요"}]
    reply = await llm.reply("오늘 회의 몇 시야?", history)

    assert reply == Reply("오후 2시예요", "happy")
    assert seen["url"] == "https://api.tokenfactory.nebius.com/v1/chat/completions"
    assert seen["auth"] == "Bearer KEY"
    body = seen["body"]
    assert body["model"] == "nvidia/Nemotron-3_5-Lightning"
    assert body["response_format"] == {"type": "json_object"}
    assert [m["role"] for m in body["messages"]] == ["system", "user", "assistant", "user"]
    assert body["messages"][-1]["content"] == "오늘 회의 몇 시야?"
    assert "JSON" in body["messages"][0]["content"]
    assert llm.name == "nemotron:nvidia/Nemotron-3_5-Lightning"


async def test_chat_base_url_without_trailing_slash():
    seen = {}
    llm = OpenAiChatLlm(
        name="solar", base_url="https://api.upstage.ai/v1", api_key="K", model="solar-mini4",
        client=httpx.AsyncClient(transport=chat_transport(seen)),
    )
    await llm.reply("안녕", [])
    assert seen["url"] == "https://api.upstage.ai/v1/chat/completions"


async def test_chat_extra_body_is_merged():
    seen = {}
    llm = OpenAiChatLlm(
        name="solar", base_url="https://x/v1", api_key="K", model="m",
        client=httpx.AsyncClient(transport=chat_transport(seen)),
        extra_body={"reasoning_effort": "low"},
    )
    await llm.reply("안녕", [])
    assert seen["body"]["reasoning_effort"] == "low"


async def test_chat_sends_past_replies_to_model_in_json_shape():
    # 지난 답을 글만 넣으면 Nemotron이 다음 JSON에서 "text" 열쇠를 빠뜨린다 (2026-10-29 실측: {":": "츄르"})
    seen = {}
    llm = OpenAiChatLlm(
        name="nemotron", base_url="https://x/v1", api_key="K", model="m",
        client=httpx.AsyncClient(transport=chat_transport(seen)),
    )
    history = [
        {"role": "user", "content": "안녕"},
        {"role": "assistant", "content": "두 번째 대화입니다."},
        {"role": "user", "content": "또 안녕"},
        {"role": "assistant", "content": '{"text": "네!", "emotion": "happy"}'},
    ]
    await llm.reply("간식 하나만", history)
    sent = seen["body"]["messages"]
    assert json.loads(sent[2]["content"]) == {"text": "두 번째 대화입니다.", "emotion": "neutral"}
    assert sent[4]["content"] == '{"text": "네!", "emotion": "happy"}'  # 이미 JSON이면 그대로
    assert sent[1]["content"] == "안녕" and sent[3]["content"] == "또 안녕"  # 사용자 말은 손대지 않음
    assert history[1]["content"] == "두 번째 대화입니다."  # 저장된 기억은 바꾸지 않음


def test_parse_json_with_broken_text_key_uses_the_other_string():
    # 열쇠 이름이 망가진 답에서도 글은 건진다
    assert parse_reply('{":": "츄르", "emotion": "happy"}') == Reply("츄르", "happy")
    assert parse_reply('{":": "츄르"}') == Reply("츄르", "neutral")
    assert parse_reply('{"emotion": "happy"}') == Reply("", "happy")  # 글이 아예 없으면 그대로 빈 답


def test_parse_reply_with_flower_recommendation():
    content = '{"text": "노란 프리지아 어때요?", "emotion": "happy", "flower": {"name": "프리지아", "color": "yellow"}}'
    assert parse_reply(content) == Reply("노란 프리지아 어때요?", "happy", "프리지아", "yellow")


def test_parse_reply_flower_color_outside_robot_palette_is_dropped():
    content = '{"text": "파란 장미요", "emotion": "happy", "flower": {"name": "장미", "color": "blue"}}'
    assert parse_reply(content) == Reply("파란 장미요", "happy", "장미", "")
    assert parse_reply('{"text": "안녕", "emotion": "happy", "flower": null}') == Reply("안녕", "happy")
    assert parse_reply('{"text": "안녕", "emotion": "happy"}') == Reply("안녕", "happy")


def test_system_prompt_is_a_cat_florist_with_the_robot_palette():
    for word in ("꽃", "white", "red", "yellow", "purple", "JSON", "flower"):
        assert word in SYSTEM_PROMPT
    assert "듀오" not in SYSTEM_PROMPT


def test_color_is_inferred_from_the_spoken_text_when_the_flower_field_is_missing():
    # 모델이 말로는 "노란 튤립"을 추천하면서 flower 칸을 비우는 일이 실측에서 나왔다 (2026-10-29)
    assert parse_reply('{"text": "기쁨을 나누기에 좋은 노란 튤립을 추천해.", "emotion": "happy", "flower": null}').flower_color == "yellow"
    assert parse_reply('{"text": "붉은 장미가 제격이야.", "emotion": "loving"}').flower_color == "red"
    assert parse_reply('{"text": "자줏빛 백합을 추천해.", "emotion": "neutral"}').flower_color == "purple"
    assert parse_reply('{"text": "하얀 안개꽃 어때요?", "emotion": "sad"}').flower_color == "white"
    assert parse_reply('{"text": "오늘 정말 덥네요.", "emotion": "neutral"}').flower_color == ""
    # 모델이 색을 적었으면 글보다 그 값을 믿는다
    assert parse_reply('{"text": "노란 꽃도 좋지만 흰 백합으로 해요.", "emotion": "neutral", "flower": {"name": "백합", "color": "white"}}').flower_color == "white"


def test_system_prompt_names_flora_and_shows_example_answers():
    assert "플로라" in SYSTEM_PROMPT and "푸쉰" not in SYSTEM_PROMPT and "듀오" not in SYSTEM_PROMPT
    assert "예시" in SYSTEM_PROMPT and '"flower": {"name":' in SYSTEM_PROMPT and '"flower": null' in SYSTEM_PROMPT
    # 실측(2026-10-29 16:50): 앞 턴에서 꽃을 추천한 뒤 날씨 잡담에도 꽃을 추천함 → 잡담 규칙과 날씨 예시를 명시
    assert "잡담" in SYSTEM_PROMPT and "날씨" in SYSTEM_PROMPT and "앞에서 꽃을 추천했더라도" in SYSTEM_PROMPT


def test_flower_palette_is_configurable_in_prompt_and_parser():
    # 네 색은 대시보드(CoFlorist)의 꽃 배치 목록에서 온 기본값이고, 꽃집이 바뀌면 설정으로 바꾼다
    prompt = system_prompt(("white", "pink"))
    assert "pink(분홍색)" in prompt and "white|pink" in prompt and "yellow" not in prompt
    assert system_prompt(("white", "red", "yellow", "purple")) == SYSTEM_PROMPT
    reply = parse_reply('{"text": "분홍 장미 어때요?", "emotion": "loving", "flower": {"name": "장미", "color": "pink"}}', colors=("white", "pink"))
    assert reply.flower_color == "pink"
    assert parse_reply('{"text": "x", "emotion": "happy", "flower": {"name": "장미", "color": "pink"}}').flower_color == ""  # 기본 목록엔 없음


async def test_chat_uses_the_configured_palette():
    seen = {}
    llm = OpenAiChatLlm(
        name="nemotron", base_url="https://x/v1", api_key="K", model="m",
        client=httpx.AsyncClient(transport=chat_transport(seen, content='{"text": "분홍 장미요", "emotion": "loving", "flower": {"name": "장미", "color": "pink"}}')),
        flower_colors=("white", "pink"),
    )
    reply = await llm.reply("고백할 꽃", [])
    assert reply.flower_color == "pink"
    assert "pink" in seen["body"]["messages"][0]["content"]


async def test_chat_http_error_is_raised_without_leaking_key():
    llm = OpenAiChatLlm(
        name="solar", base_url="https://x/v1", api_key="SECRET-KEY", model="m",
        client=httpx.AsyncClient(transport=chat_transport({}, status=401)),
    )
    with pytest.raises(RuntimeError) as info:
        await llm.reply("안녕", [])
    assert "401" in str(info.value)
    assert "SECRET-KEY" not in str(info.value)


# ---------------------------------------------------------------- Gemini

class FakeInteractions:
    def __init__(self, result):
        self.result = result
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return self.result


def fake_client(result):
    interactions = FakeInteractions(result)
    client = types.SimpleNamespace(aio=types.SimpleNamespace(interactions=interactions))
    return client, interactions


def test_pcm_to_wav_is_valid_wav():
    with wave.open(io.BytesIO(pcm_to_wav(PCM, 16000)), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (1, 2, 16000, 16000)


async def test_gemini_stt_request_shape():
    client, interactions = fake_client(types.SimpleNamespace(output_text=" 오늘 회의 몇 시야? "))
    stt = GeminiStt(client, model="gemini-3.5-transcribe", language="ko-KR")
    assert await stt.transcribe(PCM, 16000) == " 오늘 회의 몇 시야? "

    kwargs = interactions.kwargs
    assert kwargs["model"] == "gemini-3.5-transcribe"
    assert kwargs["generation_config"] == {"transcription_config": {"language_codes": ["ko-KR"]}}
    (item,) = kwargs["input"]
    assert item["type"] == "audio" and item["mime_type"] == "audio/wav"
    assert base64.b64decode(item["data"])[:4] == b"RIFF"
    assert stt.name == "gemini:gemini-3.5-transcribe"


async def test_gemini_stt_without_text_returns_empty():
    client, _ = fake_client(types.SimpleNamespace(output_text=None))
    assert await GeminiStt(client).transcribe(PCM, 16000) == ""


def delta(kind, data=b""):
    return types.SimpleNamespace(
        event_type="step.delta",
        delta=types.SimpleNamespace(type=kind, data=base64.b64encode(data).decode("ascii")),
    )


async def stream_of(events):
    for event in events:
        yield event


async def test_gemini_tts_request_shape_and_audio_chunks():
    events = [
        types.SimpleNamespace(event_type="step.start"),
        delta("audio", b"\x01\x00\x02\x00"),
        delta("text", b"ignored"),
        delta("audio", b"\x03\x00"),
        types.SimpleNamespace(event_type="interaction.complete"),
    ]
    client, interactions = fake_client(stream_of(events))
    tts = GeminiTts(client, model="gemini-3.8-flash-tts", voice="Kore")
    chunks = [chunk async for chunk in tts.synthesize("오후 2시예요", "happy")]

    assert chunks == [b"\x01\x00\x02\x00", b"\x03\x00"]
    kwargs = interactions.kwargs
    assert kwargs["model"] == "gemini-3.8-flash-tts"
    assert kwargs["stream"] is True
    assert kwargs["response_format"] == {"type": "audio"}
    assert kwargs["generation_config"] == {"speech_config": [{"voice": "Kore"}]}
    (turn,) = kwargs["input"]
    assert turn["type"] == "user_input"
    (part,) = turn["content"]
    assert part["type"] == "text" and part["text"] == "오후 2시예요"
    assert part["annotations"] == [{"type": "speech_metadata", "style": style_for_emotion("happy")}]
    assert tts.name == "gemini:gemini-3.8-flash-tts"


def test_every_allowed_emotion_has_a_speaking_style():
    from duo_server.protocol import ALLOWED_EMOTIONS

    for emotion in ALLOWED_EMOTIONS:
        assert style_for_emotion(emotion), emotion
    assert style_for_emotion("unknown") == style_for_emotion("neutral")


# ---------------------------------------------------------------- NVIDIA 음성 인식

class FakeAsrService:
    def __init__(self, responses):
        self.responses = responses
        self.chunks = None
        self.config = None

    def streaming_response_generator(self, audio_chunks, streaming_config):
        self.chunks = list(audio_chunks)
        self.config = streaming_config
        return iter(self.responses)


def asr_response(text, final=True):
    alternative = types.SimpleNamespace(transcript=text)
    result = types.SimpleNamespace(is_final=final, alternatives=[alternative])
    return types.SimpleNamespace(results=[result])


async def test_nvidia_asr_joins_final_results_and_sets_korean():
    service = FakeAsrService([asr_response("오늘", final=False), asr_response("오늘 회의 "), asr_response("몇 시야?")])
    asr = NvidiaAsr(service, language="ko-KR", model_name="nemotron-asr-streaming")
    assert await asr.transcribe(PCM, 16000) == "오늘 회의 몇 시야?"

    assert b"".join(service.chunks) == PCM
    assert all(len(chunk) <= 3200 for chunk in service.chunks)
    config = service.config.config
    assert config.language_code == "ko-KR"
    assert config.sample_rate_hertz == 16000
    assert config.audio_channel_count == 1
    assert service.config.interim_results is False
    assert asr.name == "nvidia:nemotron-asr-streaming"


async def test_nvidia_asr_no_result_returns_empty():
    assert await NvidiaAsr(FakeAsrService([]), language="ko-KR").transcribe(PCM, 16000) == ""


# ---------------------------------------------------------------- OpenAI(gpt-5 계열)는 요청 모양이 다름

async def test_openai_profile_uses_max_completion_tokens_and_no_temperature():
    """gpt-5.6은 max_tokens를 거부하고(max_completion_tokens만 받음) temperature도 기본값만 받는다 (2026-10-21 실측)."""
    seen = {}
    llm = OpenAiChatLlm(
        name="openai", base_url="https://api.openai.com/v1", api_key="K", model="gpt-5.6",
        client=httpx.AsyncClient(transport=chat_transport(seen)), profile="openai",
    )
    await llm.reply("안녕", [])
    body = seen["body"]
    assert "max_tokens" not in body and body["max_completion_tokens"] >= 300
    assert "temperature" not in body
    assert body["response_format"] == {"type": "json_object"}


async def test_default_profile_keeps_max_tokens_for_solar_and_nemotron():
    seen = {}
    llm = OpenAiChatLlm(
        name="solar", base_url="https://api.upstage.ai/v1", api_key="K", model="solar-mini4",
        client=httpx.AsyncClient(transport=chat_transport(seen)),
    )
    await llm.reply("안녕", [])
    assert seen["body"]["max_tokens"] == 300 and seen["body"]["temperature"] == 0.7
    assert "max_completion_tokens" not in seen["body"]
