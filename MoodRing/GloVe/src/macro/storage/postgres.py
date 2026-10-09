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

-- Conservative availability for legacy records: when they entered this database.
ALTER TABLE silver.fomc_communications ADD COLUMN IF NOT EXISTS available_at TIMESTAMPTZ;
UPDATE silver.fomc_communications SET available_at = created_at WHERE available_at IS NULL;
ALTER TABLE silver.fomc_communications ALTER COLUMN available_at SET DEFAULT now();
ALTER TABLE silver.fomc_communications ALTER COLUMN available_at SET NOT NULL;
ALTER TABLE silver.earnings_events ADD COLUMN IF NOT EXISTS available_at TIMESTAMPTZ;
UPDATE silver.earnings_events SET available_at = created_at WHERE available_at IS NULL;
ALTER TABLE silver.earnings_events ALTER COLUMN available_at SET DEFAULT now();
ALTER TABLE silver.earnings_events ALTER COLUMN available_at SET NOT NULL;
ALTER TABLE silver.insider_trades ADD COLUMN IF NOT EXISTS available_at TIMESTAMPTZ;
UPDATE silver.insider_trades SET available_at = created_at WHERE available_at IS NULL;
ALTER TABLE silver.insider_trades ALTER COLUMN available_at SET DEFAULT now();
ALTER TABLE silver.insider_trades ALTER COLUMN available_at SET NOT NULL;
ALTER TABLE silver.fred_observations ADD COLUMN IF NOT EXISTS available_at TIMESTAMPTZ;
UPDATE silver.fred_observations SET available_at = created_at WHERE available_at IS NULL;
ALTER TABLE silver.fred_observations ALTER COLUMN available_at SET DEFAULT now();
ALTER TABLE silver.fred_observations ALTER COLUMN available_at SET NOT NULL;

-- Corrections cannot silently keep their old historical availability.
CREATE OR REPLACE FUNCTION silver.track_macro_availability() RETURNS trigger AS $$
BEGIN
    IF (to_jsonb(NEW) - 'available_at' - 'created_at') IS DISTINCT FROM
       (to_jsonb(OLD) - 'available_at' - 'created_at') THEN
        NEW.available_at := GREATEST(NEW.available_at, now());
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS macro_availability ON silver.fomc_communications;
CREATE TRIGGER macro_availability BEFORE UPDATE ON silver.fomc_communications
    FOR EACH ROW EXECUTE FUNCTION silver.track_macro_availability();
DROP TRIGGER IF EXISTS macro_availability ON silver.earnings_events;
CREATE TRIGGER macro_availability BEFORE UPDATE ON silver.earnings_events
    FOR EACH ROW EXECUTE FUNCTION silver.track_macro_availability();
DROP TRIGGER IF EXISTS macro_availability ON silver.insider_trades;
CREATE TRIGGER macro_availability BEFORE UPDATE ON silver.insider_trades
    FOR EACH ROW EXECUTE FUNCTION silver.track_macro_availability();
DROP TRIGGER IF EXISTS macro_availability ON silver.fred_observations;
CREATE TRIGGER macro_availability BEFORE UPDATE ON silver.fred_observations
    FOR EACH ROW EXECUTE FUNCTION silver.track_macro_availability();

"""


def ensure_macro_schema(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text(DDL))
