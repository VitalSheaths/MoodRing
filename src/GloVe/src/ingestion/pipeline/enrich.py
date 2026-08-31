from __future__ import annotations

from datetime import datetime, timezone
from ingestion.models.ingest_models import RawNewsItem


def enrich_raw_items(items: list[RawNewsItem], run_id: str) -> list[RawNewsItem]:
    enriched: list[RawNewsItem] = []
    for item in items:
        payload = dict(item.payload)
        payload["_ingestion_run_id"] = run_id
        payload["_enriched_at"] = datetime.now(timezone.utc).isoformat()
        enriched.append(
            RawNewsItem(
                source=item.source,
                source_item_id=item.source_item_id,
                fetched_at=item.fetched_at,
                payload=payload,
            )
        )
    return enriched
