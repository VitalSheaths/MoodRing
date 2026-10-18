"""Explicit session fixtures and deterministic calendar-date coverage windows."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator

from ingestion.sec.contracts import utc
from ingestion.universe.identity import Contract, Id, Time

VERSION = 'market-contract-v1'


class MarketContract(Contract):
    schema_version: Literal['market-contract-v1'] = VERSION


class Session(MarketContract):
    calendar_id: Id
    calendar_version: Id
    timezone: str
    session_date: date
    mode: Literal['regular', 'extended', 'index_observation']
    opens_at: Time
    closes_at: Time
    early_close: bool = False

    @model_validator(mode='after')
    def boundaries(self):
        try:
            zone = ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError('unknown_timezone') from exc
        if self.opens_at >= self.closes_at:
            raise ValueError('empty_session')
        if (self.opens_at.astimezone(zone).date() != self.session_date or
                self.closes_at.astimezone(zone).date() != self.session_date):
            raise ValueError('session_local_date_mismatch')
        return self


class SessionCalendar(MarketContract):
    """Finite pinned schedule, never a claim of full exchange-calendar coverage."""
    calendar_id: Id
    calendar_version: Id
    timezone: str
    mode: Literal['regular', 'extended', 'index_observation']
    valid_from: date
    valid_until: date  # inclusive; every date inside must be enumerated
    sessions: tuple[Session, ...]
    non_sessions: tuple[date, ...]

    @field_validator('sessions')
    @classmethod
    def ordered(cls, value):
        return tuple(sorted(value, key=lambda s: s.session_date))

    @field_validator('non_sessions')
    @classmethod
    def ordered_dates(cls, value):
        return tuple(sorted(value))

    @model_validator(mode='after')
    def schedule(self):
        if self.valid_until < self.valid_from or (self.valid_until - self.valid_from).days > 40000:
            raise ValueError('invalid_calendar_window')
        days = [s.session_date for s in self.sessions] + list(self.non_sessions)
        if len(days) != len(set(days)):
            raise ValueError('duplicate_calendar_date')
        expected = (self.valid_until - self.valid_from).days + 1
        if len(days) != expected or any(d < self.valid_from or d > self.valid_until for d in days):
            raise ValueError('calendar_dates_must_be_explicit')
        for session in self.sessions:
            if (session.calendar_id, session.calendar_version, session.timezone, session.mode) != (
                    self.calendar_id, self.calendar_version, self.timezone, self.mode):
                raise ValueError('calendar_session_mismatch')
        # Validate the timezone even for a schedule containing no sessions.
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError('unknown_timezone') from exc
        return self

    def last_completed(self, cutoff: datetime) -> Session | None:
        cutoff = utc(cutoff)
        completed = [s for s in self.sessions if s.closes_at <= cutoff]
        return completed[-1] if completed else None


class DateWindow(MarketContract):
    """Calendar dates; session filtering and completion use the pinned schedule."""
    start: date
    end: date
    start_inclusive: bool
    end_inclusive: bool
    calendar_id: Id
    calendar_version: Id
    cutoff: Time
    leap_day_policy: Literal['clamp_feb28', 'march1']

    @model_validator(mode='after')
    def nonempty(self):
        first = self.start.toordinal() + (not self.start_inclusive)
        last = self.end.toordinal() - (not self.end_inclusive)
        if first > last:
            raise ValueError('empty_date_window')
        return self

    @property
    def first_date(self) -> date:
        return date.fromordinal(self.start.toordinal() + (not self.start_inclusive))

    @property
    def last_date(self) -> date:
        return date.fromordinal(self.end.toordinal() - (not self.end_inclusive))

    def contains(self, day: date) -> bool:
        return ((day >= self.start if self.start_inclusive else day > self.start) and
                (day <= self.end if self.end_inclusive else day < self.end))

    def completed_sessions(self, calendar: SessionCalendar) -> tuple[Session, ...]:
        if (self.calendar_id, self.calendar_version) != (calendar.calendar_id, calendar.calendar_version):
            raise ValueError('window_calendar_mismatch')
        first, last = self.first_date, self.last_date
        if first < calendar.valid_from or last > calendar.valid_until:
            raise ValueError('calendar_coverage_unavailable')
        return tuple(s for s in calendar.sessions if self.contains(s.session_date) and s.closes_at <= self.cutoff)

    @classmethod
    def ten_years(cls, *, end: date, cutoff: datetime, calendar_id: str, calendar_version: str,
                  leap_day_policy: Literal['clamp_feb28', 'march1']):
        """(end - 10 calendar years, end], excluding any incomplete sessions."""
        if leap_day_policy not in ('clamp_feb28', 'march1'):
            raise ValueError('unknown_leap_day_policy')
        try:
            start = end.replace(year=end.year - 10)
        except ValueError:
            if end.month != 2 or end.day != 29 or end.year <= 10:
                raise ValueError('invalid_ten_year_window') from None
            start = date(end.year - 10, 2, 28) if leap_day_policy == 'clamp_feb28' else date(end.year - 10, 3, 1)
        return cls(start=start, end=end, start_inclusive=False, end_inclusive=True,
                   cutoff=cutoff, calendar_id=calendar_id, calendar_version=calendar_version,
                   leap_day_policy=leap_day_policy)


CoverageCode = Literal['present', 'non_session', 'pre_listing', 'post_delisting', 'suspended',
                       'unsupported', 'provider_gap', 'incomplete_session']


class CoverageDay(MarketContract):
    session_date: date
    outcome: CoverageCode
    observation_checksum: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')

    @model_validator(mode='after')
    def present_reference(self):
        if (self.outcome == 'present') != (self.observation_checksum is not None):
            raise ValueError('present_requires_observation_checksum')
        return self


class CoverageReport(MarketContract):
    instrument_id: Id
    window: DateWindow
    calendar: SessionCalendar
    active_from: date
    inactive_from: date | None = None  # first date outside lifetime
    days: tuple[CoverageDay, ...]

    @field_validator('days')
    @classmethod
    def ordered_days(cls, value):
        return tuple(sorted(value, key=lambda d: d.session_date))

    @model_validator(mode='after')
    def coverage(self):
        self.window.completed_sessions(self.calendar)  # proves calendar/window coverage
        if self.inactive_from is not None and self.inactive_from <= self.active_from:
            raise ValueError('empty_instrument_lifetime')
        first, last = self.window.first_date.toordinal(), self.window.last_date.toordinal()
        expected = {date.fromordinal(n) for n in range(first, last + 1)}
        if len(self.days) != len(expected) or {d.session_date for d in self.days} != expected:
            raise ValueError('coverage_requires_each_requested_date_once')
        sessions = {s.session_date: s for s in self.calendar.sessions}
        for day in self.days:
            session = sessions.get(day.session_date)
            required = ('non_session' if session is None else
                        'incomplete_session' if session.closes_at > self.window.cutoff else
                        'pre_listing' if day.session_date < self.active_from else
                        'post_delisting' if self.inactive_from is not None and day.session_date >= self.inactive_from else None)
            if required is not None and day.outcome != required:
                raise ValueError('coverage_reason_conflicts_with_calendar_or_lifetime')
            if required is None and day.outcome not in ('present', 'suspended', 'unsupported', 'provider_gap'):
                raise ValueError('invalid_active_session_coverage')
        return self

    @property
    def requested_complete(self) -> bool:
        return all(d.outcome in ('present', 'non_session') for d in self.days)

    @property
    def active_complete(self) -> bool:
        return all(d.outcome in ('present', 'non_session', 'pre_listing', 'post_delisting') for d in self.days)
