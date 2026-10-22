"""Explicit disposable PostgreSQL experiment; no application-DB or retention mode."""
import argparse
from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from time import monotonic
from uuid import uuid4

from pydantic import BaseModel,ConfigDict,Field
from sqlalchemy import create_engine,text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from ingestion.jobs.measurement import MeasurementError,collect,phase_delta,retention_dry_run,encode_report,verify_target,growth_scenario
from ingestion.jobs.migrations import migrate
from ingestion.jobs.postgres import PostgresStore
from ingestion.jobs.offline import run
from ingestion.market.synthetic import FixtureAdapter
from ingestion.sec.contracts import payload_digest
from ingestion.universe.identity import content_hash
import synthetic_market_demo as lesson


_OWNED_TARGETS={}  # Only successful creations in this process may run a workload.


class WorkloadLimits(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True,strict=True)
    jobs:int=Field(default=12,ge=1,le=12)
    source_records:int=Field(default=128,ge=1,le=128)
    steps:int=Field(default=100,ge=1,le=100)
    heartbeats:int=Field(default=8,ge=1,le=8)
    seconds:int=Field(default=120,ge=1,le=120)
    database_bytes:int=Field(default=134217728,ge=1,le=134217728)


@contextmanager
def disposable_target(server_url,*,acknowledged=False):
    if acknowledged is not True or not server_url:raise MeasurementError('explicit_disposable_ack_required')
    admin=engine=None;created=False
    name='moodring_measure_'+uuid4().hex
    try:
        url=make_url(server_url)
        if url.drivername not in ('postgresql','postgresql+psycopg2') or url.database!='postgres':
            raise MeasurementError('disposable_server_url_required')
        options=dict(connect_timeout=5,options='-c statement_timeout=5000 -c lock_timeout=5000')
        admin=create_engine(url,isolation_level='AUTOCOMMIT',connect_args=options,hide_parameters=True)
        with admin.connect() as conn:conn.execute(text(f'CREATE DATABASE "{name}" TEMPLATE template0'))
        created=True
        engine=create_engine(url.set(database=name),connect_args=options,hide_parameters=True)
        _OWNED_TARGETS[engine]=name
        yield engine,name
    except (SQLAlchemyError,ValueError,TypeError):
        raise MeasurementError('disposable_experiment_unavailable') from None
    finally:
        if engine is not None:
            _OWNED_TARGETS.pop(engine,None)
            engine.dispose()
        try:
            if created:
                # Only the successfully created UUID name above can reach this statement.
                with admin.connect() as conn:conn.execute(text(f'DROP DATABASE "{name}"'))
        except SQLAlchemyError:
            raise MeasurementError('disposable_cleanup_failed') from None
        finally:
            if admin is not None:admin.dispose()


def update(model,**changes):return type(model).model_validate(model.model_dump()|changes)


def _payload(page,data):
    raw=json.dumps(data,sort_keys=True,separators=(',',':')).encode()
    return update(page,payload=raw,receipt=update(page.receipt,
        evidence=update(page.receipt.evidence,payload_sha256=payload_digest(raw)),payload_ref='sha256:'+payload_digest(raw)))


def sample_adapter(mode='complete',count=1,days=1):
    adapter=lesson.adapter(count=count,days=days,include_indexes=mode=='gaps')
    pages=list(adapter._pages.values())
    if mode=='failed':
        data=json.loads(pages[0].payload);data['records'][0]['low']='200';pages[0]=_payload(pages[0],data)
    elif mode=='retry':
        pages[0]=update(_payload(pages[0],dict(data_kind='synthetic',records=[])),outcome='retryable',
            failure_code='temporarily_unavailable',next_cursor_ref=None)
    elif mode in ('duplicates','revision'):
        data=json.loads(pages[0].payload)
        if mode=='revision':
            original=adapter.fetch(pages[0].receipt.parameters,0).observations[0]
            data['records'][0].update(close='103',supersedes=original.revision_id)
        pages[1]=_payload(pages[1],data)
    elif mode in ('compressible','varied'):
        padding='x'*16384 if mode=='compressible' else ''.join(hashlib.sha256(str(i).encode()).hexdigest() for i in range(256))
        pages[0]=_payload(pages[0],dict(data_kind='synthetic',records=[],padding=padding))
    return FixtureAdapter(adapter.snapshot,adapter.series,adapter.capability,pages,index_mappings=adapter._mappings)


def experiment(engine,name,*,limits=None):
    limits=WorkloadLimits.model_validate(limits or {})
    if not re.fullmatch(r'moodring_measure_[a-f0-9]{32}',name):raise MeasurementError('disposable_target_required')
    if _OWNED_TARGETS.get(engine)!=name:raise MeasurementError('owned_target_required')
    start=monotonic();verify_target(engine,name);migrate(engine)
    stores={};phases=[];source_records=0;heartbeats=0
    previous=collect(engine,name)
    baseline=previous.public
    def check(capture=None):
        if monotonic()-start>limits.seconds:raise MeasurementError('deadline_exceeded')
        if len(stores)>limits.jobs or source_records>limits.source_records or heartbeats>limits.heartbeats:
            raise MeasurementError('workload_limit')
        if capture is not None:
            rows={r['name']:r['exact_rows'] for r in capture.public['relations']}
            if rows['job_batches']>limits.steps:raise MeasurementError('step_limit')
            if capture.public['database_bytes']>limits.database_bytes:raise MeasurementError('database_size_limit')
    check(previous)
    def phase(label,action):
        nonlocal previous
        check();began=monotonic();action();after=collect(engine,name);check(after)
        phases.append(dict(name=label,elapsed_seconds=round(monotonic()-began,6),delta=phase_delta(previous,after),snapshot=after.public))
        previous=after
    def create(label,adapter,parent=None):
        nonlocal source_records
        if len(stores)>=limits.jobs:raise MeasurementError('job_limit')
        # Count all supplied source rows before writing, including unexecuted pages.
        supplied=sum(len(json.loads(p.payload).get('records',[])) for p in adapter._pages.values())
        if source_records+supplied>limits.source_records:raise MeasurementError('source_record_limit')
        source_records+=supplied
        manifest=adapter.manifest('measure:'+label,limits=lesson.JOB_LIMITS,allow_partial=label=='gaps')
        if parent:manifest=update(manifest,parent_job_id='measure:'+parent)
        stores[label]=PostgresStore(engine,manifest,create=True)
    small=sample_adapter()
    phase('create',lambda:create('small',small))
    tokens={}
    phase('claim',lambda:tokens.update(small=stores['small'].claim('measure:worker')))
    def pulse():
        nonlocal heartbeats
        for _ in range(5):
            if heartbeats>=limits.heartbeats:raise MeasurementError('heartbeat_limit')
            check();stores['small'].heartbeat(tokens['small']);heartbeats+=1
    phase('heartbeat_5',pulse)
    spec=small.series[0];page=small.fetch(spec.request,0);version=stores['small'].snapshot.version
    def commit():stores['small'].commit(spec.key,page,token=tokens['small'],expected_version=version,request=spec.request,retry_index=0)
    phase('commit_one',commit)
    phase('exact_replay',commit)
    phase('complete_one',lambda:run(stores['small'],small,tokens['small']))
    workload=[('larger','complete',3,2),('gaps','gaps',1,1),('failed','failed',1,1),
              ('retry','retry',1,1),('duplicates','duplicates',1,2),('revision','revision',1,2),
              ('compressible','compressible',1,1),('varied','varied',1,1)]
    for label,mode,count,days in workload:
        adapter=sample_adapter(mode,count,days)
        def ingest(label=label,adapter=adapter):
            create(label,adapter);store=stores[label]
            run(store,adapter,store.claim('measure:worker'))
        phase(label,ingest)
    phase('cancelled',lambda:(create('cancelled',small),stores['cancelled'].transition('cancelled')))
    phase('protected_active_dependency',lambda:(create('active',small,parent='small'),stores['active'].claim('measure:worker')))
    final=previous
    now=datetime.fromisoformat(final.public['measured_at'])
    current=retention_dry_run(final.inventory,now)
    aged=retention_dry_run(final.inventory,now+timedelta(days=120))
    # SELECT-only inventory must not mutate evidence. Compare exact relational identities.
    after_plan=collect(engine,name)
    if final.inventory!=after_plan.inventory:raise MeasurementError('inventory_changed_during_dry_run')
    checkpoints=sum(len(store.export()) for store in stores.values())
    check(after_plan)
    root=Path(__file__).resolve().parents[1]
    source_paths=[Path(__file__),Path(lesson.__file__)]+list((root/'src/ingestion').rglob('*.py'))+list((root/'src/ingestion/jobs/migrations').glob('*.sql'))
    code_hash=content_hash({str(p.relative_to(root)):payload_digest(p.read_bytes()) for p in source_paths})
    report=dict(report_version='synthetic-capacity-v1',data_kind='synthetic',target='new_owned_disposable_database',
        workload_sha256=content_hash(dict(code=code_hash,seed=payload_digest(lesson.SEED.read_bytes()),limits=limits.model_dump())),
        code_sha256=code_hash,limits=limits.model_dump(),source_records=source_records,jobs=len(stores),
        elapsed_seconds=round(monotonic()-start,6),baseline=baseline,phases=phases,
        final=final.public,aggregate_serialized_checkpoint_bytes=checkpoints,
        retention_current=current,retention_what_if_120_days=aged,retention_inventory_unchanged=True,
        notes=['Cluster WAL deltas include unrelated/background/FPI effects; not database attribution.',
            'Logical selected-column and checkpoint bytes are distinct from physical allocated storage.',
            'Tuple estimates may lag; no statistics reset/flush or bloat measurement performed.',
            'Terminal review candidates and all artifacts remain protected; no retention enforcement.',
            'Schema/index/partition/retention changes and providers require separate approval.'])
    report['growth_scenarios']=[growth_scenario(report),growth_scenario(report,artifact_sharing_percent=50),
        growth_scenario(report,extra_duplicate_percent=50,extra_revision_percent=25)]
    encode_report(report)
    return report


def measure(server_url,*,acknowledged=False,limits=None):
    with disposable_target(server_url,acknowledged=acknowledged) as (engine,name):
        return experiment(engine,name,limits=limits)


def main():
    parser=argparse.ArgumentParser(description='Bounded synthetic measurement on a newly created disposable database only.')
    parser.add_argument('--ack-disposable',action='store_true')
    parser.add_argument('--output',type=Path,help='Optional local JSON report; default is sanitized JSON on stdout.')
    args=parser.parse_args()
    try:
        report=measure(os.environ.get('PAPER_TEST_POSTGRES_URL'),acknowledged=args.ack_disposable)
        encoded=encode_report(report)
        if args.output:args.output.write_bytes(encoded+b'\n')
        else:print(encoded.decode())
    except (MeasurementError,SQLAlchemyError,ValueError,OSError) as exc:
        print(exc.code if isinstance(exc,MeasurementError) else 'measurement_failed',file=sys.stderr)
        return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
