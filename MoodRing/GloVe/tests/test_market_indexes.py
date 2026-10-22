from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from ingestion.market.indexes import IndexReference, IndexRegistry
from market_fixtures import registry, registry_data, seed


def test_finite_registry_contains_all_slots_without_invented_aliases():
    result = registry()
    assert len(result.references) == 22
    assert sum(r.slot.startswith('sp500_sector_') for r in result.references) == 11
    assert {'sp500', 'nasdaq_composite', 'nasdaq100', 'djia', 'russell1000', 'russell2000',
            'russell3000', 'sp400', 'sp600', 'wilshire5000', 'vix'} <= {r.slot for r in result.references}
    for ref in result.references:
        assert ref.capability == 'unknown' and ref.provider_mapping_id is None
        assert ref.available_from is None and ref.shape == 'unknown'
        assert not ref.instrument.tradable and ref.instrument.issuer_id is None
        assert not ref.rights.permits('public_display', datetime(2026, 10, 18, tzinfo=timezone.utc))
    reversed_data = registry_data(); reversed_data['references'].reverse()
    assert IndexRegistry.model_validate(reversed_data).content_hash == result.content_hash


def test_distinct_price_total_return_and_volatility_variants():
    data = registry_data()
    price = next(r for r in data['references'] if r['slot'] == 'sp500')
    total = dict(price, variant='total_return', instrument=dict(price['instrument'], instrument_id='index:sp500-total-return'))
    data['references'].append(total)
    result = IndexRegistry.model_validate(data)
    assert len([r for r in result.references if r.slot == 'sp500']) == 2
    total['instrument']['instrument_id'] = price['instrument']['instrument_id']
    with pytest.raises(ValidationError, match='distinct_ids'): IndexRegistry.model_validate(data)
    vix = next(r for r in registry_data()['references'] if r['slot'] == 'vix')
    with pytest.raises(ValidationError, match='variant'): IndexReference.model_validate(vix | {'variant': 'price'})
    with pytest.raises(ValidationError, match='variant'): IndexReference.model_validate(price | {'variant': 'volatility'})


def test_supported_index_needs_evidence_without_granting_public_rights():
    data = registry_data()['references'][0]
    with pytest.raises(ValidationError, match='coverage_evidence'):
        IndexReference.model_validate(data | {'capability': 'supported'})
    with pytest.raises(ValidationError, match='unverified_index_mapping'):
        IndexReference.model_validate(data | {'provider_mapping_id': 'alias:invented'})
    evidence = seed()['observation']['receipt']['evidence']
    supported = data | dict(capability='supported', shape='close_only', provider_mapping_id='alias:synthetic',
                           available_from='2026-03-09', evidence=evidence,
                           rights=dict(provider='fake', dataset_version='synthetic-v1', retention_policy='Synthetic only'))
    ref = IndexReference.model_validate(supported)
    assert ref.shape == 'close_only'
    assert not ref.rights.permits('public_display', datetime(2026, 10, 18, tzinfo=timezone.utc))


def test_duplicate_missing_unknown_slots_and_admin_mismatch():
    data = registry_data(); data['references'].pop()
    with pytest.raises(ValidationError, match='incomplete'): IndexRegistry.model_validate(data)
    data = registry_data(); data['references'].append(data['references'][0])
    with pytest.raises(ValidationError, match='duplicate'): IndexRegistry.model_validate(data)
    data = registry_data()['references'][0]
    with pytest.raises(ValidationError, match='unknown_index_slot'): IndexReference.model_validate(data | {'slot': 'imaginary'})
    data['instrument']['administrator'] = 'Invented'
    with pytest.raises(ValidationError, match='identity_mismatch'): IndexReference.model_validate(data)
