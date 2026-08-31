from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import pandas as pd

from ingestion.models.ingest_models import RawNewsItem
from config.paths import BRONZE_DIR


def write_raw_items_parquet(items: list[RawNewsItem], source: str, run_id: str) -> Path | None:
    if not items:
        return None

    now = datetime.now(timezone.utc)
    out_dir = BRONZE_DIR / source / f"date={now.date().isoformat()}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{run_id}.parquet"

    rows = [
        {
            "source": i.source,
            "source_item_id": i.source_item_id,
            "fetched_at": i.fetched_at,
            "payload": i.payload,
        }
        for i in items
    ]
    df = pd.DataFrame(rows)
    df.to_parquet(out_path, index=False)
    return out_path
