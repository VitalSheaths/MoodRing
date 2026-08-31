from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import Literal, Optional

import numpy as np
import pandas as pd
import pytz

from utils.UniversalDateParser import UniversalDateParser


@dataclass
class AlignmentConfig:
    market_timezone: str = "US/Eastern"
    alignment_mode: Literal["calendar", "asof"] = "calendar"
    mapping_policy: Literal["same_or_previous", "same_or_next", "previous", "next"] = "same_or_previous"
    asof_direction: Literal["backward", "forward", "nearest"] = "backward"
    asof_tolerance: Optional[str] = None
    parse_progress: bool = False
    verbose: bool = False
    after_hours_maps_to_next_trading_day: bool = True
    pre_market_maps_to_same_day: bool = True


class NewsReturnAligner:
    MARKET_OPEN = time(9, 30)
    MARKET_CLOSE = time(16, 0)

    def __init__(
        self,
        news_df: pd.DataFrame,
        returns_df: pd.DataFrame,
        news_date_col: str = "date",
        returns_date_col: str = "trade_date",
        ticker_col: str = "ticker",
        cfg: Optional[AlignmentConfig] = None,
    ):
        self.news = news_df.copy()
        self.returns = returns_df.copy()
        self.news_date_col = news_date_col
        self.returns_date_col = returns_date_col
        self.ticker_col = ticker_col
        self.cfg = cfg or AlignmentConfig()
        self.market_tz = pytz.timezone(self.cfg.market_timezone)
        self.parser = UniversalDateParser(target_timezone=self.cfg.market_timezone, verbose=self.cfg.verbose)

        self._normalize_ticker()
        self._parse_dates()
        self._build_trading_calendar()

    def _normalize_ticker(self) -> None:
        for df in (self.news, self.returns):
            if self.ticker_col in df.columns:
                df[self.ticker_col] = df[self.ticker_col].astype(str).str.upper().str.strip()

    def _parse_dates(self) -> None:
        self.news["event_ts"] = self.parser.parse_series(self.news[self.news_date_col], show_progress=self.cfg.parse_progress)
        self.news["event_date"] = self.news["event_ts"].dt.normalize()
        self.news["event_time"] = self.news["event_ts"].dt.time

        self.returns["ret_ts"] = self.parser.parse_series(self.returns[self.returns_date_col], show_progress=False)
        self.returns["trade_date"] = self.returns["ret_ts"].dt.normalize()

    def _build_trading_calendar(self) -> None:
        self.trading_calendar: dict[str, np.ndarray] = {}
        for tk, g in self.returns.groupby(self.ticker_col):
            self.trading_calendar[tk] = np.array(sorted(g["trade_date"].dropna().unique()))

    def _classify_session(self, t: time | None) -> str:
        if t is None:
            return "unknown"
        if t < self.MARKET_OPEN:
            return "pre_market"
        if t <= self.MARKET_CLOSE:
            return "market_hours"
        return "after_hours"

    def _shift_for_session(self, tk: str, d: pd.Timestamp, session: str) -> pd.Timestamp:
        cal = self.trading_calendar.get(tk)
        if cal is None or len(cal) == 0 or pd.isna(d):
            return pd.NaT

        idx = np.searchsorted(cal, d, side="left")
        exists = idx < len(cal) and cal[idx] == d

        if session == "after_hours" and self.cfg.after_hours_maps_to_next_trading_day:
            if exists and idx + 1 < len(cal):
                return cal[idx + 1]
            return cal[idx] if idx < len(cal) else pd.NaT

        return d

    def _map_calendar_date(self, ticker: str, event_date: pd.Timestamp) -> pd.Timestamp:
        if pd.isna(event_date) or ticker not in self.trading_calendar:
            return pd.NaT
        cal = self.trading_calendar[ticker]
        if len(cal) == 0:
            return pd.NaT

        idx_left = np.searchsorted(cal, event_date, side="left")
        idx_right = np.searchsorted(cal, event_date, side="right")
        exists = idx_right > idx_left

        p = self.cfg.mapping_policy
        if p == "same_or_previous":
            return cal[idx_left] if exists else (cal[idx_left - 1] if idx_left > 0 else pd.NaT)
        if p == "same_or_next":
            return cal[idx_left] if exists else (cal[idx_left] if idx_left < len(cal) else pd.NaT)
        if p == "previous":
            return cal[idx_left - 1] if idx_left > 0 else pd.NaT
        if p == "next":
            return cal[idx_left] if idx_left < len(cal) else pd.NaT
        return pd.NaT

    def _calendar_merge(self, how: Literal["left", "inner", "right", "outer"] = "left") -> pd.DataFrame:
        self.news["session"] = self.news["event_time"].apply(self._classify_session)
        self.news["mapped_event_date"] = self.news.apply(
            lambda r: self._shift_for_session(r[self.ticker_col], r["event_date"], r["session"]),
            axis=1,
        )
        self.news["trading_date"] = self.news.apply(
            lambda r: self._map_calendar_date(r[self.ticker_col], r["mapped_event_date"]),
            axis=1,
        )
        self.news["days_to_trade"] = (self.news["trading_date"] - self.news["event_date"]).dt.days

        return self.news.merge(
            self.returns,
            left_on=[self.ticker_col, "trading_date"],
            right_on=[self.ticker_col, "trade_date"],
            how=how,
            suffixes=("_news", "_returns"),
        )

    def _asof_merge(self, how: Literal["left", "inner"] = "left") -> pd.DataFrame:
        left = self.news.sort_values([self.ticker_col, "event_ts"]).copy()
        right = self.returns.sort_values([self.ticker_col, "ret_ts"]).copy()
        tol = pd.Timedelta(self.cfg.asof_tolerance) if self.cfg.asof_tolerance else None

        merged = pd.merge_asof(
            left=left,
            right=right,
            left_on="event_ts",
            right_on="ret_ts",
            by=self.ticker_col,
            direction=self.cfg.asof_direction,
            tolerance=tol,
            suffixes=("_news", "_returns"),
        )
        if how == "inner":
            merged = merged[merged["ret_ts"].notna()].copy()
        return merged

    def merge(self, how: Literal["left", "inner", "right", "outer"] = "left") -> pd.DataFrame:
        if self.cfg.alignment_mode == "asof":
            return self._asof_merge(how="inner" if how == "inner" else "left")
        return self._calendar_merge(how=how)


























