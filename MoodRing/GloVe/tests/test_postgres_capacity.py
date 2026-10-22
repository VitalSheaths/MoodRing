"""Measurements on uniquely named disposable targets, never an application DB."""
from datetime import datetime,timezone,timedelta
import os

import pytest
from sqlalchemy import event,text

from ingestion.jobs.measurement import MeasurementError,collect,retention_dry_run,verify_target
from ingestion.jobs.migrations import migrate
from ingestion.jobs.postgres import PostgresStore
from ingestion.jobs.offline import run
from test_postgres_integration import postgres
from capacity_support import runner
from synthetic_support import lesson


def test_read_only_capture_and_retention_do_not_change_evidence(postgres):
    migrate(postgres);adapter=lesson.adapter(count=1,days=1,include_indexes=False)
    manifest=adapter.manifest('synthetic:measurement',limits=lesson.JOB_LIMITS)
    store=PostgresStore(postgres,manifest,create=True)
    assert run(store,adapter,store.claim('measure:worker'))['state']=='completed'
    before=store.snapshot;sql=[]
    def record(conn,cursor,statement,parameters,context,executemany):sql.append(statement.strip().split()[0].upper())
    event.listen(postgres,'before_cursor_execute',record)
    try:capture=collect(postgres,postgres.url.database)
    finally:event.remove(postgres,'before_cursor_execute',record)
    assert set(sql)<={'SET','SELECT'}
    assert capture.public['database_bytes']>=capture.public['allocated_relation_bytes']
    for relation in capture.public['relations']:
        assert relation['table_bytes']+relation['index_bytes']==relation['total_bytes']
        assert relation['heap_main_bytes']+relation['heap_auxiliary_bytes']+relation['toast_total_bytes']==relation['table_bytes']
    plan=retention_dry_run(capture.inventory,datetime.now(timezone.utc)+timedelta(days=120))
    assert plan['candidate_jobs']==1 and plan['proposed_delete_bytes']==0
    assert store.snapshot==before
    assert collect(postgres,postgres.url.database).inventory==capture.inventory
    assert capture.public['retained_wal_bytes'] is None


def test_mismatched_target_rejected_before_migration(postgres):
    fake='moodring_measure_'+'a'*32
    with pytest.raises(MeasurementError,match='owned_target_required'):runner.experiment(postgres,fake)
    with pytest.raises(MeasurementError,match='target_mismatch'):verify_target(postgres,fake)
    with postgres.connect() as conn:assert conn.execute(text("SELECT to_regnamespace('bronze')")).scalar_one() is None
    with pytest.raises(MeasurementError,match='disposable_target_required'):verify_target(postgres,'application')


def test_full_bounded_disposable_experiment_and_cleanup(postgres):
    # The fixture provides access to a test server; the runner creates another owned DB.
    server=postgres.url.set(database='postgres').render_as_string(hide_password=False)
    report=runner.measure(server,acknowledged=True)
    assert not runner._OWNED_TARGETS
    assert report['jobs']==11 and report['source_records']==17
    assert report['retention_inventory_unchanged']
    assert report['retention_current']['candidate_jobs']==0
    assert report['retention_what_if_120_days']['candidate_jobs']==8
    assert report['retention_what_if_120_days']['held_jobs']==3
    assert report['retention_what_if_120_days']['proposed_delete_bytes']==0
    assert report['final']['artifact_dedup_saved_logical_bytes']>0
    replay=next(p for p in report['phases'] if p['name']=='exact_replay')
    assert replay['delta']['logical_selected_column_bytes']==0
    states={j['state'] for j in report['retention_current']['jobs']}
    assert {'completed','completed_with_gaps','failed','cancelled','running','retryable'}<=states
    with postgres.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM pg_database WHERE datname LIKE 'moodring_measure_%'")).scalar_one()==0


def test_disposable_cleanup_also_runs_when_workload_bound_stops(postgres):
    server=postgres.url.set(database='postgres').render_as_string(hide_password=False)
    with pytest.raises(MeasurementError,match='job_limit'):runner.measure(server,acknowledged=True,limits={'jobs':1})
    with postgres.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM pg_database WHERE datname LIKE 'moodring_measure_%'")).scalar_one()==0
