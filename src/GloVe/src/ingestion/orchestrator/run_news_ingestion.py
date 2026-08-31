from __future__ import annotations

import asyncio

from config.config import settings
from ingestion.connectors.bing import BingNewsIngestor
from ingestion.connectors.finhub import FinnhubIngestor
from ingestion.connectors.serpapi import SERPAPIIngestor
from ingestion.orchestrator.jobs import run_connectors
from config.data_io import get_engine
from ingestion.storage.bronze import (
    ensure_bronze_schema,
    upsert_source,
    start_fetch_run,
    finish_fetch_run,
    insert_fetch_request,
    insert_documents,
)
from ingestion.storage.silver import ensure_silver_schema, upsert_news_items_from_bronze
from ingestion.storage.bronze import RequestLog
import uuid


async def run() -> None:
    ensure_bronze_schema(engine=get_engine())
    ensure_silver_schema(engine=get_engine())

    connectors = []

    if settings.BING_API_KEY:
        connectors.append(BingNewsIngestor(list(settings.BING_QUERIES)))
    if settings.SERPAPI_KEY:
        connectors.append(SERPAPIIngestor(list(settings.SERP_QUERIES)))
    if settings.FINNHUB_KEY:
        connectors.append(FinnhubIngestor(list(settings.FINNHUB_SYMBOLS)))

    if not connectors:
        print("[news] no connectors configured (missing API keys)")
        return

    run_id = uuid.uuid4()
    start_fetch_run(get_engine(), run_id, trigger="manual")

    results = await run_connectors(connectors, settings.MAX_CONCURRENCY)

    total_items = 0
    engine = get_engine()

    for res in results:
        for item in res.items:
            total_items += 1

        source_id = upsert_source(
            engine,
            source_name=res.source,
            source_type="api",
            source_url="",
        )

        # minimal request log (per-connector)
        from datetime import datetime, timezone
        log = RequestLog(
            source_name=res.source,
            source_type="api",
            source_url="",
            request_id=uuid.uuid4(),
            run_id=run_id,
            requested_at=datetime.now(timezone.utc),
        )
        insert_fetch_request(engine, source_id, log)

        insert_documents(engine, source_id=source_id, request_id=log.request_id, items=res.items)

    finish_fetch_run(engine, run_id, meta={"total_items": total_items})

    n_silver = upsert_news_items_from_bronze(engine)
    print(f"[news] bronze items: {total_items}, silver upserts: {n_silver}")


if __name__ == "__main__":
    asyncio.run(run())