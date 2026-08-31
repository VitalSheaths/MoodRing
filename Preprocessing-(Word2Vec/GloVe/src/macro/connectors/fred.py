from __future__ import annotations

import pandas as pd
from sqlalchemy import text
from config.data_io import get_engine

FRED_CPI_SERIES = "CPIAUCSL"
FRED_FEDFUNDS_SERIES = "FEDFUNDS"


def compute_cpi_surprise(df: pd.DataFrame) -> pd.DataFrame:
    """
    Computes CPI Surprise: Actual CPI minus 12-month rolling mean (expected baseline).
    Converts raw CPI into an economically meaningful market state signal.
    """
    if df.empty or "value" not in df.columns:
        return df

    out = df.copy().sort_values("observation_date").reset_index(drop=True)
    out["cpi_expected"] = out["value"].rolling(window=12, min_periods=1).mean()
    out["cpi_surprise"] = out["value"] - out["cpi_expected"]
    return out


def fetch_fred_observations(series_id: str = FRED_CPI_SERIES) -> pd.DataFrame:
    """Load FRED observations from DB or return empty DataFrame."""
    sql = text("""
    SELECT series_id, observation_date, value
    FROM silver.fred_observations
    WHERE series_id = :series_id
    ORDER BY observation_date ASC
    """)
    engine = get_engine()
    with engine.begin() as conn:
        df = pd.read_sql(sql, conn, params={"series_id": series_id})
    return compute_cpi_surprise(df)
