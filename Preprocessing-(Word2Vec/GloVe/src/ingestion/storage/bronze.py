from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ingestion.models.ingest_models import RawNewsItem

def ensure_bronze_schema(engine: Engine) -> None:
    ddl = """
    CREATE SCHEMA IF NOT EXISTS bronze;

    CREATE TABLE IF NOT EXISTS bronze.bronze_source (
        source_id BIGSERIAL PRIMARY KEY,
        source_name TEXT NOT NULL UNIQUE,
        source_type TEXT NOT NULL,
        source_url TEXT NOT NULL,
        enabled BOOLEAN NOT NULL DEFAULT TRUE,
        poll_interval_s INT NOT NULL DEFAULT 300,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        meta JSONB NOT NULL DEFAULT '{}'::jsonb
    );

    CREATE TABLE IF NOT EXISTS bronze.bronze_fetch_run (
        run_id UUID PRIMARY KEY,
        started_at TIMESTAMPTZ NOT NULL,
        finished_at TIMESTAMPTZ,
        worker_id TEXT,
        trigger TEXT NOT NULL,
        meta JSONB NOT NULL DEFAULT '{}'::jsonb
    );

    CREATE TABLE IF NOT EXISTS bronze.bronze_fetch_request (
        request_id UUID PRIMARY KEY,
        run_id UUID NOT NULL REFERENCES bronze.bronze_fetch_run(run_id),
        source_id BIGINT NOT NULL REFERENCES bronze.bronze_source(source_id),
        requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        method TEXT NOT NULL DEFAULT 'GET',
        url TEXT NOT NULL,
        request_headers JSONB NOT NULL DEFAULT '{}'::jsonb,
        status_code INT,
        response_headers JSONB NOT NULL DEFAULT '{}'::jsonb,
        etag TEXT,
        last_modified TEXT,
        not_modified BOOLEAN,
        error_type TEXT,
        error_message TEXT,
        duration_ms INT,
        bytes_received INT,
        content_fingerprint TEXT,
        skipped BOOLEAN NOT NULL DEFAULT FALSE,
        skip_reason TEXT
    );

    CREATE TABLE IF NOT EXISTS bronze.bronze_document (
        bronze_id UUID PRIMARY KEY,
        source_id BIGINT NOT NULL REFERENCES bronze.bronze_source(source_id),
        request_id UUID REFERENCES bronze.bronze_fetch_request(request_id),
        fetched_at TIMESTAMPTZ NOT NULL,
        source_item_id TEXT NOT NULL,
        canonical_url TEXT,
        title TEXT,
        raw_payload JSONB NOT NULL,
        raw_text TEXT,
        content_type TEXT,
        payload_sha256 TEXT NOT NULL,
        meta JSONB NOT NULL DEFAULT '{}'::jsonb
    );

    CREATE UNIQUE INDEX IF NOT EXISTS uq_bronze_document_source_item
    ON bronze.bronze_document(source_id, source_item_id);

    CREATE TABLE IF NOT EXISTS bronze.bronze_news_raw (
        id BIGSERIAL PRIMARY KEY,
        source TEXT NOT NULL,
        source_item_id TEXT NOT NULL,
        fetched_at TIMESTAMPTZ NOT NULL,
        payload JSONB NOT NULL
    );

    CREATE UNIQUE INDEX IF NOT EXISTS uq_bronze_source_item
    ON bronze.bronze_news_raw (source, source_item_id);
    """
    with engine.begin() as conn:
        conn.execute(text(ddl))

@dataclass
class RequestLog:
    source_name: str
    source_type: str
    source_url: str
    request_id: uuid.UUID
    run_id: uuid.UUID
    requested_at: datetime
    method: str = "GET"
    status_code: int | None = None
    request_headers: dict[str, Any] | None = None
    response_headers: dict[str, Any] | None = None
    etag: str | None = None
    last_modified: str | None = None
    not_modified: bool | None = None
    error_type: str | None = None
    error_message: str | None = None
    duration_ms: int | None = None
    bytes_received: int | None = None
    content_fingerprint: str | None = None
    skipped: bool = False
    skip_reason: str | None = None


def _sha256_payload(payload: dict[str, Any]) -> str:
    s = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=True)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def upsert_source(
    engine: Engine,
    *,
    source_name: str,
    source_type: str,
    source_url: str,
    poll_interval_s: int = 300,
) -> int:
    sql = text(
        """
        INSERT INTO bronze.bronze_source(source_name, source_type, source_url, poll_interval_s)
        VALUES (:source_name, :source_type, :source_url, :poll_interval_s)
        ON CONFLICT (source_name)
        DO UPDATE SET
          source_type = EXCLUDED.source_type,
          source_url = EXCLUDED.source_url,
          poll_interval_s = EXCLUDED.poll_interval_s,
          updated_at = now()
        RETURNING source_id
        """
    )
    with engine.begin() as conn:
        return int(
            conn.execute(
                sql,
                {
                    "source_name": source_name,
                    "source_type": source_type,
                    "source_url": source_url,
                    "poll_interval_s": poll_interval_s,
                },
            ).scalar_one()
        )


def start_fetch_run(engine: Engine, run_id: uuid.UUID, trigger: str = "manual") -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO bronze.bronze_fetch_run(run_id, started_at, trigger, meta)
                VALUES (:run_id, :started_at, :trigger, '{}'::jsonb)
                ON CONFLICT (run_id) DO NOTHING
                """
            ),
            {
                "run_id": str(run_id),
                "started_at": datetime.now(timezone.utc),
                "trigger": trigger,
            },
        )


def finish_fetch_run(engine: Engine, run_id: uuid.UUID, meta: dict[str, Any] | None = None) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE bronze.bronze_fetch_run
                SET finished_at = :finished_at,
                    meta = COALESCE(CAST(:meta AS jsonb), meta)
                WHERE run_id = :run_id
                """
            ),
            {
                "run_id": str(run_id),
                "finished_at": datetime.now(timezone.utc),
                "meta": json.dumps(meta or {}),
            },
        )



def insert_fetch_request(engine: Engine, source_id: int, log: RequestLog) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO bronze.bronze_fetch_request(
                  request_id, run_id, source_id, requested_at, method, url,
                  request_headers, status_code, response_headers,
                  etag, last_modified, not_modified, error_type, error_message,
                  duration_ms, bytes_received, content_fingerprint, skipped, skip_reason
                )
                VALUES (
                  :request_id, :run_id, :source_id, :requested_at, :method, :url,
                  CAST(:request_headers AS jsonb), :status_code, CAST(:response_headers AS jsonb),
                  :etag, :last_modified, :not_modified, :error_type, :error_message,
                  :duration_ms, :bytes_received, :content_fingerprint, :skipped, :skip_reason
                )
                ON CONFLICT (request_id) DO NOTHING

                """
            ),
            {
                "request_id": str(log.request_id),
                "run_id": str(log.run_id),
                "source_id": source_id,
                "requested_at": log.requested_at,
                "method": log.method,
                "url": log.source_url,
                "request_headers": json.dumps(log.request_headers or {}),
                "status_code": log.status_code,
                "response_headers": json.dumps(log.response_headers or {}),
                "etag": log.etag,
                "last_modified": log.last_modified,
                "not_modified": log.not_modified,
                "error_type": log.error_type,
                "error_message": log.error_message,
                "duration_ms": log.duration_ms,
                "bytes_received": log.bytes_received,
                "content_fingerprint": log.content_fingerprint,
                "skipped": log.skipped,
                "skip_reason": log.skip_reason,
            },
        )


def insert_documents(
    engine: Engine,
    *,
    source_id: int,
    request_id: uuid.UUID,
    items: Iterable[RawNewsItem],
) -> int:
    n = 0
    with engine.begin() as conn:
        for it in items:
            payload_sha = _sha256_payload(it.payload)
            bronze_id = str(uuid.uuid4())
            fetched_at = it.fetched_at_dt
            title = it.payload.get("headline") or it.payload.get("title") or it.payload.get("name")
            canonical_url = it.payload.get("link") or it.payload.get("url")
            raw_payload_json = json.dumps(it.payload, default=str)

            # Advanced bronze canonical store
            conn.execute(
                text(
                    """
                    INSERT INTO bronze.bronze_document(
                      bronze_id, source_id, request_id, fetched_at, source_item_id,
                      canonical_url, title, raw_payload, raw_text, content_type, payload_sha256, meta
                    )
                    VALUES (
                      :bronze_id, :source_id, :request_id, :fetched_at, :source_item_id,
                      :canonical_url, :title, CAST(:raw_payload AS jsonb), NULL, NULL, :payload_sha256, '{}'::jsonb
                    )
                    ON CONFLICT (source_id, source_item_id) DO NOTHING
                    """
                ),
                {
                    "bronze_id": bronze_id,
                    "source_id": source_id,
                    "request_id": str(request_id),
                    "fetched_at": fetched_at,
                    "source_item_id": it.source_item_id,
                    "canonical_url": canonical_url,
                    "title": title,
                    "raw_payload": raw_payload_json,
                    "payload_sha256": payload_sha,
                },
            )

            # Compatibility write (existing table)
            r = conn.execute(
                text(
                    """
                    INSERT INTO bronze.bronze_news_raw(source, source_item_id, fetched_at, payload)
                    VALUES (:source, :source_item_id, :fetched_at, CAST(:payload AS jsonb))
                    ON CONFLICT (source, source_item_id) DO NOTHING
                    """
                ),
                {
                    "source": it.source,
                    "source_item_id": it.source_item_id,
                    "fetched_at": fetched_at,
                    "payload": raw_payload_json,
                },
            )
            n += int(r.rowcount or 0)
    return n


def source_freshness(engine: Engine) -> list[dict[str, Any]]:
    sql = text(
        """
        SELECT
          s.source_name,
          s.source_url,
          max(r.requested_at) AS last_request_at,
          max(d.fetched_at) AS last_item_fetched_at,
          count(*) FILTER (WHERE r.requested_at > now() - interval '1 day') AS requests_24h,
          avg(r.duration_ms)::numeric(10,2) AS avg_duration_ms_24h
        FROM bronze.bronze_source s
        LEFT JOIN bronze.bronze_fetch_request r ON r.source_id = s.source_id
        LEFT JOIN bronze.bronze_document d ON d.source_id = s.source_id
        GROUP BY s.source_name, s.source_url
        ORDER BY s.source_name
        """
    )
    with engine.begin() as conn:
        rows = conn.execute(sql).mappings().all()
    return [dict(r) for r in rows]
