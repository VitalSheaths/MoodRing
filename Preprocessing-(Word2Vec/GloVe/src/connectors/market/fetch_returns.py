from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd
import yfinance as yf
from sqlalchemy import text

from config.data_io import get_engine


TICKERS = ["AAPL", "MSFT", "AMZN", "GOOG", "AMD", "NVDA", "TSLA", "YELP", "NFLX", "ADBE", "BA", "AIG", "SPY"]


def ensure_silver_returns_table() -> None:
    ddl = """
    CREATE SCHEMA IF NOT EXISTS silver;

    CREATE TABLE IF NOT EXISTS silver.returns_daily (
        ticker TEXT NOT NULL,
        trade_date DATE NOT NULL,
        open DOUBLE PRECISION,
        high DOUBLE PRECISION,
        low DOUBLE PRECISION,
        close DOUBLE PRECISION,
        adj_close DOUBLE PRECISION,
        volume BIGINT,
        log_return DOUBLE PRECISION,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (ticker, trade_date)
    );

    CREATE INDEX IF NOT EXISTS idx_returns_daily_ticker_date
    ON silver.returns_daily (ticker, trade_date DESC);
    """
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text(ddl))


def _chunked(seq: list[dict], size: int) -> Iterable[list[dict]]:
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def _safe_float_series(s: pd.Series) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce")
    s = s.replace([np.inf, -np.inf], np.nan)
    return s


def _safe_bigint_series(s: pd.Series) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce")
    s = s.replace([np.inf, -np.inf], np.nan)

    max_bigint = np.iinfo(np.int64).max
    s = s.clip(lower=0, upper=max_bigint)

    # Keep nullable integer semantics for clean DB NULL handling.
    return s.round().astype("Int64")


def fetch_returns(
    start: str = "2010-01-01",
    end: str | None = None,
    tickers: list[str] | None = None,
) -> pd.DataFrame:
    tickers = tickers or TICKERS
    frames: list[pd.DataFrame] = []
    try:
        raw = yf.download(
            tickers=tickers,
            start=start,
            end=end,
            auto_adjust=False,
            progress=False,
            threads=False,
            group_by="ticker",
        )
        if isinstance(raw.columns, pd.MultiIndex):
            first_level = set(raw.columns.get_level_values(0))
            for tk in tickers:
                if tk in first_level and not raw[tk].dropna(how="all").empty:
                    tmp = raw[tk].copy().reset_index()
                    tmp["ticker"] = tk
                    frames.append(tmp)
        elif not raw.empty:
            tmp = raw.copy().reset_index()
            tmp["ticker"] = tickers[0]
            frames.append(tmp)
    except Exception:
        frames = []

    if not frames:
        import time as _time
        import requests as _requests
        session = _requests.Session()
        session.headers["User-Agent"] = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        for tk in tickers:
            try:
                t = yf.Ticker(tk, session=session)
                df_tk = t.history(start=start, end=end, auto_adjust=False)
                if not df_tk.empty:
                    df_tk = df_tk.reset_index()
                    df_tk["ticker"] = tk
                    frames.append(df_tk)
                _time.sleep(0.3)
            except Exception as exc:
                print(f"[WARN] Failed to fetch {tk}: {exc}")

    if not frames:
        return pd.DataFrame(
            columns=["ticker", "trade_date", "open", "high", "low", "close", "adj_close", "volume", "log_return"]
        )

    df = pd.concat(frames, ignore_index=True)

    # Normalize columns.
    df.columns = [str(c).lower().replace(" ", "_") for c in df.columns]
    if "adj_close" not in df.columns and "adj close" in df.columns:
        df = df.rename(columns={"adj close": "adj_close"})

    # Ensure required base cols exist.
    for col in ["date", "open", "high", "low", "close", "adj_close", "volume", "ticker"]:
        if col not in df.columns:
            df[col] = np.nan

    # Datetime + keys.
    dt = pd.to_datetime(df["date"], errors="coerce", utc=True)
    df["trade_date"] = dt.dt.tz_convert(None).dt.date
    df["ticker"] = df["ticker"].astype(str).str.upper().str.strip()

    # Numeric sanitization.
    for col in ["open", "high", "low", "close", "adj_close"]:
        df[col] = _safe_float_series(df[col])

    df["volume"] = _safe_bigint_series(df["volume"])

    # Calculate log return using adj_close if available, else close.
    px_col = "adj_close" if df["adj_close"].notna().any() else "close"
    df = df.sort_values(["ticker", "trade_date"]).reset_index(drop=True)
    df["log_return"] = (
        df.groupby("ticker")[px_col]
        .transform(lambda x: np.log(x / x.shift(1)))
        .replace([np.inf, -np.inf], np.nan)
    )

    keep = ["ticker", "trade_date", "open", "high", "low", "close", "adj_close", "volume", "log_return"]
    out = df[keep].copy()

    # Drop unusable keys before DB insert.
    out = out.dropna(subset=["ticker", "trade_date"])

    return out


def upsert_returns(df: pd.DataFrame, chunk_size: int = 5000) -> int:
    if df.empty:
        return 0

    engine = get_engine()

    sql = text("""
    INSERT INTO silver.returns_daily (
        ticker, trade_date, open, high, low, close, adj_close, volume, log_return, updated_at
    )
    VALUES (
        :ticker, :trade_date, :open, :high, :low, :close, :adj_close, :volume, :log_return, now()
    )
    ON CONFLICT (ticker, trade_date) DO UPDATE SET
        open = EXCLUDED.open,
        high = EXCLUDED.high,
        low = EXCLUDED.low,
        close = EXCLUDED.close,
        adj_close = EXCLUDED.adj_close,
        volume = EXCLUDED.volume,
        log_return = EXCLUDED.log_return,
        updated_at = now();
    """)

    rows = df.to_dict(orient="records")
    written = 0

    with engine.begin() as conn:
        for batch in _chunked(rows, chunk_size):
            conn.execute(sql, batch)
            written += len(batch)

    return written


def main() -> None:
    ensure_silver_returns_table()

    df = fetch_returns()

    # Quick diagnostics before write.
    vol_max = df["volume"].max(skipna=True)
    vol_min = df["volume"].min(skipna=True)
    print(f"[returns] rows fetched: {len(df)}")
    print(f"[returns] volume range: min={vol_min}, max={vol_max}")
    print(f"[returns] tickers: {sorted(df['ticker'].dropna().unique().tolist())}")

    n = upsert_returns(df)
    print(f"[returns] upserted rows: {n}")


if __name__ == "__main__":
    main()