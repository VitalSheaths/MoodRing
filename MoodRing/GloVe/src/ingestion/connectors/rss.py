from __future__ import annotations

import asyncio
import calendar
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

import feedparser
import httpx
import hashlib

from config.config import settings
from ingestion.connectors.base import FetchResult, RequestMetrics
from ingestion.models.ingest_models import RawNewsItem
from ingestion.storage.state_store import JSONStateStore


class RSSIngestor:
    def __init__(self, feed_urls: list[str], source_name: str = "rss") -> None:
        self.feed_urls = feed_urls
        self.source_name = source_name
        self.source_type = "rss"
        self._state_store = JSONStateStore("rss_http_cache.json")
        self._state = self._state_store.load()

    def _source_name_for_url(self, url: str) -> str:
        return f"{self.source_name}|{url}"

    async def _fetch_one(self, client: httpx.AsyncClient, url: str) -> RequestMetrics:
        cache = self._state.get(url, {})
        req_headers: dict[str, str] = {}
        if cache.get("etag"):
            req_headers["If-None-Match"] = cache["etag"]
        if cache.get("last_modified"):
            req_headers["If-Modified-Since"] = cache["last_modified"]

        metrics = RequestMetrics(
            source_name=self._source_name_for_url(url),
            source_type=self.source_type,
            source_url=url,
            request_headers=dict(req_headers),
        )

        t0 = perf_counter()
        try:
            resp = await client.get(url, headers=req_headers)
            metrics.status_code = resp.status_code
            metrics.response_headers = {k: v for k, v in resp.headers.items()}

            metrics.etag = resp.headers.get("ETag")
            metrics.last_modified = resp.headers.get("Last-Modified")
            metrics.not_modified = resp.status_code == 304
            metrics.bytes_received = len(resp.content) if resp.content is not None else 0
            metrics.duration_ms = int((perf_counter() - t0) * 1000)

            body = resp.content or b""
            body_sha256 = hashlib.sha256(body).hexdigest()
            metrics.content_fingerprint = body_sha256

            cached_sha = cache.get("body_sha256")
            if resp.status_code == 304:
                metrics.not_modified = True
                metrics.skipped = True
                metrics.skip_reason = "http_304"
                return metrics

            if cached_sha and cached_sha == body_sha256:
                metrics.not_modified = True
                metrics.skipped = True
                metrics.skip_reason = "same_body_sha256"
                # still refresh cache headers/timestamp
                self._state[url] = {
                    "etag": metrics.etag,
                    "last_modified": metrics.last_modified,
                    "body_sha256": body_sha256,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
                return metrics

            resp.raise_for_status()

            parsed = feedparser.parse(body)
            if getattr(parsed, "bozo", 0):
                bozo_exc = getattr(parsed, "bozo_exception", None)
                if bozo_exc is not None:
                    metrics.error_type = "parse_error"
                    metrics.error_message = str(bozo_exc)

            items: list[RawNewsItem] = []
            for entry in parsed.entries:
                items.append(self._entry_to_item(url, entry))
            metrics.items = items

            # Persist conditional headers only when request succeeds.
            self._state[url] = {
                "etag": metrics.etag,
                "last_modified": metrics.last_modified,
                "body_sha256": body_sha256,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            return metrics

        except httpx.TimeoutException as exc:
            metrics.duration_ms = int((perf_counter() - t0) * 1000)
            metrics.error_type = "timeout"
            metrics.error_message = str(exc)
            return metrics
        except httpx.HTTPError as exc:
            metrics.duration_ms = int((perf_counter() - t0) * 1000)
            metrics.error_type = "http_error"
            metrics.error_message = str(exc)
            return metrics
        except Exception as exc:
            metrics.duration_ms = int((perf_counter() - t0) * 1000)
            metrics.error_type = "unknown_error"
            metrics.error_message = str(exc)
            return metrics

    def _entry_to_item(self, url: str, entry: Any) -> RawNewsItem:
        published_struct = entry.get("published_parsed") or entry.get("updated_parsed")
        published_at = None
        if published_struct:
            ts = calendar.timegm(published_struct)
            published_at = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

        payload = {
            "feed_url": url,
            "title": entry.get("title"),
            "summary": entry.get("summary") or entry.get("description"),
            "link": entry.get("link"),
            "published": entry.get("published") or entry.get("updated"),
            "published_at": published_at,
            "id": entry.get("id"),
        }
        source_item_id = payload.get("link") or payload.get("id") or payload.get("title")
        return RawNewsItem.from_payload(self.source_name, payload, source_item_id)

    async def fetch(self) -> FetchResult:
        timeout = httpx.Timeout(settings.REQUEST_TIMEOUT_S)
        async with httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": settings.USER_AGENT},
            follow_redirects=True,
        ) as client:
            tasks = [self._fetch_one(client, url) for url in self.feed_urls]
            req_results = await asyncio.gather(*tasks)

        self._state_store.save(self._state)

        return FetchResult(
            source_name=self.source_name,
            source_type=self.source_type,
            requests=req_results,
        )
