from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

DDL_SQL = """
CREATE SCHEMA IF NOT EXISTS silver;

CREATE TABLE IF NOT EXISTS silver.returns_volatility_features (
    ticker TEXT NOT NULL,
    trade_date DATE NOT NULL,

    -- GARCH(1,1) features
    garch_vol DOUBLE PRECISION,
    garch_aic DOUBLE PRECISION,
    garch_bic DOUBLE PRECISION,
    garch_fit_ok BOOLEAN,
    garch_fit_error TEXT,

    -- EGARCH(1,1) features
    egarch_vol DOUBLE PRECISION,
    egarch_aic DOUBLE PRECISION,
    egarch_bic DOUBLE PRECISION,
    egarch_fit_ok BOOLEAN,
    egarch_fit_error TEXT,

    -- Scaling diagnostics
    arch_scale_before DOUBLE PRECISION,
    arch_scale_factor DOUBLE PRECISION,
    n_obs INTEGER,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (ticker, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_silver_volatility_ticker_date
ON silver.returns_volatility_features (ticker, trade_date DESC);
"""


def ensure_silver_volatility_schema(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text(DDL_SQL))


def upsert_volatility_features(engine: Engine, df) -> int:
    """Insert volatility features from DataFrame into silver table."""
    if df.empty:
        return 0

    keep = [
        "ticker", "trade_date",
        "garch_vol", "garch_aic", "garch_bic", "garch_fit_ok", "garch_fit_error",
        "egarch_vol", "egarch_aic", "egarch_bic", "egarch_fit_ok", "egarch_fit_error",
        "arch_scale_before", "arch_scale_factor", "n_obs"
    ]
    rows = df[keep].dropna(subset=["ticker", "trade_date"]).to_dict(orient="records")

    sql = text("""
               INSERT INTO silver.returns_volatility_features (ticker, trade_date,
                                                               garch_vol, garch_aic, garch_bic, garch_fit_ok,
                                                               garch_fit_error,
                                                               egarch_vol, egarch_aic, egarch_bic, egarch_fit_ok,
                                                               egarch_fit_error,
                                                               arch_scale_before, arch_scale_factor, n_obs, updated_at)
               VALUES (:ticker, :trade_date,
                       :garch_vol, :garch_aic, :garch_bic, :garch_fit_ok, :garch_fit_error,
                       :egarch_vol, :egarch_aic, :egarch_bic, :egarch_fit_ok, :egarch_fit_error,
                       :arch_scale_before, :arch_scale_factor, :n_obs, now())
               ON CONFLICT (ticker, trade_date) DO UPDATE SET garch_vol         = EXCLUDED.garch_vol,
                                                              garch_aic         = EXCLUDED.garch_aic,
                                                              garch_bic         = EXCLUDED.garch_bic,
                                                              garch_fit_ok      = EXCLUDED.garch_fit_ok,
                                                              garch_fit_error   = EXCLUDED.garch_fit_error,
                                                              egarch_vol        = EXCLUDED.egarch_vol,
                                                              egarch_aic        = EXCLUDED.egarch_aic,
                                                              egarch_bic        = EXCLUDED.egarch_bic,
                                                              egarch_fit_ok     = EXCLUDED.egarch_fit_ok,
                                                              egarch_fit_error  = EXCLUDED.egarch_fit_error,
                                                              arch_scale_before = EXCLUDED.arch_scale_before,
                                                              arch_scale_factor = EXCLUDED.arch_scale_factor,
                                                              n_obs             = EXCLUDED.n_obs,
                                                              updated_at        = now();
               """)
    with engine.begin() as conn:
        result = conn.execute(sql, rows)
    return int(result.rowcount or 0)