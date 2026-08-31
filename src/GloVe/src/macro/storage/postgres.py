from __future__ import annotations

import json
from sqlalchemy import text
from sqlalchemy.engine import Engine

DDL = """
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS gold;

CREATE TABLE IF NOT EXISTS silver.fomc_communications (
    comm_id TEXT PRIMARY KEY,
    meeting_date TIMESTAMPTZ NOT NULL,
    release_date TIMESTAMPTZ NOT NULL,
    comm_type TEXT NOT NULL,
    text TEXT NOT NULL,
    source_url TEXT NOT NULL,
    hawkish_score DOUBLE PRECISION,
    dovish_score DOUBLE PRECISION,
    meta JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS silver.earnings_events (
    event_id TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    report_date TIMESTAMPTZ NOT NULL,
    eps_estimate DOUBLE PRECISION,
    eps_actual DOUBLE PRECISION,
    revenue_estimate DOUBLE PRECISION,
    revenue_actual DOUBLE PRECISION,
    source TEXT NOT NULL,
    meta JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS silver.insider_trades (
    trade_id TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    insider_name TEXT NOT NULL,
    trade_type TEXT NOT NULL,
    filing_date TIMESTAMPTZ NOT NULL,
    shares BIGINT,
    price DOUBLE PRECISION,
    value DOUBLE PRECISION,
    source TEXT NOT NULL,
    meta JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS silver.fred_observations (
    series_id TEXT NOT NULL,
    observation_date DATE NOT NULL,
    value DOUBLE PRECISION,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (series_id, observation_date)
);

CREATE TABLE IF NOT EXISTS gold.macro_daily_features (
    feature_date DATE NOT NULL,
    ticker TEXT NOT NULL,
    fomc_recent_flag BOOLEAN NOT NULL DEFAULT false,
    fomc_hawk_minus_dove DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    earnings_within_3d BOOLEAN NOT NULL DEFAULT false,
    insider_net_buy_value DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    PRIMARY KEY (feature_date, ticker)
);
"""


def ensure_macro_schema(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text(DDL))
