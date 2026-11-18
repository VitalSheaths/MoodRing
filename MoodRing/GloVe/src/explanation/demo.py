"""Fictional OHLCV and explicit illustrative calendar for the no-LLM lesson."""
from datetime import date, datetime, time, timedelta, timezone

from ingestion.market.bars import DailyObservation
from ingestion.market.sessions import SessionCalendar
from ingestion.universe.identity import content_hash
from .contracts import Query, Snapshot


def demo_snapshot(owner_id: str = 'learner') -> Snapshot:
    """Construct forty fictional weekday sessions with actual contract validation.

    This is deliberately not an exchange calendar or a claim of observed prices.
    Every generated timestamp and receipt is explicitly synthetic.
    """
    start = date(2026, 8, 3)
    days = [start + timedelta(days=i) for i in range(54)]
    sessions = [dict(calendar_id='bot-calendar', calendar_version='fixture-v1',
                     timezone='UTC', mode='regular', session_date=d,
                     opens_at=datetime.combine(d, time(14), timezone.utc),
                     closes_at=datetime.combine(d, time(20), timezone.utc))
                for d in days if d.weekday() < 5]
    calendar = SessionCalendar(calendar_id='bot-calendar', calendar_version='fixture-v1',
                               timezone='UTC', mode='regular', valid_from=days[0], valid_until=days[-1],
                               sessions=sessions, non_sessions=[d for d in days if d.weekday() >= 5])
    origin = datetime(2020, 1, 1, tzinfo=timezone.utc)
    identity_evidence = dict(source='bot-fixture', dataset_version='fixture-v1',
                             source_ref='fixture:identity', payload_sha256=content_hash('fictional-identity'),
                             available_at=origin, ingested_at=origin)
    instrument = dict(instrument_id='stock:example', issuer_id='issuer:example',
                      display_name='Fictional Example Company', kind='common_stock', country='US',
                      currency='USD', measure_unit='currency', status='active', tradable=True,
                      effective_from=origin, evidence=identity_evidence)
    mapping = dict(mapping_id='alias:example', instrument_id='stock:example', provider='bot-fixture',
                   symbol='EXAMPLE', mic='XNAS', effective_from=origin, evidence=identity_evidence)
    canonical = dict(mapping, mapping_id='canonical:example', provider='canonical')
    observations = []
    for i, session in enumerate(calendar.sessions):
        # Constant volume baseline, then a high-volume final day with no net move.
        close = 100 + min(i, len(sessions)-2)
        prices = dict(open=str(close), high=str(close+2), low=str(close-2), close=str(close),
                      volume='3000' if i == len(sessions)-1 else '1000')
        digest = content_hash(prices)
        receipt = dict(provider='bot-fixture', feed='synthetic-daily', dataset='demo-ohlcv',
                       dataset_version='fixture-v1', request_ref=f'fixture:request-{i}',
                       requested_at=session.closes_at, responded_at=session.closes_at+timedelta(minutes=1),
                       evidence=dict(source='bot-fixture', dataset_version='fixture-v1',
                                     source_ref=f'fixture:row-{i}', payload_sha256=digest,
                                     available_at=session.closes_at, ingested_at=session.closes_at+timedelta(minutes=1)),
                       payload_ref='sha256:'+digest,
                       parameters=dict(instrument_id='stock:example', mapping_id='alias:example',
                                       symbol='EXAMPLE', adjustment='raw', limit=1,
                                       window=dict(start=days[0], end=days[-1], start_inclusive=True,
                                                   end_inclusive=True, calendar_id='bot-calendar',
                                                   calendar_version='fixture-v1', cutoff=session.closes_at,
                                                   leap_day_policy='clamp_feb28')))
        observations.append(DailyObservation(instrument=instrument, mapping=mapping,
                                             canonical_mapping=canonical, session=session,
                                             receipt=receipt, adjustment={'basis': 'raw'}, shape='ohlc',
                                             currency='USD', measure_unit='currency', **prices))
    return Snapshot(owner_id=owner_id, calendar=calendar, observations=tuple(observations))


def demo_query(snapshot: Snapshot) -> Query:
    """Select a concrete context independently of the question text."""
    session = snapshot.calendar.sessions[-1]
    return Query(snapshot_id=snapshot.snapshot_id, instrument_id='stock:example',
                 session=session.session_date, cutoff=session.closes_at)
