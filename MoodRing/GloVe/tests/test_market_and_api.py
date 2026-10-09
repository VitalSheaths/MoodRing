import json
from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from conftest import Clock, Quotes, prediction
from api.server import create_app
from paper_trading.market import CachedQuoteProvider, MarketEnricher, Quote
from paper_trading.checker import PriceChecker
from paper_trading.backtest import BacktestEngine
from engine.decision_engine import DecisionEngine


def test_quote_cache_expires_without_extending_source_age():
    clock = Clock()
    underlying = Quotes(clock)
    cache = CachedQuoteProvider(underlying, ttl_s=60, clock=clock)
    first = cache.get_quotes(['AAPL'])['AAPL']
    clock.advance(30)
    assert cache.get_quotes(['AAPL'])['AAPL'] is first
    clock.advance(31)
    second = cache.get_quotes(['AAPL'])['AAPL']
    assert second.quote_at > first.quote_at
    assert len(underlying.calls) == 2
    clock.advance(61)
    underlying.prices = {}
    assert cache.get_quotes(['AAPL']) == {}


def test_market_metadata_cache_cleanup(tmp_path):
    clock = Clock()
    path = tmp_path / 'market_cache.json'
    path.write_text(json.dumps({
        'OLD': {'cached_at': (clock() - timedelta(hours=2)).isoformat(), 'data': {}},
        'AAPL': {'cached_at': clock().isoformat(), 'data': {'shortName': 'Apple'}},
        'BROKEN': {},
    }))
    enricher = MarketEnricher(path, clock=clock)
    assert enricher.cleanup_market_cache() == 2
    assert enricher.get_metadata('AAPL')['data']['shortName'] == 'Apple'
    assert list(json.loads(path.read_text())) == ['AAPL']


def test_api_trade_lifecycle_authorization_and_validation(tmp_path):
    clock = Clock()
    provider = Quotes(clock)
    app = create_app(f'sqlite:///{tmp_path / "api.sqlite"}', provider, users={'secret': 'alice', 'other': 'bob'},
                     enable_checker=False, clock=clock)
    headers = {'Authorization': 'Bearer secret'}
    with TestClient(app) as client:
        assert client.get('/api/v1/scenarios').status_code == 401
        created = client.post('/api/v1/scenarios', headers=headers, json={'name': 'Learning', 'config': {'auto_exit': False}})
        assert created.status_code == 201, created.text
        identity = created.json()['id']
        denied = client.get(f'/api/v1/scenarios/{identity}', headers={'Authorization': 'Bearer other'})
        assert denied.status_code == 403
        created_signal = client.post(f'/api/v1/scenarios/{identity}/signals', headers=headers,
            json={'ticker': 'AAPL', 'prediction': prediction(clock)})
        assert created_signal.status_code == 201, created_signal.text
        signal_id = created_signal.json()['id']
        assert client.post('/api/v1/paper-trading/trade', headers=headers,
                           json={'signal_id': signal_id, 'dollars': 100}).status_code == 422
        entry_headers = {**headers, 'Idempotency-Key': 'buy'}
        trade = client.post('/api/v1/paper-trading/trade', headers=entry_headers,
                            json={'signal_id': signal_id, 'dollars': 100})
        assert trade.status_code == 200, trade.text
        trade_id = trade.json()['trade']['id']
        clock.advance()
        provider.prices['AAPL'] = 110
        assert client.post('/api/v1/market/refresh', headers=headers).status_code == 200
        closed = client.post(f'/api/v1/paper-trading/close/{trade_id}', headers={**headers, 'Idempotency-Key': 'sell'})
        assert closed.status_code == 200, closed.text
        history = client.get(f'/api/v1/scenarios/{identity}/history', headers=headers).json()
        assert len(history['fills']) == 2
        assert client.get('/api/v1/market/quotes?tickers=AAPL&tickers=MSFT', headers=headers).status_code == 200
        assert client.post('/api/v1/scenarios', headers=headers,
                           json={'name': 'Invalid', 'config': {'position_size_mode': 'fractional_kelly'}}).status_code == 400
        bad = prediction(clock, prediction_time='2026-10-06T15:01:00')
        assert client.post(f'/api/v1/scenarios/{identity}/signals', headers=headers,
                           json={'ticker': 'AAPL', 'prediction': bad}).status_code == 400
        assert client.post(f'/api/v1/scenarios/{identity}/signals', headers=headers,
                           json={'ticker': 'BAD/TICKER', 'prediction': prediction(clock)}).status_code == 400


def test_unconfigured_api_auth_does_not_grant_user_access(tmp_path):
    with TestClient(create_app(f'sqlite:///{tmp_path / "no-auth.sqlite"}', Quotes(Clock()), users={}, enable_checker=False)) as client:
        assert client.get('/api/v1/scenarios').status_code == 503
        assert client.get('/health').json()['simulation_only'] is True


def test_decision_engine_blocks_missing_old_and_nonfinite_features():
    engine = DecisionEngine()
    values = dict(ticker='AAPL', prob_up=.8, mu=.02, var=.0004, trade_success_prob=.8, drawdown_risk=.02)
    assert engine.evaluate_trade_decision(**values)['action'] == 'PASS'
    assert engine.evaluate_trade_decision(**values, feature_metadata={'hours_since_update': 25})['action'] == 'PASS'
    values['var'] = float('nan')
    assert engine.evaluate_trade_decision(**values, feature_metadata={'hours_since_update': 0})['reason'] == 'INVALID_PREDICTION'


def test_replay_retains_overlapping_positions_and_uses_next_bar_prices():
    clock = Clock()
    bars = [{'timestamp': (clock() + timedelta(minutes=i)).isoformat(),
             'prices': {'AAPL': 100+i, 'MSFT': 50+i}} for i in range(3)]
    signals = [{'ticker': t, 'prediction': prediction(clock)} for t in ['AAPL', 'MSFT']]
    result = BacktestEngine().run(bars, signals, config={'auto_exit': False})['result_json']
    assert len(result['positions']) == 2
    assert len(result['trades']) == 2
    entries = {p['ticker']: p['entry_price'] for p in result['positions']}
    assert entries['AAPL'] == Decimal('101.0505')
    assert entries['MSFT'] == Decimal('51.0255')
    assert result['realized_pnl'] == 0 and result['unrealized_pnl'] > 0


def test_replay_runs_are_persisted_and_comparable_via_api(tmp_path):
    clock = Clock()
    body = {'name': 'Replay', 'bars': [
        {'timestamp': clock().isoformat(), 'prices': {'AAPL': 100}},
        {'timestamp': (clock()+timedelta(minutes=1)).isoformat(), 'prices': {'AAPL': 101}},
    ], 'signals': [{'ticker': 'AAPL', 'prediction': prediction(clock)}], 'model_version': 'checkpoint-sha256'}
    with TestClient(create_app(f'sqlite:///{tmp_path / "runs.sqlite"}', Quotes(clock), users={'token': 'alice'}, enable_checker=False)) as client:
        headers = {'Authorization': 'Bearer token'}
        runs = [client.post('/api/v1/backtest/run', json=body, headers=headers) for _ in range(2)]
        assert all(r.status_code == 201 for r in runs), [r.text for r in runs]
        response = client.post('/api/v1/backtest/compare', headers=headers, json={'ids': [r.json()['id'] for r in runs]})
        assert response.status_code == 200
        assert all(r['model_version'] == 'checkpoint-sha256' for r in response.json()['runs'])
        assert len(client.get('/api/v1/backtest/runs', headers=headers).json()['runs']) == 2


def test_quote_currency_is_not_assumed_to_be_dollars():
    clock = Clock()
    quote = Quote('SAP', 100, clock(), clock(), currency='EUR')
    with pytest.raises(ValueError, match='USD'):
        quote.require_fresh(clock(), 900)


def test_checker_recovers_from_one_provider_failure(trading):
    import asyncio
    service, clock, provider, _ = trading
    scenario = service.create_scenario('alice', 'Loop', config={'auto_exit': False})
    signal = service.publish_signal('alice', scenario['id'], 'AAPL', prediction(clock))
    service.execute_trade('alice', signal['id'], 100, 'entry')
    checker = PriceChecker(service, interval_s=1)
    async def check():
        provider.failure = True
        with pytest.raises(Exception):
            await checker.check_pending_prices()
        provider.failure = False
        clock.advance()
        provider.prices['AAPL'] = 101
        result = await checker.check_pending_prices()
        assert result['positions_updated'] == 1 and checker.last_error is None
    asyncio.run(check())


def test_yfinance_adapter_batches_quotes_and_excludes_other_currencies(monkeypatch):
    import pandas as pd
    import yfinance
    from paper_trading.market import YFinanceQuoteProvider
    clock = Clock()
    index = pd.DatetimeIndex([clock()])
    frame = pd.DataFrame({('AAPL', 'Close'): [100], ('MSFT', 'Close'): [50], ('SAP', 'Close'): [200]}, index=index)
    calls = []
    def download(symbols, **kwargs):
        calls.append((symbols, kwargs))
        return frame
    monkeypatch.setattr(yfinance, 'download', download)
    class Metadata:
        def get_metadata(self, symbol):
            return {'data': {'currency': 'EUR' if symbol == 'SAP' else 'USD'}}
    result = YFinanceQuoteProvider(Metadata()).get_quotes(['MSFT', 'AAPL', 'SAP'])
    assert set(result) == {'AAPL', 'MSFT'}
    assert len(calls) == 1 and calls[0][1]['interval'] == '1m'
    assert result['AAPL'].quote_at == clock()
