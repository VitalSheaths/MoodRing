from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Tuple

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    # Optional in notebook environments; env vars can still come from shell/CI.
    pass


def _split_csv_env(name: str, default: str = "") -> Tuple[str, ...]:
    raw = os.getenv(name, default).strip()
    if not raw:
        return tuple()
    return tuple(x.strip() for x in raw.split(",") if x.strip())


@dataclass(frozen=True)
class Settings:
    ENV: str = os.getenv("ENV", "dev")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg2://localhost/postgres",
    )
    PARQUET_ROOT: str = os.getenv("PARQUET_ROOT", "resources/generated_files/parquet")

    REQUEST_TIMEOUT_S: int = int(os.getenv("REQUEST_TIMEOUT_S", "20"))
    MAX_CONCURRENCY: int = int(os.getenv("MAX_CONCURRENCY", "10"))
    POLL_INTERVAL_S: int = int(os.getenv("POLL_INTERVAL_S", "300"))

    USER_AGENT: str = os.getenv("USER_AGENT", "news-ingestor/1.0")

    # API keys
    SERPAPI_KEY: str = os.getenv("SERPAPI_KEY", "")
    FINNHUB_KEY: str = os.getenv("FINNHUB_KEY", "")
    BING_API_KEY: str = os.getenv("BING_API_KEY", "")

    # Connector inputs
    RSS_FEED_URLS: Tuple[str, ...] = _split_csv_env(
        "RSS_FEED_URLS",
        "https://huggingface.co/datasets/danidanou/Reuters_Financial_News/resolve/main/summ_financial_data.parquet.gzip",
    )
    SERP_QUERIES: Tuple[str, ...] = _split_csv_env(
        "SERP_QUERIES",
        "stock market,earnings,interest rates,federal reserve",
    )
    FINNHUB_SYMBOLS: Tuple[str, ...] = _split_csv_env(
        "FINNHUB_SYMBOLS",
        "AAPL,MSFT,AMZN,GOOGL,NVDA",
    )
    FINNHUB_LOOKBACK_DAYS: int = int(os.getenv("FINNHUB_LOOKBACK_DAYS", "1"))
    BING_QUERIES: Tuple[str, ...] = _split_csv_env(
        "BING_QUERIES",
        "stock market,earnings",
    )


settings = Settings()
