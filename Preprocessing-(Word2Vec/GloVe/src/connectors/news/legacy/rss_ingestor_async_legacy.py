import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import feedparser  # pip install feedparser

@dataclass(frozen=True)
class NewsItem:
    id: str
    source: str
    title: str
    url: str
    published_ts: int
    summary: str

class SeenStore:
    def __init__(self, path: str, max_size: int = 10_000) -> None:
        self.path = Path(path)
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
        # naive cap
        if len(self.data) > self.max_size:
            # drop oldest
            items = sorted(self.data.items(), key=lambda kv: kv[1], reverse=True)[: self.max_size]
            self.data = dict(items)
        self.path.write_text(json.dumps(self.data), encoding="utf-8")

    def seen(self, key: str) -> bool:
        return key in self.data

    def mark(self, key: str) -> None:
        self.data[key] = int(time.time())

def make_id(feed_url: str, entry: Any) -> str:
    guid = getattr(entry, "id", None) or entry.get("id")
    link = getattr(entry, "link", None) or entry.get("link")
    title = getattr(entry, "title", "") or entry.get("title", "")
    base = guid or link or f"{feed_url}|{title}"
    h = hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]
    return f"rss_{h}"

def parse_published_ts(entry: Any) -> int:
    # feedparser sets published_parsed if present
    pp = entry.get("published_parsed") or entry.get("updated_parsed")
    if pp:
        return int(time.mktime(pp))
    return int(time.time())

async def fetch_feed(client: httpx.AsyncClient, url: str) -> bytes:
    r = await client.get(url, timeout=20)
    r.raise_for_status()
    return r.content

async def poll_feeds(
    feeds: list[tuple[str, str]],  # (label, url)
    seen: SeenStore,
) -> list[NewsItem]:
    out: list[NewsItem] = []
    async with httpx.AsyncClient(headers={"User-Agent": "news-ingestor/1.0"}) as client:
        contents = await asyncio.gather(*(fetch_feed(client, url) for _, url in feeds), return_exceptions=True)

    for (label, url), content in zip(feeds, contents):
        if isinstance(content, Exception):
            continue
        fp = feedparser.parse(content)
        for entry in fp.entries:
            item_id = make_id(url, entry)
            if seen.seen(item_id):
                continue
            item = NewsItem(
                id=item_id,
                source=label,
                title=entry.get("title", ""),
                url=entry.get("link", ""),
                published_ts=parse_published_ts(entry),
                summary=entry.get("summary", "") or entry.get("description", ""),
            )
            out.append(item)
            seen.mark(item_id)

    seen.save()
    return out

async def run_loop():
    feeds = [
        ("reuters-markets", "https://feeds.reuters.com/reuters/businessNews"),
        # add more...
    ]
    seen = SeenStore("../../../storage/seen_rss.json")

    while True:
        items = await poll_feeds(feeds, seen)
        if items:
            print(f"got {len(items)} new items")
        await asyncio.sleep(60)  # poll every 60s (tune per feed)

if __name__ == "__main__":
    asyncio.run(run_loop())