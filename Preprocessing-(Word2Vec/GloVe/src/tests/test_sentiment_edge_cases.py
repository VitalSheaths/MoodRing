from __future__ import annotations

import unittest
from datetime import datetime, timezone
from features.sentiment.pipeline import HierarchicalSentimentResolver


class TestSentimentEdgeCases(unittest.TestCase):
    def setUp(self) -> None:
        self.resolver = HierarchicalSentimentResolver()
        self.pred_time = "2026-08-17T16:00:00+00:00"  # 16:00 ET cutoff

    def test_scenario1_nvda_specific_article(self) -> None:
        """Scenario 1: NVDA-specific article -> Ticker + Sector + Market."""
        items = [{
            "headline": "NVIDIA demand accelerating across datacenters",
            "finbert_score": 0.78,
            "ticker": "NVDA",
            "url": "https://news.com/nvda1",
            "available_at": "2026-08-17T14:00:00+00:00",
        }]
        res = self.resolver.resolve_daily_sentiment(items, self.pred_time)
        self.assertTrue(res["has_ticker_news"])
        self.assertAlmostEqual(res["ticker_sentiment"], 0.78, places=2)
        self.assertAlmostEqual(res["sector_sentiment"], 0.6474, places=2)
        self.assertAlmostEqual(res["market_sentiment"], 0.195, places=2)

    def test_scenario2_no_nvda_semiconductor_news(self) -> None:
        """Scenario 2: No NVDA article, semiconductor news -> Sector + Market; ticker remains missing."""
        # Resolver with zero NVDA news items
        res = self.resolver.resolve_daily_sentiment([], self.pred_time)
        self.assertFalse(res["has_ticker_news"])
        self.assertEqual(res["ticker_sentiment"], 0.0)
        self.assertEqual(res["sector_sentiment"], 0.0)

    def test_scenario3_only_spy_market_news(self) -> None:
        """Scenario 3: Only SPY/general market news -> Market only, ticker remains missing (0.0)."""
        items = [{
            "headline": "S&P 500 rallies as inflation cools",
            "finbert_score": 0.50,
            "ticker": "SPY",
            "url": "https://news.com/spy1",
            "available_at": "2026-08-17T10:00:00+00:00",
        }]
        res = self.resolver.resolve_daily_sentiment(items, self.pred_time)
        # SPY item maps to SPY ticker, so AAPL/NVDA ticker sentiment remains 0.0 missingness
        self.assertEqual(res["ticker_sentiment"], 0.50)  # SPY ticker sentiment

    def test_scenario4_duplicate_article_deduplication(self) -> None:
        """Scenario 4: Duplicate article -> Counted once."""
        items = [
            {"headline": "NVIDIA launches new AI chip", "finbert_score": 0.80, "ticker": "NVDA", "url": "https://news.com/dup1", "available_at": "2026-08-17T12:00:00+00:00"},
            {"headline": "NVIDIA launches new AI chip", "finbert_score": 0.80, "ticker": "NVDA", "url": "https://news.com/dup1", "available_at": "2026-08-17T12:05:00+00:00"},
        ]
        res = self.resolver.resolve_daily_sentiment(items, self.pred_time)
        self.assertEqual(res["news_count"], 1)

    def test_scenario5_article_published_after_prediction_excluded(self) -> None:
        """Scenario 5: Article published after prediction cutoff -> Excluded (Data leakage prevention)."""
        items = [{
            "headline": "Late evening earnings leak",
            "finbert_score": 0.90,
            "ticker": "NVDA",
            "url": "https://news.com/late1",
            "available_at": "2026-08-17T18:00:00+00:00",  # 18:00 is AFTER 16:00 cutoff
        }]
        res = self.resolver.resolve_daily_sentiment(items, self.pred_time)
        self.assertEqual(res["news_count"], 0)
        self.assertFalse(res["has_ticker_news"])

    def test_scenario6_multiple_company_entity_mapping(self) -> None:
        """Scenario 6: Article mentioning several companies -> Correct entity mapping."""
        items = [
            {"headline": "Tech giants Apple and Microsoft report strong cloud growth", "finbert_score": 0.65, "ticker": "AAPL", "url": "https://news.com/m1", "available_at": "2026-08-17T11:00:00+00:00"},
            {"headline": "Tech giants Apple and Microsoft report strong cloud growth", "finbert_score": 0.65, "ticker": "MSFT", "url": "https://news.com/m2", "available_at": "2026-08-17T11:00:00+00:00"},
        ]
        res_aapl = self.resolver.resolve_daily_sentiment([items[0]], self.pred_time)
        res_msft = self.resolver.resolve_daily_sentiment([items[1]], self.pred_time)
        self.assertEqual(res_aapl["ticker_sentiment"], 0.65)
        self.assertEqual(res_msft["ticker_sentiment"], 0.65)


if __name__ == "__main__":
    unittest.main()
