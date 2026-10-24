"""Authoritative local API contracts. Unsupported legacy tier/options models removed.

Money/quantity response values are decimal strings (never rounded binary floats).
Opaque UUID identifiers are strings. JSON metadata/driver dictionaries are intentional
extension fields, not undocumented copies of trading entities.
"""
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field
from paper_trading.policy import ScenarioPolicy


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ScenarioRequest(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    initial_capital: Decimal = Field(default=Decimal("10000"), gt=0, le=1000000000)
    config: ScenarioPolicy = Field(default_factory=ScenarioPolicy)
    model_version: str = Field(default="manual-v1", min_length=1, max_length=128)
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
    market_regime: str = Field(default="unknown", max_length=40)
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


class ResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Decision(ResponseModel):
    ticker: str
    action: Literal["BUY", "PASS"]
    reason: str | None = None
    position_size: float
    reason_codes: list[dict[str, Any]]
    calculated_position_size: float | None = None
    max_allocation: float | None = None
    final_position_size: float | None = None
    confidence: float | None = None
    market_regime: str | None = None
    regime_multiplier: float | None = None
    trade_card: str | None = None
    expected_return: float | None = None
    expected_net_edge: float | None = None
    estimated_cost_drag: float | None = None
    raw_mu: float | None = None
    effective_mu: float | None = None
    predicted_sigma: float | None = None
    predicted_variance: float | None = None
    expected_variance: float | None = None
    trade_success_probability: float | None = None
    trade_success_prob: float | None = None
    var_95: float | None = None
    cvar_95: float | None = None
    estimated_transaction_cost: float | None = None
    current_price: float | None = None
    stop_loss_price: float | None = None
    take_profit_price: float | None = None


class Position(ResponseModel):
    id: str
    portfolio_id: str
    ticker: str
    quantity: Decimal
    entry_price: Decimal
    cost_basis: Decimal
    mark_price: Decimal
    quote_at: datetime
    stop_loss_price: Decimal
    take_profit_price: Decimal
    status: Literal["open", "closed"]
    opened_at: datetime
    closed_at: datetime | None


class MarkedPosition(Position):
    market_value: Decimal
    unrealized_pnl: Decimal
    quote_is_stale: bool


class Portfolio(ResponseModel):
    id: str
    scenario_id: str
    user_id: str
    initial_capital: Decimal
    cash_balance: Decimal
    total_pnl: Decimal
    winning_trades: int
    closed_trades: int
    peak_equity: Decimal
    created_at: datetime
    market_value: Decimal
    equity: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    total_return_pct: Decimal
    gross_exposure_pct: Decimal
    drawdown: Decimal
    positions: list[MarkedPosition]
    valuation_is_stale: bool


class Scenario(ResponseModel):
    id: str
    user_id: str
    name: str
    config_json: ScenarioPolicy
    research_json: dict[str, Any]
    model_version: str
    dataset_version: str | None
    feature_version: str | None
    created_at: datetime
    portfolio: Portfolio


class Scenarios(ResponseModel):
    scenarios: list[Scenario]


class ScenarioComparison(ResponseModel):
    scenario_id: str
    name: str
    model_version: str
    dataset_version: str | None
    feature_version: str | None
    config: ScenarioPolicy
    research: dict[str, Any]
    initial_capital: Decimal
    equity: Decimal
    total_return_pct: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    gross_exposure_pct: Decimal
    drawdown: Decimal
    closed_trades: int
    winning_trades: int
    valuation_is_stale: bool


class ScenarioComparisons(ResponseModel):
    scenarios: list[ScenarioComparison]


class Signal(ResponseModel):
    id: str
    scenario_id: str
    user_id: str
    ticker: str
    stance: str
    prediction_json: PredictionRequest
    decision_json: Decision
    model_version: str
    feature_version: str | None
    prediction_time: datetime
    expires_at: datetime
    entry_price: Decimal
    created_at: datetime


class Performance(ResponseModel):
    signal_id: str
    price_1h: Decimal | None
    price_4h: Decimal | None
    price_1d: Decimal | None
    price_1w: Decimal | None
    max_gain_pct: Decimal
    max_loss_pct: Decimal
    updated_at: datetime


class SignalHistory(Signal):
    performance: Performance


class TradeRecord(ResponseModel):
    id: str
    position_id: str
    portfolio_id: str
    signal_id: str
    user_id: str
    agent_id: str | None
    entry_price: Decimal
    exit_price: Decimal | None
    pnl_dollars: Decimal | None
    pnl_pct: Decimal | None
    entry_fee: Decimal
    exit_fee: Decimal | None
    status: Literal["open", "closed"]
    opened_at: datetime
    closed_at: datetime | None
    exit_reason: str | None


class Trade(TradeRecord):
    position: Position


class TradeResponse(ResponseModel):
    ok: Literal[True]
    trade: Trade


class BuyOrderRequest(ResponseModel):
    action: Literal["buy"]
    signal_id: str
    dollars: str | None
    agent_id: str | None


class SellOrderRequest(ResponseModel):
    action: Literal["sell"]
    trade_id: str


class Order(ResponseModel):
    id: str
    portfolio_id: str
    trade_id: str
    user_id: str
    idempotency_key: str
    request_json: BuyOrderRequest | SellOrderRequest
    side: Literal["buy", "sell"]
    quantity: Decimal
    status: Literal["filled"]
    created_at: datetime


class Fill(ResponseModel):
    id: str
    order_id: str
    quantity: Decimal
    price: Decimal
    fee: Decimal
    quote_price: Decimal
    quote_at: datetime
    source: str
    filled_at: datetime


class EquityPoint(ResponseModel):
    id: str
    portfolio_id: str
    recorded_at: datetime
    event: str
    cash_balance: Decimal
    market_value: Decimal
    equity: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    drawdown: Decimal


class History(ResponseModel):
    trades: list[TradeRecord]
    orders: list[Order]
    fills: list[Fill]
    equity: list[EquityPoint]
    signals: list[Signal]


class Refresh(ResponseModel):
    positions_updated: int
    positions_closed: int
    tickers_requested: list[str]
    stale_or_missing: list[str]


class QuoteResponse(ResponseModel):
    price: Decimal
    quote_at: datetime
    fetched_at: datetime
    source: str
    currency: str
    indicative: Literal[True]


class QuotesResponse(ResponseModel):
    quotes: dict[str, QuoteResponse]


class MetadataValues(ResponseModel):
    shortName: str | None
    sector: str | None
    industry: str | None
    marketCap: int | float | None
    currency: str | None


class Metadata(ResponseModel):
    cached_at: datetime
    data: MetadataValues


class SignalOutcome(ResponseModel):
    input_index: int
    ticker: str
    status: Literal[
        "traded",
        "expired",
        "position_exists",
        "not_tradeable",
        "rejected",
        "no_later_event",
        "never_eligible",
    ]
    reason: str | None
    signal_id: str | None = None
    trade_id: str | None = None


class ReplayResult(ResponseModel):
    equity: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    positions: list[MarkedPosition]
    total_return_pct: Decimal
    trades: list[TradeRecord]
    equity_curve: list[EquityPoint]
    skipped: list[SignalOutcome]
    signal_outcomes: list[SignalOutcome]
    signal_status_counts: dict[str, int]
    unfilled_signal_count: int


class ReplayRisk(ResponseModel):
    max_drawdown: Decimal


class BacktestRun(ResponseModel):
    id: str
    user_id: str
    model_version: str
    dataset_version: str | None
    feature_version: str | None
    config_json: ScenarioPolicy
    research_json: dict[str, Any]
    result_json: ReplayResult
    risk_json: ReplayRisk
    created_at: datetime


class BacktestRuns(ResponseModel):
    runs: list[BacktestRun]


class ErrorResponse(ResponseModel):
    ok: Literal[False] = False
    detail: str


class DatabaseHealth(ResponseModel):
    backend: str
    connected: bool


class CheckerHealth(ResponseModel):
    enabled: bool
    status: Literal["disabled", "not_started", "running", "stopped", "failed"]
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    last_error: str | None


class RefreshHealth(ResponseModel):
    positions_updated: int
    positions_closed: int
    tickers_requested_count: int
    stale_or_missing_count: int


class HealthResponse(ResponseModel):
    status: Literal["ok", "not_ready"]
    simulation_only: Literal[True]
    production_ready: Literal[False]
    deployment_scope: Literal["local_learning"]
    authentication_configured: bool
    database: DatabaseHealth
    quote_provider_mode: Literal["offline", "live", "custom"]
    offline: bool
    price_checker_enabled: bool
    checker: CheckerHealth
    last_refresh: RefreshHealth | None
    price_checker_has_error: bool
