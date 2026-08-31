from __future__ import annotations

import asyncio
import httpx

from config.config import settings
from ingestion.models.ingest_models import RawNewsItem


class SERPAPIIngestor:
    BASE_URL = "https://serpapi.com/search.json"

    def __init__(self, queries: list[str], source_name: str = "serpapi_news") -> None:
        self.queries = queries
        self.source_name = source_name

    async def _fetch_query(self, client: httpx.AsyncClient, query: str) -> dict:
        params = {
            "engine": "google_news",
            "q": query,
            "api_key": settings.SERPAPI_KEY,
        }
        resp = await client.get(self.BASE_URL, params=params)
        resp.raise_for_status()
        return resp.json()

    async def fetch(self) -> list[RawNewsItem]:
        if not settings.SERPAPI_KEY:
            return []

        timeout = httpx.Timeout(settings.REQUEST_TIMEOUT_S)
        out: list[RawNewsItem] = []

        async with httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": settings.USER_AGENT},
        ) as client:
            tasks = [self._fetch_query(client, q) for q in self.queries]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        for query, data in zip(self.queries, results):
            if isinstance(data, Exception):
                continue

            for item in data.get("news_results", []):
                payload = {
                    "query": query,
                    "title": item.get("title"),
                    "link": item.get("link"),
                    "snippet": item.get("snippet"),
                    "source": item.get("source"),
                    "date": item.get("date"),
                    "thumbnail": item.get("thumbnail"),
                }
                source_item_id = payload.get("link")
                out.append(RawNewsItem.from_payload(self.source_name, payload, source_item_id))

        return out
