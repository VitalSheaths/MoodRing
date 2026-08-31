from __future__ import annotations
from ingestion.models.ingest_models import RawNewsItem


def dedupe_raw_items(items: list[RawNewsItem]) -> list[RawNewsItem]:
    seen: set[tuple[str, str]] = set()
    out: list[RawNewsItem] = []
    for item in items:
        key = (item.source, item.source_item_id)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out
