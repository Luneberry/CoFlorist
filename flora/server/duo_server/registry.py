"""기기 등록 장부.

인터넷에 서버를 열면 아무나 접속 안내를 받을 수 있으므로, 기기를 붙이려는 사람이
먼저 등록(초대 코드 + 자기 API 키)하고 자기만의 접속 주소를 받는다.

- token: 접속 주소에 들어가는 비밀 (/d/<token>/xiaozhi/ota/)
- access_token: 기기가 음성 연결 때 Bearer로 보내는 암호
장부는 JSON 파일 하나이고, 키가 들어 있으므로 본인만 읽을 수 있게(0600) 저장한다.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class Registration:
    token: str
    access_token: str
    name: str
    upstage_key: str = field(repr=False, default="")
    gemini_key: str = field(repr=False, default="")
    openai_key: str = field(repr=False, default="")
    created: str = ""
    devices: list[str] = field(default_factory=list)


def masked(key: str) -> str:
    """키를 보여 줄 때 앞 4자와 뒤 2자만 남긴다."""
    if not key:
        return ""
    if len(key) < 8:
        return "…"
    return key[:4] + "…" + key[-2:]


class Registry:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._items: dict[str, Registration] = {}
        self._load()

    # ------------------------------------------------------------ 읽기

    def get(self, token: str) -> Registration | None:
        if not token:
            return None
        return self._items.get(token)

    def all(self) -> list[Registration]:
        return list(self._items.values())

    # ------------------------------------------------------------ 쓰기

    def create(self, name: str, upstage_key: str, gemini_key: str = "", openai_key: str = "") -> Registration:
        reg = Registration(
            token=secrets.token_urlsafe(16),
            access_token=secrets.token_urlsafe(24),
            name=name.strip() or "이름 없음",
            upstage_key=upstage_key.strip(),
            gemini_key=gemini_key.strip(),
            openai_key=openai_key.strip(),
            created=datetime.now().astimezone().isoformat(timespec="seconds"),
        )
        self._items[reg.token] = reg
        self._save()
        return reg

    def note_device(self, token: str, device_id: str) -> None:
        reg = self.get(token)
        if reg is None or not device_id or device_id in reg.devices:
            return
        reg.devices.append(device_id)
        self._save()

    # ------------------------------------------------------------ 파일

    def _load(self) -> None:
        if not self._path.exists():
            return
        data = json.loads(self._path.read_text(encoding="utf-8"))
        for item in data.get("registrations", []):
            reg = Registration(**item)
            self._items[reg.token] = reg

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps({"registrations": [asdict(r) for r in self._items.values()]}, ensure_ascii=False, indent=2)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self._path)


def main(argv: list[str]) -> int:
    """`uv run python -m duo_server.registry [장부 경로]` — 등록 목록을 키를 가린 채 보여 준다."""
    path = argv[1] if len(argv) > 1 else os.environ.get("DUO_REGISTRY_PATH", "data/registrations.json")
    registry = Registry(path)
    items = registry.all()
    if not items:
        print("등록된 사람이 없습니다 (%s)" % path)
        return 0
    for reg in items:
        print(
            "%s  %s  업스테이지 %s  OpenAI %s  제미나이 %s  기기 %s  주소 비밀 %s"
            % (reg.created, reg.name, masked(reg.upstage_key) or "(없음)", masked(reg.openai_key) or "(없음)",
               masked(reg.gemini_key) or "(없음)", ", ".join(reg.devices) or "(아직 없음)", reg.token)
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
