from __future__ import annotations

import pandas as pd
from sqlalchemy import text

from config.data_io import get_engine
from features.volatility.arch_models import fit_vol_models_for_group, ArchConfig
from ingestion.storage.silver_volatility import ensure_silver_volatility_schema, upsert_volatility_features


def load_returns() -> pd.DataFrame:
    """Load returns from silver.returns_daily."""
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


def prepare_for_arch(df: pd.DataFrame) -> pd.DataFrame:
    """Prepare returns for ARCH models: scale and compute r2."""
    out = df.copy()
    out["log_return_scaled"] = out["log_return"] * 100
    out["r2"] = out["log_return_scaled"] ** 2
    return out


def main() -> None:
    ensure_silver_volatility_schema(get_engine())

    returns = load_returns()
    if returns.empty:
        print("[volatility] no returns data found")
        return

    returns = prepare_for_arch(returns)

    cfg = ArchConfig()
    engine = get_engine()

    # Fit per ticker
    out_dfs = []
    for ticker, group in returns.groupby("ticker"):
        fitted = fit_vol_models_for_group(group, cfg)
        out_dfs.append(fitted)

    combined = pd.concat(out_dfs, ignore_index=True)

    # Upsert to silver
    n = upsert_volatility_features(engine, combined)

    # Summary diagnostics
    diag = combined.groupby("ticker").agg({
        "garch_aic": "first",
        "egarch_aic": "first",
        "garch_bic": "first",
        "egarch_bic": "first",
    }).reset_index()

    print(f"[volatility] upserted {n} rows")
    print("\nModel diagnostics (AIC/BIC per ticker):")
    print(diag)


if __name__ == "__main__":
    main()