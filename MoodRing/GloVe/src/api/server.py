"""Local paper trading API. Run with: PYTHONPATH=src python -m api.server."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import timedelta
import json
import logging
import os
from pathlib import Path
import secrets
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy import text
from api import api_models as models
from api.api_models import (
    ScenarioRequest,
    SignalRequest,
    PaperTradeRequest,
    CompareRequest,
    BacktestRequest,
)

from config.config import settings
from paper_trading.backtest import BacktestEngine
from paper_trading.checker import PriceChecker
from paper_trading.market import (
    CachedQuoteProvider,
    MarketEnricher,
    YFinanceQuoteProvider,
    OfflineQuoteProvider,
    ticker_symbol,
)
from paper_trading.service import (
    NoPriceDataError,
    PaperTradingError,
    PaperTradingService,
)
from paper_trading.storage import PaperStore, make_engine


logger = logging.getLogger(__name__)
ERRORS = {
    code: {"model": models.ErrorResponse, "description": description}
    for code, description in {
        500: "Unexpected local operation failure",
        400: "Invalid trading operation",
        401: "Missing or invalid local Bearer token",
        403: "Not the owner",
        404: "Record not found",
        409: "Idempotency or position conflict",
        422: "Request validation failed",
        503: "Authentication unconfigured or market data unavailable",
    }.items()
}


bearer = HTTPBearer(
    auto_error=False,
    description="Local secret from PAPER_API_USERS_JSON; enter the token without the Bearer prefix.",
)


def require_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
):
    tokens = request.app.state.users
    if not tokens:
        raise HTTPException(503, "Configure PAPER_API_USERS_JSON before using the API")
    supplied = credentials.credentials if credentials else ""
    for token, user_id in tokens.items():
        if secrets.compare_digest(supplied.encode(), token.encode()):
            return user_id
    raise HTTPException(401, "A valid Bearer token is required")


User = Annotated[str, Depends(require_user)]
Key = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=128,
        description="Use one unique key for each logical entry or exit; reuse it only to replay that same operation.",
        examples=["lesson-entry-1"],
    ),
]


def create_app(
    database_url=None,
    quote_provider=None,
    users=None,
    enable_checker=None,
    clock=None,
    quote_mode=None,
):
    """Build the local simulator with explicit provider, authentication and checker state."""
    root = Path(__file__).resolve().parents[2]
    database_url = database_url or os.getenv(
        "PAPER_DATABASE_URL", f'sqlite:///{root / "resources/paper_trading.sqlite"}'
    )
    engine = make_engine(database_url)
    mode = (
        "custom"
        if quote_provider is not None
        else (quote_mode or os.getenv("PAPER_QUOTE_MODE", "offline"))
    )
    if mode not in ("offline", "live", "custom") or (
        mode == "custom" and quote_provider is None
    ):
        engine.dispose()
        raise ValueError("PAPER_QUOTE_MODE must be offline or live")
    # No metadata cache creation/cleanup or yfinance instance in offline mode.
    market = (
        MarketEnricher(
            Path(
                os.getenv(
                    "PAPER_MARKET_CACHE", str(root / "resources/market_cache.json")
                )
            )
        )
        if mode == "live"
        else None
    )
    provider = (
        quote_provider
        if quote_provider is not None
        else (
            OfflineQuoteProvider(**({"clock": clock} if clock else {}))
            if mode == "offline"
            else CachedQuoteProvider(YFinanceQuoteProvider(market))
        )
    )
    kwargs = {"clock": clock} if clock else {}
    service = PaperTradingService(PaperStore(engine), provider, **kwargs)
    checker = PriceChecker(
        service, int(os.getenv("PAPER_PRICE_CHECK_INTERVAL_S", "300"))
    )
    enabled = (
        enable_checker
        if enable_checker is not None
        else os.getenv("PAPER_ENABLE_PRICE_CHECKER", "true").lower() == "true"
    )
    token_map = (
        users
        if users is not None
        else json.loads(os.getenv("PAPER_API_USERS_JSON", "{}"))
    )
    if not isinstance(token_map, dict) or any(
        not isinstance(k, str) or not k or not isinstance(v, str) or not v
        for k, v in token_map.items()
    ):
        engine.dispose()
        raise ValueError(
            "PAPER_API_USERS_JSON must map nonempty secret tokens to user IDs"
        )

    @asynccontextmanager
    async def lifespan(app):
        if market is not None:
            market.cleanup_market_cache()
        task = asyncio.create_task(checker.run()) if enabled else None
        app.state.checker_task = task
        try:
            yield
        finally:
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            engine.dispose()

    from .errors import error_response

    app = FastAPI(
        title="MoodRing Paper Trading",
        version="0.2.0",
        description="""Work in progress. Local learning simulator; not production-ready.

## Start with the lesson
Run `PYTHONPATH=src python examples/api_smoke.py --self-contained` after installing
dependencies. It supplies temporary local authentication, IDs, timestamps and
idempotency keys. See the repository usage guide for commands and the capability
index for implemented versus planned features.

## Manual endpoint order and ID dependencies
1. Configure a local secret with `PAPER_API_USERS_JSON`, then **Authorize** using
   that secret. There is no signup, public identity or token-issuing endpoint.
2. `GET /ready`, then `POST /api/v1/scenarios`. Save the returned scenario `id`.
3. `POST /api/v1/scenarios/{scenario_id}/signals` with caller-supplied prediction
   inputs. This does not invoke a research model or SEC pipeline. Both BUY and
   PASS decisions are stored; PASS does not authorize an entry.
4. For BUY, submit the returned signal `id` as `signal_id` to
   `POST /api/v1/paper-trading/trade` with an `Idempotency-Key`. Save `trade.id`.
   Repeat the same key and payload only to replay that same entry; conflict is 409.
5. Inspect scenario state, then `POST /api/v1/market/refresh`. Offline quotes for
   AAPL, MSFT and NVDA are fixed synthetic values; refresh does not move prices.
   Live provider failures are never silently replaced with synthetic quotes.
6. `POST /api/v1/paper-trading/close/{trade_id}` with a distinct exit key. Replay
   that key to retrieve the original close; a new key on a closed trade is 409.
7. `GET /api/v1/scenarios/{scenario_id}/history` explains orders, fills and equity.
   Replay runs use caller-supplied events; save their run IDs before comparison.

Money and quantity responses use decimal strings. Static bearer authentication
is unsuitable for public hosting. Offline price movement and P&L are demonstrated
by replay and the paper demo, not by the fixed-quote refresh endpoint.
""",
        lifespan=lifespan,
    )
    app.state.users, app.state.paper_trading_service = token_map, service
    app.state.price_checker, app.state.market_enricher = checker, market
    app.state.checker_task = None
    app.state.quote_provider_mode = mode

    @app.exception_handler(PaperTradingError)
    async def paper_error(request, exc):
        return error_response(exc.status_code, str(exc))

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        logger.error(
            "Local API operation failed", exc_info=(type(exc), exc, exc.__traceback__)
        )
        return error_response(500, "Local API operation failed")

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        return error_response(exc.status_code, str(exc.detail), headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Never echo invalid request bodies, auth headers or arbitrary user values.
        return error_response(
            422, "Request validation failed; check the OpenAPI schema"
        )

    def health_state():
        try:
            # StaticPool in-memory SQLite shares a connection with the checker.
            # Serialize the probe with ledger transactions, just like service reads.
            with service.lock, engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            connected = True
        except Exception:
            connected = False
        task = app.state.checker_task
        status = (
            "disabled"
            if not enabled
            else "not_started"
            if task is None
            else "stopped"
            if task.cancelled()
            else "failed"
            if task.done()
            else "running"
        )
        ready = (
            bool(token_map)
            and connected
            and (not enabled or status == "running")
            and checker.last_error is None
        )
        return {
            "status": "ok" if ready else "not_ready",
            "simulation_only": True,
            "production_ready": False,
            "deployment_scope": "local_learning",
            "authentication_configured": bool(token_map),
            "database": {"backend": engine.dialect.name, "connected": connected},
            "quote_provider_mode": mode,
            "offline": mode == "offline",
            "price_checker_enabled": enabled,
            "checker": {
                "enabled": enabled,
                "status": status,
                "last_attempt_at": checker.last_attempt_at,
                "last_success_at": checker.last_success_at,
                "last_error": checker.last_error,
            },
            "last_refresh": (
                {
                    "positions_updated": checker.last_result["positions_updated"],
                    "positions_closed": checker.last_result["positions_closed"],
                    "tickers_requested_count": len(
                        checker.last_result["tickers_requested"]
                    ),
                    "stale_or_missing_count": len(
                        checker.last_result["stale_or_missing"]
                    ),
                }
                if checker.last_result is not None
                else None
            ),
            "price_checker_has_error": checker.last_error is not None,
        }

    @app.get(
        "/health",
        response_model=models.HealthResponse,
        tags=["Operations"],
        summary="Inspect local process and dependencies",
        description="Always returns 200 when the process responds. Read status or /ready for readiness; no secrets or watched symbols are exposed.",
    )
    def health():
        return health_state()

    @app.get(
        "/ready",
        response_model=models.HealthResponse,
        responses={503: {"model": models.HealthResponse}},
        tags=["Operations"],
        summary="Check local workflow readiness",
        description="503 when auth, database, or enabled checker is unavailable. This is not a production-readiness certification or live quote-availability probe.",
    )
    def readiness():
        state = health_state()
        return JSONResponse(
            status_code=200 if state["status"] == "ok" else 503,
            content=models.HealthResponse.model_validate(state).model_dump(mode="json"),
        )

    @app.post(
        "/api/v1/scenarios",
        status_code=201,
        response_model=models.Scenario,
        tags=["Scenarios"],
        summary="Create an independent scenario",
        description="Creates a portfolio with virtual cash and policy. Money and quantity responses are decimal strings.",
        responses=ERRORS,
    )
    def create_scenario(body: ScenarioRequest, user: User):
        return service.create_scenario(
            user,
            body.name,
            body.initial_capital,
            body.config.model_dump(),
            body.model_version,
            body.dataset_version,
            body.feature_version,
            body.research,
        )

    @app.get(
        "/api/v1/scenarios",
        response_model=models.Scenarios,
        tags=["Scenarios"],
        summary="List owned scenarios",
        description="Returns only scenarios belonging to the authenticated local user.",
        responses=ERRORS,
    )
    def list_scenarios(user: User):
        return {"scenarios": service.list_scenarios(user)}

    @app.post(
        "/api/v1/scenarios/compare",
        response_model=models.ScenarioComparisons,
        tags=["Scenarios"],
        summary="Compare owned scenarios",
        description="Submit 2–20 distinct scenario IDs returned by create/list.",
        responses=ERRORS,
    )
    def compare_scenarios(body: CompareRequest, user: User):
        return {"scenarios": service.compare(user, body.ids)}

    @app.get(
        "/api/v1/scenarios/{scenario_id}",
        response_model=models.Scenario,
        tags=["Scenarios"],
        summary="Inspect portfolio state",
        description="Includes cash, equity, realized/unrealized P&L and stale valuation flags.",
        responses=ERRORS,
    )
    def scenario_state(scenario_id: str, user: User):
        return service.get_scenario(user, scenario_id)

    @app.get(
        "/api/v1/scenarios/{scenario_id}/history",
        response_model=models.History,
        tags=["Scenarios"],
        summary="Read bounded scenario history",
        description="Returns recent records; limit applies per collection, maximum 5000.",
        responses=ERRORS,
    )
    def scenario_history(
        scenario_id: str, user: User, limit: int = Query(default=500, ge=1, le=5000)
    ):
        return service.scenario_history(user, scenario_id, limit)

    @app.post(
        "/api/v1/scenarios/{scenario_id}/signals",
        status_code=201,
        response_model=models.Signal,
        tags=["Signals"],
        summary="Publish an explicit prediction",
        description="Requires aware timestamps at or before the decision cutoff. Stores BUY and PASS; offline quotes are synthetic.",
        responses=ERRORS,
    )
    def publish_signal(scenario_id: str, body: SignalRequest, user: User):
        return service.publish_signal(
            user, scenario_id, body.ticker, body.prediction.model_dump(mode="json")
        )

    @app.get(
        "/api/v1/signals/{signal_id}",
        response_model=models.SignalHistory,
        tags=["Signals"],
        summary="Read a signal and outcomes",
        description="Shows stored prediction, decision and observed performance horizons.",
        responses=ERRORS,
    )
    def signal_history(signal_id: str, user: User):
        return service.signal_history(user, signal_id)

    @app.post(
        "/api/v1/paper-trading/trade",
        response_model=models.TradeResponse,
        tags=["Paper trading"],
        summary="Simulate entry from a BUY signal",
        description="Requires Idempotency-Key. Replay the same key/payload to retrieve the existing trade; conflicting use returns 409.",
        responses=ERRORS,
    )
    def execute_paper_trade(body: PaperTradeRequest, user: User, key: Key):
        return {
            "ok": True,
            "trade": service.execute_trade(
                user, body.signal_id, body.dollars, key, body.agent_id
            ),
        }

    @app.post(
        "/api/v1/paper-trading/close/{trade_id}",
        response_model=models.TradeResponse,
        tags=["Paper trading"],
        summary="Simulate a full position exit",
        description="Requires Idempotency-Key. Reusing the successful close key replays it; a new key on a closed trade returns 409.",
        responses=ERRORS,
    )
    def close_paper_trade(trade_id: str, user: User, key: Key):
        return {"ok": True, "trade": service.close_trade(user, trade_id, key)}

    @app.post(
        "/api/v1/market/refresh",
        response_model=models.Refresh,
        tags=["Market"],
        summary="Refresh owned watched symbols",
        description="Uses the configured provider to update marks. Offline mode uses synthetic fixed prices. Does not fetch SEC data.",
        responses=ERRORS,
    )
    def refresh_market(user: User):
        checker.last_attempt_at = service.clock().isoformat()
        try:
            result = service.refresh_prices(user)
        except Exception:
            checker.last_error = "Market refresh failed"
            raise
        checker.last_result, checker.last_error = result, None
        checker.last_success_at = service.clock().isoformat()
        return result

    @app.get(
        "/api/v1/market/quotes",
        response_model=models.QuotesResponse,
        tags=["Market"],
        summary="Read indicative quotes",
        description="Repeat tickers query parameters. Offline supports AAPL, MSFT and NVDA; unsupported or stale data returns 503.",
        responses=ERRORS,
    )
    def get_quotes(
        user: User,
        tickers: list[str] = Query(
            min_length=1,
            max_length=50,
            description="Repeat the parameter for multiple symbols; offline lesson supports AAPL, MSFT, NVDA.",
            examples=[["AAPL", "MSFT"]],
        ),
    ):
        try:
            symbols = [ticker_symbol(t) for t in tickers]
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            quotes = provider.get_quotes(symbols)
        except Exception as exc:
            logger.warning("Quote lookup failed", exc_info=True)
            raise NoPriceDataError("Market data temporarily unavailable") from exc
        result = {}
        for symbol in symbols:
            if symbol not in quotes:
                raise NoPriceDataError(f"No quote for {symbol}")
            try:
                quote = quotes[symbol].require_fresh(service.clock(), 900)
                if quote.ticker != symbol:
                    raise ValueError("Mismatched quote")
            except ValueError as exc:
                logger.warning("Quote validation failed", exc_info=True)
                raise NoPriceDataError("Market data temporarily unavailable") from exc
            result[symbol] = {
                "price": quote.price,
                "quote_at": quote.quote_at,
                "fetched_at": quote.fetched_at,
                "source": quote.source,
                "currency": quote.currency,
                "indicative": True,
            }
        return {"quotes": result}

    @app.get(
        "/api/v1/market/metadata/{ticker}",
        response_model=models.Metadata,
        tags=["Market"],
        summary="Read company metadata",
        description="Offline metadata is explicitly synthetic and has no market cap, industry or sector values.",
        responses=ERRORS,
    )
    def metadata(ticker: str, user: User):
        try:
            ticker = ticker_symbol(ticker)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            if mode == "offline":
                return provider.get_metadata(ticker)
            if market is None:
                raise NoPriceDataError("Metadata unavailable for custom quote provider")
            return market.get_metadata(ticker)
        except Exception as exc:
            logger.warning("Metadata lookup failed", exc_info=True)
            raise NoPriceDataError("Market data temporarily unavailable") from exc

    @app.post(
        "/api/v1/backtest/run",
        status_code=201,
        response_model=models.BacktestRun,
        tags=["Backtests"],
        summary="Run and save an offline historical replay",
        description="Caller supplies all price events and predictions. Entries use later events; no market downloads occur.",
        responses=ERRORS,
    )
    def run_backtest(body: BacktestRequest, user: User):
        try:
            result = BacktestEngine().run(
                [b.model_dump(mode="json") for b in body.bars],
                [s.model_dump(mode="json") for s in body.signals],
                initial_capital=body.initial_capital,
                config=body.config.model_dump(),
                model_version=body.model_version,
                research=body.research,
            )
        except ValueError as exc:
            raise HTTPException(
                422, "Invalid replay input; check timestamp ordering and price events"
            ) from exc
        return service.save_backtest(
            user, result, body.dataset_version, body.feature_version
        )

    @app.get(
        "/api/v1/backtest/runs",
        response_model=models.BacktestRuns,
        tags=["Backtests"],
        summary="List saved replay runs",
        description="Lists only owned runs, with limit 1–1000.",
        responses=ERRORS,
    )
    def backtest_history(user: User, limit: int = Query(default=100, ge=1, le=1000)):
        return {"runs": service.list_backtests(user, limit)}

    @app.post(
        "/api/v1/backtest/compare",
        response_model=models.BacktestRuns,
        tags=["Backtests"],
        summary="Compare saved replay runs",
        description="Submit 2–20 distinct owned run IDs; no performance ranking is implied.",
        responses=ERRORS,
    )
    def compare_backtests(body: CompareRequest, user: User):
        return {"runs": service.compare_backtests(user, body.ids)}

    def openapi():
        """Describe current routes with fresh timestamps for executable lesson examples."""
        # Fresh lesson timestamps each time /openapi.json is read, not stale literals.
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        now = service.clock()
        prediction = {
            "prob_up": 0.8,
            "mu": 0.03,
            "var": 0.0004,
            "trade_success_prob": 0.75,
            "drawdown_risk": 0.02,
            "prediction_time": now.isoformat(),
            "feature_available_at": (now - timedelta(seconds=1)).isoformat(),
        }
        replay_prediction = {
            **prediction,
            "prediction_time": (now - timedelta(minutes=2)).isoformat(),
            "feature_available_at": (now - timedelta(minutes=3)).isoformat(),
        }
        examples = {
            "ScenarioRequest": {
                "name": "Offline learning",
                "initial_capital": "10000",
                "config": {"auto_exit": False},
            },
            "PredictionRequest": prediction,
            "SignalRequest": {"ticker": "AAPL", "prediction": prediction},
            "PaperTradeRequest": {
                "signal_id": "USE_RETURNED_SIGNAL_ID",
                "dollars": "100",
            },
            "CompareRequest": {
                "ids": ["USE_FIRST_RETURNED_ID", "USE_SECOND_RETURNED_ID"]
            },
            "BacktestRequest": {
                "name": "Offline replay",
                "config": {"auto_exit": False},
                "bars": [
                    {
                        "timestamp": (now - timedelta(minutes=2)).isoformat(),
                        "prices": {"AAPL": "100"},
                    },
                    {
                        "timestamp": (now - timedelta(minutes=1)).isoformat(),
                        "prices": {"AAPL": "101"},
                    },
                ],
                "signals": [{"ticker": "AAPL", "prediction": replay_prediction}],
            },
        }
        for name, example in examples.items():
            schema["components"]["schemas"][name]["examples"] = [example]
        return schema

    app.openapi = openapi
    return app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api.server:create_app", factory=True, host="127.0.0.1", port=8000)
