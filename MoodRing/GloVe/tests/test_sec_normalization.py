"""Synthetic metadata only; all network access is forbidden in these tests."""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import socket

import pytest

from ingestion.sec.contracts import UniverseManifest, payload_digest
from ingestion.sec.normalization import Limits, NormalizationError, Result, eligible, normalize
from ingestion.sec.offline import Checkpoint, eligible_records, run_batch

ROOT = Path(__file__).parent / 'fixtures/sec'
FIXTURE = ROOT / 'submissions'


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def blocked(*args,**kwargs): raise AssertionError('Network is outside this increment')
    monkeypatch.setattr(socket.socket,'connect',blocked)
    monkeypatch.setattr(socket.socket,'connect_ex',blocked)
    monkeypatch.setattr(socket,'getaddrinfo',blocked)


def inputs():
    return (json.loads((FIXTURE/'metadata.json').read_text()),
            json.loads((FIXTURE/'context.json').read_text()),
            UniverseManifest.model_validate_json((ROOT/'universe.json').read_text()))


def wire(data): return json.dumps(data,separators=(',',':')).encode()


def example(**kwargs):
    data,context,universe = inputs()
    return normalize(wire(data),context,universe,**kwargs)


def only(data,index=0):
    result = deepcopy(data)
    for key,values in result['filings']['recent'].items(): result['filings']['recent'][key] = [values[index]]
    return result


def test_all_forms_provenance_empty_facts_and_field_pointers():
    data,context,universe = inputs(); raw = (FIXTURE/'metadata.json').read_bytes()
    result = normalize(raw,context,universe)
    assert len(result.checkpoint.records) == 6
    assert all(e.status == 'accepted' for e in result.events)
    assert result.raw_sha256 == payload_digest(raw) == result.checkpoint.source_sha256
    assert [r.filing.form for r in result.checkpoint.records] == data['filings']['recent']['form']
    for index,row in enumerate(result.rows):
        assert not row.envelope.facts
        assert row.envelope.filing.cik == '0000000123'
        assert row.envelope.filing.provenance.payload_sha256 == result.raw_sha256
        assert row.pointers[0] == '/cik'
        assert f'/filings/recent/primaryDocument/{index}' in row.pointers
    assert result.checkpoint.records[3].filing.amends_accession == result.checkpoint.records[0].filing.accession
    assert Result.model_validate_json(result.model_dump_json()) == result


def test_shared_offline_runner_and_serialized_checkpoint_parity(tmp_path):
    data,context,universe = inputs(); raw = wire(data); result = normalize(raw,context,universe)
    path = tmp_path/'normalized.jsonl'
    path.write_text(''.join(json.dumps(dict(envelope=row.envelope.model_dump(mode='json'),payload_text=raw.decode()))+'\n' for row in result.rows))
    state,events = run_batch(path,universe,batch_size=2)
    state = Checkpoint.model_validate_json(state.model_dump_json())
    while events:
        state,events = run_batch(path,universe,state,batch_size=2)
    assert state.records == result.checkpoint.records
    assert state.outcomes == result.checkpoint.outcomes
    assert eligible_records(state,datetime.fromisoformat('2026-10-02T00:00:00+00:00')) == eligible(result,'2026-10-02T00:00:00Z')


def test_replay_is_deterministic_and_does_not_trust_previous_records():
    result = example()
    assert example(previous=result) == result
    changed = result.model_dump(); changed['rows'] = []
    assert example(previous=Result.model_validate(changed)) == result


@pytest.mark.parametrize('change',['bytes','context','universe','limits'])
def test_replay_rejects_changed_binding(change):
    data,context,universe = inputs(); raw = wire(data); previous = normalize(raw,context,universe)
    limits = None
    if change == 'bytes': raw += b' '
    if change == 'context': context['ingested_at'] = '2026-10-05T00:00:00Z'
    if change == 'universe': universe = UniverseManifest.model_validate(universe.model_dump()|{'universe_version':'changed'})
    if change == 'limits': limits = dict(max_rows=999)
    with pytest.raises(NormalizationError,match='replay_context_mismatch'):
        normalize(raw,context,universe,previous=previous,limits=limits)


@pytest.mark.parametrize('value',['123',123])
def test_issuer_string_and_integer_are_supported(value):
    data,context,universe = inputs(); data['cik'] = value
    assert normalize(wire(data),context,universe).checkpoint.records[0].filing.cik == '0000000123'


@pytest.mark.parametrize('value',[True,0,-1,1.5,None,{},'１２３','0','12345678901'])
def test_invalid_issuer_is_sanitized(value):
    data,context,universe = inputs(); data['cik'] = value
    with pytest.raises(NormalizationError,match='invalid_issuer'): normalize(wire(data),context,universe)


def test_unknown_availability_not_inferred_and_local_receipt_separate():
    data,context,universe = inputs(); context['availability'] = {}; context['amendments'] = {}
    result = normalize(wire(only(data)),context,universe)
    assert result.events[0].reason == 'unknown_availability'
    assert not eligible(result,'2030-01-01T00:00:00Z')
    result = example()
    for cutoff,count in [('2026-10-01T14:59:59Z',0),('2026-10-01T15:00:00Z',3),('2026-10-01T15:00:01Z',3)]:
        assert len(eligible(result,cutoff)) == count
        assert not eligible(result,cutoff,require_local_receipt=True)
    assert len(eligible(result,'2026-10-04T00:00:00Z',require_local_receipt=True)) == 6


@pytest.mark.parametrize('change',['future_availability','before_acceptance','naive_availability','bad_basis','partial_availability'])
def test_invalid_availability_never_accepted(change):
    data,context,universe = inputs(); data = only(data); key = data['filings']['recent']['accessionNumber'][0]
    if change == 'future_availability': context['availability'][key]['available_at'] = '2026-10-05T00:00:00Z'
    if change == 'before_acceptance': context['availability'][key]['available_at'] = '2026-10-01T13:00:00Z'
    if change == 'naive_availability': context['availability'][key]['available_at'] = '2026-10-01T15:00:00'
    if change == 'bad_basis': context['availability'][key]['basis'] = 'filing_date'
    if change == 'partial_availability': del context['availability'][key]['evidence']
    try: result = normalize(wire(data),context,universe)
    except NormalizationError: return
    assert not result.checkpoint.records


@pytest.mark.parametrize('field,value',[
    ('accessionNumber','bad'),('accessionNumber','１２３４５６７８９０-26-000001'),
    ('filingDate','20261001'),('filingDate','2026-02-30'),('reportDate','2027-01-01'),
    ('acceptanceDateTime','2026-10-01T14:00:00'),('acceptanceDateTime','2026-10-05T14:00:00Z'),
    ('primaryDocument','../secret'),('primaryDocument','https://private.invalid'),('form',None),
])
def test_row_contract_errors_are_quarantined(field,value):
    data,context,universe = inputs(); data = only(data); data['filings']['recent'][field][0] = value
    result = normalize(wire(data),context,universe)
    assert result.events[0].reason == 'invalid_contract'
    assert not result.checkpoint.records


def test_empty_optional_dates_and_offset_normalization():
    data,context,universe = inputs(); data = only(data)
    data['filings']['recent']['reportDate'][0] = ''
    data['filings']['recent']['acceptanceDateTime'][0] = ''
    record = normalize(wire(data),context,universe).checkpoint.records[0]
    assert record.filing.report_period is None and record.filing.accepted_at is None
    data['filings']['recent']['acceptanceDateTime'][0] = '2026-10-01T10:00:00-04:00'
    assert normalize(wire(data),context,universe).checkpoint.records[0].filing.accepted_at.hour == 14


def test_duplicate_and_content_conflict_share_existing_rules():
    data,context,universe = inputs(); data = only(data)
    for key,values in data['filings']['recent'].items(): values.extend([values[0],values[0]])
    data['filings']['recent']['primaryDocument'][2] = 'changed.htm'
    result = normalize(wire(data),context,universe)
    assert [e.reason for e in result.events] == ['validated_source_evidence','same_source_evidence','conflicting_source_identity']
    assert len(result.checkpoint.records) == 1


def test_unresolved_amendment_stops_prefix_and_reports_remaining_rows():
    data,context,universe = inputs(); del context['amendments'][data['filings']['recent']['accessionNumber'][3]]
    result = normalize(wire(data),context,universe)
    assert result.events[-1].reason == 'missing_amendment_link'
    assert result.checkpoint.next_line == 3
    assert result.public_summary()['remaining_rows'] == 2
    data = only(data,3)
    result = normalize(wire(data),context,universe)
    assert not result.checkpoint.records
    context = inputs()[1]
    result = normalize(wire(data),context,universe)
    assert result.events[0].reason == 'missing_amendment_base'
    assert result.checkpoint.next_line == 0


def test_conflicting_amendment_family_and_period_are_rejected():
    data,context,universe = inputs()
    for field,value in [('form','10-Q/A'),('reportDate','2026-09-29')]:
        changed = deepcopy(data); changed['filings']['recent'][field][3] = value
        result = normalize(wire(changed),context,universe)
        assert result.events[3].reason == 'conflicting_amendment_base'
        assert result.checkpoint.records[0].filing.form == '10-K'


def test_universe_membership_half_open_boundary():
    data,context,universe = inputs()
    changed = universe.model_dump(); changed['members'][0]['effective_until'] = '2026-10-01T15:00:00Z'
    result = normalize(wire(only(data)),context,UniverseManifest.model_validate(changed))
    assert result.events[0].reason == 'outside_universe'


@pytest.mark.parametrize('mutation,code',[
    ('missing','invalid_schema'),('misaligned','misaligned_arrays'),('wrong_array','invalid_schema'),('root','invalid_schema')])
def test_source_shapes(mutation,code):
    data,context,universe = inputs()
    if mutation == 'missing': del data['filings']['recent']['form']
    if mutation == 'misaligned': data['filings']['recent']['form'].pop()
    if mutation == 'wrong_array': data['filings']['recent']['form'] = {}
    if mutation == 'root': data = []
    with pytest.raises(NormalizationError,match=code): normalize(wire(data),context,universe)


def test_unknown_fields_are_ignored_but_cannot_override_recognized_fields():
    data,context,universe = inputs(); baseline = normalize(wire(data),context,universe)
    data['private_unused'] = {'form':'override','cik':'456'}
    data['filings']['recent']['unsupportedColumn'] = ['ignored']
    result = normalize(wire(data),context,universe)
    assert [r.filing.accession for r in result.checkpoint.records] == [r.filing.accession for r in baseline.checkpoint.records]
    assert result.raw_sha256 != baseline.raw_sha256


@pytest.mark.parametrize('raw,code',[(b'\xff','invalid_utf8'),(b'{','invalid_json'),(b'{"x":1,"x":2}','duplicate_json_key'),
    (b'{"nested":{"x":1,"x":2}}','duplicate_json_key'),(b'{"x":NaN}','nonfinite_json_number'),
    (b'{"x":Infinity}','nonfinite_json_number'),(b'{"x":1e999}','nonfinite_json_number'),
    (b'{"x":"\\ud800"}','invalid_json')])
def test_malformed_bytes_and_json(raw,code):
    _,context,universe = inputs()
    with pytest.raises(NormalizationError,match=code): normalize(raw,context,universe)


def test_raw_and_row_limits_at_and_beyond():
    data,context,universe = inputs(); raw = wire(data)
    assert normalize(raw,context,universe,limits=dict(max_input_bytes=len(raw),max_rows=6))
    with pytest.raises(NormalizationError,match='limit_input_bytes'):
        normalize(raw,context,universe,limits=dict(max_input_bytes=len(raw)-1))
    # Context must also fit the reduced row cap; prune it to test source-row overflow.
    context['availability'] = {}; context['amendments'] = {}
    with pytest.raises(NormalizationError,match='limit_rows'): normalize(raw,context,universe,limits=dict(max_rows=5))
    with pytest.raises(NormalizationError,match='limit_context'): example(limits=dict(max_rows=1))


def test_string_and_depth_limits_at_and_beyond():
    data,context,universe = inputs(); data['unused'] = 'a'*100
    assert normalize(wire(data),context,universe,limits=dict(max_string_bytes=100))
    with pytest.raises(NormalizationError,match='limit_string_bytes'):
        normalize(wire(data),context,universe,limits=dict(max_string_bytes=99))
    # Existing recognized shape has depth four. Escaped braces do not count.
    data['unused'] = '{"escaped": "\\""}'
    assert normalize(wire(data),context,universe,limits=dict(max_depth=4))
    with pytest.raises(NormalizationError,match='limit_depth'):
        normalize(wire(data),context,universe,limits=dict(max_depth=3))
    with pytest.raises(NormalizationError,match='limit_depth'):
        normalize(b'['*65+b']'*65,context,universe)


def test_result_budget_exact_and_overflow_are_atomic():
    result = example(); size = len(result.model_dump_json().encode())
    # Binding hash is fixed-width even when limits change.
    assert example(limits=dict(max_result_bytes=size))
    with pytest.raises(NormalizationError,match='limit_result_bytes'):
        example(limits=dict(max_result_bytes=size-1))


@pytest.mark.parametrize('field',list(Limits.model_fields))
@pytest.mark.parametrize('value',[0,-1,True,1.5,'1'])
def test_limits_require_finite_strict_positive_integers(field,value):
    with pytest.raises(NormalizationError,match='invalid_context_or_contract'): example(limits={field:value})


@pytest.mark.parametrize('field',list(Limits.model_fields))
def test_limits_cannot_exceed_approved_ceiling(field):
    maximum = getattr(Limits(),field)
    with pytest.raises(NormalizationError): example(limits={field:maximum+1})


def test_safe_summary_and_errors_do_not_echo_private_details():
    data,context,universe = inputs()
    secret = 'TEST_SECRET authorization Bearer /private/TEST_PATH https://private.invalid?signed=TEST_TOKEN'
    data['filings']['recent']['primaryDocument'][0] = secret
    context['availability'][data['filings']['recent']['accessionNumber'][1]]['evidence'] = secret
    result = normalize(wire(data),context,universe)
    assert secret not in json.dumps(result.public_summary())
    assert all(x not in json.dumps(result.public_summary()) for x in ['TEST_SECRET','authorization','/private/','signed='])
    with pytest.raises(NormalizationError) as exc: normalize(secret.encode(),context,universe)
    assert str(exc.value) == 'invalid_json'


@pytest.mark.parametrize('cutoff',[None,'PRIVATE_BAD_DATE','2026-10-01T15:00:00',123])
def test_cutoff_errors_are_safe(cutoff):
    with pytest.raises(NormalizationError,match='invalid_cutoff'): eligible(example(),cutoff)


def test_approved_row_ceiling_and_context_ceiling():
    data,context,universe = inputs(); data = only(data)
    for field,values in data['filings']['recent'].items(): values *= 1000
    context['availability'] = {}; context['amendments'] = {}
    result = normalize(wire(data),context,universe)
    assert len(result.events) == 1000 and result.checkpoint.next_line == 1000
    for values in data['filings']['recent'].values(): values.append(values[0])
    with pytest.raises(NormalizationError,match='limit_rows'): normalize(wire(data),context,universe)
    context['availability'] = {str(i):{} for i in range(1001)}
    with pytest.raises(NormalizationError,match='limit_context'): normalize(wire(only(data)),context,universe)


def test_approved_input_and_string_ceilings():
    data,context,universe = inputs(); raw = wire(data)
    assert normalize(raw+b' '*(1048576-len(raw)),context,universe)
    with pytest.raises(NormalizationError,match='limit_input_bytes'):
        normalize(raw+b' '*(1048577-len(raw)),context,universe)
    data['unused'] = 'x'*65536
    assert normalize(wire(data),context,universe)
    data['unused'] += 'x'
    with pytest.raises(NormalizationError,match='limit_string_bytes'): normalize(wire(data),context,universe)
    # UTF-8 bytes, not character count; ignored fields are still bounded.
    data['unused'] = 'é'*32769
    with pytest.raises(NormalizationError,match='limit_string_bytes'):
        normalize(json.dumps(data,ensure_ascii=False).encode(),context,universe)


def test_escaped_strings_and_exact_depth_ceiling():
    data,context,universe = inputs(); data['unused'] = 'é'*50
    assert normalize(wire(data),context,universe,limits=dict(max_string_bytes=100))
    with pytest.raises(NormalizationError,match='limit_string_bytes'):
        normalize(wire(data),context,universe,limits=dict(max_string_bytes=99))
    nested = 0
    for _ in range(63): nested = [nested]
    data['unused'] = nested
    assert normalize(wire(data),context,universe)
    data['unused'] = [nested]
    with pytest.raises(NormalizationError,match='limit_depth'): normalize(wire(data),context,universe)


def test_empty_source_and_unsupported_forms():
    data,context,universe = inputs()
    for values in data['filings']['recent'].values(): values.clear()
    result = normalize(wire(data),context,universe)
    assert result.total_rows == 0 and not result.events
    data = only(inputs()[0]); data['filings']['recent']['form'][0] = '4'
    assert normalize(wire(data),context,universe).events[0].reason == 'unsupported_form'


def test_context_is_copied_not_mutated_and_requires_known_version():
    data,context,universe = inputs(); before = deepcopy(context)
    normalize(wire(data),context,universe)
    assert context == before
    for field,value in [('adapter_version','v2'),('ingested_at','2026-10-04T00:00:00'),('unknown_private',{'authorization':'TEST'})]:
        changed = context|{field:value}
        with pytest.raises(NormalizationError,match='invalid_context_or_contract'):
            normalize(wire(data),changed,universe)


def test_oversize_and_deep_input_are_rejected_before_decoder(monkeypatch):
    import ingestion.sec.normalization as module
    def forbidden(*args,**kwargs): raise AssertionError('Decoder should not run')
    monkeypatch.setattr(module.json,'loads',forbidden)
    # Build context/universe before json.loads is patched globally.
    from ingestion.sec.normalization import Context
    context = Context(ingested_at='2026-10-04T00:00:00Z')
    universe = UniverseManifest(universe_version='test',members=[dict(ticker='TEST',cik='123',effective_from='2026-01-01T00:00:00Z')])
    with pytest.raises(NormalizationError,match='limit_input_bytes'): normalize(b' '*(1048577),context,universe)
    with pytest.raises(NormalizationError,match='limit_depth'): normalize(b'['*65+b']'*65,context,universe)


def test_default_result_ceiling_rejects_large_valid_metadata_atomically():
    data,context,universe = inputs(); data = only(data)
    recent = data['filings']['recent']
    for field,values in recent.items(): values *= 1000
    accessions = [f'0000000999-26-{i:06d}' for i in range(1000)]
    recent['accessionNumber'] = accessions
    sample = next(iter(context['availability'].values()))|{'evidence':'界'*1000}
    context['availability'] = {key:sample for key in accessions}
    context['amendments'] = {}
    with pytest.raises(NormalizationError,match='limit_result_bytes'):
        normalize(wire(data),context,universe)


def test_fixture_manifest_matches_approved_schema_and_limits():
    manifest = json.loads((FIXTURE/'manifest.json').read_text())
    assert manifest['data_kind'] == 'synthetic'
    assert manifest['adapter_version'] == 'submissions-subset-v1'
    assert manifest['limits'] == Limits().model_dump()
    assert set(manifest['recognized_fields']) == set(inputs()[0]['filings']['recent'])
    assert all((FIXTURE/name).is_file() for name in manifest['files'])
