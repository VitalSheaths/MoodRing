"""Disposable PostgreSQL only; synthetic market pages, never network ingestion."""
from dataclasses import replace
import json
import os
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from ingestion.jobs.errors import StorageError
from ingestion.jobs.migrations import inspect_schema, migrate
from ingestion.jobs.offline import FakeStore, Rejected, SimulatedCrash, run
from ingestion.jobs.postgres import PostgresStore
from ingestion.storage.postgres import metadata, start_run, finish_run
from job_fixtures import Clock, Provider, manifest, one
from test_postgres_integration import postgres  # reuse its isolated create/drop database fixture


@pytest.fixture
def migrated(postgres):
    assert migrate(postgres) == 'applied'
    return postgres


def store(engine, m=None):
    return PostgresStore(engine, m or one(), create=True)


def commit(db, token, provider=None, crash=None):
    snapshot = db.snapshot
    spec = snapshot.manifest.series[0]
    return db.commit(spec.key,(provider or Provider()).fetch(spec.request,0),token=token,
                     expected_version=snapshot.version,request=spec.request,retry_index=0,crash=crash)


def expire(engine, job_id='job:synthetic', *, retry=False):
    # Database-controlled setup, not a client clock or a sleep. Snapshot lease remains coherent.
    with engine.begin() as conn:
        if retry:
            conn.execute(text('UPDATE bronze.job_coordination SET next_retry_at=clock_timestamp()-interval \'1 second\' WHERE run_id=:id'), {'id':job_id})
        else:
            conn.execute(text('''UPDATE bronze.job_coordination SET
                expires_at=heartbeat_at+interval '1 microsecond',guard_expires_at=NULL WHERE run_id=:id'''), {'id':job_id})


def test_fresh_migration_and_explicit_reopen(migrated):
    assert inspect_schema(migrated) == 'applied'
    assert migrate(migrated) == 'already_applied'
    db = store(migrated)
    assert db.snapshot.state == 'pending'
    assert PostgresStore(migrated,db.manifest).snapshot == db.snapshot
    assert run(db,Provider(),db.claim('worker:one'))['state'] == 'completed'


def test_legacy_upgrade_preserves_rows_and_helpers(postgres):
    with postgres.begin() as conn:
        conn.execute(text('CREATE SCHEMA bronze'))
        metadata.create_all(conn)
    legacy = start_run(postgres)
    finish_run(postgres,legacy,'completed',7,5)
    with postgres.connect() as conn:
        before = dict(conn.execute(text('SELECT * FROM bronze.ingestion_runs WHERE run_id=:id'), {'id':legacy}).mappings().one())
    assert migrate(postgres) == 'applied'
    with postgres.connect() as conn:
        after = dict(conn.execute(text('SELECT * FROM bronze.ingestion_runs WHERE run_id=:id'), {'id':legacy}).mappings().one())
    assert before == after
    another = start_run(postgres); finish_run(postgres,another,'completed',2,1)
    assert migrate(postgres) == 'already_applied'


def test_incompatible_schema_is_not_partially_migrated(postgres):
    with postgres.begin() as conn:
        conn.execute(text('CREATE SCHEMA bronze; CREATE TABLE bronze.ingestion_runs(run_id text PRIMARY KEY)'))
    assert inspect_schema(postgres) == 'incompatible'
    with pytest.raises(StorageError,match='schema_mismatch'): migrate(postgres)
    with postgres.connect() as conn:
        assert conn.execute(text("SELECT to_regclass('bronze.job_manifests')")).scalar_one() is None


def test_checksum_and_schema_drift_are_detected(migrated):
    with migrated.begin() as conn:
        conn.execute(text("UPDATE bronze.ingestion_job_migrations SET checksum=repeat('0',64)"))
    assert inspect_schema(migrated) == 'checksum_mismatch'
    with pytest.raises(StorageError,match='migration_checksum_mismatch'): migrate(migrated)


@pytest.mark.parametrize('boundary',['before_artifact','after_artifact','during_commit'])
def test_transaction_crash_rolls_back_all_evidence(migrated,boundary):
    db = store(migrated); token = db.claim('worker:one'); before = db.snapshot
    with pytest.raises(SimulatedCrash): commit(db,token,crash=boundary)
    assert PostgresStore(migrated,db.manifest).snapshot == before
    with migrated.connect() as conn:
        assert conn.execute(text('SELECT count(*) FROM bronze.job_artifacts')).scalar_one() == 0


def test_after_commit_before_ack_replays_original(migrated):
    db = store(migrated); token = db.claim('worker:one'); version = db.snapshot.version
    spec = db.manifest.series[0]; supplied = Provider().fetch(spec.request,0)
    with pytest.raises(SimulatedCrash): commit(db,token,crash='after_commit')
    reopened = PostgresStore(migrated,db.manifest); before = reopened.snapshot
    result = reopened.commit(spec.key,supplied,token=token,expected_version=version,request=spec.request,retry_index=0)
    assert result == before.batches[0].result and reopened.snapshot == before
    with pytest.raises(Rejected,match='replay_conflict'):
        reopened.commit(spec.key,replace(supplied,payload=b'changed'),token=token,expected_version=version,request=spec.request,retry_index=0)


def test_fixture_parity_gaps_retry_and_fresh_engine(migrated):
    m = manifest(); db = store(migrated,m)
    result = run(db,Provider(),db.claim('worker:one'))
    assert result['state'] == 'retryable'
    expire(migrated,retry=True)
    second = create_engine(migrated.url)
    try:
        recovered = PostgresStore(second,m)
        actual = run(recovered,Provider(),recovered.claim('worker:two'))
        fake_clock = Clock(); fake = FakeStore(m,fake_clock)
        run(fake,Provider(),fake.claim('worker:one')); fake_clock.advance(5)
        expected = run(fake,Provider(),fake.claim('worker:two'))
        assert actual == expected
    finally:
        second.dispose()


def test_server_time_takeover_and_stale_worker(migrated):
    db = store(migrated); token = db.claim('worker:one')
    with pytest.raises(Rejected,match='stale_lease'): db.claim('worker:competitor')
    db.heartbeat(token); expire(migrated)
    fresh = PostgresStore(migrated,db.manifest); next_token = fresh.claim('worker:replacement')
    assert next_token > token
    with pytest.raises(Rejected,match='stale_lease'): commit(db,token)
    assert run(fresh,Provider(),next_token)['state'] == 'completed'


def test_migration_failure_rolls_back_ddl_and_history(postgres,monkeypatch):
    import ingestion.jobs.migrations as migrations
    monkeypatch.setattr(migrations,'DDL',migrations.DDL+'\nSELECT 1/0;')
    with pytest.raises(StorageError,match='migration_failed'): migrate(postgres)
    with postgres.connect() as conn:
        assert conn.execute(text("SELECT to_regnamespace('bronze')")).scalar_one() is None


def test_schema_drift_rejected_before_open(migrated):
    with migrated.begin() as conn:
        conn.execute(text('ALTER TABLE bronze.job_checkpoints ADD COLUMN unexpected integer'))
    assert inspect_schema(migrated) == 'incompatible'
    with pytest.raises(StorageError,match='schema_mismatch'): PostgresStore(migrated,one(),create=True)


def test_owned_runs_and_terminal_evidence_cannot_be_mutated_by_legacy_helpers(migrated):
    db = store(migrated); token = db.claim('worker:one'); commit(db,token)
    with pytest.raises(DBAPIError): finish_run(migrated,db.manifest.job_id,'completed',0,0)
    assert run(db,Provider(),token)['state'] == 'completed'
    before = db.snapshot
    for table in ('job_manifests','job_attempts','job_artifacts','job_batches','job_revisions','job_record_appearances','job_checkpoints'):
        with pytest.raises(DBAPIError):
            with migrated.begin() as conn:
                conn.execute(text('DELETE FROM bronze.'+table))
    assert db.snapshot == before


def test_payload_constraint_and_dangling_artifact_are_rejected(migrated):
    db = store(migrated); token = db.claim('worker:one'); commit(db,token)
    with pytest.raises(DBAPIError):
        with migrated.begin() as conn:
            conn.execute(text("INSERT INTO bronze.job_artifacts VALUES (repeat('0',64),decode('00','hex'))"))
    with pytest.raises(DBAPIError):
        with migrated.begin() as conn:
            conn.execute(text("UPDATE bronze.job_batches SET artifact_sha=repeat('0',64)"))
    assert db.public_result()['counters']['accepted_revision_count'] == 1


def test_privileged_corruption_is_detected_on_recovery(migrated):
    db = store(migrated); token = db.claim('worker:one'); commit(db,token)
    with migrated.begin() as conn:
        # Deliberate owner-level corruption exercises read validation, not normal writer permissions.
        conn.execute(text('ALTER TABLE bronze.job_record_appearances DISABLE TRIGGER job_record_immutable'))
        conn.execute(text("UPDATE bronze.job_record_appearances SET record=jsonb_set(record,'{close}','\"999\"'::jsonb)"))
        conn.execute(text('ALTER TABLE bronze.job_record_appearances ENABLE TRIGGER job_record_immutable'))
    with pytest.raises(StorageError,match='evidence_corrupt'): PostgresStore(migrated,db.manifest)


def test_independent_connections_race_claims_and_commits(migrated):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    m = one(); first = store(migrated,m); second_engine = create_engine(migrated.url)
    try:
        second = PostgresStore(second_engine,m); barrier = Barrier(2)
        def claim(db):
            barrier.wait(timeout=10)
            try: return db.claim('worker:race')
            except Rejected as exc: return exc.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(claim,(first,second)))
        assert claims.count('stale_lease') == 1
        token = next(c for c in claims if isinstance(c,int)); version = first.snapshot.version
        spec = m.series[0]; page = Provider().fetch(spec.request,0); barrier = Barrier(2)
        def execute(db):
            barrier.wait(timeout=10)
            return db.commit(spec.key,page,token=token,expected_version=version,request=spec.request,retry_index=0)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(execute,(first,second)))
        assert results[0] == results[1]
        assert first.public_result()['counters']['accepted_unique_record_count'] == 1
    finally:
        second_engine.dispose()


def test_commit_expiring_inside_transaction_rolls_back_at_commit(migrated):
    m = one(lease_seconds=1); db = store(migrated,m); token = db.claim('worker:short')
    before = db.snapshot
    # Only this test uses a real wait: it must cross actual server-clock lease expiry
    # after the final writes. Other expiry/retry tests set database timestamps directly.
    db._before_commit = lambda conn: conn.execute(text('SELECT pg_sleep(1.1)'))
    with pytest.raises(Rejected,match='stale_lease'): commit(db,token)
    assert PostgresStore(migrated,m).snapshot == before


def test_subprocess_restart_uses_persisted_state(migrated):
    env = dict(os.environ,JOB_TEST_URL=migrated.url.render_as_string(hide_password=False),PYTHONPATH='src:tests')
    common = """from sqlalchemy import create_engine
from ingestion.jobs.postgres import PostgresStore
from ingestion.jobs.offline import run
from job_fixtures import one,Provider
engine=create_engine(__import__('os').environ['JOB_TEST_URL'])
"""
    writer = common + """try:
 store=PostgresStore(engine,one(),create=True)
 token=store.claim('worker:writer')
 snapshot=store.snapshot
 spec=snapshot.manifest.series[0]
 store.commit(spec.key,Provider().fetch(spec.request,0),token=token,expected_version=snapshot.version,request=spec.request,retry_index=0)
 print(store.snapshot.batches[0].batch_key)
finally: engine.dispose()
"""
    first = subprocess.run([sys.executable,'-c',writer],env=env,capture_output=True,text=True,timeout=30)
    assert first.returncode == 0, 'Child write failed (details intentionally withheld)'
    expire(migrated)
    reader = common + """try:
 store=PostgresStore(engine,one())
 assert run(store,Provider(),store.claim('worker:recovered'))['state']=='completed'
 assert store.public_result()['counters']['accepted_revision_count']==1
 print(store.snapshot.batches[0].batch_key)
finally: engine.dispose()
"""
    second = subprocess.run([sys.executable,'-c',reader],env=env,capture_output=True,text=True,timeout=30)
    assert second.returncode == 0, 'Child recovery failed (details intentionally withheld)'
    assert second.stdout.strip() == first.stdout.strip()


def test_connection_errors_do_not_echo_private_driver_details(monkeypatch):
    from sqlalchemy.exc import OperationalError
    class Broken:
        def begin(self):
            raise OperationalError('TEST_PRIVATE_SQL',{'password':'TEST_PRIVATE_SECRET'},Exception('/private/TEST_DRIVER_PATH'))
    with pytest.raises(StorageError) as exc: PostgresStore(Broken(),one())
    assert str(exc.value) == 'database_unavailable'
    assert all(value not in str(exc.value) for value in ('TEST_PRIVATE','password','/private/'))


@pytest.mark.parametrize('mode,partial,expected',[('equity',False,'completed'),('quarantine',False,'failed'),
                                               ('quarantine',True,'completed_with_gaps')])
def test_required_coverage_policy_parity(migrated,mode,partial,expected):
    m = one(allow_partial=partial); db = store(migrated,m); fake = FakeStore(m,Clock())
    actual = run(db,Provider({'equity':mode}),db.claim('worker:one'))
    reference = run(fake,Provider({'equity':mode}),fake.claim('worker:one'))
    assert actual == reference and actual['state'] == expected


@pytest.mark.parametrize('bound,value,mode,code',[
    ('max_pages_per_instrument',1,'pages','limit_pages'),
    ('max_total_records',1,'pages','limit_total_records'),
    ('max_retries_per_page',0,'always_retry','limit_retries'),
    ('max_continuation_depth',0,'pages','limit_depth'),
    ('max_steps',1,'pages','limit_steps'),
    ('max_records_per_page',1,'equity','limit_records_page'),
])
def test_runtime_resource_bounds_match_fake(migrated,bound,value,mode,code):
    from test_offline_jobs import with_limits
    m = with_limits(**{bound:value}); db = store(migrated,m); fake = FakeStore(m,Clock())
    actual = run(db,Provider({'equity':mode}),db.claim('worker:one'))
    reference = run(fake,Provider({'equity':mode}),fake.claim('worker:one'))
    assert actual == reference and actual['code'] == code
    assert db.snapshot.batches == PostgresStore(migrated,m).snapshot.batches


def test_revision_duplicates_and_replay_counters(migrated):
    from test_offline_jobs import TwoRecords
    for suffix,provider in [('revisions',TwoRecords()),('duplicates',Provider({'equity':'pages'}))]:
        m = one(job_id='job:'+suffix); db = store(migrated,m); fake = FakeStore(m,Clock())
        actual = run(db,provider,db.claim('worker:one'))
        expected = run(fake,provider,fake.claim('worker:one'))
        assert actual == expected and actual['counters']['accepted_unique_record_count'] == 1
        assert actual['counters']['accepted_revision_count'] == (2 if suffix=='revisions' else 1)


def test_independent_series_compare_and_swap_conflict(migrated):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    m = manifest([dict(role='equity',required=True),dict(role='index',required=True)])
    first = store(migrated,m); token = first.claim('worker:one'); version = first.snapshot.version
    second_engine = create_engine(migrated.url)
    try:
        second = PostgresStore(second_engine,m); barrier = Barrier(2)
        def execute(pair):
            db,spec = pair; supplied = Provider().fetch(spec.request,0)
            barrier.wait(timeout=10)
            try: return db.commit(spec.key,supplied,token=token,expected_version=version,request=spec.request,retry_index=0)
            except Rejected as exc: return exc.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(execute,zip((first,second),m.series)))
        assert results.count('version_conflict') == 1
        assert first.public_result()['counters']['accepted_revision_count'] == 1
    finally:
        second_engine.dispose()


def test_failure_after_sql_writes_rolls_back_records_counters_and_cursor(migrated):
    db = store(migrated); token = db.claim('worker:one'); before = db.snapshot
    def crash(_): raise SimulatedCrash()
    db._before_commit = crash
    with pytest.raises(SimulatedCrash): commit(db,token)
    assert PostgresStore(migrated,db.manifest).snapshot == before
    with migrated.connect() as conn:
        for table in ('job_artifacts','job_batches','job_revisions','job_record_appearances'):
            assert conn.execute(text('SELECT count(*) FROM bronze.'+table)).scalar_one() == 0


def test_source_config_window_calendar_and_adjustment_bind_reopen(migrated):
    from ingestion.jobs.contracts import Manifest
    m = one(); db = store(migrated,m)
    for field in ('source_sha256','universe_sha256','provider','dataset'):
        data = m.model_dump(); data[field] = 'f'*64 if field.endswith('sha256') else 'changed'
        with pytest.raises(Rejected,match='identity_conflict'): PostgresStore(migrated,Manifest.model_validate(data))
    changed = one(allow_partial=True)
    with pytest.raises(Rejected,match='identity_conflict'): PostgresStore(migrated,changed)
    # Changing a window/calendar/basis requires a different immutable manifest too.
    for kind in ('window','calendar','adjustment'):
        data = m.model_dump(); spec = data['series'][0]
        if kind == 'window':
            spec['request']['window']['cutoff'] = '2026-03-09T20:00:00Z'
            spec['coverage']['window'] = spec['request']['window']
        if kind == 'calendar':
            spec['request']['window']['calendar_version'] = 'changed'
            spec['coverage']['window'] = spec['request']['window']
            spec['coverage']['calendar']['calendar_version'] = 'changed'
            for session in spec['coverage']['calendar']['sessions']: session['calendar_version'] = 'changed'
        if kind == 'adjustment': spec['request']['adjustment'] = 'split_adjusted'
        with pytest.raises(Rejected,match='identity_conflict'): PostgresStore(migrated,Manifest.model_validate(data))


def test_byte_budget_survives_storage_split(migrated):
    from test_offline_jobs import with_limits
    baseline = FakeStore(one(),Clock())
    m = with_limits(max_checkpoint_bytes=len(baseline.export())+1000)
    db = store(migrated,m); token = db.claim('worker:one')
    result = run(db,Provider(),token)
    assert result['code'] == 'limit_checkpoint_bytes'
    assert not db.snapshot.batches
    with migrated.connect() as conn:
        assert conn.execute(text('SELECT count(*) FROM bronze.job_artifacts')).scalar_one() == 0


def test_attempt_budget_exhaustion_is_persisted(migrated):
    from test_offline_jobs import with_limits
    m = with_limits(max_attempts=1); db = store(migrated,m)
    assert run(db,Provider({'equity':'always_retry'}),db.claim('worker:one'))['state'] == 'retryable'
    expire(migrated,retry=True)
    with pytest.raises(Rejected,match='limit_attempts'): db.claim('worker:two')
    assert PostgresStore(migrated,m).snapshot.state == 'failed'


def test_full_contract_identifier_length_is_preserved(migrated):
    m = one(job_id='j'*100); db = store(migrated,m)
    assert run(db,Provider(),db.claim('worker:one'))['state'] == 'completed'
    assert PostgresStore(migrated,m).manifest.job_id == 'j'*100


def test_legacy_upgrade_failure_preserves_original_widths_and_rows(postgres,monkeypatch):
    import ingestion.jobs.migrations as migrations
    from sqlalchemy import inspect
    with postgres.begin() as conn:
        conn.execute(text('CREATE SCHEMA bronze')); metadata.create_all(conn)
    identifier = start_run(postgres); finish_run(postgres,identifier,'completed',3,2)
    monkeypatch.setattr(migrations,'DDL',migrations.DDL+'\nSELECT 1/0;')
    with pytest.raises(StorageError): migrate(postgres)
    columns = {c['name']:c for c in inspect(postgres).get_columns('ingestion_runs',schema='bronze')}
    assert columns['status']['type'].length == 16 and columns['run_id']['type'].length == 64
    with postgres.connect() as conn:
        row = conn.execute(text('SELECT status,total_fetched,total_inserted FROM bronze.ingestion_runs WHERE run_id=:id'),{'id':identifier}).one()
        assert tuple(row) == ('completed',3,2)
        assert conn.execute(text("SELECT to_regclass('bronze.job_manifests')")).scalar_one() is None


def test_reopen_requires_exact_existing_job(migrated):
    # The fixture already migrated this database; opening a different identity creates nothing.
    with pytest.raises(StorageError,match='job_not_found'): PostgresStore(migrated,one())
    with migrated.connect() as conn:
        assert conn.execute(text('SELECT count(*) FROM bronze.ingestion_runs')).scalar_one() == 0


def test_store_never_applies_a_pending_migration(postgres):
    assert inspect_schema(postgres) == 'pending'
    with pytest.raises(StorageError,match='schema_mismatch'): PostgresStore(postgres,one(),create=True)
    assert inspect_schema(postgres) == 'pending'
    with postgres.connect() as conn:
        assert conn.execute(text("SELECT to_regnamespace('bronze')")).scalar_one() is None


def test_concurrent_migration_application_is_serialized(postgres):
    from concurrent.futures import ThreadPoolExecutor
    second = create_engine(postgres.url)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(migrate,(postgres,second)))
        assert sorted(results) == ['already_applied','applied']
        assert inspect_schema(postgres) == 'applied'
    finally:
        second.dispose()


def test_instrument_and_date_bounds_reject_before_fetch(migrated):
    from ingestion.jobs.contracts import Manifest
    from market_fixtures import seed
    m = manifest(); m = manifest(limits=m.limits.model_dump()|{'max_instruments':1})
    db = store(migrated,m); provider = Provider()
    assert run(db,provider,db.claim('worker:one'))['code'] == 'limit_instruments'
    assert not provider.calls
    data = one(job_id='job:date',limits=one().limits.model_dump()|{'max_date_span':1}).model_dump()
    spec = data['series'][0]
    spec['request']['window']['start'] = '2026-03-06'
    spec['coverage']['window'] = spec['request']['window']
    spec['coverage']['calendar'] = seed()['calendar']
    spec['coverage']['days'] = [dict(session_date=d,outcome=o) for d,o in [
        ('2026-03-06','provider_gap'),('2026-03-07','non_session'),('2026-03-08','non_session'),('2026-03-09','provider_gap')]]
    db = store(migrated,Manifest.model_validate(data)); provider = Provider()
    assert run(db,provider,db.claim('worker:one'))['code'] == 'limit_date_span'
    assert not provider.calls


def test_initial_byte_budget_rejection_creates_no_partial_job(migrated):
    from test_offline_jobs import with_limits
    with pytest.raises(Rejected,match='limit_checkpoint_bytes'):
        store(migrated,with_limits(max_checkpoint_bytes=1))
    with migrated.connect() as conn:
        assert conn.execute(text('SELECT count(*) FROM bronze.ingestion_runs')).scalar_one() == 0


def test_corrupt_coordination_cannot_become_a_valid_recovery_snapshot(migrated):
    db = store(migrated); token = db.claim('worker:one'); commit(db,token)
    with migrated.begin() as conn:
        # This privileged change intentionally breaks the checkpoint/record relation.
        conn.execute(text('ALTER TABLE bronze.job_checkpoints DISABLE TRIGGER job_checkpoint_mutation_guard'))
        conn.execute(text("UPDATE bronze.job_checkpoints SET progress=jsonb_set(progress,'{cursor_ref}','\"cursor:invented\"'::jsonb)"))
        conn.execute(text('ALTER TABLE bronze.job_checkpoints ENABLE TRIGGER job_checkpoint_mutation_guard'))
    with pytest.raises(StorageError,match='evidence_corrupt'): PostgresStore(migrated,db.manifest)


@pytest.mark.parametrize('basis,expected',[('active_lifetime','completed'),('requested_window','failed')])
def test_lifetime_vs_full_window_policy_survives_recovery(migrated,basis,expected):
    from ingestion.jobs.contracts import Manifest
    from market_fixtures import seed
    data = one(coverage_basis=basis).model_dump(); spec = data['series'][0]
    spec['request']['window']['start'] = '2026-03-06'
    spec['coverage']['window'] = spec['request']['window']
    spec['coverage']['active_from'] = '2026-03-09'
    spec['coverage']['calendar'] = seed()['calendar']
    spec['coverage']['days'] = [dict(session_date=d,outcome=o) for d,o in [
        ('2026-03-06','pre_listing'),('2026-03-07','non_session'),('2026-03-08','non_session'),('2026-03-09','provider_gap')]]
    m = Manifest.model_validate(data); db = store(migrated,m); fake = FakeStore(m,Clock())
    actual = run(db,Provider(),db.claim('worker:one'))
    assert actual == run(fake,Provider(),fake.claim('worker:one'))
    assert actual == PostgresStore(migrated,m).public_result()
    assert actual['state'] == expected
    assert actual['series'][0]['active_lifetime_complete']
    assert not actual['series'][0]['requested_window_complete']
