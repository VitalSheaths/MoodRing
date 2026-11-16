"""Bounded local pilot with durable attempt reservations; no default approval.

The CLI requires an exact operator-approved run ID. Synthetic tests inject a fake
backend; preparing or validating a proposal never imports or calls a model.
"""
from collections import Counter
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import platform
import sqlite3
import uuid

from .archive import digest,json_bytes,read_bytes
from .point_in_time import utc
from .scoring_contract import CONTRACT,cache_namespace,validate_checkpoint


class RetryableScoringError(Exception):
    """A backend failure that may consume one explicitly counted retry."""


class AttemptBudgetExceeded(RuntimeError):
    """The run cannot schedule another input within its frozen attempt ceiling."""


def backend_runtime():
    """Pin tokenizer implementation and runtime in addition to retained files."""
    from importlib.metadata import version
    return dict(python=platform.python_version(),machine=platform.machine(),
                tokenizers=version('tokenizers'),safetensors=version('safetensors'))


def relative_file(root,name):
    """Keep bound files inside the declared local artifact root; reject symlinks."""
    rel=Path(name)
    if rel.is_absolute() or '..' in rel.parts or not rel.parts:raise ValueError('relative_binding_required')
    path=root/rel
    if any(p.is_symlink() for p in (path,*path.parents)):raise ValueError('symlink_path')
    return path


def run_limits(identity):
    """Allow explicitly bound smaller pilots without raising the original caps."""
    if 'pilot_limits' not in identity:return dict(unique_inputs=5000,max_reserved_attempts=5500)
    limits=identity['pilot_limits']
    if not isinstance(limits,dict) or set(limits)!={'unique_inputs','max_reserved_attempts'}:
        raise ValueError('pilot_limits_schema')
    n=limits['unique_inputs'];attempts=limits['max_reserved_attempts']
    if type(n) is not int or type(attempts) is not int or not 1<=n<=5000 or not n<=attempts<=min(5500,2*n):
        raise ValueError('pilot_limits_range')
    return limits


def validate_plan(plan,inputs,root):
    """Check exact scope, source receipts, code and text before any model load."""
    identity=plan['identity'];root=Path(root).absolute()
    scope=identity.get('scoring_scope','isolated_pilot_only')
    if scope not in ('isolated_pilot_only','train_dev_scoring_shard'):raise ValueError('scoring_scope')
    start='2020-01-01' if scope=='train_dev_scoring_shard' else '2023-01-01'
    if digest(json_bytes(identity))!=plan['run_id']:raise ValueError('run_identity_changed')
    if identity['contract_sha256']!=digest(json_bytes(CONTRACT)):raise ValueError('contract_changed')
    if identity['runner_sha256']!=digest(read_bytes(Path(__file__))):raise ValueError('runner_changed')
    if identity['inputs_sha256']!=digest(json_bytes(inputs)):raise ValueError('inputs_changed')
    if identity['bindings_sha256']!=digest(json_bytes(plan['bindings'])):raise ValueError('bindings_changed')
    if identity['cache_namespace']!=cache_namespace(plan['model_contract']):raise ValueError('model_contract_changed')
    if not 0<len(inputs)<=5000:raise ValueError('unique_input_budget')
    limits=run_limits(identity)
    if 'pilot_limits' in identity and len(inputs)!=limits['unique_inputs']:raise ValueError('bound_input_count')
    ids=[r['exact_model_input_sha256'] for r in inputs]
    if ids!=sorted(set(ids)) or identity['input_ids_sha256']!=digest(json_bytes(ids)):raise ValueError('input_order_or_duplicates')
    for row in inputs:
        if digest(row['title'].encode())!=row['exact_model_input_sha256']:raise ValueError('input_title_changed')
        if type(row['token_count']) is not int or not 2<=row['token_count']<=512:raise ValueError('input_token_eligibility')
        if not start<=row['representative_session']<'2025-01-01':raise ValueError('scoring_partition_scope')
        if not start<=utc(row['availability_at']).date().isoformat()<'2025-01-01':raise ValueError('scoring_observation_scope')
        if scope=='train_dev_scoring_shard' and (row['representative_session']<'2023-01-01')!=(utc(row['availability_at']).date().isoformat()<'2023-01-01'):
            raise ValueError('cross_partition_observation')
        if row['cache_key']!=digest(json_bytes([identity['cache_namespace'],row['exact_model_input_sha256']])):raise ValueError('cache_key_changed')
    for name,sha in plan['bindings'].items():
        if digest(read_bytes(relative_file(root,name)))!=sha:raise ValueError('bound_file_changed:'+name)
    output=relative_file(root,identity['output_relative_path'])
    return ids,output


def validate_approval(approval,plan):
    """Require a separate operator decision; proposal preparation cannot release it."""
    expected=plan['identity'].get('scoring_scope','isolated_pilot_only')
    if approval.get('granted') is not True or approval.get('scope')!=expected or approval.get('run_id')!=plan['run_id']:
        raise PermissionError('exact_pilot_approval_required')
    if not isinstance(approval.get('approved_by'),str) or not approval['approved_by'].strip():raise PermissionError('operator_required')
    utc(approval.get('approved_at'))


def atomic_json(path,value):
    """Publish once with durable bytes and directory entry; retain crash remnants."""
    if path.exists():raise ValueError('immutable_output_exists')
    temporary=path.parent/('.pending-'+uuid.uuid4().hex+'.tmp')
    with temporary.open('xb') as f:
        f.write(json_bytes(value));f.flush();os.fsync(f.fileno())
    os.rename(temporary,path)
    fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)


@contextmanager
def locked_journal(output,identity):
    """One process per approved output path; FULL SQLite commits precede forwards."""
    output.mkdir(parents=True,exist_ok=True,mode=0o700)
    if any(p.is_symlink() for p in (output,*output.parents)):raise ValueError('symlink_output')
    fd=os.open(output/'.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    connection=None
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        db=output/'attempts.sqlite'
        marker=output/'run-binding.json'
        if marker.exists():
            if not db.exists() or json.loads(read_bytes(marker))!=identity:raise ValueError('missing_or_changed_bound_journal')
        elif list(output.glob('checkpoint-*.json')) or (output/'completion.json').exists():
            raise ValueError('missing_run_binding')
        for suffix in ('','-journal','-wal','-shm'):
            if Path(str(db)+suffix).is_symlink():raise ValueError('symlink_journal')
        connection=sqlite3.connect(db)
        connection.execute('PRAGMA journal_mode=DELETE');connection.execute('PRAGMA synchronous=FULL')
        if connection.execute('PRAGMA integrity_check').fetchone()!=('ok',):raise ValueError('corrupt_journal')
        with connection:
            connection.execute('CREATE TABLE IF NOT EXISTS metadata (singleton INTEGER PRIMARY KEY CHECK(singleton=1), body BLOB NOT NULL)')
            connection.execute('CREATE TABLE IF NOT EXISTS attempts (seq INTEGER PRIMARY KEY, reservation BLOB NOT NULL, reservation_hash TEXT NOT NULL, result BLOB, result_hash TEXT)')
            existing=connection.execute('SELECT body FROM metadata').fetchone()
            if existing is None:
                if connection.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]:raise ValueError('unbound_attempt_journal')
                connection.execute('INSERT INTO metadata VALUES (1,?)',(json_bytes(identity),))
            elif existing[0]!=json_bytes(identity):raise ValueError('journal_identity_changed')
        if not marker.exists():
            if connection.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]:raise ValueError('attempts_without_run_binding')
            atomic_json(marker,identity)
        yield connection
    finally:
        if connection is not None:connection.close()
        os.close(fd)


def scored_rows(ids,probabilities,attempts):
    """Validate all batch outputs before committing any as usable scores."""
    if not isinstance(probabilities,list) or len(probabilities)!=len(ids):raise ValueError('backend_output_count')
    rows=[]
    for h,p in zip(ids,probabilities):
        if not isinstance(p,dict):raise ValueError('unexpected_missing_backend_output')
        rows.append(dict(exact_model_input_sha256=h,status='scored',attempts=attempts[h],probabilities=p,
            headline_sentiment=p.get('positive',float('nan'))-p.get('negative',float('nan'))))
    manifest=dict(run_id='validation',contract_sha256=digest(json_bytes(CONTRACT)),input_ids_sha256=digest(json_bytes(ids)),
        rows_sha256=digest(json_bytes(rows)),cumulative_forward_attempts=sum(attempts.values()))
    validate_checkpoint(manifest,rows,ids,'validation')
    return rows


def journal_state(db,ids,run_id,max_reserved_attempts=5500):
    """Reconstruct counters and completed outputs; missing results stay charged."""
    allowed=set(ids);counts=Counter();completed={};parent='0'*64;seq=0
    for number,reservation,sha,result,result_sha in db.execute('SELECT * FROM attempts ORDER BY seq'):
        if number!=seq or digest(reservation)!=sha:raise ValueError('corrupt_reservation')
        event=json.loads(reservation);batch=event['ids']
        if not 1<=len(batch)<=16 or len(set(batch))!=len(batch) or not set(batch)<=allowed:raise ValueError('reservation_input_scope')
        expected=dict(seq=seq,run_id=run_id,parent=parent,ids=batch,numbers=[counts[h]+1 for h in batch])
        if event!=expected or any(h in completed for h in batch):raise ValueError('reservation_sequence_changed')
        counts.update(batch)
        if max(counts.values())>2 or sum(counts.values())>max_reserved_attempts:raise ValueError('journal_attempt_budget')
        if (result is None)!=(result_sha is None):raise ValueError('partial_result_receipt')
        if result is not None:
            if digest(result)!=result_sha:raise ValueError('corrupt_result')
            value=json.loads(result)
            if value['kind']=='fatal':raise ValueError('fatal_backend_failure_requires_review')
            if value['kind']=='success':
                completed.update({r['exact_model_input_sha256']:r for r in scored_rows(batch,value['probabilities'],counts)})
            elif value['kind']!='retryable':raise ValueError('unknown_result_state')
        parent=sha;seq+=1
    for h,n in counts.items():
        if n==2 and h not in completed:
            completed[h]=dict(exact_model_input_sha256=h,status='failed',attempts=n,probabilities=None,
                headline_sentiment=None,failure_reason='two_attempts_without_verified_success')
    return counts,completed,parent,seq


def checkpoint_value(rows,run_id,attempts):
    """Keep checkpoint input order and exact row checksums explicit."""
    ids=[r['exact_model_input_sha256'] for r in rows]
    return dict(manifest=dict(run_id=run_id,contract_sha256=digest(json_bytes(CONTRACT)),
        input_ids_sha256=digest(json_bytes(ids)),rows_sha256=digest(json_bytes(rows)),
        cumulative_forward_attempts=attempts),rows=rows)


def inspect_checkpoints(output,ids,completed,run_id,attempts):
    """Reject corrupt, missing-prefix or journal-inconsistent published checkpoints."""
    paths=sorted(output.glob('checkpoint-*.json'));hashes={}
    for i,path in enumerate(paths):
        if path.name!=f'checkpoint-{i:04d}.json':raise ValueError('checkpoint_sequence_gap')
        expected=ids[i*250:(i+1)*250];value=json.loads(read_bytes(path))
        validate_checkpoint(value['manifest'],value['rows'],expected,run_id)
        if value['manifest']['cumulative_forward_attempts']>attempts:raise ValueError('checkpoint_attempts_exceed_journal')
        if any(h not in completed for h in expected) or value['rows']!=[completed[h] for h in expected]:raise ValueError('checkpoint_journal_mismatch')
        hashes[path.name]=digest(read_bytes(path))
    return hashes


def run(plan,inputs,root,scorer,approval,hook=None):
    """Run only the approved fixed pilot. Hook supports synthetic crash injection."""
    validate_approval(approval,plan)
    ids,output=validate_plan(plan,inputs,root)
    attempt_limit=run_limits(plan['identity'])['max_reserved_attempts']
    by_id={r['exact_model_input_sha256']:r for r in inputs}
    hook=hook or (lambda stage:None)
    with locked_journal(output,plan['identity']) as db:
        counts,completed,parent,seq=journal_state(db,ids,plan['run_id'],attempt_limit)
        hashes=inspect_checkpoints(output,ids,completed,plan['run_id'],sum(counts.values()))
        if (output/'completion.json').exists() and len(hashes)!=(len(ids)+249)//250:raise ValueError('premature_completion')
        for index in range(len(hashes),(len(ids)+249)//250):
            chunk=ids[index*250:(index+1)*250]
            while any(h not in completed for h in chunk):
                remaining=attempt_limit-sum(counts.values())
                if remaining<=0:raise AttemptBudgetExceeded(f'{attempt_limit}_forward_attempt_ceiling')
                batch=[h for h in chunk if h not in completed][:min(16,remaining)]
                reservation=dict(seq=seq,run_id=plan['run_id'],parent=parent,ids=batch,numbers=[counts[h]+1 for h in batch])
                blob=json_bytes(reservation);sha=digest(blob)
                with db:db.execute('INSERT INTO attempts(seq,reservation,reservation_hash) VALUES (?,?,?)',(seq,blob,sha))
                counts.update(batch);hook('after_reservation')
                try:
                    probabilities=scorer([by_id[h] for h in batch]);hook('after_forward')
                except RetryableScoringError:
                    value=dict(kind='retryable')
                except ValueError:
                    data=json_bytes(dict(kind='fatal',reason='backend_binding_or_input_changed'))
                    with db:db.execute('UPDATE attempts SET result=?,result_hash=? WHERE seq=? AND result IS NULL',(data,digest(data),seq))
                    raise
                else:
                    try:scored_rows(batch,probabilities,counts)
                    except (ValueError,TypeError,OverflowError):
                        value=dict(kind='fatal',reason='invalid_backend_output')
                        data=json_bytes(value)
                        with db:db.execute('UPDATE attempts SET result=?,result_hash=? WHERE seq=? AND result IS NULL',(data,digest(data),seq))
                        raise ValueError('invalid_backend_output')
                    value=dict(kind='success',probabilities=probabilities)
                data=json_bytes(value)
                with db:db.execute('UPDATE attempts SET result=?,result_hash=? WHERE seq=? AND result IS NULL',(data,digest(data),seq))
                hook('after_result')
                counts,completed,parent,seq=journal_state(db,ids,plan['run_id'],attempt_limit)
            payload=checkpoint_value([completed[h] for h in chunk],plan['run_id'],sum(counts.values()))
            path=output/f'checkpoint-{index:04d}.json'
            atomic_json(path,payload);hook('after_checkpoint');hashes[path.name]=digest(read_bytes(path))
        completion=dict(run_id=plan['run_id'],unique_inputs=len(ids),forward_attempts=sum(counts.values()),
            scored=sum(r['status']=='scored' for r in completed.values()),failed=sum(r['status']=='failed' for r in completed.values()),
            checkpoint_sha256=hashes,purpose=('train_dev_scores_pending_aggregation' if plan['identity'].get('scoring_scope')=='train_dev_scoring_shard' else CONTRACT['purpose']),panel_scores_published=False,
            full_scoring_held=True,forecasting_training_held=True)
        path=output/'completion.json'
        if path.exists():
            if json.loads(read_bytes(path))!=completion:raise ValueError('completion_mismatch')
        else:atomic_json(path,completion)
        return completion


class LocalBackend:
    """Lazy, offline pinned model. Constructor alone never loads or calls it."""
    def __init__(self,path,contract):self.path=path;self.contract=contract;self.model=None
    def __call__(self,rows):
        if self.model is None:
            os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',TOKENIZERS_PARALLELISM='false')
            from .news_sentiment import LocalFinBERT,model_provenance
            evidence=model_provenance(self.path)
            if evidence['local_file_sha256']!=self.contract['files'] or evidence['packages']!=self.contract['packages']:
                raise ValueError('pinned_backend_changed')
            if self.contract.get('runtime')!=backend_runtime():raise ValueError('pinned_runtime_changed')
            self.model=LocalFinBERT(self.path)
        if any(len(self.model.tokenizer.encode(r['title'],truncation=False))!=r['token_count'] for r in rows):raise ValueError('token_count_changed')
        return self.model.score([r['title'] for r in rows])


if __name__=='__main__':
    import argparse
    from .sentiment_policy import verified_table
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('proposal','inputs','root','model-path','approval'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();plan=json.loads(read_bytes(a.proposal));approval=json.loads(read_bytes(a.approval))
    validate_approval(approval,plan)
    _,table=verified_table(a.inputs,'inputs');inputs=table.to_pylist()
    if len(inputs)!=run_limits(plan['identity'])['unique_inputs']:raise ValueError('production_pilot_requires_exact_bound_count')
    print(json.dumps(run(plan,inputs,a.root,LocalBackend(a.model_path,plan['model_contract']),approval),indent=2))
