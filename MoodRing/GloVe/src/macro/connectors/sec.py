from __future__ import annotations

import pandas as pd
from sqlalchemy import text
from config.data_io import get_engine


def compute_net_insider_buying_30d(df: pd.DataFrame) -> pd.DataFrame:
    """
    Computes 30-day Net Insider Buying Pressure: Sum of buys minus sum of sells over a 30-day rolling window.
    Transforms raw Form 4 trades into an economically meaningful insider sentiment pressure signal.
    """
    if df.empty or "filing_date" not in df.columns:
        return df

    out = df.copy().sort_values(["ticker", "filing_date"]).reset_index(drop=True)
    out["signed_value"] = out.apply(
        lambda r: r["value"] if str(r.get("trade_type", "")).lower() in ("buy", "p") else -r["value"],
        axis=1
    )

    out["net_insider_buy_30d"] = (
        out.groupby("ticker")["signed_value"]
        .transform(lambda x: x.rolling(window=30, min_periods=1).sum())
    )

    return out


def fetch_insider_trades(ticker: str | None = None) -> pd.DataFrame:
    """Load Form 4 insider trades from silver.insider_trades."""
    sql = """
    SELECT trade_id, ticker, insider_name, trade_type, filing_date, shares, price, value, source
    FROM silver.insider_trades
    """
    params = {}
    if ticker:
        sql += " WHERE ticker = :ticker"
        params["ticker"] = ticker

    engine = get_engine()
    with engine.begin() as conn:
        df = pd.read_sql(text(sql), conn, params=params)

    return compute_net_insider_buying_30d(df)
