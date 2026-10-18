import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from ingestion.sec.contracts import Envelope, Filing, NormalizedFact, UniverseManifest, utc
from ingestion.sec.offline import Checkpoint, eligible_records, run_batch

FIXTURES = Path(__file__).parent / 'fixtures/sec'


def universe():
    return UniverseManifest.model_validate_json((FIXTURES / 'universe.json').read_text())


def base():
    return json.loads((FIXTURES / 'filings.jsonl').read_text().splitlines()[0])


def test_timezone_and_separate_filing_agent_identity():
    record = Envelope.model_validate(base()['envelope'])
    assert record.filing.cik == '0000000123'
    assert record.filing.accession.startswith('0000000999')
    assert record.filing.accepted_at == datetime(2026, 10, 1, 14, tzinfo=timezone.utc)
    assert record.filing.eligible(datetime(2026, 10, 1, 15, tzinfo=timezone.utc))
    assert not record.filing.eligible(datetime(2026, 10, 1, 14, tzinfo=timezone.utc))
    with pytest.raises(ValueError):
        record.filing.eligible(datetime(2026, 10, 1))


@pytest.mark.parametrize('field,value', [('form','4'),('report_period','2027-01-01'),
 ('accepted_at','2026-10-01T14:00:00'), ('cik','0'), ('source_url','https://evil.invalid/filing'),
 ('document_name','../x'), ('amends_accession','0000000999-26-000001')])
def test_invalid_metadata_is_rejected(field, value):
    filing = base()['envelope']['filing']
    filing[field] = value
    with pytest.raises(ValidationError):
        Filing.model_validate(filing)


def test_missing_availability_cannot_use_report_period_as_cutoff():
    wire = base()['envelope']
    wire['filing']['provenance'].update(available_at=None, availability_basis=None, availability_evidence=None)
    filing = Envelope.model_validate(wire).filing
    assert not filing.eligible(datetime(2030, 1, 1, tzinfo=timezone.utc))
    wire['filing']['provenance']['available_at'] = '2026-10-01T15:00:00Z'
    with pytest.raises(ValidationError):
        Envelope.model_validate(wire)


def test_batches_restart_and_preserve_all_input_outcomes():
    path = FIXTURES / 'filings.jsonl'
    state, events = run_batch(path, universe(), batch_size=2)
    assert [e.status for e in events] == ['accepted','accepted']
    # Simulate process restart using only serialized checkpoint state.
    state = Checkpoint.model_validate_json(state.model_dump_json())
    while True:
        state, events = run_batch(path, universe(), state, batch_size=2)
        if not events: break
    uninterrupted, _ = run_batch(path, universe(), batch_size=100)
    assert state == uninterrupted
    assert state.next_line == 8
    assert [e.status for e in state.outcomes] == ['accepted','accepted','duplicate','quarantined',
                                                'quarantined','quarantined','quarantined','accepted']
    assert len(state.records) == 3
    assert state.records[0].filing.accession != state.records[1].filing.accession
    earlier = eligible_records(state, datetime(2026,10,2,tzinfo=timezone.utc))
    assert all(not r.filing.form.endswith('/A') for r in earlier)
    later = eligible_records(state, datetime(2026,10,4,tzinfo=timezone.utc))
    assert len(later) == 3 # original remains alongside its amendment


def test_retryable_base_does_not_advance_cursor():
    path = FIXTURES / 'missing_base.jsonl'
    state, events = run_batch(path, universe())
    assert state.next_line == 0 and events[0].status == 'retryable'
    again, retried = run_batch(path, universe(), state)
    assert again == state and retried == events


def test_checkpoint_rejects_changed_sources_universe_and_cursor(tmp_path):
    path = tmp_path / 'filings.jsonl'
    path.write_bytes((FIXTURES/'filings.jsonl').read_bytes())
    state, _ = run_batch(path, universe(), batch_size=1)
    path.write_bytes(path.read_bytes()+b'\n')
    with pytest.raises(ValueError, match='different source'):
        run_batch(path, universe(), state)
    changed = universe().model_copy(update={'universe_version':'new-version'})
    with pytest.raises(ValueError, match='different source'):
        run_batch(FIXTURES/'filings.jsonl', changed, state)
    data = state.model_dump(); data['next_line'] = 5
    with pytest.raises(ValidationError):
        Checkpoint.model_validate(data)


def test_hash_conflict_and_amendment_relationships(tmp_path):
    records = [json.loads(line) for line in (FIXTURES/'filings.jsonl').read_text().splitlines()[:2]]
    records[1]['envelope']['filing']['cik'] = '456'
    records[1]['envelope']['filing']['source_url'] = records[1]['envelope']['filing']['source_url'].replace('/123/','/456/')
    path=tmp_path/'bad.jsonl'; path.write_text('\n'.join(json.dumps(r) for r in records)+'\n')
    state, events=run_batch(path,universe())
    assert events[1].reason == 'conflicting_amendment_base'
    tampered=base(); tampered['payload_text'] += 'modified'
    path.write_text(json.dumps(tampered)+'\n')
    state, events=run_batch(path,universe())
    assert events[0].reason == 'payload_hash_mismatch' and not state.records


def test_universe_intervals_and_membership(tmp_path):
    manifest=universe().model_dump(mode='json')
    manifest['members'].append(manifest['members'][0])
    with pytest.raises(ValidationError, match='overlap'):
        UniverseManifest.model_validate(manifest)
    wire=base(); member=universe().model_dump(mode='json'); member['members'][0]['effective_until']='2026-10-01T15:00:00Z'
    path=tmp_path/'membership.jsonl'; path.write_text(json.dumps(wire)+'\n')
    state,events=run_batch(path,UniverseManifest.model_validate(member))
    assert events[0].reason=='outside_universe'


def test_fact_missing_unit_period_and_finite_value():
    values=dict(accession='0000000999-26-000001',concept='example:Revenue',value='0',unit='USD',
                period_start='2026-07-01',period_end='2026-09-30',fiscal_year=2026,evidence_pointer='span')
    assert NormalizedFact(**values).value == 0
    for changes in ({'unit':'bogus'}, {'period_start':'2026-12-01'}, {'value':'NaN'}, {'value':None}):
        with pytest.raises(ValidationError): NormalizedFact(**(values | changes))
    assert NormalizedFact(**(values | {'value':None,'missing_reason':'not_reported'})).value is None


def test_bounded_input_and_batch_validation(tmp_path):
    path=tmp_path/'oversized.jsonl'; path.write_bytes(b'x'*65537)
    with pytest.raises(ValueError, match='bounded'): run_batch(path,universe())
    for size in (0,101,True,1.5):
        with pytest.raises(ValueError, match='batch_size'): run_batch(FIXTURES/'filings.jsonl',universe(),batch_size=size)


def test_interrupted_batch_replays_from_last_committed_checkpoint(monkeypatch):
    path=FIXTURES/'filings.jsonl'
    state,_=run_batch(path,universe(),batch_size=1)
    saved=state.model_dump_json()
    validate=Envelope.model_validate
    calls=[0]
    def interrupted(value):
        calls[0]+=1
        if calls[0]==2: raise RuntimeError('simulated interruption')
        return validate(value)
    with monkeypatch.context() as context:
        context.setattr(Envelope,'model_validate',interrupted)
        with pytest.raises(RuntimeError,match='interruption'):
            run_batch(path,universe(),state,batch_size=2)
    assert state.model_dump_json()==saved
    resumed,_=run_batch(path,universe(),Checkpoint.model_validate_json(saved),batch_size=100)
    complete,_=run_batch(path,universe(),batch_size=100)
    assert resumed==complete


@pytest.mark.parametrize('form',['10-K','10-Q','8-K'])
def test_supported_filing_families_and_contract_version(form):
    filing=base()['envelope']['filing']; filing['form']=form
    assert Filing.model_validate(filing).form==form
    filing['schema_version']='sec-contract-v2'
    with pytest.raises(ValidationError): Filing.model_validate(filing)


def test_append_only_duplicate_facts_cannot_change_silently(tmp_path):
    original=base(); modified=base()
    modified['envelope']['facts']=[dict(accession=modified['envelope']['filing']['accession'],concept='example:Revenue',
                                      value='5',unit='USD',period_end='2026-09-30',fiscal_year=2026,evidence_pointer='span')]
    path=tmp_path/'conflicting-facts.jsonl'; path.write_text(json.dumps(original)+'\n'+json.dumps(modified)+'\n')
    state,events=run_batch(path,universe())
    assert len(state.records)==1 and events[1].reason=='conflicting_source_identity'


@pytest.mark.parametrize('time',[123,True,'2026-10-01','2026-10-01T14:00:00'])
def test_no_implicit_epoch_or_timezone_coercion(time):
    wire=base()['envelope']; wire['filing']['provenance']['ingested_at']=time
    with pytest.raises(ValidationError): Envelope.model_validate(wire)


def test_record_limit_does_not_allow_an_unbounded_fixture_run(monkeypatch):
    import ingestion.sec.offline as offline
    monkeypatch.setattr(offline,'MAX_RECORDS',2)
    with pytest.raises(ValueError,match='record limit'):
        run_batch(FIXTURES/'filings.jsonl',universe(),batch_size=100)


def test_amendment_cannot_link_a_different_reporting_period(tmp_path):
    records=[json.loads(line) for line in (FIXTURES/'filings.jsonl').read_text().splitlines()[:2]]
    records[1]['envelope']['filing']['report_period']='2026-06-30'
    path=tmp_path/'wrong-period.jsonl'; path.write_text('\n'.join(json.dumps(r) for r in records)+'\n')
    state,events=run_batch(path,universe())
    assert len(state.records)==1 and events[1].reason=='conflicting_amendment_base'
    good,_=run_batch(FIXTURES/'filings.jsonl',universe(),batch_size=2)
    snapshot=good.model_dump(mode='json'); snapshot['records'][1]['filing']['report_period']='2026-06-30'
    with pytest.raises(ValidationError,match='report period'):
        Checkpoint.model_validate(snapshot)
