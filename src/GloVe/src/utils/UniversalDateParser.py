from __future__ import annotations

from datetime import datetime
from typing import Union
import re

import pandas as pd
import pytz
from dateutil import parser as dateutil_parser


class UniversalDateParser:
    TIMEZONE_MAP = {
        "EST": "US/Eastern", "EDT": "US/Eastern",
        "CST": "US/Central", "CDT": "US/Central",
        "PST": "US/Pacific", "PDT": "US/Pacific",
        "GMT": "UTC", "UTC": "UTC", "BST": "Europe/London", "IST": "Asia/Kolkata",
    }

    def __init__(self, target_timezone: str = "US/Eastern", verbose: bool = False):
        self.target_tz = pytz.timezone(target_timezone)
        self.verbose = verbose

    def normalize_timezone_string(self, date_str: str) -> str:
        s = date_str
        for abbr, iana in self.TIMEZONE_MAP.items():
            s = re.sub(rf"\b{abbr}\b$", iana, s, flags=re.IGNORECASE)
        return s

    def parse_single(self, value: Union[str, int, float, datetime, pd.Timestamp]) -> pd.Timestamp | pd.NaT:
        if pd.isna(value) or value is None or value == "":
            return pd.NaT

        if isinstance(value, (datetime, pd.Timestamp)):
            ts = pd.Timestamp(value)
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            return ts.tz_convert(self.target_tz)

        s = self.normalize_timezone_string(str(value).strip())

        if s.isdigit() and len(s) >= 10:
            try:
                n = int(s)
                if len(s) == 13:
                    n = n / 1000
                return pd.Timestamp(n, unit="s", tz="UTC").tz_convert(self.target_tz)
            except Exception:
                pass

        try:
            ts = pd.to_datetime(s, utc=True, errors="raise")
            return ts.tz_convert(self.target_tz)
        except Exception:
            pass

        try:
            d = dateutil_parser.parse(s)
            if d.tzinfo is None:
                d = pytz.UTC.localize(d)
            return pd.Timestamp(d).tz_convert(self.target_tz)
        except Exception:
            if self.verbose:
                print(f"Failed date parse: {value}")
            return pd.NaT

    def parse_series(self, series: pd.Series, show_progress: bool = False) -> pd.Series:
        ts = pd.to_datetime(series, errors="coerce", utc=True)

        mask = ts.isna() & series.notna()
        if mask.any():
            if show_progress:
                from tqdm.auto import tqdm
                tqdm.pandas()
                ts2 = series[mask].progress_apply(self.parse_single)
            else:
                ts2 = series[mask].apply(self.parse_single)
            ts.loc[mask] = ts2

        return ts.dt.tz_convert(self.target_tz)