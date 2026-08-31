from __future__ import annotations

from ingestion.storage.postgres import get_engine
from ingestion.storage.gold import (
    ensure_gold_schema,
    build_macro_daily_features,
    build_macro_weekly_features,
)


def main() -> None:
    engine = get_engine()

    ensure_gold_schema(engine)
    n_daily = build_macro_daily_features(
        engine,
        returns_table="silver.returns_daily",
        returns_date_col="trade_date",
        returns_ticker_col="ticker",
    )
    n_weekly = build_macro_weekly_features(engine)

    print(f"[macro] gold daily upsert rows: {n_daily}")
    print(f"[macro] gold weekly upsert rows: {n_weekly}")


if __name__ == "__main__":
    main()