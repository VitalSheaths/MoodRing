from dataclasses import replace
import json

import pytest
from pydantic import ValidationError

from ingestion.jobs.contracts import Manifest, configuration_hash, request_identity
from ingestion.jobs.offline import FakeStore, Rejected, SimulatedCrash, SuppliedPage, run
from ingestion.market.bars import DailyObservation
from ingestion.market.providers import ProviderPage
from ingestion.sec.contracts import payload_digest
from job_fixtures import Clock, Provider, manifest, one, series
from market_fixtures import seed


def setup(m=None):
    clock = Clock(); store = FakeStore(m or one(), clock)
    return store, clock, store.claim('worker:one')


def commit(store, token, provider=None, crash=None):
    spec = store.snapshot.manifest.series[0]
    request = spec.request
    page = (provider or Provider()).fetch(request, 0)
    return store.commit(spec.key, page, token=token, expected_version=store.snapshot.version,
                        request=request, retry_index=0, crash=crash)


def restored(store, clock):
    blob = store.export()
    return FakeStore.restore(blob, store.snapshot.manifest, clock, payload_digest(blob))


def test_six_series_policy_retry_then_gaps_and_aggregation():
    store, clock, token = setup(manifest())
    result = run(store, Provider(), token)
    assert result['state'] == 'retryable'
    assert result['counters']['accepted_unique_record_count'] == 3
    assert result['counters']['retryable_count'] == 1
    with pytest.raises(Rejected, match='not_due'): store.claim('worker:early')
    clock.advance(5)
    store = restored(store, clock)
    result = run(store, Provider(), store.claim('worker:two'))
    assert result['state'] == 'completed_with_gaps'
    c = result['counters']
    assert c == dict(request_count=7, attempt_count=2, page_count=4, transport_response_count=7,
                     raw_artifact_count=4, accepted_unique_record_count=4, accepted_revision_count=4,
                     duplicate_count=0, quarantined_count=1, retryable_count=1, unsupported_count=1,
                     missing_session_count=2)
    assert all(s['fully_covered'] for s in result['series'] if s['required'])
    assert sum(s['unsupported_optional'] for s in result['series']) == 1
    assert sum(s['quarantined'] for s in result['series']) == 1
    assert sum(s['unresolved_provider_gaps'] for s in result['series']) == 1


@pytest.mark.parametrize('mode,partial,state', [('equity', False, 'completed'), ('quarantine', False, 'failed'),
                                             ('quarantine', True, 'completed_with_gaps'), ('always_retry', False, 'retryable')])
def test_terminal_policy_and_required_coverage(mode, partial, state):
    store, _, token = setup(one(allow_partial=partial))
    result = run(store, Provider({'equity': mode}), token)
    assert result['state'] == state
    assert store.snapshot.progress[0].cursor_ref is None
    if mode == 'quarantine':
        assert result['series'][0]['missing_required_sessions'] == 1
        assert result['counters']['accepted_unique_record_count'] == 0


@pytest.mark.parametrize('boundary', ['before_artifact', 'after_artifact', 'during_commit'])
def test_precommit_crashes_leave_checkpoint_records_and_counters_unchanged(boundary):
    store, clock, token = setup()
    before, public = store.snapshot, store.public_result()
    with pytest.raises(SimulatedCrash): commit(store, token, crash=boundary)
    assert store.snapshot == before and store.public_result() == public
    if boundary != 'before_artifact': assert store._artifacts  # allowed orphan, not accepted evidence
    rebuilt = restored(store, clock)
    assert rebuilt.snapshot == before
    result = run(rebuilt, Provider(), token)
    assert result['state'] == 'completed' and result['counters']['accepted_revision_count'] == 1


def test_after_commit_before_ack_replays_original_under_new_lease():
    store, clock, token = setup()
    version = store.snapshot.version
    spec = store.snapshot.manifest.series[0]; supplied = Provider().fetch(spec.request, 0)
    with pytest.raises(SimulatedCrash):
        store.commit(spec.key, supplied, token=token, expected_version=version, request=spec.request, retry_index=0, crash='after_commit')
    original = store.snapshot.batches[0].result
    clock.advance()
    store = restored(store, clock); new_token = store.claim('worker:two')
    before = store.snapshot
    replay = store.commit(spec.key, supplied, token=new_token, expected_version=version, request=spec.request, retry_index=0)
    assert replay == original and store.snapshot == before
    assert store.public_result()['counters']['accepted_revision_count'] == 1
    with pytest.raises(Rejected, match='stale_lease'):
        store.commit(spec.key, supplied, token=token, expected_version=version, request=spec.request, retry_index=0)


def test_after_ack_restart_observes_unchanged_commit_and_replay_conflict_rejects():
    store, clock, token = setup()
    result = commit(store, token)
    assert restored(store, clock).snapshot == store.snapshot
    spec = store.snapshot.manifest.series[0]; supplied = Provider().fetch(spec.request, 0)
    before = store.snapshot
    assert store.commit(spec.key, supplied, token=token, expected_version=0, request=spec.request, retry_index=0) == result
    with pytest.raises(Rejected, match='replay_conflict'):
        store.commit(spec.key, replace(supplied, payload=b'changed'), token=token, expected_version=0, request=spec.request, retry_index=0)
    assert store.snapshot == before


def test_fencing_heartbeat_expiry_and_compare_and_swap():
    store, clock, token = setup()
    version = store.snapshot.version
    store.heartbeat(token)
    spec = store.snapshot.manifest.series[0]; page = Provider().fetch(spec.request, 0)
    with pytest.raises(Rejected, match='version_conflict'):
        store.commit(spec.key, page, token=token, expected_version=version, request=spec.request, retry_index=0)
    with pytest.raises(Rejected, match='stale_lease'): store.claim('worker:competitor')
    clock.advance()
    with pytest.raises(Rejected, match='stale_lease'): store.heartbeat(token)
    new = store.claim('worker:replacement')
    assert new > token and store.snapshot.attempts[0].finished_at is not None
    with pytest.raises(Rejected, match='stale_lease'): commit(store, token)
    assert run(store, Provider(), new)['state'] == 'completed'


@pytest.mark.parametrize('target', ['pending', 'completed', 'completed_with_gaps', 'invented'])
def test_invalid_or_unearned_transition_rejects(target):
    store, _, token = setup()
    with pytest.raises(Rejected, match='illegal_transition'): store.transition(target, token)


def test_pause_resume_cancel_terminal_immutability_and_recovery_link():
    store, clock, token = setup()
    store.transition('paused', token)
    token = store.claim('worker:two')
    store.transition('cancelled', token)
    before = store.snapshot
    with pytest.raises(Rejected): store.claim('worker:three')
    with pytest.raises(Rejected): store.transition('running', token)
    assert store.snapshot == before
    recovery = one(job_id='job:recovery', recovery_of=before.manifest.job_id)
    assert FakeStore(recovery, clock).snapshot.state == 'pending'


@pytest.mark.parametrize('field', ['source_sha256', 'universe_sha256', 'config_sha256', 'provider', 'dataset', 'code_version'])
def test_resume_manifest_binding(field):
    store, clock, token = setup(); commit(store, token)
    blob = store.export(); data = store.snapshot.manifest.model_dump()
    if field == 'config_sha256':
        data['backoff_seconds'] += 1
        data[field] = configuration_hash(data['limits'], data['allow_partial'], data['lease_seconds'], data['backoff_seconds'], data['coverage_basis'])
    else:
        data[field] = 'f' * 64 if field.endswith('sha256') else 'changed'
    with pytest.raises(Rejected, match='invalid_contract'):
        FakeStore.restore(blob, Manifest.model_validate(data), clock, payload_digest(blob))


def test_snapshot_checksum_detects_corruption():
    store, clock, _ = setup(); blob = store.export()
    with pytest.raises(Rejected, match='identity_conflict'):
        FakeStore.restore(blob + b' ', store.snapshot.manifest, clock, payload_digest(blob))


@pytest.mark.parametrize('field', ['provider', 'dataset', 'dataset_version', 'feed'])
def test_request_identity_changes_for_source_dimensions(field):
    m = one(); request = m.series[0].request
    changed = Manifest.model_validate(m.model_dump() | {field: 'different'})
    assert request_identity(m, request) != request_identity(changed, request)


@pytest.mark.parametrize('dimension', ['window', 'calendar', 'adjustment'])
def test_request_identity_changes_for_request_dimensions(dimension):
    m = one(); request = m.series[0].request; data = request.model_dump()
    if dimension == 'window': data['window']['end'] = '2026-03-10'
    if dimension == 'calendar': data['window']['calendar_version'] = 'changed'
    if dimension == 'adjustment': data['adjustment'] = 'split_adjusted'
    from ingestion.market.providers import RequestParameters
    assert request_identity(m, request) != request_identity(m, RequestParameters.model_validate(data))


def two_records_provider(request, retry_index):
    supplied = Provider().fetch(request, retry_index)
    first = supplied.observations[0]
    second = DailyObservation.model_validate(first.model_dump() | {'close': '101', 'supersedes': first.revision_id})
    page = ProviderPage.model_validate(supplied.page.model_dump() | {'observation_checksums': [first.semantic_checksum, second.semantic_checksum]})
    return SuppliedPage(page, supplied.payload, (first, second))


class TwoRecords:
    fetch = staticmethod(two_records_provider)


def with_limits(**changes):
    m = one(); return one(limits=m.limits.model_dump() | changes)


@pytest.mark.parametrize('limits,provider,code', [
    ({'max_pages_per_instrument': 1}, Provider({'equity': 'pages'}), 'limit_pages'),
    ({'max_records_per_page': 1}, TwoRecords(), 'limit_records_page'),
    ({'max_total_records': 1}, TwoRecords(), 'limit_total_records'),
    ({'max_retries_per_page': 0}, Provider({'equity': 'always_retry'}), 'limit_retries'),
    ({'max_continuation_depth': 0}, Provider({'equity': 'pages'}), 'limit_depth'),
    ({'max_steps': 1}, Provider({'equity': 'pages'}), 'limit_steps'),
])
def test_finite_runtime_bounds(limits, provider, code):
    store, _, token = setup(with_limits(**limits))
    result = run(store, provider, token)
    assert result['state'] == 'failed' and result['code'] == code
    if code in ('limit_pages', 'limit_steps'):
        assert len(store.snapshot.batches) == 1 and store.snapshot.progress[0].cursor_ref == 'cursor:two'
    else:
        assert not store.snapshot.batches and store.snapshot.progress[0].cursor_ref is None


def test_instrument_date_and_attempt_bounds():
    m = manifest(); m = manifest(limits=m.limits.model_dump() | {'max_instruments': 1})
    store, _, token = setup(m); provider = Provider()
    assert run(store, provider, token)['code'] == 'limit_instruments' and not provider.calls
    m = one(); data = m.model_dump(); spec = data['series'][0]
    spec['request']['window']['start'] = '2026-03-06'
    spec['coverage']['window'] = spec['request']['window']
    spec['coverage']['calendar'] = seed()['calendar']
    spec['coverage']['days'] = [dict(session_date=d, outcome=o) for d, o in [
        ('2026-03-06','provider_gap'),('2026-03-07','non_session'),('2026-03-08','non_session'),('2026-03-09','provider_gap')]]
    data['limits']['max_date_span'] = 1
    data['config_sha256'] = configuration_hash(data['limits'], data['allow_partial'], data['lease_seconds'], data['backoff_seconds'], data['coverage_basis'])
    store, _, token = setup(Manifest.model_validate(data)); provider = Provider()
    assert run(store, provider, token)['code'] == 'limit_date_span' and not provider.calls
    store, clock, token = setup(with_limits(max_attempts=1))
    run(store, Provider({'equity': 'always_retry'}), token)
    before = store.snapshot.batches
    clock.advance(5)
    with pytest.raises(Rejected, match='limit_attempts'): store.claim('worker:two')
    assert store.snapshot.state == 'failed' and store.snapshot.batches == before


def test_serialized_limit_checks_candidate_before_commit():
    m = one(); baseline = FakeStore(m, Clock())
    m = with_limits(max_checkpoint_bytes=len(baseline.export()) + 1000)
    store, _, token = setup(m)
    assert run(store, Provider(), token)['code'] == 'limit_checkpoint_bytes'
    assert not store.snapshot.batches


def test_revision_and_duplicate_counters_are_separate():
    store, _, token = setup()
    result = run(store, TwoRecords(), token)
    assert result['counters']['accepted_unique_record_count'] == 1
    assert result['counters']['accepted_revision_count'] == 2
    store, _, token = setup()
    result = run(store, Provider({'equity': 'pages'}), token)
    assert result['counters']['page_count'] == 2 and result['counters']['duplicate_count'] == 1
    assert result['counters']['accepted_revision_count'] == 1


def test_provider_exception_sanitization_and_fixture_boundary():
    class Unsafe:
        def fetch(self, *_):
            raise RuntimeError('TEST_SENTINEL Authorization Bearer /private/internal signed?token=hidden TEST_CREDENTIAL TEST_PRIVATE_PARAMETERS TEST_RESPONSE_METADATA https://fake.invalid/?signature=TEST_SIGNATURE')
    store, _, token = setup()
    result = run(store, Unsafe(), token)
    text = json.dumps(result)
    for fragment in ('TEST_SENTINEL', 'Authorization', 'Bearer', '/private', 'token=', 'request_ref', 'source_ref', 'payload_hex', 'TEST_CREDENTIAL', 'TEST_PRIVATE_PARAMETERS', 'TEST_RESPONSE_METADATA', 'TEST_SIGNATURE'):
        assert fragment not in text
    assert result['code'] == 'retry_scheduled' and result['counters']['transport_response_count'] == 0
    from pathlib import Path
    fixture = (Path(__file__).parent / 'fixtures/jobs/policy.json').read_text().lower()
    for fragment in ('authorization', 'api_key', 'password', 'token=', '/users/', 'https://'):
        assert fragment not in fixture


@pytest.mark.parametrize('available', [None, '2026-03-09T20:02:00Z'])
def test_unknown_availability_and_future_evidence_cannot_be_accepted(available):
    class Unknown:
        def fetch(self, request, retry_index):
            supplied = Provider().fetch(request, retry_index)
            data = supplied.observations[0].model_dump()
            data['receipt']['evidence']['available_at'] = available
            observation = DailyObservation.model_validate(data)
            page = ProviderPage.model_validate(supplied.page.model_dump() | {'receipt': observation.receipt})
            return SuppliedPage(page, supplied.payload, (observation,))
    store, _, token = setup()
    result = run(store, Unknown(), token)
    assert result['state'] == 'failed' and result['counters']['accepted_revision_count'] == 0
    assert result['counters']['quarantined_count'] == 1


def test_checkpoint_size_also_bounds_initialization_and_lease_updates():
    with pytest.raises(Rejected, match='limit_checkpoint_bytes'):
        FakeStore(with_limits(max_checkpoint_bytes=1), Clock())
    m = one(); clock = Clock(); initial = FakeStore(m, clock)
    # Permit the manifest/checkpoint, but not an additional attempt/lease envelope.
    m = with_limits(max_checkpoint_bytes=len(initial.export()) + 20)
    store = FakeStore(m, clock); before = store.snapshot
    with pytest.raises(Rejected, match='limit_checkpoint_bytes'): store.claim('worker:one')
    assert store.snapshot == before


def test_reconstruction_after_committed_page_matches_uninterrupted_run():
    full, _, full_token = setup()
    run(full, Provider({'equity': 'pages'}), full_token)
    partial, clock, token = setup()
    commit(partial, token, Provider({'equity': 'pages'}))
    rebuilt = restored(partial, clock)
    run(rebuilt, Provider({'equity': 'pages'}), token)
    assert rebuilt.snapshot == full.snapshot
    before = rebuilt.export()
    assert run(rebuilt, Provider(), token)['state'] == 'completed'
    assert rebuilt.export() == before


def test_cycle_and_changed_request_do_not_advance_checkpoint():
    class Cycle:
        def fetch(self, request, retry_index):
            supplied = Provider().fetch(request, retry_index)
            next_cursor = {None: 'cursor:two', 'cursor:two': 'cursor:three', 'cursor:three': 'cursor:two'}[request.cursor_ref]
            page = ProviderPage.model_validate(supplied.page.model_dump() | {'outcome': 'partial', 'next_cursor_ref': next_cursor})
            return replace(supplied, page=page)
    store, _, token = setup()
    result = run(store, Cycle(), token)
    assert result['code'] == 'cursor_cycle'
    assert len(store.snapshot.batches) == 2 and store.snapshot.progress[0].cursor_ref == 'cursor:three'
    store, _, token = setup(); spec = store.snapshot.manifest.series[0]
    supplied = Provider().fetch(spec.request, 0)
    changed = spec.request.model_dump(); changed['symbol'] = 'DIFFERENT'
    from ingestion.market.providers import RequestParameters
    before = store.snapshot
    with pytest.raises(Rejected, match='identity_conflict'):
        store.commit(spec.key, supplied, token=token, expected_version=before.version,
                     request=RequestParameters.model_validate(changed), retry_index=0)
    assert store.snapshot == before


def test_concurrent_commits_compare_expected_version_atomically():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    roles = [dict(role='equity', required=True), dict(role='index', required=True)]
    store, _, token = setup(manifest(roles)); version = store.snapshot.version
    barrier = Barrier(2)
    def race(spec):
        page = Provider().fetch(spec.request, 0)
        barrier.wait(timeout=5)
        try:
            return store.commit(spec.key, page, token=token, expected_version=version, request=spec.request, retry_index=0)
        except Rejected as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(race, store.snapshot.manifest.series))
    assert results.count('version_conflict') == 1
    assert len(store.snapshot.batches) == 1
    assert store.public_result()['counters']['accepted_unique_record_count'] == 1


def test_restore_rejects_inconsistent_cursor_even_with_recomputed_transport_digest():
    store, clock, token = setup(); commit(store, token)
    data = json.loads(store.export()); data['progress'][0]['cursor_ref'] = 'cursor:invented'
    blob = json.dumps(data).encode()
    with pytest.raises(Rejected, match='invalid_contract'):
        FakeStore.restore(blob, store.snapshot.manifest, clock, payload_digest(blob))


def test_cumulative_record_bound_preserves_last_committed_cursor():
    store, _, token = setup(with_limits(max_total_records=1))
    result = run(store, Provider({'equity': 'pages'}), token)
    assert result['code'] == 'limit_total_records'
    assert result['counters']['accepted_revision_count'] == 1
    assert store.snapshot.progress[0].cursor_ref == 'cursor:two'


def test_retry_budget_exhaustion_preserves_previous_commits():
    store, clock, token = setup(with_limits(max_retries_per_page=1))
    assert run(store, Provider({'equity': 'always_retry'}), token)['state'] == 'retryable'
    before = store.snapshot.batches
    clock.advance(5)
    result = run(store, Provider({'equity': 'always_retry'}), store.claim('worker:retry'))
    assert result['state'] == 'failed' and result['code'] == 'limit_retries'
    assert store.snapshot.batches == before and store.snapshot.progress[0].cursor_ref is None


def test_merged_universe_binding_and_configuration_hash_are_verified():
    from job_fixtures import universe_snapshot
    m = one(); m.verify_universe(universe_snapshot())
    data = m.model_dump(); data['config_sha256'] = '0' * 64
    with pytest.raises(ValidationError, match='configuration_checksum_mismatch'):
        Manifest.model_validate(data)
    data = m.model_dump(); data['universe_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='universe_checksum_mismatch'):
        Manifest.model_validate(data).verify_universe(universe_snapshot())


def test_unredacted_response_metadata_is_rejected_and_not_projected():
    class Unredacted:
        def fetch(self, request, retry_index):
            supplied = Provider().fetch(request, retry_index)
            data = supplied.page.model_dump()
            data['authorization'] = 'TEST_ONLY_PRIVATE_HEADER'
            data['private_parameters'] = {'secret': 'TEST_ONLY_PARAMETER'}
            data['response_metadata'] = 'TEST_ONLY_UNREDACTED'
            return ProviderPage.model_validate(data)  # typed boundary rejects unknown fields
    store, _, token = setup()
    result = run(store, Unredacted(), token)
    assert result['state'] == 'retryable'
    assert all(text not in json.dumps(result) for text in ('TEST_ONLY', 'authorization', 'private_parameters', 'response_metadata'))
    assert all(batch.page is None for batch in store.snapshot.batches)


def test_outside_declared_active_lifetime_cannot_be_committed_as_present():
    m = one(); data = m.model_dump()
    data['series'][0]['coverage']['active_from'] = '2026-03-10'
    data['series'][0]['coverage']['days'][0]['outcome'] = 'pre_listing'
    store, _, token = setup(Manifest.model_validate(data))
    result = run(store, Provider(), token)
    assert result['counters']['accepted_revision_count'] == 0
    assert result['counters']['quarantined_count'] == 1
    assert store.snapshot.progress[0].cursor_ref is None


def test_requested_window_policy_cannot_claim_full_history_from_active_only_coverage():
    for basis, expected in [('active_lifetime', 'completed'), ('requested_window', 'failed')]:
        m = one(coverage_basis=basis); data = m.model_dump(); spec = data['series'][0]
        spec['request']['window']['start'] = '2026-03-06'
        spec['coverage']['window'] = spec['request']['window']
        spec['coverage']['active_from'] = '2026-03-09'
        spec['coverage']['calendar'] = seed()['calendar']
        spec['coverage']['days'] = [dict(session_date=d, outcome=o) for d, o in [
            ('2026-03-06','pre_listing'), ('2026-03-07','non_session'), ('2026-03-08','non_session'), ('2026-03-09','provider_gap')]]
        store, _, token = setup(Manifest.model_validate(data))
        result = run(store, Provider(), token)
        assert result['state'] == expected
        assert result['series'][0]['active_lifetime_complete']
        assert not result['series'][0]['requested_window_complete']


def test_restore_reverifies_committed_raw_bytes():
    store, clock, token = setup(); commit(store, token)
    data = json.loads(store.export()); data['batches'][0]['payload_hex'] = b'corrupt'.hex()
    blob = json.dumps(data).encode()
    with pytest.raises(Rejected, match='invalid_contract'):
        FakeStore.restore(blob, store.snapshot.manifest, clock, payload_digest(blob))


def test_orphan_artifacts_respect_the_same_finite_memory_budget():
    store, _, token = setup(with_limits(max_checkpoint_bytes=100000))
    spec = store.snapshot.manifest.series[0]; supplied = Provider().fetch(spec.request, 0)
    before = store.snapshot
    with pytest.raises(SimulatedCrash):
        store.commit(spec.key, replace(supplied, payload=b'a' * 50001), token=token, expected_version=before.version,
                     request=spec.request, retry_index=0, crash='after_artifact')
    with pytest.raises(Rejected, match='limit_checkpoint_bytes'):
        store.commit(spec.key, replace(supplied, payload=b'b' * 50001), token=token, expected_version=before.version,
                     request=spec.request, retry_index=0, crash='after_artifact')
    assert store.snapshot == before and len(store._artifacts) == 1
    assert store.public_result()['counters']['raw_artifact_count'] == 0
