"""Opt-in tests; create a fresh database so an existing research DB is never mutated."""
import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from ingestion.storage.gold import ensure_gold_schema, build_macro_daily_features, build_macro_weekly_features
from ingestion.storage.silver import create_model_features_view_safe, create_technical_features_with_confidence
from macro.storage.postgres import ensure_macro_schema
from paper_trading.service import PaperTradingService
from paper_trading.storage import PaperStore
from conftest import Clock, Quotes, prediction


@pytest.fixture
def postgres():
    url = os.getenv('PAPER_TEST_POSTGRES_URL')
    if not url:
        pytest.skip('Set PAPER_TEST_POSTGRES_URL to a disposable PostgreSQL server')
    name = 'moodring_test_' + uuid4().hex
    admin = create_engine(url, isolation_level='AUTOCOMMIT')
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    engine = create_engine(make_url(url).set(database=name))
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{name}"'))
        admin.dispose()


def test_legacy_macro_migration_preserves_values_and_is_idempotent(postgres):
    with postgres.begin() as connection:
        connection.execute(text('''CREATE SCHEMA gold;
            CREATE TABLE gold.macro_daily_features (feature_date DATE NOT NULL, ticker TEXT NOT NULL,
                fomc_recent_flag BOOLEAN DEFAULT false, fomc_hawk_minus_dove DOUBLE PRECISION DEFAULT 0,
                earnings_within_3d BOOLEAN DEFAULT false, insider_net_buy_value DOUBLE PRECISION DEFAULT 0,
                PRIMARY KEY(feature_date,ticker));
            INSERT INTO gold.macro_daily_features VALUES ('2026-10-06','AAPL',true,.5,false,1234);'''))
    ensure_gold_schema(postgres)
    ensure_gold_schema(postgres)
    with postgres.begin() as connection:
        row = connection.execute(text('SELECT * FROM gold.macro_daily_features')).mappings().one()
        assert row['fomc_recent_5d'] and row['insider_net_buy_value_7d'] == 1234
        assert 'cpi_asof' in row and 'updated_at' in row


def test_fresh_schema_respects_release_availability_and_signed_flows(postgres):
    ensure_macro_schema(postgres)
    ensure_gold_schema(postgres)
    with postgres.begin() as connection:
        connection.execute(text('''
            CREATE TABLE silver.returns_daily (ticker TEXT, trade_date DATE);
            INSERT INTO silver.returns_daily VALUES ('AAPL','2026-10-06'),('AAPL','2026-10-07');
            INSERT INTO silver.fred_observations (series_id,observation_date,value,available_at)
                VALUES ('CPIAUCSL','2026-10-01',999,'2026-10-07 13:00:00+00');
            INSERT INTO silver.fomc_communications
                (comm_id,meeting_date,release_date,comm_type,text,source_url,hawkish_score,dovish_score,available_at)
                VALUES ('late','2026-10-06 00:00+00','2026-10-06 22:00+00','statement','x','x',1,0,'2026-10-06 22:00+00');
            INSERT INTO silver.insider_trades (trade_id,ticker,insider_name,trade_type,filing_date,value,source,available_at)
                VALUES ('buy','AAPL','A','purchase','2026-10-06 13:00+00',100,'fixture','2026-10-06 13:00+00'),
                ('sell','AAPL','B','sale','2026-10-06 14:00+00',30,'fixture','2026-10-06 14:00+00');
        '''))
    assert build_macro_daily_features(postgres) == 2
    assert build_macro_weekly_features(postgres) == 1
    with postgres.begin() as connection:
        rows = connection.execute(text('SELECT * FROM gold.macro_daily_features ORDER BY feature_date')).mappings().all()
        assert rows[0]['cpi_asof'] is None and rows[0]['fomc_hawk_minus_dove'] == 0
        assert rows[1]['cpi_asof'] == 999 and rows[1]['fomc_hawk_minus_dove'] == 1
        assert rows[0]['insider_net_buy_value_7d'] == rows[1]['insider_net_buy_value_7d'] == 70
        weekly = connection.execute(text('SELECT insider_net_buy_value_7d_sum FROM gold.macro_weekly_features')).scalar_one()
        assert weekly == 70  # The same trade is not counted twice through overlapping seven-day windows.


def test_feature_view_uses_latest_contributor_and_explicit_market_timezone(postgres):
    with postgres.begin() as connection:
        connection.execute(text('''CREATE SCHEMA silver;
            CREATE TABLE silver.returns_daily (ticker TEXT,trade_date DATE,close DOUBLE PRECISION,
                log_return DOUBLE PRECISION,created_at TIMESTAMPTZ,updated_at TIMESTAMPTZ);
            CREATE TABLE silver.returns_technical_features (ticker TEXT,trade_date DATE,rsi_14 DOUBLE PRECISION,
                macd DOUBLE PRECISION,macd_signal DOUBLE PRECISION,macd_hist DOUBLE PRECISION,
                plus_di DOUBLE PRECISION,minus_di DOUBLE PRECISION,adx DOUBLE PRECISION,
                created_at TIMESTAMPTZ,updated_at TIMESTAMPTZ);
            CREATE TABLE silver.returns_volatility_features (ticker TEXT,trade_date DATE,garch_vol DOUBLE PRECISION,
                egarch_vol DOUBLE PRECISION,garch_aic DOUBLE PRECISION,egarch_aic DOUBLE PRECISION,
                created_at TIMESTAMPTZ,updated_at TIMESTAMPTZ);
            INSERT INTO silver.returns_daily VALUES
                ('AAPL','2026-10-06',100,.01,'2026-10-06 20:01+00','2026-10-06 20:01+00');
            INSERT INTO silver.returns_technical_features (ticker,trade_date,rsi_14,created_at,updated_at)
                VALUES ('AAPL','2026-10-06',55,'2026-10-06 20:02+00','2026-10-07 20:00+00');
            INSERT INTO silver.returns_volatility_features (ticker,trade_date,garch_vol,created_at,updated_at)
                VALUES ('AAPL','2026-10-06',1,'2026-10-06 20:03+00','2026-10-06 20:03+00');
        '''))
    create_technical_features_with_confidence(postgres)
    create_model_features_view_safe(postgres)
    create_model_features_view_safe(postgres)
    with postgres.begin() as connection:
        row = connection.execute(text('SELECT * FROM silver.returns_model_features')).mappings().one()
        assert row['features_created_at'] == datetime(2026, 10, 7, 20, tzinfo=timezone.utc)
        assert row['prediction_time'] == row['features_created_at']
        count = connection.execute(text("SELECT count(*) FROM silver.returns_model_features WHERE prediction_time <= '2026-10-06 21:00+00'" )).scalar_one()
        assert count == 0


def test_paper_ledger_on_postgres(postgres):
    clock = Clock()
    quotes = Quotes(clock)
    service = PaperTradingService(PaperStore(postgres), quotes, clock)
    scenario = service.create_scenario('alice','Postgres',config={'auto_exit':False})
    signal = service.publish_signal('alice',scenario['id'],'AAPL',prediction(clock))
    trade = service.execute_trade('alice',signal['id'],100,'buy')
    assert service.execute_trade('alice',signal['id'],100,'buy')['id'] == trade['id']
    clock.advance()
    quotes.prices['AAPL'] = 110
    service.refresh_prices()
    closed = service.close_trade('alice',trade['id'],'sell')
    assert service.get_scenario('alice',scenario['id'])['portfolio']['cash_balance'] == 10000 + closed['pnl_dollars']
    assert len(service.scenario_history('alice',scenario['id'])['fills']) == 2


def test_macro_revision_moves_availability_forward_without_changing_noops(postgres):
    ensure_macro_schema(postgres)
    with postgres.begin() as connection:
        connection.execute(text("INSERT INTO silver.fred_observations(series_id,observation_date,value,available_at) VALUES ('CPIAUCSL','2026-01-01',100,'2026-02-01 12:00+00')"))
        connection.execute(text("UPDATE silver.fred_observations SET value = 100"))
        at = connection.execute(text('SELECT available_at FROM silver.fred_observations')).scalar_one()
        assert at == datetime(2026,2,1,12,tzinfo=timezone.utc)
        connection.execute(text("UPDATE silver.fred_observations SET value = 200"))
        revised = connection.execute(text('SELECT available_at FROM silver.fred_observations')).scalar_one()
        assert revised > at
