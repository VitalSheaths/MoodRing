"""Timestamped quotes and a separate, slower metadata cache."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
from pathlib import Path
import re
import threading
from typing import Protocol

from paper_trading.policy import decimal

log = logging.getLogger(__name__)


def utcnow():
    return datetime.now(timezone.utc)


def timestamp(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None:
        raise ValueError('A UTC offset is required on timestamps')
    return value.astimezone(timezone.utc)


def ticker_symbol(value):
    value = value.strip().upper()
    if not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,14}', value):
        raise ValueError('Use a stock ticker such as AAPL or BRK-B')
    return value


@dataclass(frozen=True)
class Quote:
    ticker: str
    price: Decimal
    quote_at: datetime
    fetched_at: datetime
    source: str = 'yfinance_1m'
    currency: str = 'USD'

    def __post_init__(self):
        object.__setattr__(self, 'ticker', ticker_symbol(self.ticker))
        object.__setattr__(self, 'price', decimal(self.price))
        object.__setattr__(self, 'quote_at', timestamp(self.quote_at))
        object.__setattr__(self, 'fetched_at', timestamp(self.fetched_at))
        if self.price <= 0:
            raise ValueError('Quote price must be positive')

    def require_fresh(self, now, max_age_s):
        if self.currency != 'USD':
            raise ValueError('This paper ledger accepts USD-denominated quotes only')
        age = (timestamp(now) - self.quote_at).total_seconds()
        fetch_age = (timestamp(now) - self.fetched_at).total_seconds()
        if age < 0 or fetch_age < 0 or age > max_age_s or fetch_age > max_age_s:
            raise ValueError(f'No fresh quote for {self.ticker}; source timestamp is {self.quote_at.isoformat()}')
        return self


class QuoteProvider(Protocol):
    def get_quotes(self, tickers: list[str]) -> dict[str, Quote]: ...


class YFinanceQuoteProvider:
    """Poll one-minute bars. These are indicative, potentially delayed prices."""
    def __init__(self, metadata_cache=None):
        self.metadata_cache = metadata_cache

    def get_quotes(self, tickers):
        if not tickers:
            return {}
        import yfinance as yf
        import pandas as pd
        symbols = sorted(set(ticker_symbol(t) for t in tickers))
        frame = yf.download(symbols, period='1d', interval='1m', group_by='ticker',
                            auto_adjust=False, progress=False, threads=False, prepost=False)
        fetched_at = utcnow()
        result = {}
        for symbol in symbols:
            try:
                data = frame[symbol] if isinstance(frame.columns, pd.MultiIndex) else frame
                close = data['Close'].dropna()
                if close.empty:
                    continue
                at = close.index[-1].to_pydatetime()
                if self.metadata_cache is not None:
                    currency = self.metadata_cache.get_metadata(symbol)['data'].get('currency')
                else:
                    currency = yf.Ticker(symbol).fast_info.get('currency')
                if currency != 'USD':
                    log.warning('Skipping %s: currency is %s, not USD', symbol, currency)
                    continue
                # Never infer UTC for an ambiguous provider timestamp.
                result[symbol] = Quote(symbol, decimal(close.iloc[-1]), timestamp(at), fetched_at, currency=currency)
            except (KeyError, ValueError, TypeError) as exc:
                log.warning('No usable quote for %s: %s', symbol, exc)
        return result


class CachedQuoteProvider:
    def __init__(self, provider: QuoteProvider, ttl_s=60, clock=utcnow):
        self.provider, self.ttl_s, self.clock = provider, ttl_s, clock
        self.cache = {}
        self.lock = threading.Lock()

    def get_quotes(self, tickers):
        symbols = sorted(set(ticker_symbol(t) for t in tickers))
        with self.lock:
            now = self.clock()
            missing = [t for t in symbols if t not in self.cache
                       or (now - self.cache[t].fetched_at).total_seconds() > self.ttl_s]
            if missing:
                fresh = self.provider.get_quotes(missing)
                for symbol in missing:
                    # A failed refresh must not revive a previously cached execution price.
                    self.cache.pop(symbol, None)
                    if symbol in fresh:
                        self.cache[symbol] = fresh[symbol]
            return {t: self.cache[t] for t in symbols if t in self.cache}


class MarketEnricher:
    """Metadata only. The one-hour cache is never an execution-price source."""
    def __init__(self, path: Path, ttl_s=3600, clock=utcnow):
        self.path, self.ttl_s, self.clock = Path(path), ttl_s, clock
        self.lock = threading.RLock()
        self.cache = {}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                if isinstance(data, dict):
                    self.cache = data
            except (OSError, ValueError):
                log.warning('Discarding unreadable market metadata cache')

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(self.cache, default=str))
        temporary.replace(self.path)

    def cleanup_market_cache(self):
        with self.lock:
            stale = []
            for symbol, item in self.cache.items():
                try:
                    age = (self.clock() - timestamp(item['cached_at'])).total_seconds()
                    if not 0 <= age <= self.ttl_s:
                        stale.append(symbol)
                except (KeyError, TypeError, ValueError):
                    stale.append(symbol)
            for symbol in stale:
                del self.cache[symbol]
            if stale:
                self._save()
            return len(stale)

    def get_metadata(self, symbol):
        symbol = ticker_symbol(symbol)
        with self.lock:
            self.cleanup_market_cache()
            if symbol not in self.cache:
                import yfinance as yf
                info = yf.Ticker(symbol).info
                self.cache[symbol] = {'cached_at': self.clock().isoformat(), 'data': {
                    k: info.get(k) for k in ('shortName', 'sector', 'industry', 'marketCap', 'currency')
                }}
                self._save()
            return self.cache[symbol]
