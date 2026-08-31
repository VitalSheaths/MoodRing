from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from time import perf_counter
from typing import List
import uuid

from config.config import settings
from config.paths import ensure_dirs
from ingestion.connectors.base import FetchResult, Ingestor, RequestMetrics
from ingestion.connectors.rss import RSSIngestor
from ingestion.connectors.serpapi import SERPAPIIngestor
from ingestion.connectors.finhub import FinnhubIngestor
from ingestion.connectors.bing import BingNewsIngestor
from ingestion.models.ingest_models import RawNewsItem
from ingestion.pipeline.normalise import normalise_raw_items
from ingestion.pipeline.dedupe import dedupe_raw_items
from ingestion.pipeline.enrich import enrich_raw_items
from ingestion.pipeline.align import align_raw_items
from ingestion.storage.postgres import (
    ensure_schema,
    finish_run,
    get_engine,
    start_run,
)
from ingestion.storage.bronze import (
    RequestLog,
    finish_fetch_run,
    insert_documents,
    insert_fetch_request,
    start_fetch_run,
    upsert_source,
)
from ingestion.storage.parquet import write_raw_items_parquet


def build_connectors() -> List[Ingestor]:
    connectors: List[Ingestor] = []

    if settings.RSS_FEED_URLS:
        connectors.append(RSSIngestor(feed_urls=list(settings.RSS_FEED_URLS), source_name="rss"))

    if settings.SERPAPI_KEY and settings.SERP_QUERIES:
        connectors.append(SERPAPIIngestor(queries=list(settings.SERP_QUERIES), source_name="serpapi"))

    if settings.FINNHUB_KEY and settings.FINNHUB_SYMBOLS:
        connectors.append(FinnhubIngestor(symbols=list(settings.FINNHUB_SYMBOLS), source_name="finnhub"))

    if settings.BING_API_KEY and settings.BING_QUERIES:
        connectors.append(BingNewsIngestor(queries=list(settings.BING_QUERIES), source_name="bing"))

    return connectors


def _connector_source_url(connector: Ingestor) -> str:
    if hasattr(connector, "feed_urls"):
        return ",".join(getattr(connector, "feed_urls"))
    if hasattr(connector, "queries"):
        return "query://" + ",".join(getattr(connector, "queries"))
    if hasattr(connector, "symbols"):
        return "symbol://" + ",".join(getattr(connector, "symbols"))
    if hasattr(connector, "BASE_URL"):
        return str(getattr(connector, "BASE_URL"))
    return f"connector://{connector.source_name}"


def _legacy_to_fetch_result(connector: Ingestor, items: list[RawNewsItem], duration_ms: int) -> FetchResult:
    req = RequestMetrics(
        source_name=connector.source_name,
        source_type="api",
        source_url=_connector_source_url(connector),
        status_code=None,
        duration_ms=duration_ms,
        bytes_received=None,
        items=items,
    )
    return FetchResult(source_name=connector.source_name, source_type="api", requests=[req])


async def _run_one_connector(connector: Ingestor, sem: asyncio.Semaphore) -> tuple[Ingestor, FetchResult]:
    async with sem:
        t0 = perf_counter()
        try:
            raw = await connector.fetch()
            dt = int((perf_counter() - t0) * 1000)
            if isinstance(raw, FetchResult):
                return connector, raw
            return connector, _legacy_to_fetch_result(connector, raw, dt)
        except Exception as exc:
            dt = int((perf_counter() - t0) * 1000)
            req = RequestMetrics(
                source_name=connector.source_name,
                source_type="api",
                source_url=_connector_source_url(connector),
                error_type="connector_error",
                error_message=str(exc),
                duration_ms=dt,
                items=[],
            )
            return connector, FetchResult(source_name=connector.source_name, source_type="api", requests=[req])


async def run_once(trigger: str = "manual") -> None:
    ensure_dirs()
    engine = get_engine()
    ensure_schema(engine)

    # Compatibility run table (existing)
    run_id = start_run(engine, run_id=f"run_{uuid.uuid4().hex}")

    # Advanced run table (UUID)
    run_uuid = uuid.uuid4()
    start_fetch_run(engine, run_uuid, trigger=trigger)

    total_fetched = 0
    total_inserted = 0

    try:
        connectors = build_connectors()
        if not connectors:
            finish_run(engine, run_id, "success", total_fetched=0, total_inserted=0)
            finish_fetch_run(engine, run_uuid, meta={"status": "success", "reason": "no_connectors"})
            print("No connectors configured (missing inputs/API keys).")
            return

        sem = asyncio.Semaphore(settings.MAX_CONCURRENCY)
        pairs = await asyncio.gather(*[_run_one_connector(c, sem) for c in connectors])

        by_source_for_parquet: dict[str, list[RawNewsItem]] = defaultdict(list)

        for connector, result in pairs:
            source_print_count = 0

            for req in result.requests:
                source_id = upsert_source(
                    engine,
                    source_name=req.source_name,
                    source_type=req.source_type,
                    source_url=req.source_url,
                    poll_interval_s=settings.POLL_INTERVAL_S,
                )

                log = RequestLog(
                    source_name=req.source_name,
                    source_type=req.source_type,
                    source_url=req.source_url,
                    request_id=req.request_id,
                    run_id=run_uuid,
                    requested_at=req.requested_at if isinstance(req.requested_at, datetime) else datetime.now(timezone.utc),
                    method=req.method,
                    status_code=req.status_code,
                    request_headers=req.request_headers,
                    response_headers=req.response_headers,
                    etag=req.etag,
                    last_modified=req.last_modified,
                    not_modified=req.not_modified,
                    error_type=req.error_type,
                    error_message=req.error_message,
                    duration_ms=req.duration_ms,
                    bytes_received=req.bytes_received,
                    content_fingerprint=req.content_fingerprint,
                    skipped=req.skipped,
                    skip_reason=req.skip_reason,
                )
                insert_fetch_request(engine, source_id, log)

                if req.skipped or req.not_modified or not req.items:
                    continue

                if req.error_type:
                    print(f"[WARN] {req.source_name}: {req.error_type} {req.error_message}")

                staged = normalise_raw_items(req.items)
                staged = dedupe_raw_items(staged)
                staged = enrich_raw_items(staged, run_id=run_id)
                staged = align_raw_items(staged)

                inserted = insert_documents(
                    engine,
                    source_id=source_id,
                    request_id=req.request_id,
                    items=staged,
                )

                total_fetched += len(staged)
                total_inserted += inserted
                source_print_count += len(staged)

                if staged:
                    by_source_for_parquet[connector.source_name].extend(staged)

            print(f"[INFO] {connector.source_name}: fetched={source_print_count}")

        for source, rows in by_source_for_parquet.items():
            write_raw_items_parquet(rows, source=source, run_id=run_id)

        finish_run(
            engine,
            run_id,
            status="success",
            total_fetched=total_fetched,
            total_inserted=total_inserted,
        )
        finish_fetch_run(
            engine,
            run_uuid,
            meta={"status": "success", "total_fetched": total_fetched, "total_inserted": total_inserted},
        )
        print(f"[DONE] run_id={run_id} fetched={total_fetched} inserted={total_inserted}")

    except Exception as exc:
        finish_run(
            engine,
            run_id,
            status="failed",
            total_fetched=total_fetched,
            total_inserted=total_inserted,
            error_message=str(exc),
        )
        finish_fetch_run(
            engine,
            run_uuid,
            meta={
                "status": "failed",
                "total_fetched": total_fetched,
                "total_inserted": total_inserted,
                "error": str(exc),
            },
        )
        raise


async def run_forever() -> None:
    while True:
        await run_once(trigger="scheduler")
        await asyncio.sleep(settings.POLL_INTERVAL_S)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Unified ingestion pipeline runner")
    p.add_argument("--loop", action="store_true", help="Run continuously")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if args.loop:
        asyncio.run(run_forever())
    else:
        asyncio.run(run_once(trigger="manual"))


if __name__ == "__main__":
    main()
