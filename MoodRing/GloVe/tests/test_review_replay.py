from datetime import timedelta

from paper_trading.backtest import BacktestEngine
from conftest import Clock, prediction


def test_replay_accounts_for_every_input_once():
    clock = Clock()
    bars = [{'timestamp': (clock() + timedelta(minutes=i)).isoformat(), 'prices': {'AAPL': 100}}
            for i in range(3)]
    signals = [
        {'ticker': 'AAPL', 'prediction': prediction(clock)},
        {'ticker': 'AAPL', 'prediction': prediction(clock)},
        {'ticker': 'AAPL', 'prediction': prediction(clock, mu=-.01)},
        {'ticker': 'MISSING', 'prediction': prediction(clock)},
        {'ticker': 'AAPL', 'prediction': prediction(clock, prediction_time=(clock()+timedelta(minutes=2)).isoformat())},
        {'ticker': 'AAPL', 'prediction': prediction(clock, prediction_time=(clock()+timedelta(minutes=3)).isoformat())},
        {'ticker': 'AAPL', 'prediction': prediction(clock, prediction_time=(clock()-timedelta(hours=2)).isoformat(),
                                                       feature_available_at=(clock()-timedelta(hours=3)).isoformat())},
    ]
    result = BacktestEngine().run(bars, signals, config={'auto_exit': False})['result_json']
    assert [o['status'] for o in result['signal_outcomes']] == [
        'traded', 'position_exists', 'not_tradeable', 'rejected', 'no_later_event', 'never_eligible', 'expired']
    assert sum(result['signal_status_counts'].values()) == len(signals)
    assert result['unfilled_signal_count'] == 6
    assert len(result['trades']) == result['signal_status_counts']['traded'] == 1


def test_replay_signal_expiring_before_fill_is_counted():
    clock = Clock()
    bars = [{'timestamp': (clock()+timedelta(seconds=i)).isoformat(), 'prices': {'AAPL': 100}} for i in (0, 61)]
    result = BacktestEngine().run(bars, [{'ticker': 'AAPL', 'prediction': prediction(clock)}],
                                  config={'signal_max_age_s': 60})['result_json']
    assert result['signal_status_counts'] == {'expired': 1}
    assert result['unfilled_signal_count'] == 1
    assert result['trades'] == []
