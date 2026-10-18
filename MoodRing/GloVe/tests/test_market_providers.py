from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from ingestion.market.providers import ProviderCapability, ProviderPage, ProviderReceipt, RequestParameters, validate_page_sequence
from market_fixtures import bar, seed


@pytest.mark.parametrize('field,value', [
    ('requested_at', '2026-03-09T20:06:00Z'),
    ('responded_at', '2026-03-09T20:06:00Z'),
    ('requested_at', '2026-03-09T20:02:00'),
    ('request_ref', 'https://fake.invalid/?api_key=private'),
    ('payload_ref', '/private/path/file'),
])
def test_receipt_chronology_and_opaque_refs(field, value):
    data = seed()['observation']['receipt'] | {field: value}
    with pytest.raises(ValidationError): ProviderReceipt.model_validate(data)


def test_allowlisted_params_reject_secrets_and_generic_failures_hide_inputs():
    data = seed()['observation']['receipt']
    data['parameters']['api_key'] = 'DO_NOT_ECHO_THIS'
    with pytest.raises(ValidationError) as exc: ProviderReceipt.model_validate(data)
    assert 'DO_NOT_ECHO_THIS' not in str(exc.value)
    data = seed()['observation']['receipt']; data['evidence']['source_ref'] = 'https://fake.invalid?token=secret'
    with pytest.raises(ValidationError, match='sanitized'): ProviderReceipt.model_validate(data)
    data = seed()['observation']['receipt']; data['evidence']['source'] = 'different'
    with pytest.raises(ValidationError, match='mismatch'): ProviderReceipt.model_validate(data)
    with pytest.raises(ValidationError):
        ProviderPage(receipt=bar().receipt, outcome='retryable', failure_code='filesystem path leaked')


def capability_data():
    receipt = bar().receipt
    return dict(provider=receipt.provider, feed=receipt.feed, dataset=receipt.dataset, dataset_version=receipt.dataset_version,
                status='supported', instrument_kinds=['common_stock'], intervals=['1d'], adjustments=['raw'],
                max_records=2, max_calendar_days=10, evidence=receipt.evidence.model_dump(), rights=dict(
                    provider=receipt.provider, dataset_version=receipt.dataset_version,
                    retention_policy='Synthetic only; not a data license.'))


def test_capability_is_distinct_from_entitlement_and_fail_closed():
    capability = ProviderCapability.model_validate(capability_data())
    at = datetime(2026, 3, 10, tzinfo=timezone.utc)
    assert capability.supports(bar().receipt.parameters, 'common_stock', at)
    assert not capability.rights.permits('public_display', at)
    assert not capability.supports(bar().receipt.parameters, 'index', at)
    for change in ({'status': 'unknown'}, {'status': 'unsupported'}, {'max_calendar_days': 1}, {'max_records': 1}):
        assert not ProviderCapability.model_validate(capability_data() | change).supports(bar().receipt.parameters, 'common_stock', at)
    data = capability_data(); data['evidence']['available_at'] = None
    assert not ProviderCapability.model_validate(data).supports(bar().receipt.parameters, 'common_stock', at)
    with pytest.raises(ValidationError): ProviderCapability.model_validate(capability_data() | {'evidence': None})
    with pytest.raises(ValidationError): ProviderCapability.model_validate(capability_data() | {'coverage_from': '2026-01-01T00:00:00'})


def pages():
    first = dict(receipt=seed()['observation']['receipt'], outcome='partial', observation_checksums=['1' * 64], next_cursor_ref='cursor:two')
    second_receipt = seed()['observation']['receipt']
    second_receipt['parameters']['cursor_ref'] = 'cursor:two'
    second_receipt['requested_at'] = '2026-03-09T20:04:00Z'
    second = dict(receipt=second_receipt, outcome='complete', observation_checksums=['1' * 64, '2' * 64])
    return first, second


def test_fake_pages_deduplicate_and_validate_continuation():
    first, second = pages()
    chain = tuple(ProviderPage.model_validate(p) for p in (first, second))
    assert validate_page_sequence(chain) == ('1' * 64, '2' * 64)
    assert validate_page_sequence(()) == ()
    assert validate_page_sequence((chain[0],)) == ('1' * 64,)
    second['receipt']['parameters']['cursor_ref'] = 'cursor:wrong'
    with pytest.raises(ValueError, match='broken_page_chain'):
        validate_page_sequence((chain[0], ProviderPage.model_validate(second)))


@pytest.mark.parametrize('change', [
    dict(outcome='complete', next_cursor_ref='cursor:two'),
    dict(outcome='retryable', failure_code='rate_limited', observation_checksums=['1' * 64]),
    dict(outcome='quarantined', failure_code='temporarily_unavailable'),
    dict(outcome='partial', observation_checksums=['1' * 64, '1' * 64]),
])
def test_invalid_page_envelopes(change):
    with pytest.raises(ValidationError): ProviderPage.model_validate(dict(receipt=bar().receipt) | change)


@pytest.mark.parametrize('outcome,code', [('unsupported', 'unsupported_dates'), ('unsupported', 'unsupported_instrument'),
                                        ('unsupported', 'unsupported_adjustment'), ('quarantined', 'invalid_payload'),
                                        ('retryable', 'rate_limited'), ('retryable', 'temporarily_unavailable')])
def test_safe_failure_codes(outcome, code):
    page = ProviderPage(receipt=bar().receipt, outcome=outcome, failure_code=code)
    assert not page.observation_checksums and page.next_cursor_ref is None


def test_cursor_cycles_changed_requests_and_oversized_pages():
    first, second = pages()
    second['outcome'] = 'partial'; second['next_cursor_ref'] = 'cursor:two'
    with pytest.raises(ValidationError, match='repeated_cursor'): ProviderPage.model_validate(second)
    first, second = pages(); second['receipt']['parameters']['symbol'] = 'OTHER'
    with pytest.raises(ValueError, match='request_mismatch'):
        validate_page_sequence(tuple(ProviderPage.model_validate(p) for p in (first, second)))
    first['observation_checksums'] = ['1' * 64, '2' * 64, '3' * 64]
    with pytest.raises(ValidationError, match='exceeds'): ProviderPage.model_validate(first)


def test_raw_payload_bytes_and_reference_are_verified():
    from market_fixtures import ROOT
    receipt = bar().receipt
    receipt.verify_payload((ROOT / 'raw_payload.json').read_bytes())
    with pytest.raises(ValueError, match='payload_checksum_mismatch'):
        receipt.verify_payload(b'changed synthetic bytes')
    data = receipt.model_dump(mode='json'); data['payload_ref'] = 'sha256:' + '0' * 64
    with pytest.raises(ValidationError, match='reference_checksum_mismatch'):
        ProviderReceipt.model_validate(data)


def test_capability_effective_window_bounds_and_explicit_support_dimensions():
    data = capability_data()
    data.update(coverage_from='2026-03-07T00:00:00Z', coverage_until='2026-03-10T00:00:00Z')
    capability = ProviderCapability.model_validate(data)
    at = datetime(2026, 3, 10, tzinfo=timezone.utc)
    request_data = bar().receipt.parameters.model_dump()
    assert not capability.supports(RequestParameters.model_validate(request_data), 'common_stock', at)
    request_data['window']['start_inclusive'] = False
    assert capability.supports(RequestParameters.model_validate(request_data), 'common_stock', at)
    for change in ({'adjustments': ['split_adjusted']}, {'intervals': []}, {'instrument_kinds': []}):
        limited = ProviderCapability.model_validate(data | change)
        assert not limited.supports(RequestParameters.model_validate(request_data), 'common_stock', at)
    with pytest.raises(ValidationError):
        ProviderCapability.model_validate(data | {'coverage_until': '2026-03-06T00:00:00Z'})


def test_page_continuation_chronology_cycles_and_partial_gaps():
    first, second = pages()
    second['receipt']['requested_at'] = '2026-03-09T20:03:00Z'
    with pytest.raises(ValueError, match='page_chronology'):
        validate_page_sequence(tuple(ProviderPage.model_validate(p) for p in (first, second)))
    first, second = pages()
    with pytest.raises(ValueError, match='start_without_cursor'):
        validate_page_sequence((ProviderPage.model_validate(second),))
    gap = ProviderPage(receipt=bar().receipt, outcome='partial')
    assert validate_page_sequence((gap,)) == ()
    first, second = pages()
    third = seed()['observation']['receipt']
    third['parameters']['cursor_ref'] = 'cursor:three'
    second.update(outcome='partial', next_cursor_ref='cursor:three')
    third['requested_at'] = '2026-03-09T20:04:00Z'
    last = dict(receipt=third, outcome='partial', next_cursor_ref='cursor:two')
    with pytest.raises(ValueError, match='cursor_cycle'):
        validate_page_sequence(tuple(ProviderPage.model_validate(p) for p in (first, second, last)))
