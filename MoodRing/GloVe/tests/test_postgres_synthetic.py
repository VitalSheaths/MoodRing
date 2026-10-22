"""Small selected-universe jobs on explicitly disposable PostgreSQL only."""
from datetime import datetime,timezone

import pytest
from sqlalchemy import create_engine

from ingestion.jobs.migrations import migrate
from ingestion.jobs.postgres import PostgresStore
from ingestion.jobs.offline import FakeStore, Rejected, SimulatedCrash, run
from test_postgres_integration import postgres
from synthetic_support import lesson


@pytest.mark.parametrize('basis',['raw','split_adjusted'])
def test_selected_membership_pages_persist_and_recover_with_fake_parity(postgres,basis):
    migrate(postgres)
    adapter=lesson.adapter(count=2,days=1,adjustment=basis)
    manifest=adapter.manifest('synthetic:persisted',limits=lesson.JOB_LIMITS,allow_partial=True)
    manifest.verify_universe(lesson.selection().snapshot)
    fake=FakeStore(manifest,lambda:datetime(2026,10,21,2,tzinfo=timezone.utc))
    expected=run(fake,adapter,fake.claim('synthetic:worker'))
    stored=PostgresStore(postgres,manifest,create=True)
    actual=run(stored,adapter,stored.claim('synthetic:worker'))
    assert actual==expected and actual['state']=='completed_with_gaps'
    second=create_engine(postgres.url)
    try:
        reopened=PostgresStore(second,manifest)
        assert reopened.public_result()==actual
        assert reopened.snapshot.batches==stored.snapshot.batches
        assert reopened.snapshot.manifest.universe_sha256==lesson.selection().snapshot.content_hash
    finally:second.dispose()


def test_adapter_page_acknowledgement_loss_replays_once_after_reopen(postgres):
    migrate(postgres);adapter=lesson.adapter(count=1,days=1,include_indexes=False)
    manifest=adapter.manifest('synthetic:ack',limits=lesson.JOB_LIMITS)
    stored=PostgresStore(postgres,manifest,create=True);token=stored.claim('synthetic:worker')
    version=stored.snapshot.version;spec=adapter.series[0];page=adapter.fetch(spec.request,0)
    with pytest.raises(SimulatedCrash):stored.commit(spec.key,page,token=token,expected_version=version,
        request=spec.request,retry_index=0,crash='after_commit')
    second=create_engine(postgres.url)
    try:
        reopened=PostgresStore(second,manifest);before=reopened.snapshot
        replay=reopened.commit(spec.key,page,token=token,expected_version=version,request=spec.request,retry_index=0)
        assert replay==before.batches[0].result and reopened.snapshot==before
        assert run(reopened,adapter,token)['state']=='completed'
        assert reopened.public_result()['counters']['accepted_unique_record_count']==1
        other=adapter.manifest('synthetic:ack',limits=lesson.JOB_LIMITS,allow_partial=True)
        with pytest.raises(Rejected,match='identity_conflict'):PostgresStore(second,other)
    finally:second.dispose()
