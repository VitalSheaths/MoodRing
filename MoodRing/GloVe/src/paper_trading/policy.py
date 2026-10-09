from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


def decimal(value) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError('Amount must be finite')
    return result


def money(value) -> Decimal:
    return decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


class ScenarioPolicy(BaseModel):
    position_size_mode: Literal['fixed_pct', 'confidence_weighted', 'fractional_kelly'] = 'fixed_pct'
    position_pct: float = Field(default=2, gt=0, le=25)
    max_position_pct: float = Field(default=10, gt=0, le=25)
    max_gross_exposure_pct: float = Field(default=50, gt=0, le=100)
    cash_reserve_pct: float = Field(default=10, ge=0, lt=100)
    risk_per_trade_pct: float = Field(default=1, gt=0, le=5)
    max_drawdown_pct: float = Field(default=20, gt=0, le=100)
    max_prediction_drawdown: float = Field(default=0.20, gt=0, le=1)
    commission_bps: float = Field(default=10, ge=0, le=100)
    slippage_bps: float = Field(default=5, ge=0, le=100)
    kelly_fraction: float = Field(default=0.25, gt=0, le=0.5)
    kelly_win_probability: float | None = Field(default=None, gt=0, lt=1)
    kelly_payoff_ratio: float | None = Field(default=None, gt=0)
    quote_max_age_s: int = Field(default=900, ge=1, le=3600)
    signal_max_age_s: int = Field(default=3600, ge=1, le=86400)
    feature_max_age_s: int = Field(default=86400, ge=1, le=604800)
    auto_exit: bool = True

    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

    def fraction(self, confidence: float) -> Decimal:
        fraction = decimal(self.position_pct) / 100
        if self.position_size_mode == 'confidence_weighted':
            fraction *= decimal(0.5 + 1.5 * confidence)
        elif self.position_size_mode == 'fractional_kelly':
            if self.kelly_win_probability is None or self.kelly_payoff_ratio is None:
                raise ValueError('Fractional Kelly requires a calibrated win probability and payoff ratio')
            p, b = decimal(self.kelly_win_probability), decimal(self.kelly_payoff_ratio)
            fraction = max(Decimal(0), p - (1 - p) / b) * decimal(self.kelly_fraction)
        return min(fraction, decimal(self.max_position_pct) / 100)

    def budget(self, *, equity, cash, exposure, drawdown, confidence, prediction_drawdown, stop_fraction):
        if drawdown >= decimal(self.max_drawdown_pct) / 100:
            raise ValueError('Portfolio drawdown limit reached; new entries are paused')
        if prediction_drawdown < 0 or prediction_drawdown > decimal(self.max_prediction_drawdown):
            raise ValueError('Predicted drawdown exceeds the scenario limit')
        fee_rate = decimal(self.commission_bps) / 10000
        slip_rate = decimal(self.slippage_bps) / 10000
        risk_fraction = max(stop_fraction, prediction_drawdown) + 2 * (fee_rate + slip_rate)
        caps = [
            equity * self.fraction(confidence),
            cash - equity * decimal(self.cash_reserve_pct) / 100,
            (equity * decimal(self.max_gross_exposure_pct) / 100 - exposure) * (1 + fee_rate),
            equity * decimal(self.risk_per_trade_pct) / 100 / risk_fraction,
        ]
        return max(Decimal(0), min(caps)).quantize(Decimal('0.01'), rounding=ROUND_DOWN)
