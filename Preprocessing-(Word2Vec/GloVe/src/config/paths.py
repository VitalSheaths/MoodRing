from __future__ import annotations

from pathlib import Path
from config.config import settings

SRC_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SRC_DIR.parents[2]  # .../AppliedDL&NLP-Assignment-2

RESOURCES_DIR = PROJECT_ROOT / "resources"
GENERATED_DIR = RESOURCES_DIR / "generated_files"

_parquet_root = Path(settings.PARQUET_ROOT).expanduser()
PARQUET_ROOT = _parquet_root if _parquet_root.is_absolute() else (PROJECT_ROOT / _parquet_root)

BRONZE_DIR = PARQUET_ROOT / "bronze"
SILVER_DIR = PARQUET_ROOT / "silver"
GOLD_DIR = PARQUET_ROOT / "gold"

INGESTION_STATE_DIR = SRC_DIR / "ingestion" / "storage" / "state"
INGESTION_LOG_DIR = SRC_DIR / "ingestion" / "storage" / "logs"

PIPELINE_DIRS = (RESOURCES_DIR,
        GENERATED_DIR,
        PARQUET_ROOT,
        BRONZE_DIR,
        SILVER_DIR,
        GOLD_DIR,
        INGESTION_STATE_DIR,
        INGESTION_LOG_DIR)
def ensure_dirs() -> None:
    for p in PIPELINE_DIRS:
        p.mkdir(parents=True, exist_ok=True)
