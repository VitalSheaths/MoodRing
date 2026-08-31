from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy import text
from config.data_io import get_engine


def compute_earnings_surprise(df: pd.DataFrame) -> pd.DataFrame:
    """
    Computes EPS Surprise: (actual - estimate) / (|estimate| + 1e-5).
    Transforms raw earnings numbers into a normalized market surprise signal.
    """
    if df.empty:
        return df

    out = df.copy()
    eps_act = pd.to_numeric(out["eps_actual"], errors="coerce").fillna(0.0)
    eps_est = pd.to_numeric(out["eps_estimate"], errors="coerce").fillna(0.0)

    out["eps_surprise"] = (eps_act - eps_est) / (np.abs(eps_est) + 1e-5)
    return out


def fetch_earnings_events(ticker: str | None = None) -> pd.DataFrame:
    """Load earnings events from silver.earnings_events."""
    sql = """
    SELECT event_id, ticker, report_date, eps_estimate, eps_actual, revenue_estimate, revenue_actual, source
    FROM silver.earnings_events
    """
    params = {}
    if ticker:
        sql += " WHERE ticker = :ticker"
        params["ticker"] = ticker

    engine = get_engine()
    with engine.begin() as conn:
        df = pd.read_sql(text(sql), conn, params=params)

    return compute_earnings_surprise(df)
