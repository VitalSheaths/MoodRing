"""Read-only disposable-target measurement and pure retention planning; no enforcement."""
from dataclasses import dataclass
from datetime import timedelta
import json
import re

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from ingestion.sec.contracts import explicit_time
from .migrations import TABLES, HISTORY
from .contracts import TERMINAL

NAMES=tuple(TABLES)+('ingestion_runs','bronze_news_raw',HISTORY)
DATABASE_PATTERN=r'moodring_(?:measure|test)_[a-f0-9]{32}'


class MeasurementError(Exception):
    def __init__(self,code):self.code=code;super().__init__(code)


class RetentionPolicy(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True,strict=True)
    completed_days:int=Field(default=30,ge=1,le=3650)
    gaps_days:int=Field(default=60,ge=1,le=3650)
    failed_days:int=Field(default=90,ge=1,le=3650)
    cancelled_days:int=Field(default=14,ge=1,le=3650)


@dataclass(frozen=True)
class Capture:
    public:dict
    inventory:dict  # internal identities; never serialized by the report runner
    wal_lsn:str|None


def _guard(conn,expected):
    if not isinstance(expected,str) or not re.fullmatch(DATABASE_PATTERN,expected):
        raise MeasurementError('disposable_target_required')
    actual=conn.execute(text('SELECT current_database()')).scalar_one()
    if actual!=expected:raise MeasurementError('target_mismatch')


def verify_target(engine,expected):
    """Read-only preflight before any migration or workload can run."""
    try:
        with engine.begin() as conn:
            conn.execute(text('SET TRANSACTION READ ONLY'))
            _guard(conn,expected)
    except SQLAlchemyError:
        raise MeasurementError('target_preflight_unavailable') from None


def _collect(conn,expected):
    _guard(conn,expected)
    count={}
    for name in NAMES:
        # Identifiers are a constant allowlist, not caller input. Limit scans before counts.
        count[name]=conn.execute(text(f'SELECT count(*) FROM (SELECT 1 FROM bronze.{name} LIMIT 1001) bounded')).scalar_one()
        if count[name]>1000:raise MeasurementError('inventory_row_limit')
    if count['job_manifests']>12 or count['job_record_appearances']>128 or count['job_batches']>100:
        raise MeasurementError('workload_limit')
    rows=conn.execute(text('''SELECT c.relname AS name,
        pg_relation_size(c.oid) AS heap_main_bytes,
        pg_table_size(c.oid) AS table_bytes, pg_indexes_size(c.oid) AS index_bytes,
        pg_total_relation_size(c.oid) AS total_bytes,
        CASE WHEN c.reltoastrelid=0 THEN 0 ELSE pg_total_relation_size(c.reltoastrelid) END AS toast_total_bytes,
        s.n_live_tup,s.n_dead_tup,s.n_tup_ins,s.n_tup_upd,s.n_tup_del,s.autovacuum_count,s.autoanalyze_count
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        LEFT JOIN pg_stat_all_tables s ON s.relid=c.oid
        WHERE n.nspname='bronze' AND c.relname=ANY(:names) AND c.relkind='r' ORDER BY c.relname'''),{'names':list(NAMES)}).mappings().all()
    if len(rows)!=len(NAMES):raise MeasurementError('schema_mismatch')
    tables=[]
    for row in rows:
        item=dict(row);item['exact_rows']=count[item['name']]
        item['heap_auxiliary_bytes']=item['table_bytes']-item['toast_total_bytes']-item['heap_main_bytes']
        tables.append(item)
    indexes=[dict(r) for r in conn.execute(text('''SELECT t.relname AS table_name,i.relname AS index_name,
        pg_relation_size(i.oid) AS bytes FROM pg_index x JOIN pg_class t ON t.oid=x.indrelid
        JOIN pg_namespace n ON n.oid=t.relnamespace JOIN pg_class i ON i.oid=x.indexrelid
        WHERE n.nspname='bronze' AND t.relname=ANY(:names) ORDER BY t.relname,i.relname LIMIT 65'''),{'names':list(NAMES)}).mappings()]
    if len(indexes)>64:raise MeasurementError('index_limit')
    settings=conn.execute(text('''SELECT current_setting('server_version_num')::integer AS server_version_num,
        current_setting('block_size')::integer AS block_size,
        current_setting('wal_level') AS wal_level,current_setting('full_page_writes') AS full_page_writes,
        current_setting('wal_compression') AS wal_compression,current_setting('track_counts') AS track_counts''')).mappings().one()
    size=conn.execute(text('SELECT pg_database_size(current_database())')).scalar_one()
    when=conn.execute(text('SELECT clock_timestamp()')).scalar_one()
    # Permission/recovery limitations are explicit; never request a monitoring role here.
    permitted=conn.execute(text("SELECT has_function_privilege(current_user,'pg_catalog.pg_current_wal_insert_lsn()','EXECUTE') AND NOT pg_is_in_recovery()" )).scalar_one()
    wal=conn.execute(text('SELECT pg_current_wal_insert_lsn()::text')).scalar_one() if permitted else None
    jobs=[dict(r) for r in conn.execute(text('''SELECT m.run_id AS id,r.status AS state,r.ended_at,
        m.manifest->>'parent_job_id' AS parent,m.manifest->>'recovery_of' AS recovery
        FROM bronze.job_manifests m JOIN bronze.ingestion_runs r USING(run_id) ORDER BY m.run_id''')).mappings()]
    artifacts=[dict(r) for r in conn.execute(text('SELECT sha AS id,octet_length(payload) AS bytes FROM bronze.job_artifacts ORDER BY sha')).mappings()]
    refs=[tuple(r) for r in conn.execute(text('SELECT DISTINCT run_id,artifact_sha FROM bronze.job_batches WHERE artifact_sha IS NOT NULL ORDER BY run_id,artifact_sha'))]
    logical=conn.execute(text('''SELECT
        COALESCE((SELECT sum(octet_length(manifest::text)) FROM bronze.job_manifests),0)
        + COALESCE((SELECT sum(octet_length(progress::text)) FROM bronze.job_checkpoints),0)
        + COALESCE((SELECT sum(octet_length(metadata::text)) FROM bronze.job_batches),0)
        + COALESCE((SELECT sum(octet_length(record::text)) FROM bronze.job_record_appearances),0)
        + COALESCE((SELECT sum(octet_length(payload)) FROM bronze.job_artifacts),0)''')).scalar_one()
    unique_bytes=sum(a['bytes'] for a in artifacts)
    referenced_bytes=conn.execute(text('''SELECT COALESCE(sum(octet_length(a.payload)),0)
        FROM bronze.job_batches b JOIN bronze.job_artifacts a ON a.sha=b.artifact_sha''')).scalar_one()
    result=dict(measured_at=when.isoformat(),database_bytes=size,relations=tables,indexes=indexes,settings=dict(settings),
        allocated_relation_bytes=sum(t['total_bytes'] for t in tables),
        heap_main_bytes=sum(t['heap_main_bytes'] for t in tables),toast_total_bytes=sum(t['toast_total_bytes'] for t in tables),
        index_bytes=sum(t['index_bytes'] for t in tables),logical_selected_column_bytes=int(logical),
        unique_artifact_bytes=unique_bytes,artifact_reference_bytes=int(referenced_bytes),
        artifact_dedup_saved_logical_bytes=int(referenced_bytes)-unique_bytes,
        retained_wal_bytes=None,replication_archive_retention_bytes=None,free_disk_bytes=None,
        unsupported_metrics=['retained_wal','replication_archive_retention','free_disk','exact_bloat'],
        wal_scope='cluster_insert_lsn_difference_not_database_attribution',
        tuple_statistics='estimates_may_lag_not_exact_live_or_dead_counts')
    return Capture(result,dict(jobs=jobs,artifacts=artifacts,references=refs),wal)


def collect(engine,expected_database):
    """SELECT-only capture, restricted to explicitly named disposable targets."""
    try:
        with engine.connect().execution_options(isolation_level='REPEATABLE READ') as conn:
            with conn.begin():
                conn.execute(text('SET TRANSACTION READ ONLY'))
                conn.execute(text("SET LOCAL statement_timeout='5s'"))
                conn.execute(text('SET LOCAL search_path=pg_catalog'))
                return _collect(conn,expected_database)
    except SQLAlchemyError:
        raise MeasurementError('measurement_unavailable') from None


def _lsn_number(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Fa-f0-9]{1,8}/[A-Fa-f0-9]{1,8}',value):
        raise MeasurementError('invalid_wal_sample')
    high,low=value.split('/')
    return (int(high,16)<<32)+int(low,16)


def phase_delta(before,after):
    wal=None if before.wal_lsn is None or after.wal_lsn is None else _lsn_number(after.wal_lsn)-_lsn_number(before.wal_lsn)
    if wal is not None and wal<0:raise MeasurementError('wal_sample_discontinuity')
    fields=('database_bytes','allocated_relation_bytes','heap_main_bytes','toast_total_bytes','index_bytes',
            'logical_selected_column_bytes','unique_artifact_bytes')
    return dict(**{key:after.public[key]-before.public[key] for key in fields},
                cluster_wal_insert_bytes=wal,wal_available=wal is not None)


def retention_dry_run(inventory,asof,*,policy=None,holds=()):
    """No engine or mutation path. Candidate means review only; artifacts remain held."""
    try:
        policy=RetentionPolicy.model_validate(policy or {})
        asof=explicit_time(asof)
        if asof is None:raise ValueError()
        jobs,artifacts,refs=inventory['jobs'],inventory['artifacts'],inventory['references']
        if len(jobs)>12 or len(artifacts)>100 or len(refs)>100:raise MeasurementError('inventory_limit')
        byid={j['id']:j for j in jobs};byartifact={a['id']:a for a in artifacts}
        if len(byid)!=len(jobs) or len(byartifact)!=len(artifacts):raise ValueError()
        if any(not isinstance(a['bytes'],int) or isinstance(a['bytes'],bool) or a['bytes']<0 for a in artifacts):raise ValueError()
        if len(holds)>12 or set(holds)-byid.keys():raise ValueError()
        if any(j['state'] not in TERMINAL|{'pending','running','paused','retryable'} for j in jobs):raise ValueError()
        ages={'completed':policy.completed_days,'completed_with_gaps':policy.gaps_days,
              'failed':policy.failed_days,'cancelled':policy.cancelled_days}
        kept={};candidates=set();dependencies={}
        incomplete=any(job not in byid or artifact not in byartifact for job,artifact in refs)
        for job in jobs:
            key=job['id'];dependencies[key]=tuple(p for p in (job.get('parent'),job.get('recovery')) if p)
            if any(p not in byid for p in dependencies[key]):incomplete=True
            end=explicit_time(job.get('ended_at'))
            if key in holds:kept[key]='explicit_hold'
            elif job['state'] not in TERMINAL:kept[key]='nonterminal'
            elif end is None or end>asof:kept[key]='unknown_or_future_end'
            elif asof-end<timedelta(days=ages[job['state']]):kept[key]='within_illustrative_age'
            else:candidates.add(key)
        def visit(key,path):
            nonlocal incomplete
            if key in path:incomplete=True;return
            for dep in dependencies.get(key,()):visit(dep,path|{key})
        for key in byid:visit(key,set())
        if incomplete:
            kept={k:'incomplete_dependency_inventory' for k in byid};candidates=set()
        else:
            pending=list(kept)
            while pending:
                key=pending.pop()
                for dep in dependencies[key]:
                    if dep not in kept:
                        kept[dep]='dependency_of_retained_job';candidates.discard(dep);pending.append(dep)
        shared={a:set() for a in byartifact}
        for job,artifact in refs:
            if artifact in shared:shared[artifact].add(job)
        labels={key:f'job_{i:02d}' for i,key in enumerate(sorted(byid),1)}
        return dict(mode='dry_run_no_deletion',asof=asof.isoformat(),illustrative_policy_days=policy.model_dump(),
            incomplete_inventory=incomplete,jobs=[dict(label=labels[k],state=byid[k]['state'],
                action='archive_review_candidate' if k in candidates else 'hold',reason='illustrative_age_only' if k in candidates else kept[k]) for k in sorted(byid)],
            candidate_jobs=len(candidates),held_jobs=len(kept),
            shared_artifact_count=sum(len(v)>1 for v in shared.values()),
            bytes_referenced_by_retained_jobs=sum(byartifact[k]['bytes'] for k,v in shared.items() if v & kept.keys()),
            bytes_referenced_only_by_review_candidates=sum(byartifact[k]['bytes'] for k,v in shared.items() if v and not v & kept.keys()),
            unreferenced_artifact_bytes=sum(byartifact[k]['bytes'] for k,v in shared.items() if not v),
            protected_artifact_bytes=sum(a['bytes'] for a in artifacts),proposed_delete_bytes=0,
            enforcement_authorized=False)
    except (ValidationError,ValueError,TypeError,KeyError,OverflowError):
        raise MeasurementError('invalid_retention_inventory') from None


def encode_report(report):
    try:encoded=json.dumps(report,sort_keys=True,indent=2,allow_nan=False).encode()
    except (ValueError,TypeError):raise MeasurementError('invalid_report') from None
    if len(encoded)>1048576:raise MeasurementError('report_limit')
    return encoded


def growth_scenario(report,*,jobs_per_day=10,days=30,artifact_sharing_percent=0,
                    extra_duplicate_percent=0,extra_revision_percent=0):
    """Illustrative arithmetic outside the observed workload, never an operating limit."""
    args=(jobs_per_day,days,artifact_sharing_percent,extra_duplicate_percent,extra_revision_percent)
    if any(isinstance(v,bool) or not isinstance(v,int) for v in args) or not 1<=jobs_per_day<=1000 or not 1<=days<=3650 or any(not 0<=v<=100 for v in args[2:]):
        raise MeasurementError('invalid_growth_scenario')
    jobs=report['jobs'];final=report['final'];baseline=report['baseline']
    count=jobs_per_day*days
    metadata=max(0,final['logical_selected_column_bytes']-final['unique_artifact_bytes'])
    metadata_scaled=metadata*count*(100+extra_duplicate_percent+extra_revision_percent)//(100*jobs)
    artifact_scaled=final['artifact_reference_bytes']*count*(100-artifact_sharing_percent)//(100*jobs)
    wal_values=[phase['delta']['cluster_wal_insert_bytes'] for phase in report['phases']]
    wal=None if any(v is None for v in wal_values) else sum(wal_values)*count//jobs
    return dict(mode='illustrative_not_capacity_approval',retained_jobs=count,
        assumptions=dict(jobs_per_day=jobs_per_day,days=days,artifact_sharing_percent=artifact_sharing_percent,
            extra_duplicate_percent=extra_duplicate_percent,extra_revision_percent=extra_revision_percent,
            terminal_state_mix='same_as_small_mixed_fixture',active_jobs='excluded_add_separately'),
        logical_selected_column_bytes=metadata_scaled+artifact_scaled,
        physical_mixed_workload_extrapolation_bytes=baseline['allocated_relation_bytes']+
            max(0,final['allocated_relation_bytes']-baseline['allocated_relation_bytes'])*count//jobs,
        cluster_wal_insert_extrapolation_bytes=wal,retained_wal_bytes=None,
        outside_observed_job_count=count>jobs,
        limitations=['Physical extrapolation holds fixture mix/compressibility constant and does not model sensitivity changes.',
            'Sharing/extra duplicate/revision sensitivity affects logical arithmetic only; physical/WAL effects need new measurements.',
            'No throughput, lifetime, linearity, production storage or retention-safety guarantee.'])
