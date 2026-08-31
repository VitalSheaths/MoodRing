from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass(frozen=True)
class MacroEvent:
    event_id: str
    event_type: str
    name: str
    scheduled_at: datetime
    country: str = "US"
    importance: str = "medium"
    source: str = ""
    actual_at: Optional[datetime] = None
    consensus_value: Optional[float] = None
    actual_value: Optional[float] = None
    previous_value: Optional[float] = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EarningsEvent:
    event_id: str
    ticker: str
    report_date: datetime
    source: str = "yfinance"
    eps_estimate: Optional[float] = None
    eps_actual: Optional[float] = None
    revenue_estimate: Optional[float] = None
    revenue_actual: Optional[float] = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InsiderTrade:
    trade_id: str
    ticker: str
    insider_name: str
    trade_type: str
    filing_date: datetime
    source: str = "sec_form4"
    shares: Optional[int] = None
    price: Optional[float] = None
    value: Optional[float] = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FomcCommunication:
    comm_id: str
    meeting_date: datetime
    release_date: datetime
    comm_type: str  # statement | minutes
    text: str
    source_url: str
    hawkish_score: Optional[float] = None
    dovish_score: Optional[float] = None
    meta: dict[str, Any] = field(default_factory=dict)
