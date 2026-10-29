"""기기 등록 장부 시험: 등록한 사람의 키와 기기 번호를 파일에 남긴다."""
import json
import os
import stat

from duo_server.registry import Registration, Registry, masked


def test_create_makes_two_distinct_secrets(tmp_path):
    registry = Registry(tmp_path / "data" / "reg.json")
    reg = registry.create("민수", "SECRET-upstage", "SECRET-gemini")
    assert isinstance(reg, Registration)
    assert len(reg.token) >= 16 and len(reg.access_token) >= 16
    assert reg.token != reg.access_token
    assert set(reg.token) <= set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
    assert reg.name == "민수" and reg.upstage_key == "SECRET-upstage" and reg.gemini_key == "SECRET-gemini"
    assert reg.created


def test_get_by_token_and_unknown_is_none(tmp_path):
    registry = Registry(tmp_path / "reg.json")
    reg = registry.create("민수", "SECRET-upstage")
    assert registry.get(reg.token) is reg
    assert registry.get("nope") is None
    assert registry.get("") is None


def test_registrations_survive_restart_and_file_is_private(tmp_path):
    path = tmp_path / "data" / "reg.json"
    first = Registry(path)
    reg = first.create("민수", "SECRET-upstage", "")
    first.note_device(reg.token, "aa:bb:cc:dd:ee:01")

    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600

    second = Registry(path)
    loaded = second.get(reg.token)
    assert loaded is not None
    assert loaded.access_token == reg.access_token
    assert loaded.upstage_key == "SECRET-upstage"
    assert loaded.devices == ["aa:bb:cc:dd:ee:01"]
    assert json.loads(path.read_text())["registrations"][0]["name"] == "민수"


def test_note_device_keeps_each_device_once(tmp_path):
    registry = Registry(tmp_path / "reg.json")
    reg = registry.create("민수", "SECRET-upstage")
    registry.note_device(reg.token, "aa:bb:cc:dd:ee:01")
    registry.note_device(reg.token, "aa:bb:cc:dd:ee:01")
    registry.note_device(reg.token, "aa:bb:cc:dd:ee:02")
    registry.note_device("unknown", "aa:bb:cc:dd:ee:03")
    assert reg.devices == ["aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:02"]


def test_all_lists_in_creation_order(tmp_path):
    registry = Registry(tmp_path / "reg.json")
    a = registry.create("a", "SECRET-1")
    b = registry.create("b", "SECRET-2")
    assert [r.token for r in registry.all()] == [a.token, b.token]


def test_masked_hides_the_middle():
    assert masked("up_1234567890abcdef") == "up_1…ef"
    assert masked("short") == "…"
    assert masked("") == ""


def test_openai_key_is_kept_and_old_files_without_it_still_load(tmp_path):
    path = tmp_path / "reg.json"
    reg = Registry(path).create("민수", "", "", openai_key="SECRET-oa")
    assert reg.openai_key == "SECRET-oa"
    assert Registry(path).get(reg.token).openai_key == "SECRET-oa"

    data = json.loads(path.read_text())
    del data["registrations"][0]["openai_key"]  # 예전 장부에는 이 칸이 없다
    path.write_text(json.dumps(data))
    assert Registry(path).get(reg.token).openai_key == ""
