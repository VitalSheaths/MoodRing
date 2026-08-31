from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


SILVER_SCHEMA = "silver"


DDL_SQL = f"""
CREATE SCHEMA IF NOT EXISTS {SILVER_SCHEMA};

CREATE TABLE IF NOT EXISTS {SILVER_SCHEMA}.news_item (
    news_id BIGSERIAL PRIMARY KEY,
    bronze_raw_id BIGINT UNIQUE NOT NULL REFERENCES bronze.bronze_news_raw(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    source_item_id TEXT NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL,

    headline TEXT,
    summary TEXT,
    body TEXT,
    canonical_url TEXT,

    published_text TEXT,
    published_at TIMESTAMPTZ,

    raw_payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_silver_news_item_source_fetched
ON {SILVER_SCHEMA}.news_item(source, fetched_at DESC);

CREATE INDEX IF NOT EXISTS idx_silver_news_item_published_at
ON {SILVER_SCHEMA}.news_item(published_at DESC);
"""

def ensure_silver_schema(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text(DDL_SQL))

def upsert_news_items_from_bronze(engine: Engine) -> int:
    with engine.begin() as conn:
        result = conn.execute(text(UPSERT_SQL))
    return int(result.rowcount or 0)

# Parse only formats that are safe; keep original text in published_text always.
UPSERT_SQL = f"""
INSERT INTO {SILVER_SCHEMA}.news_item (
    bronze_raw_id,
    source,
    source_item_id,
    fetched_at,
    headline,
    summary,
    body,
    canonical_url,
    published_text,
    published_at,
    raw_payload
)
SELECT
    b.id AS bronze_raw_id,
    b.source,
    b.source_item_id,
    b.fetched_at,
    COALESCE(b.payload->>'headline', b.payload->>'title', b.payload->>'name') AS headline,
    COALESCE(b.payload->>'summary', b.payload->>'snippet', b.payload->>'description') AS summary,
    COALESCE(b.payload->>'article', b.payload->>'content', b.payload->>'body') AS body,
    COALESCE(b.payload->>'link', b.payload->>'url') AS canonical_url,
    COALESCE(
        b.payload->>'published_at',
        b.payload->>'published',
        b.payload->>'updated',
        b.payload->>'datePublished',
        b.payload->>'date',
        b.payload->>'datetime'
    ) AS published_text,
    CASE
        WHEN (b.payload->>'datetime') ~ '^\\d{{10}}(\\.\\d+)?$'
            THEN to_timestamp((b.payload->>'datetime')::double precision)
        WHEN COALESCE(b.payload->>'published_at', b.payload->>'datePublished') ~ '^\\d{{4}}-\\d{{2}}-\\d{{2}}T'
            THEN COALESCE(b.payload->>'published_at', b.payload->>'datePublished')::timestamptz
        ELSE NULL
    END AS published_at,
    b.payload AS raw_payload
FROM (
    SELECT DISTINCT ON (
        COALESCE(b.payload->>'link', b.payload->>'url', lower(trim(COALESCE(b.payload->>'headline', b.payload->>'title', b.payload->>'name'))))
    )
        b.id AS id,
        b.source AS source,
        b.source_item_id AS source_item_id,
        b.fetched_at AS fetched_at,
        b.payload AS payload
    FROM bronze.bronze_news_raw b
    ORDER BY COALESCE(b.payload->>'link', b.payload->>'url', lower(trim(COALESCE(b.payload->>'headline', b.payload->>'title', b.payload->>'name')))), b.fetched_at DESC
) b
ON CONFLICT (bronze_raw_id) DO UPDATE SET
    headline = EXCLUDED.headline,
    summary = EXCLUDED.summary,
    body = EXCLUDED.body,
    canonical_url = EXCLUDED.canonical_url,
    published_text = EXCLUDED.published_text,
    published_at = EXCLUDED.published_at,
    raw_payload = EXCLUDED.raw_payload;
"""

def create_technical_features_with_confidence(engine: Engine) -> None:
    """
    Create extended technical features table with confidence scoring.
    Prevents lookahead bias by tracking when each feature becomes 'reliable'.
    """
    sql = text("""
    CREATE OR REPLACE VIEW silver.returns_technical_features_scored AS
SELECT
    ticker,
    trade_date,
    created_at,
    rsi_14,
    macd,
    macd_signal,
    macd_hist,
    plus_di,
    minus_di,
    adx,
    ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY trade_date) AS row_num_per_ticker,
    CASE WHEN ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY trade_date) < 26 THEN 0.0 ELSE 1.0 END AS feature_confidence,
    (trade_date - MIN(trade_date) OVER (PARTITION BY ticker))::int AS days_since_first_obs
FROM silver.returns_technical_features;
    """)
    with engine.begin() as conn:
        conn.execute(sql)
# Add to src/ingestion/storage/silver.py

def create_model_features_view_safe(engine: Engine) -> None:
    """
    Create unified view with lookahead-bias safeguards.
    - Sorts by created_at (chronological).
    - Includes confidence scores.
    - Supports walk-forward validation by timestamp.
    """
    sql = text("""
    DROP VIEW IF EXISTS silver.returns_model_features CASCADE;

    CREATE VIEW silver.returns_model_features AS
    SELECT
        r.ticker,
        r.trade_date,
        r.created_at AS features_created_at,
        (r.trade_date + TIME '16:00:00')::timestamptz AS predicted_at,
        (r.trade_date + TIME '16:00:00')::timestamptz AS prediction_time,
        'v1.0' AS feature_version,
        r.close,
        r.log_return,
        COALESCE(r.log_return - spy.log_return, 0.0) AS rel_strength_spy,
        COALESCE(t.rsi_14, 50.0) AS rsi_14,
        t.macd,
        t.macd_signal,
        t.macd_hist,
        COALESCE(t.plus_di, 0.0) AS plus_di,
        COALESCE(t.minus_di, 0.0) AS minus_di,
        COALESCE(t.adx, 0.0) AS adx,
        v.garch_vol,
        v.egarch_vol,
        v.garch_aic,
        v.egarch_aic,
        CASE
            WHEN v.garch_vol > 3.0 THEN 'high_volatility'
            WHEN COALESCE(t.adx, 0.0) > 25.0 AND COALESCE(t.plus_di, 0.0) > COALESCE(t.minus_di, 0.0) THEN 'bull_trend'
            WHEN COALESCE(t.adx, 0.0) > 25.0 AND COALESCE(t.minus_di, 0.0) > COALESCE(t.plus_di, 0.0) THEN 'bear_trend'
            ELSE 'sideways'
        END AS market_regime,
        -- Confidence: 1.0 if past warmup, else 0.0
        CASE 
            WHEN ROW_NUMBER() OVER (PARTITION BY r.ticker ORDER BY r.trade_date) < 26 
            THEN 0.0 
            ELSE 1.0 
        END AS feature_confidence,
        -- For walk-forward splits
        ROW_NUMBER() OVER (PARTITION BY r.ticker ORDER BY r.trade_date) AS observation_num
    FROM silver.returns_daily r
    LEFT JOIN silver.returns_daily spy
        ON spy.ticker = 'SPY' AND r.trade_date = spy.trade_date
    LEFT JOIN silver.returns_technical_features t
        ON r.ticker = t.ticker AND r.trade_date = t.trade_date
    LEFT JOIN silver.returns_volatility_features v
        ON r.ticker = v.ticker AND r.trade_date = v.trade_date
    WHERE r.log_return IS NOT NULL
    ORDER BY r.ticker, r.trade_date, r.created_at;
    """)
    with engine.begin() as conn:
        conn.execute(sql)

def ensure_model_features_view(engine: Engine) -> None:
    # Ensure base silver schema/tables are present first
    ensure_silver_schema(engine)
    # If you have a volatility schema helper, call it here too (optional)
    # from ingestion.storage.silver_volatility import ensure_silver_volatility_schema
    # ensure_silver_volatility_schema(engine)

    # Build the scored technical view (depends on data in silver.returns_technical_features)
    create_technical_features_with_confidence(engine)

    # Create final model view using the scored view
    create_model_features_view_safe(engine)