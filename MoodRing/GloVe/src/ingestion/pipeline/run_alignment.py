from __future__ import annotations

import pandas as pd
from sqlalchemy import text

from config.data_io import get_engine
from features.alignment.NewsReturnAligner import NewsReturnAligner, AlignmentConfig


def ensure_aligned_table() -> None:
    ddl = """
    CREATE SCHEMA IF NOT EXISTS silver;

    CREATE TABLE IF NOT EXISTS silver.news_returns_aligned (
        ticker TEXT NOT NULL,
        trading_date DATE NOT NULL,
        event_ts TIMESTAMPTZ,
        title TEXT,
        summary TEXT,
        source TEXT,
        url TEXT,
        PRIMARY KEY (ticker, trading_date, event_ts)
    );
    """
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text(ddl))


def load_news() -> pd.DataFrame:
    sql = """
    SELECT
        headline AS title,
        summary,
        canonical_url AS url,
        source,
        published_at AS date,
        COALESCE(raw_payload->>'symbol', raw_payload->>'ticker', 'AAPL') AS ticker
    FROM silver.news_item
    WHERE published_at IS NOT NULL
    """
    engine = get_engine()
    with engine.begin() as conn:
        return pd.read_sql(text(sql), conn)


def load_returns() -> pd.DataFrame:
    sql = """
    SELECT ticker, trade_date
    FROM silver.returns_daily
    """
    engine = get_engine()
    with engine.begin() as conn:
        return pd.read_sql(text(sql), conn)


def upsert_aligned(df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    keep = ["ticker", "trading_date", "event_ts", "title", "summary", "source", "url"]
    rows = df[keep].dropna(subset=["ticker", "trading_date"]).to_dict(orient="records")

    sql = text("""
    INSERT INTO silver.news_returns_aligned (
        ticker, trading_date, event_ts, title, summary, source, url
    )
    VALUES (
        :ticker, :trading_date, :event_ts, :title, :summary, :source, :url
    )
    ON CONFLICT DO NOTHING
    """)
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(sql, rows)
    return len(rows)


def main() -> None:
    ensure_aligned_table()
    news = load_news()
    rets = load_returns()

    aligner = NewsReturnAligner(
        news_df=news,
        returns_df=rets,
        news_date_col="date",
        returns_date_col="trade_date",
        ticker_col="ticker",
        cfg=AlignmentConfig(),
    )
    aligned = aligner.merge(how="inner")

    n = upsert_aligned(aligned)
    print(f"[aligned] rows inserted: {n}")


if __name__ == "__main__":
    main()