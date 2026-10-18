from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from ingestion.market.bars import Adjustment, DailyObservation, validate_revisions, validate_series
from ingestion.universe.identity import content_hash
from market_fixtures import bar, registry, seed


@pytest.mark.parametrize('field,value', [
    ('close', 'NaN'), ('open', 'Infinity'), ('low', '-1'), ('high', '98'),
    ('open', '104'), ('close', '104'), ('volume', '-1'), ('notional', '-1'),
    ('close', 102.0), ('close', True), ('high', None), ('currency', 'EUR'),
    ('measure_unit', 'index_points'), ('shape', 'close_only'),
])
def test_invalid_prices_shape_units(field, value):
    with pytest.raises(ValidationError):
        bar(**{field: value})


def test_valid_stock_zero_and_missing_volume_are_distinct():
    zero, missing = bar(), bar(volume=None)
    assert zero.close == Decimal('102') and zero.volume == 0
    assert missing.volume is None
    assert zero.semantic_checksum != missing.semantic_checksum
    assert zero.observation_key == missing.observation_key
    with pytest.raises(ValidationError):
        zero.close = Decimal('12')


def test_hash_equivalent_decimals_order_and_transport_changes():
    original = bar()
    data = original.model_dump(mode='json')
    data['close'] = '102.000'
    data['receipt']['request_ref'] = 'request:retried'
    data['receipt']['requested_at'] = '2026-03-10T20:02:00Z'
    data['receipt']['responded_at'] = '2026-03-10T20:04:00Z'
    data['receipt']['evidence']['ingested_at'] = '2026-03-10T20:05:00Z'
    data['receipt']['evidence']['payload_sha256'] = '2' * 64
    data['receipt']['payload_ref'] = 'sha256:' + '2' * 64
    retry = DailyObservation.model_validate(dict(reversed(list(data.items()))))
    assert retry.semantic_checksum == original.semantic_checksum
    assert retry.receipt.content_hash != original.receipt.content_hash
    assert validate_revisions((original, retry)) == (original,)
    assert content_hash({'x': Decimal('1.000')}) == content_hash({'x': Decimal('1')})


def test_revision_chain_preserves_original_and_rejects_branch_or_orphan():
    original = bar()
    correction = bar(close='101', supersedes=original.revision_id)
    versions = validate_revisions((original, correction, correction))
    assert versions == (original, correction)
    assert original.close == 102
    with pytest.raises(ValueError, match='missing_revision_predecessor'):
        validate_revisions((correction,))
    with pytest.raises(ValueError, match='latest_predecessor'):
        validate_revisions((original, bar(close='101')))
    with pytest.raises(ValueError, match='latest_predecessor'):
        validate_revisions((original, correction, bar(close='100', supersedes=original.revision_id)))
    reverted = bar(supersedes=correction.revision_id)
    assert reverted.semantic_checksum == original.semantic_checksum
    assert reverted.revision_id != original.revision_id
    assert validate_revisions((original, correction, reverted)) == (original, correction, reverted)
    with pytest.raises(ValueError, match='unchanged_values'):
        validate_revisions((original, bar(supersedes=original.revision_id)))
    data = correction.model_dump(mode='json')
    data['receipt']['evidence']['available_at'] = '2026-03-09T20:00:00Z'
    with pytest.raises(ValueError, match='availability_reversed'):
        validate_revisions((original, DailyObservation.model_validate(data)))


def test_alias_rename_does_not_change_observation_identity():
    original = bar()
    data = original.model_dump(mode='json')
    data['mapping'].update(mapping_id='alias:old', symbol='OLD', effective_from='2020-01-01T00:00:00Z')
    data['receipt']['parameters'].update(mapping_id='alias:old', symbol='OLD')
    same = DailyObservation.model_validate(data)
    assert same.semantic_checksum == original.semantic_checksum
    data['mapping']['effective_until'] = '2026-03-08T00:00:00Z'
    with pytest.raises(ValidationError, match='identity_unavailable'):
        DailyObservation.model_validate(data)
    data = original.model_dump(mode='json')
    data['receipt']['parameters']['instrument_id'] = 'stock:another'
    with pytest.raises(ValidationError, match='identity_request_mismatch'):
        DailyObservation.model_validate(data)


@pytest.mark.parametrize('basis', ['split_adjusted', 'dividend_adjusted', 'total_return'])
def test_adjustment_modes_require_vintage_and_do_not_mix(basis):
    with pytest.raises(ValidationError, match='factor_and_vintage'):
        Adjustment(basis=basis)
    adjustment = dict(basis=basis, factor_ref='factor:synthetic', factor_sha256='3' * 64,
                      vintage_at='2026-03-09T20:01:00Z')
    data = seed()['observation']
    data['adjustment'] = adjustment
    data['receipt']['parameters']['adjustment'] = basis
    adjusted = DailyObservation.model_validate(data)
    assert adjusted.series_key != bar().series_key
    with pytest.raises(ValueError, match='mixed_observation_series'):
        validate_series((bar(), adjusted))
    assert not adjusted.eligible(datetime(2026, 3, 9, 20, tzinfo=timezone.utc))
    assert adjusted.eligible(datetime(2026, 3, 9, 20, 1, tzinfo=timezone.utc))
    data['adjustment']['vintage_at'] = '2026-03-10T00:00:00Z'
    with pytest.raises(ValidationError, match='vintage_after_response'):
        DailyObservation.model_validate(data)


def test_raw_adjusted_close_is_separate_explicit_field():
    adj = dict(basis='split_adjusted', factor_ref='factor:one', factor_sha256='3' * 64,
               vintage_at='2026-03-09T20:01:00Z')
    observation = bar(adjusted_close=dict(value='51', adjustment=adj))
    assert observation.adjustment.basis == 'raw' and observation.close == 102
    assert observation.adjusted_close.value == 51
    with pytest.raises(ValidationError):
        Adjustment(basis='raw', factor_ref='factor:invalid')
    with pytest.raises(ValidationError):
        bar(adjusted_close=dict(value='51', adjustment=dict(basis='raw')))


def test_index_close_only_null_volume_and_variant_units():
    index = next(r for r in registry().references if r.slot == 'vix')
    data = seed()['observation']
    data.update(instrument=index.instrument.model_dump(mode='json'), canonical_mapping=None, currency=None,
                measure_unit='volatility_points', shape='close_only', open=None, high=None, low=None,
                volume=None, close='17')
    data['mapping']['instrument_id'] = index.instrument.instrument_id
    data['receipt']['parameters']['instrument_id'] = index.instrument.instrument_id
    observation = DailyObservation.model_validate(data)
    assert not observation.instrument.tradable and observation.volume is None
    for changes in ({'volume': '0'}, {'notional': '0'}, {'open': '17'}, {'currency': 'USD'},
                    {'measure_unit': 'index_points'}):
        with pytest.raises(ValidationError):
            DailyObservation.model_validate(data | changes)


@pytest.mark.parametrize('path,value', [
    (('session', 'opens_at'), '2026-03-09T13:30:00'),
    (('receipt', 'parameters', 'window', 'cutoff'), '2026-03-09T19:59:00Z'),
    (('receipt', 'evidence', 'available_at'), '2026-03-09T19:59:00Z'),
])
def test_daily_bar_cannot_precede_session_completion(path, value):
    data = seed()['observation']; target = data
    for key in path[:-1]: target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValidationError):
        DailyObservation.model_validate(data)


def test_unknown_source_availability_fails_historical_eligibility():
    data = seed()['observation']; data['receipt']['evidence']['available_at'] = None
    unknown = DailyObservation.model_validate(data)
    assert not unknown.eligible(datetime(2030, 1, 1, tzinfo=timezone.utc))


def test_pinned_offline_checksums():
    import json
    from market_fixtures import ROOT
    golden = json.loads((ROOT / 'checksums.json').read_text())
    observation = bar()
    for key in ('series_key', 'observation_key', 'semantic_checksum', 'revision_id'):
        assert getattr(observation, key) == golden[key]
    assert registry().content_hash == golden['registry_checksum']


def test_revisions_cannot_reverse_ingestion_or_cross_observation_keys():
    first = bar()
    data = seed()['observation']
    data['close'] = '101'; data['supersedes'] = first.revision_id
    data['receipt']['evidence']['ingested_at'] = '2026-03-09T20:04:00Z'
    with pytest.raises(ValueError, match='ingestion_reversed'):
        validate_revisions((first, DailyObservation.model_validate(data)))
    data = seed()['observation']
    data['instrument']['instrument_id'] = 'stock:different'
    data['canonical_mapping']['instrument_id'] = 'stock:different'
    data['mapping']['instrument_id'] = 'stock:different'
    data['receipt']['parameters']['instrument_id'] = 'stock:different'
    data['supersedes'] = first.revision_id
    with pytest.raises(ValueError, match='missing_revision_predecessor'):
        validate_revisions((first, DailyObservation.model_validate(data)))


def test_offset_normalization_and_future_alias_evidence():
    data = seed()['observation']
    data['session']['opens_at'] = '2026-03-09T09:30:00-04:00'
    data['session']['closes_at'] = '2026-03-09T16:00:00-04:00'
    observation = DailyObservation.model_validate(data)
    assert observation.semantic_checksum == bar().semantic_checksum
    data['mapping']['evidence']['available_at'] = '2026-03-10T00:00:00Z'
    data['mapping']['evidence']['ingested_at'] = '2026-03-10T01:00:00Z'
    with pytest.raises(ValidationError, match='identity_unavailable'):
        DailyObservation.model_validate(data)


def test_canonical_symbol_is_separate_from_provider_alias():
    observation = bar()
    assert observation.canonical_mapping.provider == 'canonical'
    assert observation.mapping.provider == 'fake'
    with pytest.raises(ValidationError, match='stock_requires_canonical_mapping'):
        bar(canonical_mapping=None)
    data = seed()['observation']
    data['canonical_mapping']['provider'] = 'fake'
    with pytest.raises(ValidationError, match='canonical_mapping_mismatch'):
        DailyObservation.model_validate(data)


@pytest.mark.parametrize('mic', [None, 'XNYS'])
def test_common_stock_provider_and_canonical_venue_agree(mic):
    data = seed()['observation']; data['mapping']['mic'] = mic
    with pytest.raises(ValidationError, match='mic_mismatch'):
        DailyObservation.model_validate(data)
