"""대화 이벤트 피드: 대시보드(CoFlorist 외부 대화)가 구독하는 SSE 스트림의 약속."""
import json

import pytest

from duo_server.feed import ConversationFeed, format_event, stimulus_hz_for


def test_publish_assigns_an_id_and_keeps_history():
    feed = ConversationFeed()
    first = feed.publish({"type": "state", "state": "listening"})
    second = feed.publish({"type": "message", "role": "user", "text": "안녕"})
    assert first["id"] and second["id"] and first["id"] != second["id"]
    assert [e["type"] for e in feed.replay()] == ["state", "message"]


def test_publish_rejects_events_outside_the_dashboard_contract():
    feed = ConversationFeed()
    for bad in (
        {"type": "state", "state": "dancing"},
        {"type": "message", "role": "robot", "text": "x"},
        {"type": "message", "role": "user", "text": ""},
        {"type": "message", "role": "user", "text": "x" * 2001},
        {"type": "command", "text": "x"},
    ):
        with pytest.raises(ValueError):
            feed.publish(bad)
    assert feed.replay() == []


def test_duplicate_ids_are_ignored_and_history_is_bounded():
    feed = ConversationFeed(keep=3)
    feed.publish({"id": "a", "type": "state", "state": "idle"})
    assert feed.publish({"id": "a", "type": "state", "state": "idle"}) is None
    for i in range(5):
        feed.publish({"type": "state", "state": "idle"})
    assert len(feed.replay()) == 3


def test_replay_resumes_after_the_last_seen_id():
    feed = ConversationFeed()
    a = feed.publish({"type": "state", "state": "listening"})
    b = feed.publish({"type": "message", "role": "user", "text": "안녕"})
    c = feed.publish({"type": "state", "state": "thinking"})
    assert [e["id"] for e in feed.replay(a["id"])] == [b["id"], c["id"]]
    assert [e["id"] for e in feed.replay("unknown")] == [a["id"], b["id"], c["id"]]


def test_assistant_message_carries_flower_fields_for_the_robot():
    feed = ConversationFeed()
    event = feed.message("assistant", "흰 백합 어때요?", flower="백합", flower_color="white", emotion="sad", stimulus_hz=60)
    assert event["flower"] == "백합" and event["flower_color"] == "white" and event["emotion"] == "sad" and event["stimulus_hz"] == 60
    assert "flower" not in feed.message("user", "위로가 필요해")


def test_format_event_is_server_sent_events_with_the_id_line():
    event = {"id": "abc", "type": "message", "role": "user", "text": "안녕"}
    assert format_event(event) == "id: abc\ndata: " + json.dumps(event, ensure_ascii=False) + "\n\n"


async def test_stream_once_replays_history_then_ends():
    feed = ConversationFeed()
    feed.publish({"type": "state", "state": "listening"})
    feed.publish({"type": "message", "role": "user", "text": "안녕"})
    chunks = [chunk async for chunk in feed.stream(once=True)]
    assert len(chunks) == 2 and chunks[0].startswith("id: ") and '"listening"' in chunks[0]


def test_stimulus_hz_follows_the_design_table():
    # 설계 문서 2절: 사랑 150, 기쁨 120, 감사·차분 90, 위로 60, 혼란·졸림 40, 꽃 없음 20
    assert stimulus_hz_for("loving", "red") == 150
    assert stimulus_hz_for("happy", "yellow") == 120
    assert stimulus_hz_for("surprised", "yellow") == 120
    assert stimulus_hz_for("neutral", "white") == 90
    assert stimulus_hz_for("neutral", "purple") == 90
    assert stimulus_hz_for("sad", "white") == 60
    assert stimulus_hz_for("confused", "white") == 40
    assert stimulus_hz_for("sleepy", "purple") == 40
    assert stimulus_hz_for("happy", "") == 20  # 꽃 없는 잡담
    assert stimulus_hz_for("", "") == 20
