from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class CanonicalNewsItem:
    source: str
    source_item_id: str
    title: str
    url: str
    published_at: Optional[str]
    summary: Optional[str]
    language: Optional[str] = None
