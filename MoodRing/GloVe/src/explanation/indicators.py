"""Pure indicator cards shared by tools and any later approved feature consumer."""
from __future__ import annotations

import math
from statistics import fmean, pstdev, stdev

import pandas as pd

from features.technical.macd import add_macd
from ingestion.market.bars import DailyObservation
from .contracts import Card

VERSION = 'indicator-cards-v1'
SPECS = (
    ('return_1', 'trend', 'fraction'), ('range_pct', 'volatility', 'fraction'),
    ('relative_volume_20', 'volume', 'multiple'), ('rsi_14', 'trend', 'index'),
    ('macd', 'trend', 'price'), ('macd_signal', 'trend', 'price'),
    ('macd_hist', 'trend', 'price'), ('atr_14_pct', 'volatility', 'fraction'),
    ('bollinger_pct_b', 'volatility', 'fraction'),
    ('bollinger_bandwidth', 'volatility', 'fraction'),
    ('realized_vol_20', 'volatility', 'daily_log_return_std'),
    ('obv_change_20', 'volume', 'shares'),
)
UNSUPPORTED = (
    ('volume_profile', 'volume', 'unsupported', 'intraday_data_required'),
    ('garch', 'volatility', 'not_evaluated', 'fit_and_convergence_evidence_required'),
    ('evt', 'tail', 'not_evaluated', 'training_threshold_and_tail_sample_required'),
    ('portfolio_risk', 'portfolio', 'not_evaluated', 'weights_and_aligned_histories_required'),
    ('direction', 'model', 'not_evaluated', 'validated_run_required'),
    ('expected_return', 'model', 'not_evaluated', 'validated_run_required'),
    ('variance', 'model', 'not_evaluated', 'validated_run_required'),
    ('trade_success', 'model', 'not_evaluated', 'validated_run_required'),
    ('drawdown', 'model', 'not_evaluated', 'validated_run_required'),
)


def _wilder(values: list[float], period: int = 14) -> float:
    """Seed Wilder smoothing with the first full arithmetic mean."""
    result = fmean(values[:period])
    for value in values[period:]:
        result = ((period - 1) * result + value) / period
    return result


def calculate(rows: tuple[DailyObservation, ...], missing: str | None = None) -> tuple[Card, ...]:
    """Compute only contiguous eligible history; floats are an explicit derived view.

    Prices remain exact in DailyObservation. Sample volatility uses ddof=1;
    Bollinger bands use population deviation. Warm-up is never filled with 50/0.
    """
    values: dict[str, float] = {}
    reasons: dict[str, str] = {}
    if rows and missing is None:
        close = [float(row.close) for row in rows]
        high = [float(row.high) for row in rows]
        low = [float(row.low) for row in rows]
        n = len(rows)
        values['range_pct'] = (high[-1] - low[-1]) / close[-1]
        if n >= 2:
            values['return_1'] = close[-1] / close[-2] - 1
        if n >= 15:
            changes = [b - a for a, b in zip(close, close[1:])]
            gain = _wilder([max(x, 0) for x in changes])
            loss = _wilder([max(-x, 0) for x in changes])
            if gain == loss == 0:
                reasons['rsi_14'] = 'flat_series_undefined'
            else:
                values['rsi_14'] = 100 if loss == 0 else 100 - 100 / (1 + gain / loss)
            ranges = [max(high[i] - low[i], abs(high[i] - close[i-1]),
                          abs(low[i] - close[i-1])) for i in range(1, n)]
            values['atr_14_pct'] = _wilder(ranges) / close[-1]
        if n >= 20:
            mean, sigma = fmean(close[-20:]), pstdev(close[-20:])
            values['bollinger_bandwidth'] = 4 * sigma / mean
            if sigma:
                values['bollinger_pct_b'] = (close[-1] - mean + 2*sigma) / (4*sigma)
            else:
                reasons['bollinger_pct_b'] = 'zero_band_width'
        if n >= 21:
            values['realized_vol_20'] = stdev([
                math.log(b / a) for a, b in zip(close[-21:-1], close[-20:])])
            if any(row.volume is None for row in rows[-21:]):
                reasons['relative_volume_20'] = reasons['obv_change_20'] = 'missing_volume'
            else:
                volume = [float(row.volume) for row in rows[-21:]]
                baseline = fmean(volume[:-1])  # deliberately excludes the target session
                if baseline:
                    values['relative_volume_20'] = volume[-1] / baseline
                else:
                    reasons['relative_volume_20'] = 'zero_volume_baseline'
                values['obv_change_20'] = sum(
                    (1 if b > a else -1 if b < a else 0) * v
                    for a, b, v in zip(close[-21:-1], close[-20:], volume[1:]))
        if n >= 26:
            frame = pd.DataFrame({'ticker': ['fixture']*n, 'close': close})
            computed = add_macd(frame).iloc[-1]
            for name in ('macd', 'macd_signal', 'macd_hist'):
                if math.isfinite(computed[name]):
                    values[name] = float(computed[name])
    cards = []
    for key, bundle, unit in SPECS:
        value = values.get(key)
        if value is not None and math.isfinite(value):
            cards.append(Card(key=key, bundle=bundle, unit=unit, status='ok', value=value))
        else:
            reason = missing or reasons.get(key) or ('nonfinite_derived_value' if value is not None else 'insufficient_history')
            cards.append(Card(key=key, bundle=bundle, unit=unit, status='missing', reason=reason))
    cards.extend(Card(key=k, bundle=b, unit='not_applicable', status=s, reason=r)
                 for k, b, s, r in UNSUPPORTED)
    return tuple(cards)
