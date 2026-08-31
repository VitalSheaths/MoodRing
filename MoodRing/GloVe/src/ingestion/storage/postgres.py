from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import uuid

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    func,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert
from sqlalchemy.engine import Engine

from config.config import settings
from ingestion.models.ingest_models import RawNewsItem


BRONZE_SCHEMA = "bronze"
metadata = MetaData()

bronze_news_raw = Table(
    "bronze_news_raw",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("source", Text, nullable=False),
    Column("source_item_id", Text, nullable=False),
    Column("fetched_at", DateTime(timezone=True), nullable=False),
    Column("payload", JSONB, nullable=False),
    schema=BRONZE_SCHEMA,
)

ingestion_runs = Table(
    "ingestion_runs",
    metadata,
    Column("run_id", String(64), primary_key=True),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("ended_at", DateTime(timezone=True), nullable=True),
    Column("status", String(16), nullable=False),
    Column("total_fetched", Integer, nullable=False, default=0),
    Column("total_inserted", Integer, nullable=False, default=0),
    Column("error_message", Text, nullable=True),
    schema=BRONZE_SCHEMA,
)


def get_engine(database_url: str | None = None) -> Engine:
    return create_engine(database_url or settings.DATABASE_URL, future=True)


def ensure_schema(engine: Engine) -> None:
    # Migration-first: validate required objects exist; do not auto-create in public.
    required = {(BRONZE_SCHEMA, "bronze_news_raw"), (BRONZE_SCHEMA, "ingestion_runs")}
    with engine.begin() as conn:
        found_rows = conn.execute(
            text(
                """
                SELECT table_schema, table_name
                FROM information_schema.tables
                WHERE (table_schema, table_name) IN (
                    ('bronze', 'bronze_news_raw'),
                    ('bronze', 'ingestion_runs')
                )
                """
            )
        ).fetchall()

    found = {(r[0], r[1]) for r in found_rows}
    missing = required - found
    if missing:
        raise RuntimeError(
            "Missing required DB tables. Run your SQL migration first. Missing: "
            + ", ".join([f"{s}.{t}" for s, t in sorted(missing)])
        )


def start_run(engine: Engine, run_id: str | None = None) -> str:
    run_id = run_id or f"run_{uuid.uuid4().hex}"
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        conn.execute(
            ingestion_runs.insert().values(
                run_id=run_id,
                started_at=now,
                status="running",
                total_fetched=0,
                total_inserted=0,
            )
        )
    return run_id


def finish_run(
    engine: Engine,
    run_id: str,
    status: str,
    total_fetched: int,
    total_inserted: int,
    error_message: str | None = None,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            update(ingestion_runs)
            .where(ingestion_runs.c.run_id == run_id)
            .values(
                ended_at=datetime.now(timezone.utc),
                status=status,
                total_fetched=total_fetched,
                total_inserted=total_inserted,
                error_message=error_message,
            )
        )


def insert_raw_items(engine: Engine, items: list[RawNewsItem]) -> int:
    if not items:
        return 0

    rows: list[dict[str, Any]] = [
        {
            "source": i.source,
            "source_item_id": i.source_item_id,
            "fetched_at": i.fetched_at_dt,
            "payload": i.payload,
        }
        for i in items
    ]

    stmt = pg_insert(bronze_news_raw).values(rows)
    stmt = stmt.on_conflict_do_nothing(index_elements=["source", "source_item_id"])

    with engine.begin() as conn:
        result = conn.execute(stmt)
    return int(result.rowcount or 0)


def get_table_counts(engine: Engine) -> dict[str, int]:
    with engine.begin() as conn:
        raw_count = conn.execute(func.count().select().select_from(bronze_news_raw)).scalar_one()
        run_count = conn.execute(func.count().select().select_from(ingestion_runs)).scalar_one()
    return {"bronze_news_raw": int(raw_count), "ingestion_runs": int(run_count)}
