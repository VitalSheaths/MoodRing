"""Pydantic models for API responses.

Provides comprehensive response models for OpenAPI documentation:
- Standard response wrappers
- Error responses with consistent format
- Signal and trade idea models
- Analytics and metrics responses
- Pagination support
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Generic, TypeVar

from pydantic import BaseModel, Field


# Generic type for data responses
T = TypeVar('T')


class APIResponse(BaseModel, Generic[T]):
    """Standard API response wrapper.

    All API responses follow this format for consistency.
    """
    success: bool = Field(
        description="Whether the request was successful",
        json_schema_extra={"example": True}
    )
    data: Optional[T] = Field(
        default=None,
        description="Response data (null on error)",
        json_schema_extra={"example": None}
    )
    error: Optional[str] = Field(
        default=None,
        description="Error message (null on success)",
        json_schema_extra={"example": None}
    )
    request_id: Optional[str] = Field(
        default=None,
        description="Unique request ID for tracing",
        json_schema_extra={"example": "req_abc123-def4-5678-90ab-cdef12345678"}
    )

    class Config:
        """Pydantic model config providing an OpenAPI example for the response schema."""
        json_schema_extra = {
            "example": {
                "success": True,
                "data": {"example": "data"},
                "error": None,
                "request_id": "req_abc123-def4-5678-90ab-cdef12345678"
            }
        }


class ErrorResponse(BaseModel):
    """Standard error response format."""
    success: bool = Field(
        default=False,
        description="Always false for errors",
        json_schema_extra={"example": False}
    )
    error: str = Field(
        description="Error message",
        json_schema_extra={"example": "Invalid API key"}
    )
    error_code: Optional[str] = Field(
        default=None,
        description="Machine-readable error code",
        json_schema_extra={"example": "INVALID_API_KEY"}
    )
    details: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Additional error details",
        json_schema_extra={"example": None}
    )
    request_id: Optional[str] = Field(
        default=None,
        description="Request ID for support",
        json_schema_extra={"example": "req_abc123"}
    )


class SignalResponse(BaseModel):
    """Trading signal response model."""
    id: int = Field(
        description="Signal ID",
        json_schema_extra={"example": 12345}
    )
    ticker: str = Field(
        description="Stock ticker symbol",
        json_schema_extra={"example": "AAPL"}
    )
    stance: str = Field(
        description="Trading stance (bullish/bearish/mixed/unknown)",
        json_schema_extra={"example": "bullish"}
    )
    confidence: float = Field(
        description="Signal confidence (0.0-1.0)",
        ge=0.0,
        le=1.0,
        json_schema_extra={"example": 0.85}
    )
    event_type: str = Field(
        description="Type of event detected",
        json_schema_extra={"example": "earnings_beat"}
    )
    reasoning: str = Field(
        description="AI-generated reasoning for the signal",
        json_schema_extra={"example": "Strong earnings beat with positive guidance"}
    )
    strategy: Optional[str] = Field(
        default=None,
        description="Recommended options strategy",
        json_schema_extra={"example": "call_debit_spread"}
    )
    created_at: int = Field(
        description="Unix timestamp when signal was created",
        json_schema_extra={"example": 1708041600}
    )
    post_title: str = Field(
        description="Original Reddit post title",
        json_schema_extra={"example": "AAPL crushing it - earnings beat by 20%"}
    )
    subreddit: str = Field(
        description="Source subreddit",
        json_schema_extra={"example": "wallstreetbets"}
    )
    credibility_score: Optional[float] = Field(
        default=None,
        description="ML credibility score (0.0-1.0)",
        json_schema_extra={"example": 0.78}
    )


class TradeIdeaResponse(BaseModel):
    """Options trade idea response model."""
    ticker: str = Field(
        description="Stock ticker",
        json_schema_extra={"example": "TSLA"}
    )
    strategy: str = Field(
        description="Options strategy name",
        json_schema_extra={"example": "call_debit_spread"}
    )
    stance: str = Field(
        description="Bullish/bearish/neutral",
        json_schema_extra={"example": "bullish"}
    )
    entry_price: Optional[float] = Field(
        default=None,
        description="Recommended entry price",
        json_schema_extra={"example": 250.50}
    )
    stop_loss: Optional[float] = Field(
        default=None,
        description="Stop loss price",
        json_schema_extra={"example": 240.00}
    )
    target: Optional[float] = Field(
        default=None,
        description="Target price",
        json_schema_extra={"example": 270.00}
    )
    contracts: Optional[int] = Field(
        default=None,
        description="Number of contracts",
        json_schema_extra={"example": 10}
    )
    max_loss: Optional[float] = Field(
        default=None,
        description="Maximum potential loss",
        json_schema_extra={"example": 500.00}
    )
    max_gain: Optional[float] = Field(
        default=None,
        description="Maximum potential gain",
        json_schema_extra={"example": 1500.00}
    )
    iv_percentile: Optional[float] = Field(
        default=None,
        description="Implied volatility percentile",
        json_schema_extra={"example": 65.0}
    )


class TradeDriverDetail(BaseModel):
    """Post-hoc feature attribution driver (SHAP / Integrated Gradients)."""
    feature_name: str = Field(description="Name of contributing feature", json_schema_extra={"example": "eps_surprise"})
    attribution_score: float = Field(description="Feature attribution weight", json_schema_extra={"example": 0.42})
    description: str = Field(description="Human readable driver explanation", json_schema_extra={"example": "Positive earnings surprise (+18.4%)"})


class TradeDecisionResponse(BaseModel):
    """Risk-aware Paper Trading Decision Engine response model."""
    ticker: str = Field(description="Stock ticker symbol", json_schema_extra={"example": "AAPL"})
    action: str = Field(description="Decision action (BUY/SELL/PASS)", json_schema_extra={"example": "BUY"})
    confidence: float = Field(description="Calibrated direction probability P(Up)", ge=0.0, le=1.0, json_schema_extra={"example": 0.87})
    expected_return: float = Field(description="Expected return μ (%)", json_schema_extra={"example": 2.8})
    expected_variance: float = Field(description="Aleatoric variance σ²", json_schema_extra={"example": 0.0012})
    trade_success_prob: float = Field(description="Calibrated P(TP hit before SL)", ge=0.0, le=1.0, json_schema_extra={"example": 0.76})
    var_95: float = Field(description="Value at Risk 95%", json_schema_extra={"example": -1.45})
    cvar_95: float = Field(description="Expected Shortfall 95%", json_schema_extra={"example": -2.10})
    prob_gain_over_2pct: float = Field(description="Probability of Gain > 2%", ge=0.0, le=1.0, json_schema_extra={"example": 0.68})
    prob_loss_over_1pct: float = Field(description="Probability of Loss > 1%", ge=0.0, le=1.0, json_schema_extra={"example": 0.15})
    market_regime: str = Field(description="Market regime classification", json_schema_extra={"example": "bull_trend"})
    regime_multiplier: float = Field(description="Explainable regime position sizing multiplier", json_schema_extra={"example": 1.0})
    position_size: float = Field(description="Allocated position size ($)", json_schema_extra={"example": 2500.0})
    stop_loss_price: float = Field(description="Calculated stop loss price", json_schema_extra={"example": 320.00})
    take_profit_price: float = Field(description="Calculated take profit price", json_schema_extra={"example": 345.00})
    reason_codes: List[TradeDriverDetail] = Field(description="Post-hoc attribution top drivers (SHAP)", json_schema_extra={
        "example": [
            {"feature_name": "eps_surprise", "attribution_score": 0.45, "description": "Positive earnings surprise (+18.4%)"},
            {"feature_name": "net_insider_buy_30d", "attribution_score": 0.30, "description": "Strong 30-day net insider buying ($2.4M)"},
            {"feature_name": "rsi_14", "attribution_score": 0.15, "description": "RSI recovered from oversold (32.1 -> 44.5)"},
            {"feature_name": "fomc_llm_score", "attribution_score": 0.10, "description": "Dovish macro stance (LLM Score: -0.45)"}
        ]
    })
    feature_version: str = Field(default="v1.0", description="Feature schema version", json_schema_extra={"example": "v1.0"})
    architecture_version: str = Field(default="temporal_transformer_v1", description="Model architecture", json_schema_extra={"example": "temporal_transformer_v1"})
    prediction_time: str = Field(description="Prediction timestamp cutoff", json_schema_extra={"example": "2026-07-24 16:00:00+05:30"})


class ExperimentTrackingModel(BaseModel):
    """Experiment tracking schema for backtests and paper trading runs."""
    architecture: str = Field(description="Model architecture name", json_schema_extra={"example": "temporal_transformer_v1"})
    dataset_version: str = Field(description="Dataset version tag", json_schema_extra={"example": "ds_2026_07"})
    feature_version: str = Field(description="Feature pipeline version", json_schema_extra={"example": "v1.0"})
    loss_function: str = Field(description="Loss function specification", json_schema_extra={"example": "beta_nll"})
    beta_param: float = Field(default=0.5, description="Beta-NLL parameter", json_schema_extra={"example": 0.5})
    random_seed: int = Field(default=42, description="Random seed", json_schema_extra={"example": 42})
    learning_rate: float = Field(default=1e-3, description="Base learning rate", json_schema_extra={"example": 0.001})
    checkpoint_path: str = Field(description="Saved model checkpoint path", json_schema_extra={"example": "checkpoints/model_ep50.pt"})
    metrics: Dict[str, float] = Field(description="Validation metrics", json_schema_extra={"example": {"rmse": 0.012, "nll": -2.45}})
    paper_trading_return: float = Field(description="Total paper trading return (%)", json_schema_extra={"example": 18.5})
    sharpe_ratio: float = Field(description="Sharpe ratio", json_schema_extra={"example": 2.1})
    max_drawdown: float = Field(description="Max drawdown (%)", json_schema_extra={"example": -8.4})


class PaginatedResponse(BaseModel, Generic[T]):
    """Paginated response wrapper."""
    items: List[T] = Field(description="List of items in current page")
    total: int = Field(
        description="Total number of items",
        json_schema_extra={"example": 1000}
    )
    page: int = Field(
        description="Current page number (1-indexed)",
        json_schema_extra={"example": 1}
    )
    page_size: int = Field(
        description="Number of items per page",
        json_schema_extra={"example": 50}
    )
    total_pages: int = Field(
        description="Total number of pages",
        json_schema_extra={"example": 20}
    )
    has_next: bool = Field(
        description="Whether there is a next page",
        json_schema_extra={"example": True}
    )
    has_prev: bool = Field(
        description="Whether there is a previous page",
        json_schema_extra={"example": False}
    )


class HealthResponse(BaseModel):
    """Health check response model."""
    status: str = Field(
        description="Health status",
        json_schema_extra={"example": "healthy"}
    )
    version: str = Field(
        description="API version",
        json_schema_extra={"example": "0.1.0"}
    )
    uptime_seconds: int = Field(
        description="Server uptime in seconds",
        json_schema_extra={"example": 3600}
    )
    database: Dict[str, Any] = Field(
        description="Database health information",
        json_schema_extra={
            "example": {
                "status": "connected",
                "signals_stored": 50000,
                "size_mb": 150.5
            }
        }
    )
    system: Dict[str, Any] = Field(
        description="System metrics",
        json_schema_extra={
            "example": {
                "memory_rss_mb": 250.5,
                "cpu_percent": 15.2,
                "num_threads": 8
            }
        }
    )


class AnalyticsResponse(BaseModel):
    """Analytics metrics response."""
    win_rate: float = Field(
        description="Win rate percentage",
        ge=0.0,
        le=100.0,
        json_schema_extra={"example": 65.5}
    )
    total_signals: int = Field(
        description="Total number of signals",
        json_schema_extra={"example": 1000}
    )
    total_trades: int = Field(
        description="Total number of trades",
        json_schema_extra={"example": 850}
    )
    avg_confidence: float = Field(
        description="Average confidence score",
        ge=0.0,
        le=1.0,
        json_schema_extra={"example": 0.75}
    )
    top_tickers: List[Dict[str, Any]] = Field(
        description="Top performing tickers",
        json_schema_extra={
            "example": [
                {"ticker": "AAPL", "signals": 50, "win_rate": 70.0},
                {"ticker": "TSLA", "signals": 45, "win_rate": 65.5}
            ]
        }
    )
    performance_by_category: Dict[str, Dict[str, float]] = Field(
        description="Performance metrics by event category",
        json_schema_extra={
            "example": {
                "earnings_beat": {"win_rate": 72.0, "count": 100},
                "product_launch": {"win_rate": 65.0, "count": 50}
            }
        }
    )


class TierLimitsResponse(BaseModel):
    """User tier limits response."""
    tier: str = Field(
        description="User tier",
        json_schema_extra={"example": "pro"}
    )
    daily_api_limit: int = Field(
        description="Daily API call limit",
        json_schema_extra={"example": 1000}
    )
    daily_api_used: int = Field(
        description="API calls used today",
        json_schema_extra={"example": 150}
    )
    daily_api_remaining: int = Field(
        description="Remaining API calls",
        json_schema_extra={"example": 850}
    )
    real_time_access: bool = Field(
        description="Real-time data access",
        json_schema_extra={"example": True}
    )
    advanced_features: bool = Field(
        description="Advanced features enabled",
        json_schema_extra={"example": True}
    )
    signal_delay_minutes: int = Field(
        description="Signal delay in minutes",
        json_schema_extra={"example": 0}
    )


class BacktestRequest(BaseModel):
    """Backtest configuration request."""
    ticker: Optional[str] = Field(
        default=None,
        description="Specific ticker to backtest (null for all)",
        json_schema_extra={"example": "AAPL"}
    )
    start_date: str = Field(
        description="Start date (YYYY-MM-DD)",
        json_schema_extra={"example": "2024-01-01"}
    )
    end_date: str = Field(
        description="End date (YYYY-MM-DD)",
        json_schema_extra={"example": "2024-12-31"}
    )
    initial_capital: float = Field(
        description="Initial capital amount",
        gt=0,
        json_schema_extra={"example": 10000.0}
    )
    strategy: Optional[str] = Field(
        default=None,
        description="Specific strategy to test",
        json_schema_extra={"example": "call_debit_spread"}
    )
    min_confidence: float = Field(
        default=0.7,
        description="Minimum confidence threshold",
        ge=0.0,
        le=1.0,
        json_schema_extra={"example": 0.7}
    )


class BacktestResponse(BaseModel):
    """Backtest results response."""
    total_return: float = Field(
        description="Total return percentage",
        json_schema_extra={"example": 25.5}
    )
    sharpe_ratio: float = Field(
        description="Sharpe ratio",
        json_schema_extra={"example": 1.8}
    )
    max_drawdown: float = Field(
        description="Maximum drawdown percentage",
        json_schema_extra={"example": -15.2}
    )
    win_rate: float = Field(
        description="Win rate percentage",
        json_schema_extra={"example": 65.0}
    )
    total_trades: int = Field(
        description="Total number of trades",
        json_schema_extra={"example": 150}
    )
    avg_return_per_trade: float = Field(
        description="Average return per trade",
        json_schema_extra={"example": 2.5}
    )
    best_trade: float = Field(
        description="Best single trade return",
        json_schema_extra={"example": 45.0}
    )
    worst_trade: float = Field(
        description="Worst single trade return",
        json_schema_extra={"example": -12.0}
    )


# OpenAPI examples for common responses (unchanged)
SIGNAL_LIST_EXAMPLE = {
    "success": True,
    "data": {
        "items": [
            {
                "id": 12345,
                "ticker": "AAPL",
                "stance": "bullish",
                "confidence": 0.85,
                "event_type": "earnings_beat",
                "reasoning": "Strong earnings beat with positive guidance",
                "created_at": 1708041600,
                "subreddit": "wallstreetbets"
            }
        ],
        "total": 1000,
        "page": 1,
        "page_size": 50,
        "has_next": True
    },
    "error": None,
    "request_id": "req_abc123"
}

ERROR_401_EXAMPLE = {
    "success": False,
    "error": "Authentication required. Please provide a valid API key.",
    "error_code": "UNAUTHORIZED",
    "request_id": "req_abc123"
}

ERROR_429_EXAMPLE = {
    "success": False,
    "error": "Rate limit exceeded. Your tier allows 1000 calls/day. Upgrade at /pricing.",
    "error_code": "RATE_LIMIT_EXCEEDED",
    "details": {
        "tier": "pro",
        "daily_limit": 1000,
        "daily_used": 1000,
        "reset_in_hours": 8
    },
    "request_id": "req_abc123"
}