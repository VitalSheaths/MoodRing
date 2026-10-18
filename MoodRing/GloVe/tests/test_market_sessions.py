from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from ingestion.market.sessions import CoverageReport, DateWindow, Session, SessionCalendar
from market_fixtures import ROOT, bar, calendar, seed


def test_dst_weekend_and_completed_cutoff():
    schedule = calendar()
    assert [s.opens_at.hour for s in schedule.sessions] == [14, 13]
    assert [s.opens_at.astimezone(ZoneInfo(schedule.timezone)).hour for s in schedule.sessions] == [9, 9]
    window = bar().receipt.parameters.window
    assert len(window.completed_sessions(schedule)) == 2
    prior = window.model_dump() | {'cutoff': '2026-03-09T19:59:00Z'}
    assert len(DateWindow.model_validate(prior).completed_sessions(schedule)) == 1
    assert schedule.last_completed(datetime(2026, 3, 9, 20, tzinfo=timezone.utc)).session_date == date(2026, 3, 9)
    assert schedule.last_completed(datetime(2026, 3, 5, tzinfo=timezone.utc)) is None


def test_holiday_early_close_and_session_date_validation():
    schedule = SessionCalendar.model_validate_json((ROOT / 'early_close.json').read_text())
    assert schedule.non_sessions == (date(2026, 11, 26),)
    close = schedule.sessions[0]
    assert close.early_close and close.closes_at.hour == 18
    assert schedule.last_completed(datetime(2026, 11, 27, 17, 59, tzinfo=timezone.utc)) is None
    assert schedule.last_completed(datetime(2026, 11, 27, 18, tzinfo=timezone.utc)) == close
    for changes in ({'session_date': '2026-11-26'}, {'timezone': 'Invented/Zone'},
                    {'opens_at': '2026-11-27T19:00:00Z'}, {'opens_at': '2026-11-27T00:30:00Z'}):
        with pytest.raises(ValidationError): Session.model_validate(close.model_dump() | changes)


def test_calendar_requires_explicit_non_sessions_and_unique_dates():
    data = seed()['calendar']
    with pytest.raises(ValidationError, match='explicit'):
        SessionCalendar.model_validate(data | {'non_sessions': []})
    with pytest.raises(ValidationError, match='duplicate'):
        SessionCalendar.model_validate(data | {'non_sessions': ['2026-03-06', '2026-03-08']})
    data['sessions'][0]['calendar_version'] = 'wrong'
    with pytest.raises(ValidationError, match='mismatch'):
        SessionCalendar.model_validate(data)


@pytest.mark.parametrize('start_inclusive,end_inclusive,count', [(True, True, 2), (False, True, 1), (True, False, 1), (False, False, 0)])
def test_explicit_window_inclusivity(start_inclusive, end_inclusive, count):
    data = bar().receipt.parameters.window.model_dump() | dict(start_inclusive=start_inclusive, end_inclusive=end_inclusive)
    window = DateWindow.model_validate(data)
    assert len(window.completed_sessions(calendar())) == count


def test_ten_calendar_years_leap_day_policy_and_finite_calendar_coverage():
    args = dict(end=date(2024, 2, 29), cutoff=datetime(2024, 3, 1, tzinfo=timezone.utc),
                calendar_id='fixture-equity', calendar_version='fixture-2026-v1')
    a = DateWindow.ten_years(**args, leap_day_policy='clamp_feb28')
    b = DateWindow.ten_years(**args, leap_day_policy='march1')
    assert a.start == date(2014, 2, 28) and b.start == date(2014, 3, 1)
    assert not a.start_inclusive and a.end_inclusive
    assert not a.contains(a.start) and a.contains(a.end)
    with pytest.raises(ValueError, match='coverage_unavailable'): a.completed_sessions(calendar())
    with pytest.raises(ValueError, match='unknown_leap'): DateWindow.ten_years(**args, leap_day_policy='guess')


def coverage_data():
    return dict(instrument_id='stock:example', window=bar().receipt.parameters.window.model_dump(),
                calendar=calendar().model_dump(), active_from='2026-03-09', days=[
                    dict(session_date='2026-03-06', outcome='pre_listing'),
                    dict(session_date='2026-03-07', outcome='non_session'),
                    dict(session_date='2026-03-08', outcome='non_session'),
                    dict(session_date='2026-03-09', outcome='present', observation_checksum=bar().semantic_checksum)])


def test_requested_vs_active_coverage_no_padding():
    report = CoverageReport.model_validate(coverage_data())
    assert report.active_complete and not report.requested_complete
    assert len(report.days) == 4
    data = coverage_data(); data['days'][0] = dict(session_date='2026-03-06', outcome='present', observation_checksum='1' * 64)
    with pytest.raises(ValidationError, match='conflicts'): CoverageReport.model_validate(data)
    data = coverage_data(); data['days'].pop()
    with pytest.raises(ValidationError, match='each_requested_date'): CoverageReport.model_validate(data)


@pytest.mark.parametrize('outcome', ['suspended', 'unsupported', 'provider_gap'])
def test_active_session_gaps_are_explicit(outcome):
    data = coverage_data(); data['days'][-1] = dict(session_date='2026-03-09', outcome=outcome)
    report = CoverageReport.model_validate(data)
    assert not report.active_complete and not report.requested_complete


def test_delisted_and_incomplete_sessions():
    data = coverage_data(); data.update(active_from='2020-01-01', inactive_from='2026-03-09')
    data['days'][0] = dict(session_date='2026-03-06', outcome='present', observation_checksum='1' * 64)
    data['days'][-1] = dict(session_date='2026-03-09', outcome='post_delisting')
    assert CoverageReport.model_validate(data).active_complete
    data = coverage_data(); data['window']['cutoff'] = '2026-03-09T19:59:00Z'
    with pytest.raises(ValidationError, match='conflicts'): CoverageReport.model_validate(data)
    data['days'][-1] = dict(session_date='2026-03-09', outcome='incomplete_session')
    assert not CoverageReport.model_validate(data).active_complete
