"""Synthetic pages built with the merged contracts; no provider or calendar I/O."""
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import json
from pathlib import Path

from ingestion.jobs.contracts import Manifest, Series, configuration_hash
from ingestion.jobs.offline import SuppliedPage
from ingestion.market.bars import DailyObservation
from ingestion.market.providers import ProviderPage
from ingestion.sec.contracts import payload_digest
from ingestion.universe.identity import content_hash
from market_fixtures import registry, seed
from universe_fixtures import snapshot as _universe_snapshot

universe_snapshot = lru_cache(maxsize=1)(_universe_snapshot)

POLICY = json.loads((Path(__file__).parent / 'fixtures/jobs/policy.json').read_text())


class Clock:
    def __init__(self): self.now = datetime(2026, 10, 22, tzinfo=timezone.utc)
    def __call__(self): return self.now
    def advance(self, seconds=61): self.now += timedelta(seconds=seconds)


def series(role='equity', required=True):
    data = seed()['observation']
    if role in ('index', 'reference', 'unsupported'):
        slot = {'index': 'sp500', 'reference': 'nasdaq100', 'unsupported': 'russell2000'}[role]
        ref = next(r for r in registry().references if r.slot == slot)
        from ingestion.market.indexes import IndexReference
        ref = IndexReference.model_validate(ref.model_dump() | dict(calendar_id=data['session']['calendar_id'],
            calendar_version=data['session']['calendar_version'], timezone=data['session']['timezone']))
        data.update(instrument=ref.instrument.model_dump(mode='json'), canonical_mapping=None,
                    shape='close_only', open=None, high=None, low=None, volume=None, currency=None, measure_unit='index_points')
    else:
        ref = None
        identity = {'equity': 'instrument:co000', 'retry': 'instrument:co002', 'quarantine': 'instrument:co003'}[role]
        catalog = universe_snapshot().catalog
        data['instrument'] = next(i for i in catalog.instruments if i.instrument_id == identity).model_dump(mode='json')
        data['canonical_mapping'] = catalog.resolve(identity, 'canonical', datetime(2026,3,9,20,tzinfo=timezone.utc)).model_dump(mode='json')
        data['mapping'] = catalog.resolve(identity, 'fake', datetime(2026,3,9,20,tzinfo=timezone.utc)).model_dump(mode='json')
        data['receipt']['parameters'].update(mapping_id=data['mapping']['mapping_id'], symbol=data['mapping']['symbol'])
    key = data['instrument']['instrument_id']
    data['mapping']['instrument_id'] = key
    data['receipt']['parameters']['instrument_id'] = key
    data['receipt']['parameters']['window']['start'] = '2026-03-09'
    data['receipt']['parameters']['window']['end'] = '2026-03-09'
    observation = DailyObservation.model_validate(data)
    calendar = seed()['calendar']
    calendar.update(valid_from='2026-03-09', valid_until='2026-03-09', sessions=[calendar['sessions'][1]], non_sessions=[])
    coverage = dict(instrument_id=key, window=observation.receipt.parameters.window,
                    calendar=calendar, active_from=observation.instrument.effective_from.date(),
                    days=[dict(session_date='2026-03-09', outcome='provider_gap')])
    return Series(instrument=observation.instrument, request=observation.receipt.parameters,
                  coverage=coverage, required=required, reference=ref), observation


def manifest(roles=None, **changes):
    roles = POLICY['series'] if roles is None else roles
    specs = tuple(series(r['role'], r['required'])[0] for r in roles)
    values = dict(job_id='job:synthetic', provider='fake', feed='synthetic-daily', dataset='daily-aggregates',
                  dataset_version='synthetic-v1', universe_sha256=universe_snapshot().content_hash,
                  source_sha256=content_hash(POLICY), config_sha256=content_hash(POLICY['limits']),
                  code_version='fixture-v1', series=specs, limits=POLICY['limits'], allow_partial=POLICY['allow_partial'], coverage_basis=POLICY['coverage_basis'],
                  lease_seconds=POLICY['lease_seconds'], backoff_seconds=POLICY['backoff_seconds'])
    values.update(changes)
    values['config_sha256'] = configuration_hash(values['limits'], values['allow_partial'], values['lease_seconds'], values['backoff_seconds'], values['coverage_basis'])
    result = Manifest.model_validate(values)
    result.verify_universe(universe_snapshot())
    return result


def one(**changes):
    return manifest([dict(role='equity', required=True)], **changes)


class Provider:
    def __init__(self, modes=None):
        self.modes = modes or {}
        self.calls = []

    def fetch(self, request, retry_index):
        self.calls.append((request.instrument_id, request.cursor_ref, retry_index))
        role = request.instrument_id.split(':')[-1]
        role = {'sp500': 'index', 'nasdaq100': 'reference', 'russell2000': 'unsupported',
                'co000': 'equity', 'co002': 'retry', 'co003': 'quarantine'}.get(role, role)
        mode = self.modes.get(role, role)
        _, observation = series(role)
        receipt_data = observation.receipt.model_dump()
        receipt_data['parameters'] = request
        payload = json.dumps(dict(data_kind='synthetic', symbol=request.symbol, close='102')).encode()
        receipt_data['evidence'] = dict(receipt_data['evidence'], payload_sha256=payload_digest(payload))
        receipt_data['payload_ref'] = 'sha256:' + payload_digest(payload)
        observation = DailyObservation.model_validate(observation.model_dump() | {'receipt': receipt_data})
        outcome, failure, next_cursor = 'complete', None, None
        records = (observation,)
        if mode == 'unsupported': outcome, failure, records = 'unsupported', 'unsupported_instrument', ()
        if mode in ('retry', 'always_retry') and (retry_index == 0 or mode == 'always_retry'):
            outcome, failure, records = 'retryable', 'temporarily_unavailable', ()
        if mode == 'pages' and request.cursor_ref is None: outcome, next_cursor = 'partial', 'cursor:two'
        page = ProviderPage(receipt=observation.receipt, outcome=outcome, failure_code=failure,
                            observation_checksums=tuple(o.semantic_checksum for o in records), next_cursor_ref=next_cursor)
        return SuppliedPage(page, b'corrupt synthetic payload' if mode == 'quarantine' else payload, records)
