"""Coordination contracts layered over the merged market/universe contracts."""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from ingestion.market.bars import DailyObservation
from ingestion.market.indexes import IndexReference
from ingestion.market.providers import ProviderPage, RequestParameters
from ingestion.market.sessions import CoverageReport
from ingestion.sec.contracts import Sha256
from ingestion.universe.identity import Contract, Id, Instrument, Time, content_hash
from ingestion.universe.snapshot import UniverseSnapshot


class JobContract(Contract):
    schema_version: Literal['job-contract-v1'] = 'job-contract-v1'


class Limits(JobContract):
    max_instruments: int = Field(strict=True, ge=1, le=1000)
    max_date_span: int = Field(strict=True, ge=1, le=40000)
    max_pages_per_instrument: int = Field(strict=True, ge=1, le=10000)
    max_records_per_page: int = Field(strict=True, ge=1, le=10000)
    max_total_records: int = Field(strict=True, ge=1, le=100000)
    max_attempts: int = Field(strict=True, ge=1, le=1000)
    max_retries_per_page: int = Field(strict=True, ge=0, le=100)
    max_continuation_depth: int = Field(strict=True, ge=0, le=10000)
    max_checkpoint_bytes: int = Field(strict=True, ge=1, le=100000000)
    max_steps: int = Field(strict=True, ge=1, le=100000)


def configuration_hash(limits, allow_partial, lease_seconds, backoff_seconds, coverage_basis) -> str:
    return content_hash((Limits.model_validate(limits), allow_partial, lease_seconds, backoff_seconds, coverage_basis))


class Series(JobContract):
    instrument: Instrument
    request: RequestParameters
    coverage: CoverageReport  # initial explicit gaps + existing calendar/lifetime semantics
    required: bool
    reference: IndexReference | None = None

    @model_validator(mode='after')
    def coherent(self):
        if self.instrument.instrument_id != self.request.instrument_id or self.coverage.instrument_id != self.request.instrument_id:
            raise ValueError('series_identity_mismatch')
        if self.coverage.window != self.request.window or any(d.outcome == 'present' for d in self.coverage.days):
            raise ValueError('initial_coverage_must_describe_unfilled_request')
        if self.instrument.kind == 'index':
            if self.reference is None or self.reference.instrument != self.instrument:
                raise ValueError('index_requires_existing_reference_contract')
            if (self.reference.calendar_id, self.reference.calendar_version, self.reference.timezone) != (self.coverage.calendar.calendar_id, self.coverage.calendar.calendar_version, self.coverage.calendar.timezone):
                raise ValueError('index_calendar_mismatch')
        elif self.reference is not None:
            raise ValueError('stock_cannot_have_index_reference')
        if self.request.cursor_ref is not None:
            raise ValueError('manifest_request_must_start_without_cursor')
        return self

    @property
    def key(self) -> str:
        return self.instrument.instrument_id


class Manifest(JobContract):
    job_id: Id
    job_type: Literal['offline_market_fixture'] = 'offline_market_fixture'
    parent_job_id: Id | None = None
    recovery_of: Id | None = None
    provider: Id
    feed: Id
    dataset: Id
    dataset_version: Id
    universe_sha256: Sha256
    source_sha256: Sha256
    config_sha256: Sha256
    code_version: Id
    series: tuple[Series, ...] = Field(min_length=1, max_length=1000)
    limits: Limits
    allow_partial: bool
    coverage_basis: Literal['active_lifetime', 'requested_window']
    lease_seconds: int = Field(strict=True, ge=1, le=3600)
    backoff_seconds: int = Field(strict=True, ge=1, le=3600)

    @model_validator(mode='after')
    def unique(self):
        if len({s.key for s in self.series}) != len(self.series):
            raise ValueError('duplicate_job_series')
        if self.config_sha256 != configuration_hash(self.limits, self.allow_partial, self.lease_seconds, self.backoff_seconds, self.coverage_basis):
            raise ValueError('configuration_checksum_mismatch')
        if self.job_id in (self.parent_job_id, self.recovery_of):
            raise ValueError('job_cannot_reference_itself')
        return self

    def verify_universe(self, snapshot: UniverseSnapshot) -> None:
        if snapshot.content_hash != self.universe_sha256:
            raise ValueError('universe_checksum_mismatch')
        members = {m.instrument_id for m in snapshot.members}
        instruments = {i.instrument_id: i for i in snapshot.catalog.instruments}
        if any(s.instrument.kind == 'common_stock' and (s.key not in members or instruments.get(s.key) != s.instrument) for s in self.series):
            raise ValueError('job_equity_not_in_supplied_universe')


State = Literal['pending', 'running', 'paused', 'retryable', 'completed', 'completed_with_gaps', 'failed', 'cancelled']
TERMINAL = frozenset(('completed', 'completed_with_gaps', 'failed', 'cancelled'))
Code = Literal['ok', 'invalid_contract', 'provider_unavailable', 'payload_mismatch', 'coverage_gap',
               'retry_scheduled', 'retry_exhausted', 'unsupported', 'quarantined',
               'limit_instruments', 'limit_date_span', 'limit_pages', 'limit_records_page',
               'limit_total_records', 'limit_attempts', 'limit_retries', 'limit_depth',
               'limit_checkpoint_bytes', 'limit_steps', 'stale_lease', 'version_conflict',
               'identity_conflict', 'replay_conflict', 'cursor_cycle', 'not_due', 'illegal_transition']


class Attempt(JobContract):
    number: int = Field(ge=1)
    worker: Id
    token: int = Field(ge=1)
    started_at: Time
    finished_at: Time | None = None


class Lease(JobContract):
    worker: Id
    token: int = Field(ge=1)
    heartbeat_at: Time
    expires_at: Time


class Progress(JobContract):
    series_key: Id
    cursor_ref: Id | None = None
    cursors: tuple[Id, ...] = ()
    pages: int = Field(default=0, strict=True, ge=0)
    retries: int = Field(default=0, strict=True, ge=0)
    done: bool = False
    code: Code = 'ok'


class CommitResult(JobContract):
    batch_key: Sha256
    version: int
    accepted_revisions: int
    cursor_ref: Id | None


class CommittedBatch(JobContract):
    series_key: Id
    page: ProviderPage | None
    request: RequestParameters
    retry_index: int = Field(strict=True, ge=0)
    request_identity: Sha256
    response_identity: Sha256
    batch_key: Sha256
    payload_hex: str = Field(pattern=r'^(?:[a-f0-9]{2})*$')
    observed_count: int = Field(ge=0)
    records: tuple[DailyObservation, ...] = ()
    code: Code
    result: CommitResult


class Snapshot(JobContract):
    manifest: Manifest
    state: State = 'pending'
    code: Code = 'ok'
    version: int = Field(default=0, strict=True, ge=0)
    steps: int = Field(default=0, strict=True, ge=0)
    attempts: tuple[Attempt, ...] = ()
    lease: Lease | None = None
    next_retry_at: Time | None = None
    progress: tuple[Progress, ...]
    batches: tuple[CommittedBatch, ...] = ()


    @model_validator(mode='after')
    def coordination(self):
        if tuple(p.series_key for p in self.progress) != tuple(s.key for s in self.manifest.series):
            raise ValueError('checkpoint_series_mismatch')
        if self.steps != len(self.batches) or self.version < self.steps + len(self.attempts):
            raise ValueError('checkpoint_counter_mismatch')
        if len({b.batch_key for b in self.batches}) != len(self.batches):
            raise ValueError('duplicate_committed_batch')
        versions = [b.result.version for b in self.batches]
        if versions != sorted(set(versions)) or any(v > self.version for v in versions):
            raise ValueError('invalid_commit_versions')
        for batch in self.batches:
            identity = request_identity(self.manifest, batch.request)
            if batch.series_key != batch.request.instrument_id or identity != batch.request_identity:
                raise ValueError('batch_request_mismatch')
            if batch.batch_key != content_hash((self.manifest.content_hash, identity, batch.retry_index)) or batch.result.batch_key != batch.batch_key:
                raise ValueError('batch_key_mismatch')
            if batch.observed_count < len(batch.records) or (batch.code != 'ok' and batch.records):
                raise ValueError('invalid_accepted_records')
        derived = {s.key: Progress(series_key=s.key) for s in self.manifest.series}
        specs = {s.key: s for s in self.manifest.series}
        for batch in self.batches:
            if batch.series_key not in derived:
                raise ValueError('unknown_batch_series')
            previous = derived[batch.series_key]
            expected = specs[batch.series_key].request.model_dump() | {'cursor_ref': previous.cursor_ref}
            if batch.request != RequestParameters.model_validate(expected) or batch.retry_index != previous.retries or previous.done:
                raise ValueError('checkpoint_request_sequence_mismatch')
            cursor, cursors, pages = previous.cursor_ref, previous.cursors, previous.pages
            retrying = batch.code in ('provider_unavailable', 'retry_scheduled')
            retries, done = previous.retries + 1 if retrying else 0, not retrying
            if batch.code == 'ok':
                if batch.page is None or batch.page.outcome not in ('complete', 'partial'):
                    raise ValueError('successful_batch_requires_page')
                cursor = batch.page.next_cursor_ref
                if cursor is not None:
                    if cursor in cursors:
                        raise ValueError('checkpoint_cursor_cycle')
                    cursors += (cursor,)
                pages += 1
                done = cursor is None
            if batch.result.cursor_ref != cursor:
                raise ValueError('commit_cursor_mismatch')
            derived[batch.series_key] = Progress(series_key=batch.series_key, cursor_ref=cursor,
                cursors=cursors, pages=pages, retries=retries, done=done, code=batch.code)
        if self.progress != tuple(derived[s.key] for s in self.manifest.series):
            raise ValueError('checkpoint_progress_mismatch')
        if [a.number for a in self.attempts] != list(range(1, len(self.attempts) + 1)):
            raise ValueError('invalid_attempt_sequence')
        tokens = [a.token for a in self.attempts]
        if tokens != sorted(set(tokens)):
            raise ValueError('invalid_fencing_sequence')
        for attempt in self.attempts:
            if attempt.finished_at is not None and attempt.finished_at < attempt.started_at:
                raise ValueError('invalid_attempt_time')
        if self.lease is not None:
            if not self.attempts or (self.lease.token, self.lease.worker) != (self.attempts[-1].token, self.attempts[-1].worker):
                raise ValueError('lease_attempt_mismatch')
            if self.lease.expires_at <= self.lease.heartbeat_at:
                raise ValueError('invalid_lease_interval')
        if self.state == 'running' and (self.lease is None or self.attempts[-1].finished_at is not None):
            raise ValueError('running_requires_open_attempt')
        if self.state != 'running' and self.attempts and self.attempts[-1].finished_at is None:
            raise ValueError('stopped_job_requires_closed_attempt')
        if (self.state == 'retryable') != (self.next_retry_at is not None):
            raise ValueError('invalid_retry_schedule')
        if self.state in ('completed', 'completed_with_gaps') and any(not p.done for p in self.progress):
            raise ValueError('incomplete_job_cannot_be_completed')
        return self


def request_identity(manifest: Manifest, request: RequestParameters) -> str:
    return content_hash((manifest.provider, manifest.feed, manifest.dataset, manifest.dataset_version, request))
