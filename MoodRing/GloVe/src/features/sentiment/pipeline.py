from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import pandas as pd
import numpy as np


class HierarchicalSentimentResolver:
    """
    Hierarchical FinBERT Sentiment Resolver.
    Routes article sentiment to Ticker, Sector, and Market tiers.
    Preserves missingness (does NOT replace missing ticker sentiment with SPY news).
    Enforces 4-timestamp tracking: published_at | available_at | ingested_at | feature_created_at.
    """

    SECTOR_MAPPING = {
        "AAPL": "Technology",
        "MSFT": "Technology",
        "AMZN": "Consumer Discretionary",
        "GOOG": "Communication Services",
        "AMD": "Semiconductors",
        "NVDA": "Semiconductors",
        "TSLA": "Automotive / EV",
        "YELP": "Communication Services",
        "NFLX": "Communication Services",
        "ADBE": "Software",
        "BA": "Aerospace & Defense",
        "AIG": "Financials",
    }

    def __init__(self) -> None:
        self._syndication_cache: dict[str, str] = {}

    def compute_article_hash(self, headline: str, url: str) -> str:
        """Deduplicates syndicated news items by headline & URL hashing."""
        clean_str = f"{headline.strip().lower()}|{url.strip().lower()}"
        return hashlib.sha256(clean_str.encode("utf-8")).hexdigest()[:16]

    def is_syndicated_duplicate(self, headline: str, url: str) -> bool:
        """Check if article was already processed (syndication deduplication)."""
        h = self.compute_article_hash(headline, url)
        if h in self._syndication_cache:
            return True
        self._syndication_cache[h] = headline
        return False

    def route_article_sentiment(
        self,
        base_sentiment_score: float,
        ticker: str,
        headline: str,
    ) -> Dict[str, float]:
        """
        Routes FinBERT sentiment hierarchically to Ticker, Sector, and Market tiers.
        Example: NVDA sentiment (+0.78) -> Ticker: +0.78, Sector: +0.65, Market: +0.20
        """
        sector = self.SECTOR_MAPPING.get(ticker, "General Market")

        # Hierarchical decay factors
        sector_sentiment = float(np.clip(base_sentiment_score * 0.83, -1.0, 1.0))
        market_sentiment = float(np.clip(base_sentiment_score * 0.25, -1.0, 1.0))

        return {
            "ticker": ticker,
            "sector": sector,
            "ticker_sentiment": float(np.clip(base_sentiment_score, -1.0, 1.0)),
            "sector_sentiment": sector_sentiment,
            "market_sentiment": market_sentiment,
        }

    def resolve_daily_sentiment(
        self,
        news_items: List[Dict[str, Any]],
        prediction_time_iso: str,
        target_ticker: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Aggregates daily news sentiment for a ticker.
        Enforces 4-timestamp protection: available_at <= prediction_time.
        Preserves missingness when no ticker news exists.
        """
        pred_time = datetime.fromisoformat(prediction_time_iso)
        if pred_time.tzinfo is None:
            pred_time = pred_time.replace(tzinfo=timezone.utc)

        valid_ticker_sentiments: list[float] = []
        valid_sector_sentiments: list[float] = []
        valid_market_sentiments: list[float] = []

        seen = set()
        target_sector = self.SECTOR_MAPPING.get(target_ticker)
        for item in news_items:
            headline = item.get("headline") or item.get("title") or ""
            url = item.get("url") or item.get("link") or ""

            # Availability is checked before dedupe; repeat calls/scenarios are independent.
            available_at_str = item.get("available_at") or item.get("published_at")
            if not available_at_str:
                continue
            known_times = [available_at_str] + [item[k] for k in
                ("ingested_at", "feature_created_at") if item.get(k)]
            available_times = []
            for value in known_times:
                dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                available_times.append(dt)
            if max(available_times) > pred_time:
                continue
            key = self.compute_article_hash(headline, url)
            ticker = item.get("ticker", "SPY")
            entity_key = (key, ticker)
            if entity_key in seen:
                continue
            seen.add(entity_key)

            base_score = float(item.get("finbert_score", 0.0))
            ticker = item.get("ticker", "SPY")

            routed = self.route_article_sentiment(base_score, ticker, headline)
            if target_ticker is None or ticker == target_ticker:
                valid_ticker_sentiments.append(routed["ticker_sentiment"])
            if target_ticker is None or (target_sector is not None and routed["sector"] == target_sector):
                valid_sector_sentiments.append(routed["sector_sentiment"])
            valid_market_sentiments.append(routed["market_sentiment"])

        # Preserve ticker missingness when no ticker news exists
        news_count = len(valid_ticker_sentiments)
        ticker_sentiment = float(np.mean(valid_ticker_sentiments)) if news_count > 0 else 0.0
        sector_sentiment = float(np.mean(valid_sector_sentiments)) if valid_sector_sentiments else 0.0
        market_sentiment = float(np.mean(valid_market_sentiments)) if valid_market_sentiments else 0.0

        return {
            "news_count": news_count,
            "has_ticker_news": bool(news_count > 0),
            "ticker_sentiment": ticker_sentiment,
            "sector_sentiment": sector_sentiment,
            "market_sentiment": market_sentiment,
            "feature_created_at": datetime.now(timezone.utc).isoformat(),
        }
