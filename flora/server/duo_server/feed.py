"""대시보드가 구독하는 대화 이벤트 피드 (Server-Sent Events).

CoFlorist의 '외부 대화' 약속을 따른다: 이벤트는 state와 message 두 종류뿐이고, 소리·키는 싣지 않는다.
  {"id", "type": "state", "state": "idle|listening|thinking|speaking|error"}
  {"id", "type": "message", "role": "user|assistant", "text", ...}
assistant message에는 로봇·초파리 회로용 필드를 더 싣는다: flower, flower_color, emotion, stimulus_hz.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator

FEED_STATES = ("idle", "listening", "thinking", "speaking", "error")
ROLES = ("user", "assistant")
MAX_TEXT = 2000
MAX_ID = 160

# 대화 → 초파리 당 감각 뉴런 자극 세기(Hz). docs/2026-10-29-flora-fly-brain-design.md 2절 표.
STIMULUS_NO_FLOWER = 20
STIMULUS_DEFAULT = 90  # 감사·차분 (neutral 등, 꽃 있음)
STIMULUS_BY_EMOTION = {
    "loving": 150, "happy": 120, "surprised": 120, "winking": 120,
    "sad": 60, "confused": 40, "sleepy": 40, "angry": 40,
}


def stimulus_hz_for(emotion: str, flower_color: str) -> int:
    """꽃이 없으면 거의 조용(20Hz), 있으면 감정에 따라. 이 매핑은 앱이 정한 규칙이다."""
    if not flower_color:
        return STIMULUS_NO_FLOWER
    return STIMULUS_BY_EMOTION.get(emotion or "", STIMULUS_DEFAULT)


def format_event(event: dict) -> str:
    return "id: %s\ndata: %s\n\n" % (event["id"], json.dumps(event, ensure_ascii=False))


class ConversationFeed:
    def __init__(self, keep: int = 500) -> None:
        self._keep = keep
        self._history: list[dict] = []
        self._subscribers: set[asyncio.Queue] = set()

    # ------------------------------------------------------------ 쓰기
    def publish(self, event: dict) -> dict | None:
        """약속에 맞는지 확인하고 기록·방송한다. 같은 id가 이미 있으면 None."""
        if not isinstance(event, dict):
            raise ValueError("이벤트는 JSON 객체여야 합니다")
        kind = event.get("type")
        if kind == "state":
            if event.get("state") not in FEED_STATES:
                raise ValueError("state는 %s 중 하나여야 합니다" % ", ".join(FEED_STATES))
        elif kind == "message":
            text = event.get("text")
            if event.get("role") not in ROLES or not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
                raise ValueError("message에는 role(user|assistant)과 1~%d자 text가 필요합니다" % MAX_TEXT)
        else:
            raise ValueError("type은 state 또는 message여야 합니다")
        event_id = event.get("id")
        if event_id is None:
            event_id = uuid.uuid4().hex
        elif not isinstance(event_id, str) or not event_id.strip() or len(event_id) > MAX_ID:
            raise ValueError("id는 1~%d자 문자열이어야 합니다" % MAX_ID)
        if any(item["id"] == event_id for item in self._history):
            return None
        stored = {**event, "id": event_id}
        self._history.append(stored)
        del self._history[: max(0, len(self._history) - self._keep)]
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(stored)
            except asyncio.QueueFull:
                pass  # 느린 구독자는 놓친 만큼 Last-Event-ID로 다시 받는다
        return stored

    def state(self, state: str) -> dict | None:
        return self.publish({"type": "state", "state": state})

    def message(self, role: str, text: str, **extra) -> dict | None:
        return self.publish({"type": "message", "role": role, "text": text, **extra})

    # ------------------------------------------------------------ 읽기
    def replay(self, last_id: str | None = None) -> list[dict]:
        """last_id 다음부터. 모르는 id면 처음부터."""
        if last_id:
            for index, item in enumerate(self._history):
                if item["id"] == last_id:
                    return list(self._history[index + 1:])
        return list(self._history)

    async def stream(self, last_id: str | None = None, *, once: bool = False, keepalive: float = 15.0) -> AsyncIterator[str]:
        for item in self.replay(last_id):
            yield format_event(item)
        if once:
            return
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers.add(queue)
        try:
            while True:
                try:
                    yield format_event(await asyncio.wait_for(queue.get(), keepalive))
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            self._subscribers.discard(queue)
