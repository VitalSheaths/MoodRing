from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


DDL_SQL = """
CREATE SCHEMA IF NOT EXISTS gold;

CREATE TABLE IF NOT EXISTS gold.macro_daily_features (
    feature_date DATE NOT NULL,
    ticker TEXT NOT NULL,

    -- FOMC as-of features
    last_fomc_release_date DATE,
    days_since_last_fomc INTEGER,
    fomc_hawk_minus_dove DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    fomc_recent_1d BOOLEAN NOT NULL DEFAULT FALSE,
    fomc_recent_5d BOOLEAN NOT NULL DEFAULT FALSE,

    -- Earnings / insider windows
    earnings_within_3d BOOLEAN NOT NULL DEFAULT FALSE,
    insider_net_buy_value_7d DOUBLE PRECISION NOT NULL DEFAULT 0.0,

    -- Example FRED as-of features (optional if table exists)
    cpi_asof DOUBLE PRECISION,
    fedfunds_asof DOUBLE PRECISION,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (feature_date, ticker)
);

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
    engine: Engine,
    *,
    returns_table: str = "silver.returns_daily",
    returns_date_col: str = "trade_date",
    returns_ticker_col: str = "ticker",
) -> int:
    # NOTE: returns_table/date col are controlled by your code, not user input.
    sql = f"""
    WITH cal AS (
        SELECT DISTINCT
            {returns_ticker_col}::text AS ticker,
            ({returns_date_col})::date AS trade_date
        FROM {returns_table}
        WHERE {returns_ticker_col} IS NOT NULL
          AND {returns_date_col} IS NOT NULL
    ),
    fomc_day AS (
        SELECT
            release_date::date AS release_date,
            COALESCE(AVG(hawkish_score - dovish_score), 0.0) AS hawk_minus_dove
        FROM silver.fomc_communications
        GROUP BY 1
    ),
    earn AS (
        SELECT ticker::text AS ticker, report_date::date AS report_date
        FROM silver.earnings_events
    ),
    ins AS (
        SELECT
            ticker::text AS ticker,
            filing_date::date AS filing_date,
            COALESCE(SUM(value), 0.0) AS net_buy_val
        FROM silver.insider_trades
        GROUP BY 1,2
    )
    INSERT INTO gold.macro_daily_features (
        feature_date,
        ticker,
        last_fomc_release_date,
        days_since_last_fomc,
        fomc_hawk_minus_dove,
        fomc_recent_1d,
        fomc_recent_5d,
        earnings_within_3d,
        insider_net_buy_value_7d,
        cpi_asof,
        fedfunds_asof,
        updated_at
    )
    SELECT
        c.trade_date AS feature_date,
        c.ticker,

        f_asof.release_date AS last_fomc_release_date,
        CASE
            WHEN f_asof.release_date IS NULL THEN NULL
            ELSE (c.trade_date - f_asof.release_date)
        END AS days_since_last_fomc,
        COALESCE(f_asof.hawk_minus_dove, 0.0) AS fomc_hawk_minus_dove,
        COALESCE(f_asof.release_date = c.trade_date, FALSE) AS fomc_recent_1d,
        (
            f_asof.release_date IS NOT NULL
            AND c.trade_date - f_asof.release_date <= 5
        ) AS fomc_recent_5d,

        EXISTS (
            SELECT 1
            FROM earn e
            WHERE e.ticker = c.ticker
              AND e.report_date BETWEEN c.trade_date AND c.trade_date + INTERVAL '3 day'
        ) AS earnings_within_3d,

        COALESCE((
            SELECT SUM(i.net_buy_val)
            FROM ins i
            WHERE i.ticker = c.ticker
              AND i.filing_date BETWEEN c.trade_date - INTERVAL '7 day' AND c.trade_date
        ), 0.0) AS insider_net_buy_value_7d,

        -- Optional FRED as-of joins (requires silver.fred_observations)
        (
            SELECT fo.value
            FROM silver.fred_observations fo
            WHERE fo.series_id = 'CPIAUCSL'
              AND fo.observation_date::date <= c.trade_date
            ORDER BY fo.observation_date DESC
            LIMIT 1
        ) AS cpi_asof,

        (
            SELECT fo.value
            FROM silver.fred_observations fo
            WHERE fo.series_id = 'FEDFUNDS'
              AND fo.observation_date::date <= c.trade_date
            ORDER BY fo.observation_date DESC
            LIMIT 1
        ) AS fedfunds_asof,

        now() AS updated_at
    FROM cal c
    LEFT JOIN LATERAL (
        SELECT release_date, hawk_minus_dove
        FROM fomc_day f
        WHERE f.release_date <= c.trade_date
        ORDER BY f.release_date DESC
        LIMIT 1
    ) f_asof ON TRUE
    ON CONFLICT (feature_date, ticker) DO UPDATE SET
        last_fomc_release_date = EXCLUDED.last_fomc_release_date,
        days_since_last_fomc = EXCLUDED.days_since_last_fomc,
        fomc_hawk_minus_dove = EXCLUDED.fomc_hawk_minus_dove,
        fomc_recent_1d = EXCLUDED.fomc_recent_1d,
        fomc_recent_5d = EXCLUDED.fomc_recent_5d,
        earnings_within_3d = EXCLUDED.earnings_within_3d,
        insider_net_buy_value_7d = EXCLUDED.insider_net_buy_value_7d,
        cpi_asof = EXCLUDED.cpi_asof,
        fedfunds_asof = EXCLUDED.fedfunds_asof,
        updated_at = now();
    """
    with engine.begin() as conn:
        result = conn.execute(text(sql))
    return int(result.rowcount or 0)


def build_macro_weekly_features(engine: Engine) -> int:
    sql = """
    INSERT INTO gold.macro_weekly_features (
        week_start,
        ticker,
        avg_fomc_hawk_minus_dove,
        any_fomc_recent,
        any_earnings_within_3d,
        insider_net_buy_value_7d_sum,
        updated_at
    )
    SELECT
        date_trunc('week', feature_date::timestamp)::date AS week_start,
        ticker,
        AVG(fomc_hawk_minus_dove) AS avg_fomc_hawk_minus_dove,
        BOOL_OR(fomc_recent_5d) AS any_fomc_recent,
        BOOL_OR(earnings_within_3d) AS any_earnings_within_3d,
        SUM(insider_net_buy_value_7d) AS insider_net_buy_value_7d_sum,
        now() AS updated_at
    FROM gold.macro_daily_features
    GROUP BY 1,2
    ON CONFLICT (week_start, ticker) DO UPDATE SET
        avg_fomc_hawk_minus_dove = EXCLUDED.avg_fomc_hawk_minus_dove,
        any_fomc_recent = EXCLUDED.any_fomc_recent,
        any_earnings_within_3d = EXCLUDED.any_earnings_within_3d,
        insider_net_buy_value_7d_sum = EXCLUDED.insider_net_buy_value_7d_sum,
        updated_at = now();
    """
    with engine.begin() as conn:
        result = conn.execute(text(sql))
    return int(result.rowcount or 0)