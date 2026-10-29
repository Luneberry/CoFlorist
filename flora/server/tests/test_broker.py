"""중개 확인: 등록한 사람마다 그 사람의 키로 바깥 AI 서비스에 요청이 나가는지 (서버 키·남의 키가 섞이지 않는지)."""
import json
import types

import httpx
import pytest
from fastapi.testclient import TestClient
from test_app import HEADERS, speech_packets, talk

from duo_server.app import create_app
from duo_server.config import build_registrant_factory, load_settings
from duo_server.providers import openai_chat
from duo_server.registry import Registration, Registry

INVITE = "duo-2026"
ENV = {"DUO_INVITE_CODE": INVITE, "DUO_LLM": "openai", "DUO_STT": "gemini", "DUO_TTS": "gemini"}


def answer_with_key(seen: list[dict]):
    """가짜 OpenAI: 어떤 키로 왔는지 답에 적어 돌려주고, 본 것을 기록한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        key = request.headers["authorization"].removeprefix("Bearer ")
        body = json.loads(request.content)
        seen.append({"key": key, "user": body["messages"][-1]["content"]})
        content = json.dumps({"text": "키 %s로 답함" % key, "emotion": "happy"})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    return handler


def make_app(tmp_path, monkeypatch, env):
    seen: list[dict] = []
    transport = httpx.MockTransport(answer_with_key(seen))
    # openai_chat 모듈이 보는 httpx 이름만 바꾼다 (다른 모듈의 httpx는 그대로)
    fake_httpx = types.SimpleNamespace(AsyncClient=lambda **kw: httpx.AsyncClient(transport=transport, **kw))
    monkeypatch.setattr(openai_chat, "httpx", fake_httpx)
    settings = load_settings(env)
    registry = Registry(tmp_path / "reg.json")
    app = create_app(
        registry=registry, invite_code=INVITE, legacy_routes=False, pace_audio=False,
        pipeline_factory=build_registrant_factory(settings),
        gemini_key_missing=settings.gemini_key_missing, upstage_key_required=settings.upstage_key_required,
        openai_key_required=settings.openai_key_required, metrics_path=tmp_path / "turns.jsonl",
    )
    return TestClient(app), registry, seen


def register(client, name, openai_key, gemini_key=""):
    page = client.post("/register", data={
        "name": name, "invite": INVITE, "upstage_key": "", "openai_key": openai_key, "gemini_key": gemini_key,
    })
    assert page.status_code == 200
    return page.text.split("/d/")[1].split("/")[0]


def test_each_persons_chat_goes_out_with_their_own_openai_key(tmp_path, monkeypatch):
    client, _, seen = make_app(tmp_path, monkeypatch, ENV)
    a = register(client, "A", "SECRET-A")
    b = register(client, "B", "SECRET-B")
    assert client.post("/d/%s/chat" % a, json={"text": "A의 질문"}).json()["reply"] == "키 SECRET-A로 답함"
    assert client.post("/d/%s/chat" % b, json={"text": "B의 질문"}).json()["reply"] == "키 SECRET-B로 답함"
    assert client.post("/d/%s/chat" % a, json={"text": "A의 둘째 질문"}).json()["reply"] == "키 SECRET-A로 답함"
    assert [(s["key"], s["user"]) for s in seen] == [
        ("SECRET-A", "A의 질문"), ("SECRET-B", "B의 질문"), ("SECRET-A", "A의 둘째 질문"),
    ]


def test_persons_key_beats_server_key_and_server_key_is_only_a_fallback(tmp_path, monkeypatch):
    client, _, seen = make_app(tmp_path, monkeypatch, {**ENV, "OPENAI_API_KEY": "SECRET-server"})
    a = register(client, "A", "SECRET-A")
    c = register(client, "C", "")  # 키를 안 낸 사람 → 서버 키
    client.post("/d/%s/chat" % a, json={"text": "A"})
    client.post("/d/%s/chat" % c, json={"text": "C"})
    client.post("/d/%s/chat" % a, json={"text": "A 다시"})
    assert [s["key"] for s in seen] == ["SECRET-A", "SECRET-server", "SECRET-A"]


def test_device_voice_path_also_uses_the_devices_owners_key(tmp_path, monkeypatch):
    """기기(웹소켓) 경로도 글 대화와 같은 사람별 진행기를 쓴다. 음성 단계는 가짜로 두고 답변 키만 본다."""
    client, registry, seen = make_app(tmp_path, monkeypatch, {**ENV, "DUO_STT": "fake", "DUO_TTS": "fake"})
    a = register(client, "A", "SECRET-A")
    b = register(client, "B", "SECRET-B")
    said = []
    for token in (a, b):
        reg = registry.get(token)
        headers = {**HEADERS, "Authorization": "Bearer " + reg.access_token}
        with client.websocket_connect("/d/%s/xiaozhi/v1/" % token, headers=headers) as ws:
            _, messages, _ = talk(ws, speech_packets())
        said += [m["text"] for m in messages if m["type"] == "tts" and m["state"] == "sentence_start"]
    assert said == ["키 SECRET-A로 답함", "키 SECRET-B로 답함"]
    assert [s["key"] for s in seen] == ["SECRET-A", "SECRET-B"]


def test_each_persons_gemini_client_is_built_with_their_own_key():
    factory = build_registrant_factory(load_settings(ENV))
    a = factory(Registration(token="a", access_token="x", name="A", openai_key="SECRET-A", gemini_key="SECRET-gem-A"))
    b = factory(Registration(token="b", access_token="y", name="B", openai_key="SECRET-B", gemini_key="SECRET-gem-B"))
    c = factory(Registration(token="c", access_token="z", name="C", openai_key="SECRET-C"))
    assert a.stt._client._api_client.api_key == "SECRET-gem-A"
    assert a.tts._client._api_client.api_key == "SECRET-gem-A"
    assert b.stt._client._api_client.api_key == "SECRET-gem-B"
    assert a.stt._client is not b.stt._client
    assert c.stt.name.endswith("(키 없음)") and c.tts.name.endswith("(키 없음)")  # 남의 키를 빌려 쓰지 않음
    assert a.llm._api_key == "SECRET-A" and b.llm._api_key == "SECRET-B" and c.llm._api_key == "SECRET-C"
