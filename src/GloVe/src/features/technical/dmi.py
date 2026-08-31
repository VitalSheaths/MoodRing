from __future__ import annotations

import pandas as pd
import numpy as np


def add_dmi(
        df: pd.DataFrame,
        ticker_col: str = "ticker",
        high_col: str = "high",
        low_col: str = "low",
        close_col: str = "close",
        period: int = 14,
) -> pd.DataFrame:
    out = df.copy()

    def _calc(g: pd.DataFrame) -> pd.DataFrame:
        high = g[high_col].astype(float)
        low = g[low_col].astype(float)
        close = g[close_col].astype(float)

        up_move = high.diff()
        down_move = -low.diff()

        plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
        minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        atr = tr.ewm(alpha=1 / period, adjust=False, min_periods=1).mean()

        # Safe division: avoid NaN from 0/0
        plus_dm_ema = plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=1).mean()
        minus_dm_ema = minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=1).mean()

        plus_di = 100 * np.where(atr > 1e-10, plus_dm_ema / atr, 0.0)
        minus_di = 100 * np.where(atr > 1e-10, minus_dm_ema / atr, 0.0)

        dx = np.where(
            (plus_di + minus_di) > 1e-10,
            100 * np.abs(plus_di - minus_di) / (plus_di + minus_di),
            0.0
        )
        adx = pd.Series(dx, index=g.index).ewm(alpha=1 / period, adjust=False, min_periods=1).mean()

        return pd.DataFrame(
            {"plus_di": plus_di, "minus_di": minus_di, "adx": adx},
            index=g.index,
        )

    calc = out.groupby(ticker_col, group_keys=False).apply(_calc)
    out["plus_di"] = calc["plus_di"]
    out["minus_di"] = calc["minus_di"]
    out["adx"] = calc["adx"]
    return out