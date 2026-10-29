"""답변 생성: OpenAI 호환 방식 (Nebius의 Nemotron, Upstage의 Solar).

두 서비스는 주소·키·모델 이름만 다르고 호출 방식이 같다.
"""
from __future__ import annotations

import json
import re

import httpx

from ..pipeline import FLOWER_COLORS, Reply, infer_flower_color, normalize_flower_color

_COLOR_KO = {"white": "흰색", "red": "빨간색", "yellow": "노란색", "purple": "보라색",
             "pink": "분홍색", "blue": "파란색", "orange": "주황색", "green": "초록색"}
_COLOR_MOOD = (("yellow", "기쁨과 응원"), ("red", "사랑과 열정"), ("white", "위로와 순수와 감사"), ("purple", "차분함과 품위"),
               ("pink", "다정함과 설렘"), ("blue", "평온과 신뢰"), ("orange", "활기와 격려"), ("green", "안정과 새 출발"))
_EXAMPLES_BY_COLOR = {
    "white": ("시험에 떨어져서 우울해.",
              '{"text": "많이 속상했겠어요. 위로의 마음을 담은 흰 백합 어때요?", "emotion": "sad", "flower": {"name": "백합", "color": "white"}}'),
    "yellow": ("친구가 승진했어! 축하해 주고 싶어.",
               '{"text": "축하할 일이네요. 기쁨을 나누는 노란 튤립을 추천해요.", "emotion": "happy", "flower": {"name": "튤립", "color": "yellow"}}'),
}


def system_prompt(colors=FLOWER_COLORS) -> str:
    """플로라의 지시문. 색 목록은 로봇이 집을 수 있는 꽃(기본값은 CoFlorist 배치 목록 네 가지)에서 온다."""
    colors = tuple(c.strip().lower() for c in colors if c and c.strip())
    listed = ", ".join("%s(%s)" % (c, _COLOR_KO[c]) if c in _COLOR_KO else c for c in colors)
    moods = ", ".join("%s은 %s" % (mood, c) for c, mood in _COLOR_MOOD if c in colors)
    mood_line = ("\n  " + moods + " 쪽으로.") if moods else ""
    examples = [
        *(_EXAMPLES_BY_COLOR[c] for c in ("white", "yellow") if c in colors),
        ("안녕, 너 누구야?", '{"text": "저는 꽃집 고양이 플로라예요. 오늘 기분이 어때요?", "emotion": "happy", "flower": null}'),
        ("그냥 날씨 얘기나 하자. 오늘 덥다.", '{"text": "정말 덥네요. 시원한 물 많이 드세요.", "emotion": "neutral", "flower": null}'),
    ]
    example_text = "\n".join("손님: %s\n답: %s" % pair for pair in examples)
    return (
        "너는 꽃집에서 일하는 고양이 플로리스트 '플로라'야. 손님의 말을 듣고 그 기분과 상황에 어울리는 꽃을 한 가지 골라 추천해.\n"
        "\n규칙\n"
        "- 한국어 해요체로, 한두 문장만 말해. 답은 소리로 읽히니까 짧을수록 좋아. 반말은 쓰지 마.\n"
        "- 이모지, 특수기호, 마크다운은 쓰지 마. 너를 로봇이나 AI 모델이라고 소개하지 마.\n"
        "- 꽃을 추천할 때는 꽃 이름과 색을 말로도 꼭 말하고, 같은 꽃을 flower 칸에도 똑같이 적어.\n"
        "- 색은 우리 꽃집에 있는 색 중에서만 골라: " + listed + "." + mood_line + "\n"
        "- 인사, 날씨, 농담 같은 잡담에는 꽃을 추천하지 말고 flower를 null로 두고 짧게 대답해. 앞에서 꽃을 추천했더라도 이번 말이 잡담이면 꽃 없이 답해.\n"
        "- 모르는 것은 모른다고 말해. 없는 사실을 지어내지 마.\n"
        "- 아래 JSON 형식으로만 답해. 다른 글은 붙이지 마.\n"
        '  {"text": "할 말", "emotion": "감정", "flower": {"name": "꽃 이름", "color": "' + "|".join(colors) + '"}}\n'
        '  꽃이 없으면 "flower": null\n'
        "- emotion은 다음 중 하나만 써: neutral, happy, sad, angry, surprised, loving, winking, sleepy, confused\n"
        "\n예시\n" + example_text + "\n"
    )


SYSTEM_PROMPT = system_prompt(FLOWER_COLORS)

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)


def _first_json_object(text: str) -> dict | None:
    decoder = json.JSONDecoder()
    for start, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[start:])
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def parse_reply(content: str, colors=FLOWER_COLORS) -> Reply:
    """모델 출력에서 답변 글과 감정을 꺼낸다. JSON이 아니면 글 전체를 답변으로 본다."""
    cleaned = _THINK_BLOCK.sub("", content or "").strip()
    data = _first_json_object(cleaned)
    if data is None:
        return Reply(cleaned, "neutral")
    text = data.get("text")
    if not isinstance(text, str):
        # 열쇠 이름이 망가진 답({":": "츄르"} 같은 것, Nemotron 2026-10-29 실측)에서도 글은 건진다
        others = [v for k, v in data.items() if k != "emotion" and isinstance(v, str) and v.strip()]
        text = others[0] if others else ""
    emotion = data.get("emotion")
    flower = data.get("flower")
    name = flower.get("name") if isinstance(flower, dict) else None
    return Reply(
        text.strip() if isinstance(text, str) else "",
        emotion.strip() if isinstance(emotion, str) and emotion.strip() else "neutral",
        name.strip() if isinstance(name, str) else "",
        (normalize_flower_color(flower.get("color"), colors) if isinstance(flower, dict) else "")
        or infer_flower_color(text if isinstance(text, str) else ""),
    )


def history_for_model(history: list[dict]) -> list[dict]:
    """지난 답을 모델이 낸 JSON 모양 그대로 보여 준다.

    글만 넣으면 Nemotron이 다음 답에서 "text" 열쇠를 빠뜨린다 (2026-10-29 실측). 저장된 기억은 바꾸지 않는다.
    """
    shaped = []
    for message in history:
        content = message.get("content")
        if message.get("role") == "assistant" and isinstance(content, str) and _first_json_object(content) is None:
            content = json.dumps({"text": content, "emotion": "neutral"}, ensure_ascii=False)
            message = {**message, "content": content}
        shaped.append(message)
    return shaped


class OpenAiChatLlm:
    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: str,
        model: str,
        *,
        client: httpx.AsyncClient | None = None,
        extra_body: dict | None = None,
        max_tokens: int = 300,
        temperature: float = 0.7,
        profile: str = "generic",
        flower_colors=FLOWER_COLORS,
    ) -> None:
        """profile: 'generic'(솔라·네모트론 등 OpenAI 호환 서비스) 또는 'openai'(gpt-5 계열).

        gpt-5.6은 max_tokens를 거부하고 max_completion_tokens만 받으며, temperature는 기본값(1)만 허용한다 (2026-10-21 실측).
        """
        self.name = "%s:%s" % (name, model)
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._api_key = api_key
        self._model = model
        self._client = client or httpx.AsyncClient(timeout=30.0)
        self._extra_body = extra_body or {}
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._profile = profile
        self._flower_colors = tuple(flower_colors)

    async def reply(self, user_text: str, history: list[dict]) -> Reply:
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt(self._flower_colors)},
                *history_for_model(history),
                {"role": "user", "content": user_text},
            ],
            "response_format": {"type": "json_object"},
        }
        if self._profile == "openai":
            body["max_completion_tokens"] = self._max_tokens
        else:
            body["max_tokens"] = self._max_tokens
            body["temperature"] = self._temperature
        body.update(self._extra_body)
        response = await self._client.post(
            self._url, json=body, headers={"Authorization": "Bearer " + self._api_key}
        )
        if response.status_code != 200:
            raise RuntimeError("답변 서비스 오류 %d: %s" % (response.status_code, response.text[:200]))
        content = response.json()["choices"][0]["message"].get("content") or ""
        return parse_reply(content, self._flower_colors)
