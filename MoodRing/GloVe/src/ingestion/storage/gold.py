from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

# This module owns Gold DDL. Macro storage owns only Silver event tables.
DDL_SQL = """
CREATE SCHEMA IF NOT EXISTS gold;
CREATE TABLE IF NOT EXISTS gold.macro_daily_features (
    feature_date DATE NOT NULL,
    ticker TEXT NOT NULL,
    PRIMARY KEY (feature_date, ticker)
);
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='gold'
        AND table_name='macro_daily_features' AND column_name='fomc_recent_flag')
        AND NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='gold'
        AND table_name='macro_daily_features' AND column_name='fomc_recent_5d') THEN
        ALTER TABLE gold.macro_daily_features RENAME COLUMN fomc_recent_flag TO fomc_recent_5d;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='gold'
        AND table_name='macro_daily_features' AND column_name='insider_net_buy_value')
        AND NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='gold'
        AND table_name='macro_daily_features' AND column_name='insider_net_buy_value_7d') THEN
        ALTER TABLE gold.macro_daily_features RENAME COLUMN insider_net_buy_value TO insider_net_buy_value_7d;
    END IF;
END $$;
ALTER TABLE gold.macro_daily_features
    ADD COLUMN IF NOT EXISTS last_fomc_release_date DATE,
    ADD COLUMN IF NOT EXISTS days_since_last_fomc INTEGER,
    ADD COLUMN IF NOT EXISTS fomc_hawk_minus_dove DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    ADD COLUMN IF NOT EXISTS fomc_recent_1d BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS fomc_recent_5d BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS earnings_within_3d BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS insider_net_buy_value_7d DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    ADD COLUMN IF NOT EXISTS insider_net_buy_value_1d DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    ADD COLUMN IF NOT EXISTS cpi_asof DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS fedfunds_asof DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();
CREATE INDEX IF NOT EXISTS idx_gold_macro_daily_ticker_date
    ON gold.macro_daily_features (ticker, feature_date DESC);
CREATE TABLE IF NOT EXISTS gold.macro_weekly_features (
    week_start DATE NOT NULL,
    ticker TEXT NOT NULL,
    avg_fomc_hawk_minus_dove DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    any_fomc_recent BOOLEAN NOT NULL DEFAULT FALSE,
    any_earnings_within_3d BOOLEAN NOT NULL DEFAULT FALSE,
    insider_net_buy_value_7d_sum DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (week_start, ticker)
);
"""


def ensure_gold_schema(engine: Engine) -> None:
    from macro.storage.postgres import ensure_macro_schema
    ensure_macro_schema(engine)
    with engine.begin() as conn:
        conn.execute(text(DDL_SQL))


def build_macro_daily_features(
    engine: Engine, *, returns_table: str = "silver.returns_daily",
    returns_date_col: str = "trade_date", returns_ticker_col: str = "ticker",
) -> int:
    # Identifiers are application settings, never accepted from the API.
    import re
    for identifier in (returns_table, returns_date_col, returns_ticker_col):
        if not re.fullmatch(r"[a-z_][a-z_0-9]*(\.[a-z_][a-z_0-9]*)?", identifier):
            raise ValueError("Invalid SQL identifier")
    sql = f"""
    WITH cal AS (
        SELECT DISTINCT {returns_ticker_col}::text AS ticker,
            {returns_date_col}::date AS trade_date,
            (({returns_date_col}::date + TIME '16:00:01') AT TIME ZONE 'America/New_York') AS cutoff
        FROM {returns_table}
        WHERE {returns_ticker_col} IS NOT NULL AND {returns_date_col} IS NOT NULL
    )
    INSERT INTO gold.macro_daily_features (
        feature_date, ticker, last_fomc_release_date, days_since_last_fomc,
        fomc_hawk_minus_dove, fomc_recent_1d, fomc_recent_5d, earnings_within_3d,
        insider_net_buy_value_7d, insider_net_buy_value_1d, cpi_asof, fedfunds_asof, updated_at
    )
    SELECT c.trade_date, c.ticker,
        (f.release_date AT TIME ZONE 'America/New_York')::date,
        c.trade_date - (f.release_date AT TIME ZONE 'America/New_York')::date,
        COALESCE(f.hawkish_score - f.dovish_score, 0.0),
        COALESCE((f.release_date AT TIME ZONE 'America/New_York')::date = c.trade_date, FALSE),
        COALESCE(f.release_date >= c.cutoff - INTERVAL '5 day', FALSE),
        EXISTS (SELECT 1 FROM silver.earnings_events e WHERE e.ticker = c.ticker
            AND e.available_at <= c.cutoff AND e.report_date >= c.cutoff
            AND e.report_date <= c.cutoff + INTERVAL '3 day'),
        COALESCE(ins.net_7d, 0.0), COALESCE(ins.net_1d, 0.0),
        cpi.value, fed.value, now()
    FROM cal c
    LEFT JOIN LATERAL (
        SELECT release_date, hawkish_score, dovish_score FROM silver.fomc_communications
        WHERE release_date <= c.cutoff AND available_at <= c.cutoff
        ORDER BY release_date DESC, available_at DESC LIMIT 1
    ) f ON TRUE
    LEFT JOIN LATERAL (
        SELECT SUM(CASE WHEN lower(trade_type) IN ('buy', 'purchase', 'p') THEN ABS(value)
                        WHEN lower(trade_type) IN ('sell', 'sale', 's') THEN -ABS(value) ELSE 0 END) AS net_7d,
            SUM(CASE WHEN (filing_date AT TIME ZONE 'America/New_York')::date = c.trade_date THEN
                CASE WHEN lower(trade_type) IN ('buy', 'purchase', 'p') THEN ABS(value)
                     WHEN lower(trade_type) IN ('sell', 'sale', 's') THEN -ABS(value) ELSE 0 END
                ELSE 0 END) AS net_1d
        FROM silver.insider_trades WHERE ticker = c.ticker
            AND filing_date BETWEEN c.cutoff - INTERVAL '7 day' AND c.cutoff
            AND available_at <= c.cutoff
    ) ins ON TRUE
    LEFT JOIN LATERAL (
        SELECT value FROM silver.fred_observations WHERE series_id = 'CPIAUCSL'
            AND observation_date <= c.trade_date AND available_at <= c.cutoff
        ORDER BY observation_date DESC LIMIT 1
    ) cpi ON TRUE
    LEFT JOIN LATERAL (
        SELECT value FROM silver.fred_observations WHERE series_id = 'FEDFUNDS'
            AND observation_date <= c.trade_date AND available_at <= c.cutoff
        ORDER BY observation_date DESC LIMIT 1
    ) fed ON TRUE
    ON CONFLICT (feature_date, ticker) DO UPDATE SET
        last_fomc_release_date = EXCLUDED.last_fomc_release_date,
        days_since_last_fomc = EXCLUDED.days_since_last_fomc,
        fomc_hawk_minus_dove = EXCLUDED.fomc_hawk_minus_dove,
        fomc_recent_1d = EXCLUDED.fomc_recent_1d, fomc_recent_5d = EXCLUDED.fomc_recent_5d,
        earnings_within_3d = EXCLUDED.earnings_within_3d,
        insider_net_buy_value_7d = EXCLUDED.insider_net_buy_value_7d,
        insider_net_buy_value_1d = EXCLUDED.insider_net_buy_value_1d,
        cpi_asof = EXCLUDED.cpi_asof, fedfunds_asof = EXCLUDED.fedfunds_asof, updated_at = now();
    """
    with engine.begin() as conn:
        return int(conn.execute(text(sql)).rowcount or 0)


def build_macro_weekly_features(engine: Engine) -> int:
    sql = """
    INSERT INTO gold.macro_weekly_features (
        week_start, ticker, avg_fomc_hawk_minus_dove, any_fomc_recent,
        any_earnings_within_3d, insider_net_buy_value_7d_sum, updated_at
    )
    SELECT date_trunc('week', feature_date::timestamp)::date, ticker,
        AVG(fomc_hawk_minus_dove), BOOL_OR(fomc_recent_5d), BOOL_OR(earnings_within_3d),
        SUM(insider_net_buy_value_1d), now()
    FROM gold.macro_daily_features GROUP BY 1,2
    ON CONFLICT (week_start, ticker) DO UPDATE SET
        avg_fomc_hawk_minus_dove = EXCLUDED.avg_fomc_hawk_minus_dove,
        any_fomc_recent = EXCLUDED.any_fomc_recent,
        any_earnings_within_3d = EXCLUDED.any_earnings_within_3d,
        insider_net_buy_value_7d_sum = EXCLUDED.insider_net_buy_value_7d_sum, updated_at = now();
    """
    with engine.begin() as conn:
        return int(conn.execute(text(sql)).rowcount or 0)
