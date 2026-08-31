from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import httpx

from config.config import settings
from ingestion.models.ingest_models import RawNewsItem


class FinnhubIngestor:
    BASE_URL = "https://finnhub.io/api/v1/company-news"

    def __init__(self, symbols: list[str], source_name: str = "finnhub_company_news") -> None:
        self.symbols = symbols
        self.source_name = source_name

    async def _fetch_symbol(self, client: httpx.AsyncClient, symbol: str, start: str, end: str) -> list[dict]:
        params = {
            "symbol": symbol,
            "from": start,
            "to": end,
            "token": settings.FINNHUB_KEY,
        }
        resp = await client.get(self.BASE_URL, params=params)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []

    async def fetch(self) -> list[RawNewsItem]:
        if not settings.FINNHUB_KEY:
            return []

        end_dt = datetime.now(timezone.utc).date()
        start_dt = end_dt - timedelta(days=settings.FINNHUB_LOOKBACK_DAYS)
        start_str = start_dt.isoformat()
        end_str = end_dt.isoformat()

        timeout = httpx.Timeout(settings.REQUEST_TIMEOUT_S)
        out: list[RawNewsItem] = []

        async with httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": settings.USER_AGENT},
        ) as client:
            tasks = [self._fetch_symbol(client, s, start_str, end_str) for s in self.symbols]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        for symbol, rows in zip(self.symbols, results):
            if isinstance(rows, Exception):
                continue
            for row in rows:
                payload = {
                    "symbol": symbol,
                    "id": row.get("id"),
                    "headline": row.get("headline"),
                    "summary": row.get("summary"),
                    "url": row.get("url"),
                    "source": row.get("source"),
                    "datetime": row.get("datetime"),
                    "image": row.get("image"),
                    "related": row.get("related"),
                }
                source_item_id = str(payload.get("id") or payload.get("url") or "")
                out.append(RawNewsItem.from_payload(self.source_name, payload, source_item_id or None))

        return out
