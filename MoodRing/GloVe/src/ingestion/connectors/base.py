from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable
import uuid

from ingestion.models.ingest_models import RawNewsItem


@dataclass
class RequestMetrics:
    source_name: str
    source_type: str
    source_url: str
    request_id: uuid.UUID = field(default_factory=uuid.uuid4)
    requested_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    method: str = "GET"
    status_code: int | None = None
    request_headers: dict[str, str] = field(default_factory=dict)
    response_headers: dict[str, str] = field(default_factory=dict)
    etag: str | None = None
    last_modified: str | None = None
    not_modified: bool | None = None
    error_type: str | None = None
    error_message: str | None = None
    duration_ms: int | None = None
    bytes_received: int | None = None
    items: list[RawNewsItem] = field(default_factory=list)
    content_fingerprint: str | None = None
    skipped: bool = False
    skip_reason: str | None = None


@dataclass
class FetchResult:
    source_name: str
    source_type: str
    requests: list[RequestMetrics] = field(default_factory=list)

    @property
    def items(self) -> list[RawNewsItem]:
        out: list[RawNewsItem] = []
        for r in self.requests:
            out.extend(r.items)
        return out


@runtime_checkable
class Ingestor(Protocol):
    source_name: str

    async def fetch(self) -> FetchResult | list[RawNewsItem]:
        ...
