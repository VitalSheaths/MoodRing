"""Offline lesson: two independent scenarios, entry costs, marks, and realized P&L."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from paper_trading.market import Quote
from paper_trading.service import PaperTradingService
from paper_trading.storage import PaperStore, make_engine


def main():
    clock = [datetime.now(timezone.utc)]
    prices = {'AAPL': 100, 'MSFT': 50}

    class DemoQuotes:
        def get_quotes(self, tickers):
            return {t: Quote(t, prices[t], clock[0], clock[0], 'offline_demo') for t in tickers}

    root = Path(__file__).resolve().parents[1]
    engine = make_engine(f'sqlite:///{root / "resources/demo_paper.sqlite"}')
    service = PaperTradingService(PaperStore(engine), DemoQuotes(), clock=lambda: clock[0])
    try:
        scenarios = [service.create_scenario('learner', name, config={'position_pct': pct, 'auto_exit': False})
                     for name, pct in [('Small allocation', 2), ('Larger allocation', 4)]]
        active = []
        for scenario in scenarios:
            prediction = dict(prob_up=.8, mu=.03, var=.0004, trade_success_prob=.75, drawdown_risk=.02,
                              prediction_time=clock[0].isoformat(), feature_available_at=clock[0].isoformat())
            signal = service.publish_signal('learner', scenario['id'], 'AAPL', prediction)
            trade = service.execute_trade('learner', signal['id'], None, signal['id'])
            active.append((scenario, trade))
        clock[0] += timedelta(minutes=5)
        prices['AAPL'] = 105
        service.refresh_prices('learner')
        print('After price increases from $100 to $105 (positions are still open):')
        for row in service.compare('learner', [s['id'] for s in scenarios]):
            print(f"  {row['name']}: equity ${row['equity']:.2f}; unrealized P&L ${row['unrealized_pnl']:.2f}")
        for scenario, trade in active:
            closed = service.close_trade('learner', trade['id'], f"close:{trade['id']}")
            portfolio = service.get_scenario('learner', scenario['id'])['portfolio']
            print(f"  Closed {scenario['name']}: net profit ${closed['pnl_dollars']:.2f}; cash ${portfolio['cash_balance']:.2f}")
        print(f'History persisted in {root / "resources/demo_paper.sqlite"}')
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
