from __future__ import annotations

import pandas as pd


def add_macd(
    df: pd.DataFrame,
    price_col: str = "close",
    ticker_col: str = "ticker",
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    out = df.copy()

    def _calc(g: pd.DataFrame) -> pd.DataFrame:
        px = g[price_col].astype(float)
        ema_fast = px.ewm(span=fast, adjust=False, min_periods=fast).mean()
        ema_slow = px.ewm(span=slow, adjust=False, min_periods=slow).mean()
        macd = ema_fast - ema_slow
        macd_signal = macd.ewm(span=signal, adjust=False, min_periods=signal).mean()
        macd_hist = macd - macd_signal
        return pd.DataFrame(
            {
                "macd": macd,
                "macd_signal": macd_signal,
                "macd_hist": macd_hist,
            },
            index=g.index,
        )

    calc = out.groupby(ticker_col, group_keys=False).apply(_calc)
    out["macd"] = calc["macd"]
    out["macd_signal"] = calc["macd_signal"]
    out["macd_hist"] = calc["macd_hist"]
    return out