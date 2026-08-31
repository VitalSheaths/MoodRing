from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from config.config import settings
from paths import PARQUET_ROOT, ensure_dirs


_ENGINE: Optional[Engine] = None


def get_engine() -> Engine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = create_engine(settings.DATABASE_URL, future=True, pool_pre_ping=True)
    return _ENGINE


def read_sql_df(sql: str, params: dict | None = None) -> pd.DataFrame:
    engine = get_engine()
    with engine.begin() as conn:
        return pd.read_sql(text(sql), conn, params=params)


def write_df_table(
    df: pd.DataFrame,
    table: str,
    schema: str,
    if_exists: str = "append",
    index: bool = False,
    chunksize: int = 2000,
) -> None:
    engine = get_engine()
    df.to_sql(
        name=table,
        con=engine,
        schema=schema,
        if_exists=if_exists,
        index=index,
        method="multi",
        chunksize=chunksize,
    )


def parquet_path(*parts: str) -> Path:
    ensure_dirs()
    return PARQUET_ROOT.joinpath(*parts)


def read_parquet_df(path: str | Path) -> pd.DataFrame:
    return pd.read_parquet(Path(path))


def write_parquet_df(df: pd.DataFrame, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(p, index=False)
    return p