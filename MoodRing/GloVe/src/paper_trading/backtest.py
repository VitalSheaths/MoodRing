"""Chronological replay using the SAME ledger and risk policy as paper trading."""
from datetime import timedelta
from decimal import Decimal

from paper_trading.market import Quote, timestamp
from paper_trading.service import PaperTradingService, PaperTradingError
from paper_trading.storage import PaperStore, make_engine


class ReplayQuoteProvider:
    def __init__(self):
        self.quotes = {}

    def get_quotes(self, symbols):
        return {s: self.quotes[s] for s in symbols if s in self.quotes}


class BacktestEngine:
    def run(self, bars, signals, *, initial_capital=10000, config=None, model_version='manual-v1', research=None):
        if not bars:
            raise ValueError('At least one timestamped price event is required')
        ordered = sorted(bars, key=lambda row: timestamp(row['timestamp']))
        if len({timestamp(row['timestamp']) for row in ordered}) != len(ordered):
            raise ValueError('Use one event per timestamp with all tickers in its prices dictionary')
        clock = [timestamp(ordered[0]['timestamp'])]
        provider = ReplayQuoteProvider()
        engine = make_engine('sqlite://')
        service = PaperTradingService(PaperStore(engine), provider, clock=lambda: clock[0])
        try:
            scenario = service.create_scenario('replay', 'Backtest', initial_capital, config, model_version, research=research)
            scenario_id = scenario['id']
            pending, skipped = [], []
            predictions = sorted(signals, key=lambda row: timestamp(row['prediction']['prediction_time']))
            index = 0
            for bar in ordered:
                clock[0] = timestamp(bar['timestamp'])
                provider.quotes = {symbol: Quote(symbol, price, clock[0], clock[0], 'historical_replay')
                                   for symbol, price in bar['prices'].items()}
                service.refresh_prices('replay')
                # Signals observed on an earlier bar fill on a later event, never at an already-known exit.
                for signal in pending:
                    try:
                        service.execute_trade('replay', signal['id'], None, f"replay:{signal['id']}")
                    except PaperTradingError as exc:
                        skipped.append({'signal_id': signal['id'], 'reason': str(exc)})
                pending = []
                while index < len(predictions) and timestamp(predictions[index]['prediction']['prediction_time']) <= clock[0]:
                    item = predictions[index]
                    try:
                        pending.append(service.publish_signal('replay', scenario_id, item['ticker'], item['prediction']))
                    except PaperTradingError as exc:
                        skipped.append({'ticker': item['ticker'], 'reason': str(exc)})
                    index += 1
            final = service.get_scenario('replay', scenario_id)
            history = service.scenario_history('replay', scenario_id, limit=100000)
            state = final['portfolio']
            # Open positions remain marked at the last available price; no forced hindsight exit.
            return {'config_json': final['config_json'], 'model_version': model_version,
                    'research_json': research or {}, 'result_json': {
                        'equity': state['equity'], 'realized_pnl': state['realized_pnl'],
                        'unrealized_pnl': state['unrealized_pnl'], 'positions': state['positions'],
                        'total_return_pct': state['total_return_pct'], 'trades': history['trades'],
                        'equity_curve': list(reversed(history['equity'])), 'skipped': skipped,
                        'unfilled_signal_count': len(pending) + len(predictions) - index}}
        finally:
            engine.dispose()
