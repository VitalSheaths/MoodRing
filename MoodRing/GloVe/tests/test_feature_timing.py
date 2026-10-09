from datetime import timedelta
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from conftest import Clock
from features.sentiment.pipeline import HierarchicalSentimentResolver
from features.volatility import arch_models
from config.paths import SRC_DIR, PROJECT_ROOT, INGESTION_STATE_DIR


def test_sentiment_scopes_tickers_and_repeats_across_scenarios():
    resolver = HierarchicalSentimentResolver()
    clock = Clock()
    items = [{'ticker': 'SPY', 'headline': 'Market optimism', 'url': 'spy', 'finbert_score': .8,
              'available_at': clock().isoformat()}]
    first = resolver.resolve_daily_sentiment(items, clock().isoformat(), target_ticker='NVDA')
    second = resolver.resolve_daily_sentiment(items, clock().isoformat(), target_ticker='NVDA')
    assert first['has_ticker_news'] is False
    assert first['ticker_sentiment'] == 0 and first['market_sentiment'] > 0
    assert second['market_sentiment'] == first['market_sentiment']


def test_future_article_does_not_poison_dedup_or_use_late_scoring():
    resolver = HierarchicalSentimentResolver()
    clock = Clock()
    item = {'ticker': 'NVDA', 'headline': 'Demand', 'url': 'nvda', 'finbert_score': .7,
            'available_at': (clock()+timedelta(seconds=1)).isoformat()}
    assert resolver.resolve_daily_sentiment([item], clock().isoformat())['news_count'] == 0
    clock.advance()
    assert resolver.resolve_daily_sentiment([item], clock().isoformat())['news_count'] == 1
    item['feature_created_at'] = (clock()+timedelta(seconds=1)).isoformat()
    assert resolver.resolve_daily_sentiment([item], clock().isoformat())['news_count'] == 0
    del item['available_at']
    assert resolver.resolve_daily_sentiment([item], clock().isoformat())['news_count'] == 0


def test_volatility_forecasts_do_not_change_when_future_returns_change(monkeypatch):
    observed = []
    def fake_model(y, **kwargs):
        observed.append(np.asarray(y))
        value = float(np.mean(np.square(y))) + .01
        fitted = SimpleNamespace(convergence_flag=0, aic=1, bic=2,
            forecast=lambda **kw: SimpleNamespace(variance=pd.DataFrame([[value]])))
        return SimpleNamespace(fit=lambda **kw: fitted)
    monkeypatch.setattr(arch_models, 'arch_model', fake_model)
    source = pd.DataFrame({'ticker': ['AAPL']*6, 'trade_date': pd.date_range('2026-10-01', periods=6),
                           'log_return_scaled': [.1, -.2, .3, -.1, .4, .2]})
    cfg = arch_models.ArchConfig(min_obs=3, window=4)
    first = arch_models.fit_vol_models_for_group(source, cfg)
    changed = source.copy()
    changed.loc[5, 'log_return_scaled'] = 1000
    second = arch_models.fit_vol_models_for_group(changed, cfg)
    np.testing.assert_allclose(first['garch_vol'].iloc[:5], second['garch_vol'].iloc[:5], equal_nan=True)
    assert first['garch_vol'].iloc[5] != second['garch_vol'].iloc[5]
    assert not first['garch_fit_ok'].iloc[0] and np.isnan(first['garch_vol'].iloc[0])
    assert first['n_obs'].iloc[-1] == 4


def test_config_paths_stay_in_project():
    assert SRC_DIR == PROJECT_ROOT / 'src'
    assert INGESTION_STATE_DIR == SRC_DIR / 'ingestion/storage/state'
