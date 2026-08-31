from __future__ import annotations

import json
from typing import Any

from config.paths import INGESTION_STATE_DIR


class JSONStateStore:
    def __init__(self, name: str = "ingestion_state.json") -> None:
        self.path = INGESTION_STATE_DIR / name
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def save(self, state: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(state, indent=2, ensure_ascii=True), encoding="utf-8")
