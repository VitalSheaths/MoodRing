from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Sequence

from ingestion.connectors.base import Ingestor
from ingestion.models.ingest_models import RawNewsItem


@dataclass
class ConnectorResult:
    source: str
    items: list[RawNewsItem]
    error: str | None = None


async def run_connectors(
    connectors: Sequence[Ingestor],
    max_concurrency: int,
) -> list[ConnectorResult]:
    sem = asyncio.Semaphore(max_concurrency)

    async def _run_one(connector: Ingestor) -> ConnectorResult:
        async with sem:
            try:
                items = await connector.fetch()
                return ConnectorResult(source=connector.source_name, items=items, error=None)
            except Exception as exc:  # keep pipeline alive if one connector fails
                return ConnectorResult(source=connector.source_name, items=[], error=str(exc))

    return await asyncio.gather(*[_run_one(c) for c in connectors])
