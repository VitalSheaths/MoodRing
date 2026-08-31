from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Dict
import hashlib
import json


def _stable_hash(payload: Dict[str, Any]) -> str:
    base = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:32]


@dataclass
class RawNewsItem:
    source: str
    source_item_id: str
    fetched_at: str
    payload: Dict[str, Any]

    @classmethod
    def from_payload(
        cls,
        source: str,
        payload: Dict[str, Any],
        source_item_id: str | None = None,
    ) -> "RawNewsItem":
        sid = source_item_id or _stable_hash(payload)
        return cls(
            source=source,
            source_item_id=sid,
            fetched_at=datetime.now(timezone.utc).isoformat(),
            payload=payload,
        )

    @property
    def fetched_at_dt(self) -> datetime:
        return datetime.fromisoformat(self.fetched_at.replace("Z", "+00:00"))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
