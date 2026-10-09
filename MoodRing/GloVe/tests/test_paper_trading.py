from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from sqlalchemy import select, func

from conftest import prediction
from paper_trading import schema as db
from paper_trading.policy import ScenarioPolicy
from paper_trading.service import (PaperTradingError, NoPriceDataError, NotOwnerError,
                                  TradeAlreadyClosedError, IdempotencyConflictError, PaperTradingService)
from paper_trading.storage import PaperStore, make_engine


def scenario_and_signal(service, clock, ticker='AAPL', config=None):
    scenario = service.create_scenario('alice', 'Conservative', config=config or {'auto_exit': False})
    signal = service.publish_signal('alice', scenario['id'], ticker, prediction(clock))
    return scenario, signal


def test_entry_mark_exit_cash_and_fills_survive_restart(trading):
    service, clock, quotes, url = trading
    scenario, signal = scenario_and_signal(service, clock)
    trade = service.execute_trade('alice', signal['id'], 100, 'entry')
    assert trade['entry_price'] == Decimal('100.05')
    assert trade['entry_fee'] == Decimal('.10')
    state = service.get_scenario('alice', scenario['id'])['portfolio']
    assert state['cash_balance'] == Decimal('9900')
    assert state['unrealized_pnl'] < 0  # Entry fee and slippage.
    clock.advance()
    quotes.prices['AAPL'] = 110
    assert service.refresh_prices()['positions_updated'] == 1
    state = service.get_scenario('alice', scenario['id'])['portfolio']
    assert state['cash_balance'] == Decimal('9900')
    assert state['unrealized_pnl'] > 0
    closed = service.close_trade('alice', trade['id'], 'exit')
    state = service.get_scenario('alice', scenario['id'])['portfolio']
    assert state['cash_balance'] == Decimal('10000') + closed['pnl_dollars']
    assert state['equity'] == state['cash_balance']
    assert state['realized_pnl'] == closed['pnl_dollars']
    assert state['unrealized_pnl'] == 0
    assert state['winning_trades'] == 1
    reopened_engine = make_engine(url)
    try:
        reopened = PaperTradingService(PaperStore(reopened_engine), quotes, clock)
        assert reopened.get_scenario('alice', scenario['id'])['portfolio']['cash_balance'] == state['cash_balance']
        history = reopened.scenario_history('alice', scenario['id'])
        assert len(history['orders']) == len(history['fills']) == 2
        assert {e['event'] for e in history['equity']} == {'created', 'entry', 'mark', 'exit'}
    finally:
        reopened_engine.dispose()


def test_independent_scenarios_and_ownership(trading):
    service, clock, quotes, _ = trading
    first, signal = scenario_and_signal(service, clock)
    second = service.create_scenario('alice', 'Control')
    service.execute_trade('alice', signal['id'], 100, 'buy')
    result = service.compare('alice', [first['id'], second['id']])
    assert result[0]['equity'] < result[1]['equity']
    assert service.get_scenario('alice', second['id'])['portfolio']['cash_balance'] == 10000
    with pytest.raises(NotOwnerError):
        service.get_scenario('bob', first['id'])
    with pytest.raises(NotOwnerError):
        service.execute_trade('bob', signal['id'], 100, 'steal')
    assert service.list_scenarios('bob') == []


def test_idempotency_prevents_duplicate_entries_and_exits(trading):
    service, clock, _, _ = trading
    scenario, signal = scenario_and_signal(service, clock)
    trade = service.execute_trade('alice', signal['id'], 100, 'buy')
    assert service.execute_trade('alice', signal['id'], 100, 'buy')['id'] == trade['id']
    with pytest.raises(IdempotencyConflictError):
        service.execute_trade('alice', signal['id'], 101, 'buy')
    closed = service.close_trade('alice', trade['id'], 'sell')
    assert service.close_trade('alice', trade['id'], 'sell')['pnl_dollars'] == closed['pnl_dollars']
    with pytest.raises(TradeAlreadyClosedError):
        service.close_trade('alice', trade['id'], 'new-key')
    with pytest.raises(PaperTradingError, match='already been traded'):
        service.execute_trade('alice', signal['id'], 100, 'new-signal-key')
    assert len(service.scenario_history('alice', scenario['id'])['fills']) == 2


def test_failed_fill_rolls_back_cash_position_and_order(trading, monkeypatch):
    service, clock, _, _ = trading
    scenario, signal = scenario_and_signal(service, clock)
    def fail(*args, **kwargs):
        raise RuntimeError('Storage fault')
    monkeypatch.setattr(service, '_fill', fail)
    with pytest.raises(RuntimeError):
        service.execute_trade('alice', signal['id'], 100, 'buy')
    state = service.get_scenario('alice', scenario['id'])['portfolio']
    assert state['cash_balance'] == 10000 and state['positions'] == []
    assert service.scenario_history('alice', scenario['id'])['trades'] == []


def test_concurrent_accounts_cannot_exceed_exposure(trading):
    service, clock, quotes, url = trading
    scenario = service.create_scenario('alice', 'Exposure', initial_capital=1000, config={
        'position_pct': 25, 'max_position_pct': 25, 'max_gross_exposure_pct': 30, 'auto_exit': False})
    signals = [service.publish_signal('alice', scenario['id'], ticker, prediction(clock)) for ticker in ['AAPL', 'MSFT']]
    other_engine = make_engine(url)
    other = PaperTradingService(PaperStore(other_engine), quotes, clock)
    def buy(pair):
        svc, signal = pair
        try:
            return svc.execute_trade('alice', signal['id'], 200, signal['id'])
        except PaperTradingError:
            return None
    try:
        with ThreadPoolExecutor(2) as pool:
            outcomes = list(pool.map(buy, [(service, signals[0]), (other, signals[1])]))
        assert sum(o is not None for o in outcomes) == 1
        assert len(service.get_scenario('alice', scenario['id'])['portfolio']['positions']) == 1
    finally:
        other_engine.dispose()


def test_stale_or_future_quotes_never_fill(trading):
    service, clock, quotes, _ = trading
    scenario, signal = scenario_and_signal(service, clock)
    quotes.age_s = 901
    with pytest.raises(NoPriceDataError):
        service.execute_trade('alice', signal['id'], 100, 'stale')
    quotes.age_s = -1
    with pytest.raises(NoPriceDataError):
        service.execute_trade('alice', signal['id'], 100, 'future')
    assert service.get_scenario('alice', scenario['id'])['portfolio']['cash_balance'] == 10000


def test_provider_failure_retains_mark_and_rejects_trade(trading):
    service, clock, quotes, _ = trading
    scenario, signal = scenario_and_signal(service, clock)
    service.execute_trade('alice', signal['id'], 100, 'buy')
    before = service.get_scenario('alice', scenario['id'])['portfolio']['equity']
    quotes.failure = True
    with pytest.raises(NoPriceDataError):
        service.refresh_prices()
    assert service.get_scenario('alice', scenario['id'])['portfolio']['equity'] == before
    clock.advance(1000)
    assert service.get_scenario('alice', scenario['id'])['portfolio']['valuation_is_stale']


def test_stop_loss_gap_closes_at_observed_price_and_fees(trading):
    service, clock, quotes, _ = trading
    scenario, signal = scenario_and_signal(service, clock, config={'auto_exit': True})
    trade = service.execute_trade('alice', signal['id'], 100, 'buy')
    clock.advance()
    quotes.prices['AAPL'] = 80
    result = service.refresh_prices()
    closed = service.scenario_history('alice', scenario['id'])['trades'][0]
    assert result['positions_closed'] == 1
    assert closed['exit_reason'] == 'stop_loss'
    assert closed['exit_price'] == Decimal('79.96')
    assert closed['pnl_dollars'] < -20
    service.refresh_prices()
    assert len(service.scenario_history('alice', scenario['id'])['fills']) == 2


def test_take_profit_and_signal_performance(trading):
    service, clock, quotes, _ = trading
    scenario, signal = scenario_and_signal(service, clock, config={'auto_exit': True})
    service.execute_trade('alice', signal['id'], 100, 'buy')
    clock.advance(3601)
    quotes.prices['AAPL'] = 120
    assert service.refresh_prices()['positions_closed'] == 1
    assert quotes.calls[-1] == ['AAPL']
    history = service.signal_history('alice', signal['id'])['performance']
    assert history['price_1h'] == 120 and history['price_4h'] is None
    assert history['max_gain_pct'] == 20
    assert service.scenario_history('alice', scenario['id'])['trades'][0]['exit_reason'] == 'take_profit'


def test_timestamp_contract_and_expired_prediction(trading):
    service, clock, _, _ = trading
    scenario = service.create_scenario('alice', 'Cutoff')
    with pytest.raises(PaperTradingError, match='available'):
        service.publish_signal('alice', scenario['id'], 'AAPL', prediction(clock, feature_available_at='2026-10-06T15:01:00+00:00'))
    with pytest.raises(PaperTradingError, match='offset'):
        service.publish_signal('alice', scenario['id'], 'AAPL', prediction(clock, prediction_time='2026-10-06T15:00:00'))
    signal = service.publish_signal('alice', scenario['id'], 'AAPL', prediction(clock))
    clock.advance(3601)
    with pytest.raises(PaperTradingError, match='expired'):
        service.execute_trade('alice', signal['id'], 100, 'late')


def test_prediction_drawdown_and_account_drawdown_pause(trading):
    service, clock, quotes, _ = trading
    scenario = service.create_scenario('alice', 'Risk', config={
        'max_drawdown_pct': 1, 'position_pct': 10, 'auto_exit': False})
    risk = service.publish_signal('alice', scenario['id'], 'MSFT', prediction(clock, drawdown_risk=.3))
    with pytest.raises(PaperTradingError, match='Predicted drawdown'):
        service.execute_trade('alice', risk['id'], 100, 'risk')
    signal = service.publish_signal('alice', scenario['id'], 'AAPL', prediction(clock))
    service.execute_trade('alice', signal['id'], 500, 'buy')
    clock.advance()
    quotes.prices['AAPL'] = 50
    service.refresh_prices()
    signal2 = service.publish_signal('alice', scenario['id'], 'MSFT', prediction(clock))
    with pytest.raises(PaperTradingError, match='Portfolio drawdown'):
        service.execute_trade('alice', signal2['id'], 100, 'blocked')


def test_fractional_kelly_requires_payoff_and_is_capped():
    policy = ScenarioPolicy(position_size_mode='fractional_kelly', kelly_win_probability=.6,
                            kelly_payoff_ratio=2, kelly_fraction=.25, max_position_pct=10)
    assert policy.fraction(.5) == Decimal('.10')
    with pytest.raises(ValueError, match='payoff'):
        ScenarioPolicy(position_size_mode='fractional_kelly').fraction(.5)
    negative = ScenarioPolicy(position_size_mode='fractional_kelly', kelly_win_probability=.2, kelly_payoff_ratio=1)
    assert negative.fraction(.5) == 0


def test_automatic_exit_retries_after_mark_was_committed(trading, monkeypatch):
    service, clock, quotes, _ = trading
    scenario, signal = scenario_and_signal(service, clock, config={'auto_exit': True})
    service.execute_trade('alice', signal['id'], 100, 'buy')
    clock.advance()
    quotes.prices['AAPL'] = 80
    original = service.close_trade
    def temporary_failure(*args, **kwargs):
        raise RuntimeError('Transient exit fault')
    monkeypatch.setattr(service, 'close_trade', temporary_failure)
    with pytest.raises(RuntimeError):
        service.refresh_prices()
    monkeypatch.setattr(service, 'close_trade', original)
    assert service.refresh_prices()['positions_closed'] == 1
    assert len(service.scenario_history('alice', scenario['id'])['fills']) == 2


def test_batch_refresh_tracks_multiple_tickers_with_one_provider_call(trading):
    service, clock, quotes, _ = trading
    scenario, signal = scenario_and_signal(service, clock)
    second = service.publish_signal('alice', scenario['id'], 'MSFT', prediction(clock))
    service.execute_trade('alice', signal['id'], 100, 'one')
    service.execute_trade('alice', second['id'], 100, 'two')
    clock.advance()
    calls = len(quotes.calls)
    result = service.refresh_prices()
    assert len(quotes.calls) == calls + 1
    assert quotes.calls[-1] == ['AAPL', 'MSFT']
    assert result['positions_updated'] == 2


def test_refresh_does_not_rewind_marks_with_an_older_observation(trading):
    service, clock, quotes, _ = trading
    scenario, signal = scenario_and_signal(service, clock)
    service.execute_trade('alice', signal['id'], 100, 'buy')
    clock.advance(60)
    quotes.prices['AAPL'] = 110
    service.refresh_prices()
    observed = service.get_scenario('alice', scenario['id'])['portfolio']['positions'][0]
    clock.advance(1)
    quotes.age_s = 30
    quotes.prices['AAPL'] = 90
    assert service.refresh_prices()['positions_updated'] == 0
    retained = service.get_scenario('alice', scenario['id'])['portfolio']['positions'][0]
    assert retained['mark_price'] == observed['mark_price']
    assert retained['quote_at'] == observed['quote_at']
