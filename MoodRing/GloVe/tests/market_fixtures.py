"""Synthetic contract inputs, never provider downloads or exchange-calendar proofs."""
import json
from pathlib import Path

from ingestion.market.bars import DailyObservation
from ingestion.market.indexes import INDEX_SLOTS, IndexRegistry
from ingestion.market.sessions import SessionCalendar

ROOT = Path(__file__).parent / 'fixtures' / 'market'


def seed():
    return json.loads((ROOT / 'daily.json').read_text())


def bar(**changes):
    return DailyObservation.model_validate(seed()['observation'] | changes)


def calendar():
    return SessionCalendar.model_validate(seed()['calendar'])


def registry_data():
    # Names identify reference slots; all observations/evidence below are synthetic.
    base = seed()['observation']['instrument']
    refs = []
    for slot, (name, admin, variant, unit) in INDEX_SLOTS.items():
        instrument = dict(base, instrument_id='index:' + slot, issuer_id=None, kind='index',
                          display_name=name, currency=None, measure_unit=unit, tradable=False,
                          administrator=admin)
        refs.append(dict(slot=slot, instrument=instrument, variant=variant,
                         calendar_id='unverified-index-calendar', calendar_version='unverified-v1',
                         timezone='America/New_York', capability='unknown', rights=dict(
                             provider='unverified', dataset_version='unverified-v1',
                             retention_policy='No data access or redistribution permission established.')))
    return dict(registry_version='reference-slots-v1', references=refs)


def registry():
    return IndexRegistry.model_validate(registry_data())
