from datetime import datetime, timedelta, timezone

import pytest

from paper_trading.market import Quote
from paper_trading.service import PaperTradingService
from paper_trading.storage import PaperStore, make_engine


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 6, 15, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, seconds=60):
        self.now += timedelta(seconds=seconds)


class Quotes:
    def __init__(self, clock):
        self.clock = clock
        self.prices = {'AAPL': 100, 'MSFT': 50, 'NVDA': 100}
        self.calls = []
        self.age_s = 0
        self.failure = False

    def get_quotes(self, symbols):
        self.calls.append(list(symbols))
        if self.failure:
            raise RuntimeError('Provider unavailable')
        return {s: Quote(s, self.prices[s], self.clock() - timedelta(seconds=self.age_s), self.clock(), 'fixture')
                for s in symbols if s in self.prices}


def prediction(clock, **changes):
    return {'prob_up': .9, 'mu': .04, 'var': .0004, 'trade_success_prob': .85,
            'drawdown_risk': .02, 'prediction_time': clock().isoformat(),
            'feature_available_at': (clock() - timedelta(seconds=1)).isoformat(), **changes}


@pytest.fixture
def trading(tmp_path):
    clock = Clock()
    quotes = Quotes(clock)
    url = f'sqlite:///{tmp_path / "ledger.sqlite"}'
    engine = make_engine(url)
    service = PaperTradingService(PaperStore(engine), quotes, clock)
    yield service, clock, quotes, url
    engine.dispose()
