from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional

import feedparser
import httpx


@dataclass(frozen=True)
class CalendarRSSEvent:
    event_id: str
    source: str
    title: str
    url: str
    published_ts: int
    summary: str


class SeenRSSStore:
    def __init__(self, path: Optional[Path | str] = None, max_size: int = 10_000) -> None:
        self.path = Path(path) if path else Path(__file__).resolve().parents[2] / "storage" / "seen_rss.json"
        self.max_size = max_size
        self.data: dict[str, int] = {}
        self.load()

    def load(self) -> None:
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text("utf-8"))
            except Exception:
                self.data = {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if len(self.data) > self.max_size:
            items = sorted(self.data.items(), key=lambda kv: kv[1], reverse=True)[: self.max_size]
            self.data = dict(items)
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")

    def seen(self, key: str) -> bool:
        return key in self.data

    def mark(self, key: str) -> None:
        self.data[key] = int(time.time())


MACRO_CALENDAR_FEEDS = [
    ("federal_reserve_press", "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("reuters_business", "https://huggingface.co/datasets/danidanou/Reuters_Financial_News/resolve/main/summ_financial_data.parquet.gzip"),
]


def make_rss_id(feed_url: str, entry: Any) -> str:
    guid = getattr(entry, "id", None) or entry.get("id")
    link = getattr(entry, "link", None) or entry.get("link")
    title = getattr(entry, "title", "") or entry.get("title", "")
    base = guid or link or f"{feed_url}|{title}"
    h = hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]
    return f"rss_cal_{h}"


def parse_published_ts(entry: Any) -> int:
    pp = entry.get("published_parsed") or entry.get("updated_parsed")
    if pp:
        return int(time.mktime(pp))
    return int(time.time())


async def fetch_macro_calendar_rss(
    feeds: Optional[List[tuple[str, str]]] = None,
    seen_store: Optional[SeenRSSStore] = None,
) -> List[CalendarRSSEvent]:
    feeds = feeds or MACRO_CALENDAR_FEEDS
    seen = seen_store or SeenRSSStore()
    out: List[CalendarRSSEvent] = []

    async with httpx.AsyncClient(
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) MacroCalendarIngestor/1.0"},
        timeout=30.0,
        follow_redirects=True,
    ) as client:
        for label, url in feeds:
            if url.endswith(".parquet.gzip") or url.endswith(".parquet"):
                continue  # Parquet archives handled by historical ingestion

            try:
                resp = await client.get(url)
                if not resp.is_success:
                    continue

                fp = feedparser.parse(resp.content)
                for entry in fp.entries:
                    item_id = make_rss_id(url, entry)
                    if seen.seen(item_id):
                        continue

                    title = entry.get("title", "")
                    link = entry.get("link", "")
                    summary = entry.get("summary", "") or entry.get("description", "")
                    pub_ts = parse_published_ts(entry)

                    event = CalendarRSSEvent(
                        event_id=item_id,
                        source=label,
                        title=title,
                        url=link,
                        published_ts=pub_ts,
                        summary=summary,
                    )
                    out.append(event)
                    seen.mark(item_id)
            except Exception as exc:
                print(f"[WARN] Failed to fetch macro calendar feed {label}: {exc}")

    seen.save()
    return out


if __name__ == "__main__":
    events = asyncio.run(fetch_macro_calendar_rss())
    print(f"[calendar_rss] Fetched {len(events)} new macro calendar RSS events.")
