"""Synthetic backends only; exercise real durable journal/checkpoint files."""
from collections import Counter
from copy import deepcopy
import fcntl
import json
from pathlib import Path
import sqlite3
import pytest
from research_data.archive import digest,json_bytes,read_bytes
from research_data.scoring_contract import CONTRACT,cache_namespace
from research_data.pilot_runner import run,validate_plan,RetryableScoringError,AttemptBudgetExceeded
import research_data.pilot_runner as runner


class Interrupted(BaseException):pass


def fixture(root,n=501):
    source=root/'source.txt';source.write_text('synthetic immutable source')
    model=dict(revision='synthetic',files={'synthetic':'f'*64});namespace=cache_namespace(model)
    rows=[]
    for i in range(n):
        text=f'Synthetic headline {i}';h=digest(text.encode())
        rows.append(dict(title=text,exact_model_input_sha256=h,token_count=8,representative_session='2024-01-03',
            availability_at='2024-01-03T10:00:00Z',cache_key=digest(json_bytes([namespace,h]))))
    rows.sort(key=lambda r:r['exact_model_input_sha256'])
    bindings={'source.txt':digest(read_bytes(source))}
    identity=dict(contract_sha256=digest(json_bytes(CONTRACT)),runner_sha256=digest(read_bytes(Path(runner.__file__))),
        inputs_sha256=digest(json_bytes(rows)),input_ids_sha256=digest(json_bytes([r['exact_model_input_sha256'] for r in rows])),
        bindings_sha256=digest(json_bytes(bindings)),cache_namespace=namespace,output_relative_path='run')
    plan=dict(identity=identity,run_id=digest(json_bytes(identity)),bindings=bindings,model_contract=model)
    approval=dict(granted=True,scope='isolated_pilot_only',run_id=plan['run_id'],approved_by='synthetic fixture',approved_at='2026-11-11T00:00:00Z')
    return plan,rows,approval


class Fake:
    def __init__(self):self.calls=Counter()
    def __call__(self,rows):
        self.calls.update(r['exact_model_input_sha256'] for r in rows)
        return [dict(positive=.2,negative=.2,neutral=.6) for _ in rows]


def bounded_fixture(root,n=346,attempts=400):
    plan,rows,approval=fixture(root,n)
    plan['identity']['pilot_limits']=dict(unique_inputs=n,max_reserved_attempts=attempts)
    plan['run_id']=digest(json_bytes(plan['identity']));approval['run_id']=plan['run_id']
    return plan,rows,approval


def test_smaller_pilot_checkpoint_sizes_and_noop_resume(tmp_path):
    plan,rows,approval=bounded_fixture(tmp_path);fake=Fake()
    result=run(plan,rows,tmp_path,fake,approval)
    assert result['scored']==346 and result['forward_attempts']==346
    assert [len(json.loads(p.read_text())['rows']) for p in sorted((tmp_path/'run').glob('checkpoint-*.json'))]==[250,96]
    assert run(plan,rows,tmp_path,fake,approval)==result and sum(fake.calls.values())==346


def test_smaller_attempt_limit_survives_interruption_and_resume(tmp_path):
    plan,rows,approval=bounded_fixture(tmp_path);calls=0
    def backend(batch):
        nonlocal calls
        calls+=len(batch);raise RetryableScoringError()
    def stop(stage):
        if stage=='after_reservation':raise Interrupted()
    with pytest.raises(Interrupted):run(plan,rows,tmp_path,backend,approval,stop)
    for _ in range(2):
        with pytest.raises(AttemptBudgetExceeded,match='400_'):run(plan,rows,tmp_path,backend,approval)
    assert calls==384 # 16 crash-uncertain reservations consume budget without forward.
    db=sqlite3.connect(tmp_path/'run/attempts.sqlite')
    counts,_,_,_=runner.journal_state(db,[r['exact_model_input_sha256'] for r in rows],plan['run_id'],400)
    db.close();assert sum(counts.values())==400 and max(counts.values())<=2


@pytest.mark.parametrize('limits',[None,{}, {'unique_inputs':True,'max_reserved_attempts':400},
    {'unique_inputs':346,'max_reserved_attempts':345},{'unique_inputs':346,'max_reserved_attempts':693},
    {'unique_inputs':5001,'max_reserved_attempts':5500}, {'unique_inputs':345,'max_reserved_attempts':400}])
def test_invalid_or_mismatched_smaller_limits_rejected(tmp_path,limits):
    plan,rows,approval=bounded_fixture(tmp_path);plan['identity']['pilot_limits']=limits
    plan['run_id']=digest(json_bytes(plan['identity']));approval['run_id']=plan['run_id'];fake=Fake()
    with pytest.raises(ValueError):run(plan,rows,tmp_path,fake,approval)
    assert not fake.calls and not (tmp_path/'run').exists()


def test_changed_smaller_budget_cannot_reuse_approval_or_journal(tmp_path):
    plan,rows,approval=bounded_fixture(tmp_path);fake=Fake();run(plan,rows,tmp_path,fake,approval)
    plan['identity']['pilot_limits']['max_reserved_attempts']=401
    with pytest.raises(ValueError,match='run_identity_changed'):run(plan,rows,tmp_path,fake,approval)
    plan['run_id']=digest(json_bytes(plan['identity']))
    with pytest.raises(PermissionError):run(plan,rows,tmp_path,fake,approval)
    approval['run_id']=plan['run_id']
    with pytest.raises(ValueError,match='bound_journal'):run(plan,rows,tmp_path,fake,approval)
    assert sum(fake.calls.values())==346


def test_complete_checkpoints_resume_without_rescoring_and_zero_is_observed(tmp_path):
    plan,rows,approval=fixture(tmp_path);fake=Fake()
    a=run(plan,rows,tmp_path,fake,approval)
    assert a['scored']==501 and a['forward_attempts']==501 and len(a['checkpoint_sha256'])==3
    assert a==run(plan,rows,tmp_path,fake,approval)
    assert sum(fake.calls.values())==501 and not a['panel_scores_published']
    values=[json.loads(p.read_text()) for p in sorted((tmp_path/'run').glob('checkpoint-*.json'))]
    assert [len(v['rows']) for v in values]==[250,250,1]
    assert values[0]['rows'][0]['headline_sentiment']==0 and values[0]['rows'][0]['status']=='scored'


@pytest.mark.parametrize('stage',['after_reservation','after_forward','after_result','after_checkpoint'])
def test_interruption_resume_charges_uncertain_attempts_and_reuses_durable_success(tmp_path,stage):
    plan,rows,approval=fixture(tmp_path);fake=Fake()
    def hook(actual):
        if actual==stage:raise Interrupted()
    with pytest.raises(Interrupted):run(plan,rows,tmp_path,fake,approval,hook)
    result=run(plan,rows,tmp_path,fake,approval)
    assert result['scored']==501
    assert result['forward_attempts']==(517 if stage in ('after_reservation','after_forward') else 501)
    assert sum(fake.calls.values())==(517 if stage=='after_forward' else 501)


def test_two_uncertain_attempts_become_missing_not_neutral(tmp_path):
    plan,rows,approval=fixture(tmp_path,16);fake=Fake()
    def stop(_):raise Interrupted()
    for _ in range(2):
        with pytest.raises(Interrupted):run(plan,rows,tmp_path,fake,approval,stop)
    result=run(plan,rows,tmp_path,fake,approval)
    assert result['failed']==16 and result['scored']==0 and result['forward_attempts']==32
    assert not fake.calls
    checkpoint=json.loads((tmp_path/'run/checkpoint-0000.json').read_text())
    assert all(r['headline_sentiment'] is None and r['probabilities'] is None for r in checkpoint['rows'])


def test_retryable_error_consumes_exactly_one_retry(tmp_path):
    plan,rows,approval=fixture(tmp_path,32);fake=Fake();first=True
    def backend(batch):
        nonlocal first
        if first:first=False;raise RetryableScoringError()
        return fake(batch)
    result=run(plan,rows,tmp_path,backend,approval)
    assert result['forward_attempts']==48 and result['scored']==32


def test_5500_attempt_ceiling_is_persistent_across_resume(tmp_path):
    plan,rows,approval=fixture(tmp_path,5000);calls=0
    def backend(batch):
        nonlocal calls
        calls+=len(batch);raise RetryableScoringError()
    with pytest.raises(AttemptBudgetExceeded):run(plan,rows,tmp_path,backend,approval)
    assert calls==5500
    with pytest.raises(AttemptBudgetExceeded):run(plan,rows,tmp_path,backend,approval)
    assert calls==5500
    db=sqlite3.connect(tmp_path/'run/attempts.sqlite')
    ids=[h for blob, in db.execute('SELECT reservation FROM attempts') for h in json.loads(blob)['ids']]
    db.close();assert len(ids)==5500 and max(Counter(ids).values())==2


@pytest.mark.parametrize('change',['input','binding','run_id','code','model','approval','path'])
def test_changed_inputs_or_scope_fail_before_backend(tmp_path,change):
    plan,rows,approval=fixture(tmp_path,20);fake=Fake()
    if change=='input':rows[0]['title']='changed'
    if change=='binding':(tmp_path/'source.txt').write_text('changed')
    if change=='run_id':plan['run_id']='0'*64
    if change=='code':plan['identity']['runner_sha256']='0'*64
    if change=='model':plan['model_contract']['revision']='other'
    if change=='approval':approval['granted']=False
    if change=='path':plan['identity']['output_relative_path']='../outside'
    with pytest.raises((ValueError,PermissionError)):run(plan,rows,tmp_path,fake,approval)
    assert not fake.calls and not (tmp_path/'run').exists()


@pytest.mark.parametrize('corruption',['checkpoint','journal_result','journal_reservation','completion','checkpoint_hole'])
def test_corrupt_durable_state_never_triggers_rescoring(tmp_path,corruption):
    plan,rows,approval=fixture(tmp_path);fake=Fake();run(plan,rows,tmp_path,fake,approval)
    if corruption=='checkpoint':(tmp_path/'run/checkpoint-0000.json').write_text('{}')
    elif corruption=='completion':(tmp_path/'run/completion.json').write_text('{}')
    elif corruption=='checkpoint_hole':(tmp_path/'run/checkpoint-0000.json').rename(tmp_path/'run/retained-corrupt.json')
    else:
        db=sqlite3.connect(tmp_path/'run/attempts.sqlite')
        field='result' if corruption=='journal_result' else 'reservation'
        db.execute('UPDATE attempts SET '+field+'=? WHERE seq=0',(b'{}',));db.commit();db.close()
    with pytest.raises((ValueError,KeyError)):run(plan,rows,tmp_path,fake,approval)
    assert sum(fake.calls.values())==501


def test_changed_source_on_resume_does_not_use_old_cache(tmp_path):
    plan,rows,approval=fixture(tmp_path,16);fake=Fake();run(plan,rows,tmp_path,fake,approval)
    (tmp_path/'source.txt').write_text('correction')
    with pytest.raises(ValueError,match='bound_file_changed'):run(plan,rows,tmp_path,fake,approval)
    assert sum(fake.calls.values())==16


def test_invalid_probabilities_persist_a_fatal_stop(tmp_path):
    plan,rows,approval=fixture(tmp_path,16);fake=Fake()
    with pytest.raises(ValueError,match='invalid_backend_output'):
        run(plan,rows,tmp_path,lambda batch:[dict(positive=2.,negative=0.,neutral=0.) for _ in batch],approval)
    with pytest.raises(ValueError,match='fatal_backend'):run(plan,rows,tmp_path,fake,approval)
    assert not fake.calls


def test_binding_failure_in_backend_cannot_be_retried_into_success(tmp_path):
    plan,rows,approval=fixture(tmp_path,16);fake=Fake()
    def invalid(batch):raise ValueError('changed tokenizer')
    with pytest.raises(ValueError):run(plan,rows,tmp_path,invalid,approval)
    with pytest.raises(ValueError,match='fatal_backend'):run(plan,rows,tmp_path,fake,approval)
    assert not fake.calls


def test_process_lock_and_symlink_binding_fail_closed(tmp_path):
    plan,rows,approval=fixture(tmp_path,16);fake=Fake();out=tmp_path/'run';out.mkdir()
    with (out/'.lock').open('w') as f:
        fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):run(plan,rows,tmp_path,fake,approval)
    (tmp_path/'source.txt').rename(tmp_path/'retained.txt');(tmp_path/'source.txt').symlink_to(tmp_path/'retained.txt')
    with pytest.raises(ValueError,match='symlink'):run(plan,rows,tmp_path,fake,approval)
    assert not fake.calls


def test_unpublished_temporary_file_is_not_a_checkpoint_or_cache(tmp_path):
    plan,rows,approval=fixture(tmp_path,16);out=tmp_path/'run';out.mkdir();(out/'.pending-crash.tmp').write_text('incomplete')
    result=run(plan,rows,tmp_path,Fake(),approval)
    assert result['scored']==16 and (out/'.pending-crash.tmp').read_text()=='incomplete'


@pytest.mark.parametrize('missing',['attempts.sqlite','run-binding.json'])
def test_missing_journal_or_binding_cannot_reset_the_attempt_budget(tmp_path,missing):
    plan,rows,approval=fixture(tmp_path,20);fake=Fake()
    def stop(stage):
        if stage=='after_forward':raise Interrupted()
    with pytest.raises(Interrupted):run(plan,rows,tmp_path,fake,approval,stop)
    (tmp_path/'run'/missing).rename(tmp_path/('retained-'+missing))
    with pytest.raises(ValueError):run(plan,rows,tmp_path,fake,approval)
    assert sum(fake.calls.values())==16


def test_crash_before_checkpoint_publication_reuses_journal_results(tmp_path,monkeypatch):
    plan,rows,approval=fixture(tmp_path);fake=Fake();rename=runner.os.rename
    def interrupted(source,target):
        if Path(target).name=='checkpoint-0000.json':raise Interrupted()
        return rename(source,target)
    with monkeypatch.context() as m:
        m.setattr(runner.os,'rename',interrupted)
        with pytest.raises(Interrupted):run(plan,rows,tmp_path,fake,approval)
    assert sum(fake.calls.values())==250 and list((tmp_path/'run').glob('.pending-*.tmp'))
    result=run(plan,rows,tmp_path,fake,approval)
    assert result['forward_attempts']==501 and sum(fake.calls.values())==501
