from __future__ import annotations

import pandas as pd
from sqlalchemy import text

from config.data_io import get_engine
from features.technical.rsi import add_rsi
from features.technical.macd import add_macd
from features.technical.dmi import add_dmi


def ensure_table() -> None:
    ddl = """
    CREATE SCHEMA IF NOT EXISTS silver;

    CREATE TABLE IF NOT EXISTS silver.returns_technical_features (
        ticker TEXT NOT NULL,
        trade_date DATE NOT NULL,
        rsi_14 DOUBLE PRECISION,
        macd DOUBLE PRECISION,
        macd_signal DOUBLE PRECISION,
        macd_hist DOUBLE PRECISION,
        plus_di DOUBLE PRECISION,
        minus_di DOUBLE PRECISION,
        adx DOUBLE PRECISION,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (ticker, trade_date)
    );

    CREATE INDEX IF NOT EXISTS idx_technical_ticker_date
    ON silver.returns_technical_features(ticker, trade_date);

    CREATE INDEX IF NOT EXISTS idx_technical_created_at
    ON silver.returns_technical_features(created_at);
    """
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text(ddl))


def load_returns() -> pd.DataFrame:
    """Load returns, ordered chronologically per ticker."""
    sql = """
          SELECT ticker, \
                 trade_date, \
                 open, \
                 high, \
                 low, \
                 close, \
                 adj_close, \
                 volume, \
                 log_return
          FROM silver.returns_daily
          ORDER BY ticker, trade_date \
          """
    engine = get_engine()
    with engine.begin() as conn:
        return pd.read_sql(text(sql), conn)


def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    """Compute technical features per ticker (maintains chronological order)."""
    out = df.copy()
    out = add_rsi(out, price_col="close", ticker_col="ticker", period=14, out_col="rsi_14")
    out = add_macd(out, price_col="close", ticker_col="ticker", fast=12, slow=26, signal=9)
    out = add_dmi(out, ticker_col="ticker", high_col="high", low_col="low", close_col="close", period=14)
    return out


def upsert_features(df: pd.DataFrame) -> int:
    """Upsert features with timestamp metadata."""
    keep = [
        "ticker", "trade_date",
        "rsi_14", "macd", "macd_signal", "macd_hist",
        "plus_di", "minus_di", "adx"
    ]
    rows = df[keep].to_dict(orient="records")
    if not rows:
        return 0

    sql = text("""
               INSERT INTO silver.returns_technical_features (ticker, trade_date, rsi_14, macd, macd_signal, macd_hist,
                                                              plus_di, minus_di, adx, created_at, updated_at)
               VALUES (:ticker, :trade_date, :rsi_14, :macd, :macd_signal, :macd_hist,
                       :plus_di, :minus_di, :adx, now(), now())
               ON CONFLICT (ticker, trade_date) DO UPDATE SET rsi_14      = EXCLUDED.rsi_14,
                                                              macd        = EXCLUDED.macd,
                                                              macd_signal = EXCLUDED.macd_signal,
                                                              macd_hist   = EXCLUDED.macd_hist,
                                                              plus_di     = EXCLUDED.plus_di,
                                                              minus_di    = EXCLUDED.minus_di,
                                                              adx         = EXCLUDED.adx,
                                                              updated_at  = now();
               """)
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(sql, rows)
    return len(rows)


def run() -> None:
    ensure_table()
    returns_df = load_returns()
    feats = compute_features(returns_df)
    n = upsert_features(feats)

    print(f"[technical] upserted {n} rows")

    # Create confidence-scored view
    engine = get_engine()
    from ingestion.storage.silver import create_technical_features_with_confidence
    create_technical_features_with_confidence(engine)
    print("[technical] created confidence-scored view")


if __name__ == "__main__":
    run()