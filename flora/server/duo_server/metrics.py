"""대화 한 번마다 한 줄씩 기록을 남긴다 (JSONL). 두 경우를 견줄 때 쓴다."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .pipeline import TurnResult


class MetricsWriter:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, result: TurnResult, device_id: str = "") -> None:
        row = {
            "time": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "device_id": device_id,
            **result.as_dict(),
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
