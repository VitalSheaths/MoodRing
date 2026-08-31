from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from sqlalchemy import text

from ingestion.storage.postgres import get_engine
from macro.connectors.fomc import fetch_fomc_communications
from macro.storage.postgres import ensure_macro_schema


async def run_once() -> None:
    engine = get_engine()
    ensure_macro_schema(engine)

    with engine.begin() as conn:
        row = conn.execute(text("SELECT max(release_date) FROM silver.fomc_communications")).scalar_one_or_none()
    since = row if row else datetime(2000, 1, 1, tzinfo=timezone.utc)

    comms = await fetch_fomc_communications(since=since)

    if not comms:
        print("[macro] no new fomc communications")
        return

    upsert_sql = text("""
    INSERT INTO silver.fomc_communications (
        comm_id, meeting_date, release_date, comm_type, text, source_url, hawkish_score, dovish_score, meta
    ) VALUES (
        :comm_id, :meeting_date, :release_date, :comm_type, :text, :source_url, :hawkish_score, :dovish_score, '{}'::jsonb
    )
    ON CONFLICT (comm_id) DO UPDATE SET
        text = EXCLUDED.text,
        hawkish_score = EXCLUDED.hawkish_score,
        dovish_score = EXCLUDED.dovish_score
    """)

    with engine.begin() as conn:
        for c in comms:
            conn.execute(upsert_sql, {
                "comm_id": c.comm_id,
                "meeting_date": c.meeting_date,
                "release_date": c.release_date,
                "comm_type": c.comm_type,
                "text": c.text,
                "source_url": c.source_url,
                "hawkish_score": c.hawkish_score,
                "dovish_score": c.dovish_score,
            })

    print(f"[macro] upserted fomc communications: {len(comms)}")


def main() -> None:
    asyncio.run(run_once())


if __name__ == "__main__":
    main()
