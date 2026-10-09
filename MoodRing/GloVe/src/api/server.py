"""Local paper trading API. Run with: PYTHONPATH=src python -m api.server."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from decimal import Decimal
import json
import os
from pathlib import Path
import secrets
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from config.config import settings
from paper_trading.backtest import BacktestEngine
from paper_trading.checker import PriceChecker
from paper_trading.market import CachedQuoteProvider, MarketEnricher, YFinanceQuoteProvider, ticker_symbol
from paper_trading.policy import ScenarioPolicy
from paper_trading.service import NoPriceDataError, PaperTradingError, PaperTradingService
from paper_trading.storage import PaperStore, make_engine


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class ScenarioRequest(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    initial_capital: Decimal = Field(default=Decimal('10000'), gt=0, le=1000000000)
    config: ScenarioPolicy = Field(default_factory=ScenarioPolicy)
    model_version: str = Field(default='manual-v1', min_length=1, max_length=128)
    dataset_version: str | None = Field(default=None, max_length=128)
    feature_version: str | None = Field(default=None, max_length=128)
    research: dict[str, Any] = Field(default_factory=dict)


class PredictionRequest(StrictModel):
    prob_up: float = Field(ge=0, le=1)
    mu: float
    var: float = Field(gt=0)
    trade_success_prob: float = Field(ge=0, le=1)
    drawdown_risk: float = Field(ge=0, le=1)
    prediction_time: datetime
    feature_available_at: datetime
    missing_ratio: float = Field(default=0, ge=0, le=1)
    regime_multiplier: float = Field(default=1, ge=0, le=1)
    market_regime: str = Field(default='unknown', max_length=40)
    top_drivers: list[dict[str, Any]] = Field(default_factory=list, max_length=30)


class SignalRequest(StrictModel):
    ticker: str = Field(min_length=1, max_length=16)
    prediction: PredictionRequest


class PaperTradeRequest(StrictModel):
    signal_id: str = Field(min_length=1, max_length=36)
    dollars: Decimal | None = Field(default=None, gt=0, le=1000000000)
    agent_id: str | None = Field(default=None, max_length=128)


class CompareRequest(StrictModel):
    ids: list[str] = Field(min_length=2, max_length=20)


class PriceEvent(StrictModel):
    timestamp: datetime
    prices: dict[str, Decimal]


class HistoricalSignal(SignalRequest):
    pass


class BacktestRequest(ScenarioRequest):
    bars: list[PriceEvent] = Field(min_length=1, max_length=5000)
    signals: list[HistoricalSignal] = Field(default_factory=list, max_length=10000)


def require_user(request: Request, authorization: Annotated[str | None, Header()] = None):
    tokens = request.app.state.users
    if not tokens:
        raise HTTPException(503, 'Configure PAPER_API_USERS_JSON before using the API')
    supplied = authorization.removeprefix('Bearer ') if authorization and authorization.startswith('Bearer ') else ''
    for token, user_id in tokens.items():
        if secrets.compare_digest(supplied.encode(), token.encode()):
            return user_id
    raise HTTPException(401, 'A valid Bearer token is required')


User = Annotated[str, Depends(require_user)]
Key = Annotated[str, Header(alias='Idempotency-Key', min_length=1, max_length=128)]


def create_app(database_url=None, quote_provider=None, users=None, enable_checker=None, clock=None):
    root = Path(__file__).resolve().parents[2]
    database_url = database_url or os.getenv('PAPER_DATABASE_URL', f'sqlite:///{root / "resources/paper_trading.sqlite"}')
    engine = make_engine(database_url)
    market = MarketEnricher(Path(os.getenv('PAPER_MARKET_CACHE', str(root / 'resources/market_cache.json'))))
    provider = quote_provider or CachedQuoteProvider(YFinanceQuoteProvider(market))
    kwargs = {'clock': clock} if clock else {}
    service = PaperTradingService(PaperStore(engine), provider, **kwargs)
    checker = PriceChecker(service, int(os.getenv('PAPER_PRICE_CHECK_INTERVAL_S', '300')))
    enabled = enable_checker if enable_checker is not None else os.getenv('PAPER_ENABLE_PRICE_CHECKER', 'true').lower() == 'true'
    token_map = users if users is not None else json.loads(os.getenv('PAPER_API_USERS_JSON', '{}'))
    if not isinstance(token_map, dict) or any(not isinstance(k, str) or not k or not isinstance(v, str) or not v
                                              for k, v in token_map.items()):
        engine.dispose()
        raise ValueError('PAPER_API_USERS_JSON must map nonempty secret tokens to user IDs')

    @asynccontextmanager
    async def lifespan(app):
        market.cleanup_market_cache()
        task = asyncio.create_task(checker.run()) if enabled else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            engine.dispose()

    app = FastAPI(title='MoodRing Paper Trading', version='0.1.0', lifespan=lifespan)
    app.state.users, app.state.paper_trading_service = token_map, service
    app.state.price_checker, app.state.market_enricher = checker, market

    @app.exception_handler(PaperTradingError)
    async def paper_error(request, exc):
        return JSONResponse(status_code=exc.status_code, content={'ok': False, 'detail': str(exc)})

    @app.get('/health')
    def health():
        return {'status': 'ok', 'simulation_only': True, 'price_checker_enabled': enabled,
                'last_refresh': checker.last_result, 'price_checker_has_error': checker.last_error is not None}

    @app.post('/api/v1/scenarios', status_code=201)
    def create_scenario(body: ScenarioRequest, user: User):
        return service.create_scenario(user, body.name, body.initial_capital, body.config.model_dump(),
                                       body.model_version, body.dataset_version, body.feature_version, body.research)

    @app.get('/api/v1/scenarios')
    def list_scenarios(user: User):
        return {'scenarios': service.list_scenarios(user)}

    @app.post('/api/v1/scenarios/compare')
    def compare_scenarios(body: CompareRequest, user: User):
        return {'scenarios': service.compare(user, body.ids)}

    @app.get('/api/v1/scenarios/{scenario_id}')
    def scenario_state(scenario_id: str, user: User):
        return service.get_scenario(user, scenario_id)

    @app.get('/api/v1/scenarios/{scenario_id}/history')
    def scenario_history(scenario_id: str, user: User, limit: int = Query(default=500, ge=1, le=5000)):
        return service.scenario_history(user, scenario_id, limit)

    @app.post('/api/v1/scenarios/{scenario_id}/signals', status_code=201)
    def publish_signal(scenario_id: str, body: SignalRequest, user: User):
        return service.publish_signal(user, scenario_id, body.ticker, body.prediction.model_dump(mode='json'))

    @app.get('/api/v1/signals/{signal_id}')
    def signal_history(signal_id: str, user: User):
        return service.signal_history(user, signal_id)

    @app.post('/api/v1/paper-trading/trade')
    def execute_paper_trade(body: PaperTradeRequest, user: User, key: Key):
        return {'ok': True, 'trade': service.execute_trade(user, body.signal_id, body.dollars, key, body.agent_id)}

    @app.post('/api/v1/paper-trading/close/{trade_id}')
    def close_paper_trade(trade_id: str, user: User, key: Key):
        return {'ok': True, 'trade': service.close_trade(user, trade_id, key)}

    @app.post('/api/v1/market/refresh')
    def refresh_market(user: User):
        return service.refresh_prices(user)

    @app.get('/api/v1/market/quotes')
    def get_quotes(user: User, tickers: list[str] = Query(min_length=1, max_length=50)):
        try:
            symbols = [ticker_symbol(t) for t in tickers]
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            quotes = provider.get_quotes(symbols)
        except Exception as exc:
            raise NoPriceDataError(f'Quote lookup failed: {exc}') from exc
        result = {}
        for symbol in symbols:
            if symbol not in quotes:
                raise NoPriceDataError(f'No quote for {symbol}')
            try:
                quote = quotes[symbol].require_fresh(service.clock(), 900)
            except ValueError as exc:
                raise NoPriceDataError(str(exc)) from exc
            result[symbol] = {'price': quote.price, 'quote_at': quote.quote_at, 'fetched_at': quote.fetched_at,
                              'source': quote.source, 'currency': quote.currency, 'indicative': True}
        return {'quotes': result}

    @app.get('/api/v1/market/metadata/{ticker}')
    def metadata(ticker: str, user: User):
        try:
            ticker = ticker_symbol(ticker)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return market.get_metadata(ticker)

    @app.post('/api/v1/backtest/run', status_code=201)
    def run_backtest(body: BacktestRequest, user: User):
        try:
            result = BacktestEngine().run(
                [b.model_dump(mode='json') for b in body.bars], [s.model_dump(mode='json') for s in body.signals],
                initial_capital=body.initial_capital, config=body.config.model_dump(),
                model_version=body.model_version, research=body.research)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return service.save_backtest(user, result, body.dataset_version, body.feature_version)

    @app.get('/api/v1/backtest/runs')
    def backtest_history(user: User, limit: int = Query(default=100, ge=1, le=1000)):
        return {'runs': service.list_backtests(user, limit)}

    @app.post('/api/v1/backtest/compare')
    def compare_backtests(body: CompareRequest, user: User):
        return {'runs': service.compare_backtests(user, body.ids)}

    return app


if __name__ == '__main__':
    import uvicorn
    uvicorn.run('api.server:create_app', factory=True, host='127.0.0.1', port=8000)
