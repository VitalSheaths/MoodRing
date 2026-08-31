from __future__ import annotations

import pandas as pd


def add_rsi(
    df: pd.DataFrame,
    price_col: str = "close",
    ticker_col: str = "ticker",
    period: int = 14,
    out_col: str = "rsi_14",
) -> pd.DataFrame:
    out = df.copy()

    def _rsi(g: pd.DataFrame) -> pd.Series:
        delta = g[price_col].diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
        avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
        rs = avg_gain / avg_loss.replace(0, pd.NA)
        rsi = 100 - (100 / (1 + rs))
        return rsi.fillna(50.0)

    out[out_col] = out.groupby(ticker_col, group_keys=False).apply(_rsi).reset_index(level=0, drop=True)
    return out