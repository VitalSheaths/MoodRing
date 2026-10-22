from datetime import datetime, timedelta, timezone
import json
import socket

import pytest

from ingestion.jobs.offline import FakeStore, Rejected, SimulatedCrash, run
from ingestion.market.synthetic import AdapterError, AdapterLimits, FixtureAdapter, SourcePage
from ingestion.market.providers import RequestParameters
from ingestion.market.bars import Adjustment, DailyObservation
from ingestion.sec.contracts import payload_digest
from synthetic_support import lesson


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*a,**k):raise AssertionError('No network in synthetic adapter tests')
    monkeypatch.setattr(socket.socket,'connect',forbidden)
    monkeypatch.setattr(socket,'getaddrinfo',forbidden)


def update(model,**changes):return type(model).model_validate(model.model_dump()|changes)


def sources(adapter):return list(adapter._pages.values())


def rebuilt(adapter,pages=None,**changes):
    kwargs=dict(snapshot=adapter.snapshot,series=adapter.series,capability=adapter.capability,
        pages=sources(adapter) if pages is None else pages,index_mappings=adapter._mappings)
    kwargs.update(changes)
    return FixtureAdapter(**kwargs)


def changed_payload(page,data,*,match_hash=True):
    raw=json.dumps(data,sort_keys=True,separators=(',',':')).encode()
    receipt=page.receipt
    if match_hash:receipt=update(receipt,evidence=update(receipt.evidence,payload_sha256=payload_digest(raw)),payload_ref='sha256:'+payload_digest(raw))
    return update(page,payload=raw,receipt=receipt)


def clock():return datetime(2026,10,21,2,tzinfo=timezone.utc)


def execute(adapter,allow_partial=True):
    m=adapter.manifest('synthetic:test',limits=lesson.JOB_LIMITS,allow_partial=allow_partial)
    store=FakeStore(m,clock)
    return store,run(store,adapter,store.claim('synthetic:worker'))


def test_full_selected_universe_all_pages_materialize_existing_contracts():
    adapter=lesson.adapter(count=100)
    assert len(adapter.series)==102
    assert {s.key for s in adapter.series if s.instrument.kind=='common_stock'}=={m.instrument_id for m in adapter.snapshot.members}
    accepted=0
    for page in sources(adapter):
        result=adapter.fetch(page.receipt.parameters,page.retry_index)
        result.page.receipt.verify_payload(result.payload)
        for row in result.observations:
            assert DailyObservation.model_validate_json(row.model_dump_json())==row
            accepted+=1
    assert accepted==202


def test_job_completed_with_explicit_optional_gaps_and_safe_counts():
    store,result=execute(lesson.adapter(count=2))
    assert result['state']=='completed_with_gaps'
    assert result['counters']['accepted_unique_record_count']==6
    assert result['counters']['unsupported_count']==1
    assert 'TEST_SECRET' not in json.dumps(result)
    rebuilt_store=FakeStore.restore(store.export(),store.snapshot.manifest,clock,payload_digest(store.export()))
    assert rebuilt_store.public_result()==result


def test_raw_and_split_adjusted_are_distinct_with_explicit_factor_vintage():
    raw=lesson.adapter(count=1,include_indexes=False,adjustment='raw')
    adjusted=lesson.adapter(count=1,include_indexes=False,adjustment='split_adjusted')
    a=raw.fetch(raw.series[0].request,0).observations[0]
    b=adjusted.fetch(adjusted.series[0].request,0).observations[0]
    assert a.series_key!=b.series_key and a.adjustment.factor_sha256 is None
    assert b.adjustment.factor_sha256=='a'*64 and b.adjustment.vintage_at
    assert raw.source_sha256!=adjusted.source_sha256
    assert execute(adjusted)[1]['state']=='completed'


def test_index_units_close_only_and_no_execution_capability():
    adapter=lesson.adapter(count=1)
    ref=next(s for s in adapter.series if s.reference and s.reference.slot=='sp500')
    row=adapter.fetch(ref.request,0).observations[0]
    assert row.instrument.kind=='index' and not row.instrument.tradable
    assert row.shape=='close_only' and row.measure_unit=='index_points'
    assert row.open is None and row.volume is None and row.notional is None
    page=next(p for p in sources(adapter) if p.receipt.parameters==ref.request)
    data=json.loads(page.payload);data['records'][0]['volume']='0'
    modified=rebuilt(adapter,pages=[changed_payload(p,data) if p==page else p for p in sources(adapter)])
    assert modified.fetch(ref.request,0).page.outcome=='quarantined'


@pytest.mark.parametrize('mode',['rights','capability','adjustment','index_evidence'])
def test_unknown_or_denied_capabilities_are_explicit(mode):
    adapter=lesson.adapter(count=1)
    if mode=='rights': adapter=rebuilt(adapter,capability=update(adapter.capability,rights=update(adapter.capability.rights,status='denied')))
    if mode=='capability': adapter=rebuilt(adapter,capability=update(adapter.capability,status='unknown'))
    if mode=='adjustment': adapter=rebuilt(adapter,capability=update(adapter.capability,adjustments={'split_adjusted'}))
    spec=adapter.series[0]
    if mode=='index_evidence':
        spec=next(s for s in adapter.series if s.reference and s.reference.slot=='sp500')
        ref=update(spec.reference,evidence=update(spec.reference.evidence,available_at='2026-10-21T00:00:00Z'))
        # Request cutoff precedes this evidence, while receipts still follow sessions.
        ref=update(ref,evidence=update(ref.evidence,available_at=None))
        series=tuple(update(s,reference=ref) if s==spec else s for s in adapter.series)
        adapter=rebuilt(adapter,series=series)
    assert adapter.fetch(spec.request,0).page.outcome=='unsupported'


@pytest.mark.parametrize('mode',['ohlc','missing','float','unknown_field','unknown_session','mapping','hash','deep','duplicate_key','nan'])
def test_invalid_source_is_quarantined_without_fabrication(mode):
    adapter=lesson.adapter(count=1,include_indexes=False);page=sources(adapter)[0];data=json.loads(page.payload)
    if mode=='ohlc':data['records'][0]['low']='200'
    if mode=='missing':del data['records'][0]['close']
    if mode=='float':data['records'][0]['close']=100.0
    if mode=='unknown_field':data['records'][0]['TEST_PRIVATE']='TEST_SECRET'
    if mode=='unknown_session':data['records'][0]['session_date']='2026-10-18'
    if mode=='hash':data['records'][0]['close']='103'
    if mode=='mapping':page=update(page,receipt=update(page.receipt,parameters=update(page.receipt.parameters,mapping_id='fake:missing')))
    if mode in ('deep','duplicate_key','nan'):
        raw={'deep':b'['*17+b']'*17,'duplicate_key':b'{"data_kind":"synthetic","data_kind":"synthetic","records":[]}',
             'nan':b'{"data_kind":"synthetic","records":[NaN]}'}[mode]
        page=update(page,payload=raw,receipt=update(page.receipt,evidence=update(page.receipt.evidence,payload_sha256=payload_digest(raw)),payload_ref='sha256:'+payload_digest(raw)))
    elif mode!='mapping':page=changed_payload(page,data,match_hash=mode!='hash')
    if mode=='mapping':
        # A changed request is not part of the pinned source/series contract.
        with pytest.raises(AdapterError,match='request_context_mismatch'):rebuilt(adapter,pages=[page]+sources(adapter)[1:])
        return
    changed=rebuilt(adapter,pages=[page]+sources(adapter)[1:]);reply=changed.fetch(page.receipt.parameters,0)
    assert reply.page.outcome=='quarantined' and not reply.observations
    assert 'TEST_SECRET' not in str(reply.page.failure_code)


def test_source_request_and_order_bindings():
    adapter=lesson.adapter(count=2); pages=sources(adapter)
    assert rebuilt(adapter,pages=list(reversed(pages))).source_sha256==adapter.source_sha256
    raw=json.loads(pages[0].payload);raw['records'][0]['close']='103'
    assert rebuilt(adapter,pages=[changed_payload(pages[0],raw)]+pages[1:]).source_sha256!=adapter.source_sha256
    for changed in (update(adapter.series[0].request,symbol='OTHER'),update(adapter.series[0].request,adjustment='total_return')):
        with pytest.raises(AdapterError,match='request_not_pinned'):adapter.fetch(changed,0)
    with pytest.raises(AdapterError,match='invalid_retry_index'):adapter.fetch(adapter.series[0].request,True)


def test_gap_is_not_zero_or_present_and_required_policy_fails():
    adapter=lesson.adapter(count=1,include_indexes=False);pages=sources(adapter)
    pages[0]=changed_payload(pages[0],dict(data_kind='synthetic',records=[]))
    changed=rebuilt(adapter,pages=pages)
    assert execute(changed,allow_partial=False)[1]['state']=='failed'
    _,result=execute(changed)
    assert result['state']=='completed_with_gaps' and result['counters']['missing_session_count']==1


def test_retry_and_quarantine_keep_existing_orchestration_semantics():
    adapter=lesson.adapter(count=1,include_indexes=False);pages=sources(adapter);first=pages[0]
    retry=changed_payload(first,dict(data_kind='synthetic',records=[]))
    retry=update(retry,outcome='retryable',failure_code='temporarily_unavailable',next_cursor_ref=None)
    pages=[retry,update(first,retry_index=1)]+pages[1:]
    adapter=rebuilt(adapter,pages=pages)
    now=[clock()];m=adapter.manifest('synthetic:retry',limits=lesson.JOB_LIMITS)
    store=FakeStore(m,lambda:now[0]);assert run(store,adapter,store.claim('synthetic:one'))['state']=='retryable'
    now[0]+=timedelta(seconds=2)
    assert run(store,adapter,store.claim('synthetic:two'))['state']=='completed'
    assert store.public_result()['counters']['retryable_count']==1


def test_replay_and_changed_content_under_same_key_use_existing_store():
    adapter=lesson.adapter(count=1,include_indexes=False);m=adapter.manifest('synthetic:replay',limits=lesson.JOB_LIMITS)
    store=FakeStore(m,clock);token=store.claim('synthetic:worker');spec=adapter.series[0];page=adapter.fetch(spec.request,0)
    version=store.snapshot.version
    with pytest.raises(SimulatedCrash):store.commit(spec.key,page,token=token,expected_version=version,request=spec.request,retry_index=0,crash='after_commit')
    before=store.snapshot
    store.commit(spec.key,page,token=token,expected_version=version,request=spec.request,retry_index=0)
    assert store.snapshot==before
    from dataclasses import replace
    with pytest.raises(Rejected,match='replay_conflict'):
        store.commit(spec.key,replace(page,payload=b'changed'),token=token,expected_version=version,request=spec.request,retry_index=0)


@pytest.mark.parametrize('limits,code',[(dict(max_series=1),'limit_series'),(dict(max_days=1),'limit_days'),
    (dict(max_payload_bytes=1),'limit_payload_bytes'),(dict(max_bytes=1),'limit_bytes'),(dict(max_records=1),'limit_records')])
def test_adapter_bounds(limits,code):
    with pytest.raises(AdapterError,match=code):rebuilt(lesson.adapter(count=2),limits=limits)


def test_page_record_bound_and_duplicate_request_rejected():
    adapter=lesson.adapter(count=1,include_indexes=False);pages=sources(adapter)
    data=json.loads(pages[0].payload);data['records']*=33
    with pytest.raises(AdapterError,match='limit_records_page'):rebuilt(adapter,pages=[changed_payload(pages[0],data)]+pages[1:])
    with pytest.raises(AdapterError,match='duplicate_request_response'):rebuilt(adapter,pages=pages+[pages[0]])


@pytest.mark.parametrize('field',list(AdapterLimits.model_fields))
@pytest.mark.parametrize('value',[0,True,'1'])
def test_adapter_limits_are_strict_and_cannot_increase(field,value):
    with pytest.raises(AdapterError):rebuilt(lesson.adapter(count=1),limits={field:value})
    with pytest.raises(AdapterError):rebuilt(lesson.adapter(count=1),limits={field:getattr(AdapterLimits(),field)+1})


def test_supplied_factor_metadata_is_mandatory_and_vintage_cannot_be_future():
    with pytest.raises(ValueError):Adjustment(basis='split_adjusted')
    adapter=lesson.adapter(count=1,include_indexes=False,adjustment='split_adjusted');pages=sources(adapter)
    pages[0]=update(pages[0],adjustment=update(pages[0].adjustment,vintage_at='2027-01-01T00:00:00Z'))
    changed=rebuilt(adapter,pages=pages)
    assert changed.fetch(changed.series[0].request,0).page.outcome=='quarantined'


def test_page_count_bound_at_four_and_overflow_and_cursor_cycle():
    adapter=lesson.adapter(count=1,include_indexes=False,days=1);template=sources(adapter)[0]
    pages=[]
    for index in range(5):
        request=update(template.receipt.parameters,cursor_ref=None if index==0 else f'cursor:{index}')
        pages.append(update(template,receipt=update(template.receipt,parameters=request)))
    assert rebuilt(adapter,pages=pages[:4])
    with pytest.raises(AdapterError,match='limit_pages'):rebuilt(adapter,pages=pages)
    first=update(sources(adapter)[0],outcome='partial',next_cursor_ref='cursor:one')
    second=update(first,receipt=update(first.receipt,parameters=update(first.receipt.parameters,cursor_ref='cursor:one')),
        next_cursor_ref='cursor:two')
    third=update(second,receipt=update(second.receipt,parameters=update(second.receipt.parameters,cursor_ref='cursor:two')),
        next_cursor_ref='cursor:one')
    changed=rebuilt(adapter,pages=[first,second,third]);store,result=execute(changed)
    assert result['state']=='failed' and result['code']=='cursor_cycle'
    assert len(store.snapshot.batches)==2


def test_duplicate_and_revision_source_records_preserve_existing_counter_units():
    adapter=lesson.adapter(count=1,include_indexes=False);pages=sources(adapter)
    original=json.loads(pages[0].payload)
    duplicate=changed_payload(pages[1],original)
    changed=rebuilt(adapter,pages=[pages[0],duplicate]);_,result=execute(changed)
    assert result['counters']['accepted_unique_record_count']==1
    assert result['counters']['duplicate_count']==1
    first=changed.fetch(changed.series[0].request,0).observations[0]
    revised=json.loads(duplicate.payload);revised['records'][0].update(close='103',supersedes=first.revision_id)
    revision=changed_payload(duplicate,revised)
    _,result=execute(rebuilt(adapter,pages=[pages[0],revision]))
    assert result['counters']['accepted_unique_record_count']==1
    assert result['counters']['accepted_revision_count']==2


def test_context_mismatches_and_nonselected_instrument_are_rejected():
    adapter=lesson.adapter(count=1,include_indexes=False)
    spec=adapter.series[0];excluded=next(i for i in adapter.snapshot.catalog.instruments if i.instrument_id not in {m.instrument_id for m in adapter.snapshot.members})
    changed_request=update(spec.request,instrument_id=excluded.instrument_id)
    changed_coverage=update(spec.coverage,instrument_id=excluded.instrument_id)
    with pytest.raises(AdapterError,match='series_not_in_universe'):
        rebuilt(adapter,series=[update(spec,instrument=excluded,request=changed_request,coverage=changed_coverage)])
    pages=sources(adapter)
    with pytest.raises(AdapterError,match='provider_context_mismatch'):
        rebuilt(adapter,pages=[update(pages[0],receipt=update(pages[0].receipt,feed='other'))]+pages[1:])
    with pytest.raises(AdapterError,match='adjustment_context_mismatch'):
        rebuilt(adapter,pages=[update(pages[0],adjustment=Adjustment(basis='split_adjusted',factor_ref='synthetic:f',factor_sha256='a'*64,vintage_at='2026-10-20T21:00:00Z'))]+pages[1:])
