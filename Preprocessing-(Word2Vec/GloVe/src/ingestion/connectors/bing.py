from __future__ import annotations

import asyncio
import httpx

from config.config import settings
from ingestion.models.ingest_models import RawNewsItem


class BingNewsIngestor:
    BASE_URL = "https://api.bing.microsoft.com/v7.0/news/search"

    def __init__(self, queries: list[str], source_name: str = "bing_news") -> None:
        self.queries = queries
        self.source_name = source_name

    async def _fetch_query(self, client: httpx.AsyncClient, query: str) -> dict:
        headers = {"Ocp-Apim-Subscription-Key": settings.BING_API_KEY}
        params = {"q": query, "count": 50, "mkt": "en-US"}
        resp = await client.get(self.BASE_URL, params=params, headers=headers)
        resp.raise_for_status()
        return resp.json()

    async def fetch(self) -> list[RawNewsItem]:
        if not settings.BING_API_KEY:
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
            for item in data.get("value", []):
                payload = {
                    "query": query,
                    "name": item.get("name"),
                    "description": item.get("description"),
                    "url": item.get("url"),
                    "datePublished": item.get("datePublished"),
                    "provider": item.get("provider"),
                    "category": item.get("category"),
                }
                source_item_id = payload.get("url")
                out.append(RawNewsItem.from_payload(self.source_name, payload, source_item_id))

        return out
