"""Single-process transactional fake. No network, SQL, files, timers or threads started."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import threading
from typing import Callable, Protocol

from ingestion.market.bars import DailyObservation, validate_revisions
from ingestion.market.providers import ProviderPage, RequestParameters
from ingestion.market.sessions import CoverageReport
from ingestion.sec.contracts import payload_digest, utc
from ingestion.universe.identity import content_hash
from .contracts import (Attempt, Code, CommittedBatch, CommitResult, Lease, Manifest, Progress,
                        Snapshot, TERMINAL, request_identity)


class Rejected(Exception):
    """Only fixed internal codes cross the public result boundary."""
    def __init__(self, code: Code):
        self.code = code
        super().__init__(code)


class SimulatedCrash(Exception):
    pass


@dataclass(frozen=True)
class SuppliedPage:
    page: ProviderPage
    payload: bytes
    observations: tuple[DailyObservation, ...] = ()


class SyntheticProvider(Protocol):
    def fetch(self, request: RequestParameters, retry_index: int) -> SuppliedPage: ...


def _replace(model, **changes):
    return type(model).model_validate(model.model_dump() | changes)


def _response(supplied: SuppliedPage | None) -> str:
    return content_hash(None if supplied is None else
                        (supplied.page, payload_digest(supplied.payload), supplied.observations))


class FakeStore:
    def __init__(self, manifest: Manifest, clock: Callable):
        self.clock = clock
        self._lock = threading.RLock()
        self._artifacts: dict[str, bytes] = {}
        self._snapshot = Snapshot(manifest=manifest, progress=tuple(Progress(series_key=s.key) for s in manifest.series))
        if len(self._snapshot.model_dump_json().encode()) > manifest.limits.max_checkpoint_bytes:
            raise Rejected('limit_checkpoint_bytes')

    @property
    def snapshot(self) -> Snapshot:
        return self._snapshot

    def _guard(self, token: int):
        s = self.snapshot
        now = utc(self.clock())
        if s.state != 'running' or s.lease is None or token != s.lease.token or now >= s.lease.expires_at:
            raise Rejected('stale_lease')

    def _set(self, **changes):
        candidate = _replace(self.snapshot, **changes)
        if len(candidate.model_dump_json().encode()) > candidate.manifest.limits.max_checkpoint_bytes:
            raise Rejected('limit_checkpoint_bytes')
        self._snapshot = candidate

    def claim(self, worker: str) -> int:
        with self._lock:
            s, now = self.snapshot, utc(self.clock())
            if s.state in TERMINAL:
                raise Rejected('illegal_transition')
            if s.state == 'running' and s.lease and now < s.lease.expires_at:
                raise Rejected('stale_lease')
            if s.next_retry_at is not None and now < s.next_retry_at:
                raise Rejected('not_due')
            if len(s.attempts) >= s.manifest.limits.max_attempts:
                self._set(state='failed', code='limit_attempts', next_retry_at=None)
                raise Rejected('limit_attempts')
            token = 1 if s.lease is None else s.lease.token + 1
            attempts = s.attempts
            if attempts and attempts[-1].finished_at is None:
                attempts = attempts[:-1] + (_replace(attempts[-1], finished_at=now),)
            attempt = Attempt(number=len(attempts) + 1, worker=worker, token=token, started_at=now)
            self._set(state='running', code='ok', version=s.version + 1, attempts=attempts + (attempt,),
                      next_retry_at=None, lease=Lease(worker=worker, token=token, heartbeat_at=now,
                                                    expires_at=now + timedelta(seconds=s.manifest.lease_seconds)))
            return token

    def heartbeat(self, token: int):
        with self._lock:
            self._guard(token)
            now = utc(self.clock())
            self._set(lease=_replace(self.snapshot.lease, heartbeat_at=now,
                                    expires_at=now + timedelta(seconds=self.snapshot.manifest.lease_seconds)),
                      version=self.snapshot.version + 1)

    def transition(self, target: str, token: int | None = None, code: Code = 'ok'):
        allowed = {'pending': {'cancelled'}, 'running': {'paused', 'retryable', 'completed', 'completed_with_gaps', 'failed', 'cancelled'},
                   'paused': {'cancelled'}, 'retryable': {'cancelled'}}
        with self._lock:
            s = self.snapshot
            if target not in allowed.get(s.state, set()):
                raise Rejected('illegal_transition')
            if s.state == 'running':
                self._guard(token)
            # Completion is derived, never a caller assertion.
            if target in ('completed', 'completed_with_gaps') and self.completion_state() != target:
                raise Rejected('illegal_transition')
            now = utc(self.clock())
            attempts = s.attempts
            if attempts and attempts[-1].finished_at is None:
                attempts = attempts[:-1] + (_replace(attempts[-1], finished_at=now),)
            retry_at = now + timedelta(seconds=s.manifest.backoff_seconds) if target == 'retryable' else None
            self._set(state=target, code=code, attempts=attempts, next_retry_at=retry_at, version=s.version + 1)

    def _limits(self, progress: Progress, supplied: SuppliedPage | None):
        s, m = self.snapshot, self.snapshot.manifest
        limits = m.limits
        tests = [(len(m.series) > limits.max_instruments, 'limit_instruments'),
                 (any((x.request.window.last_date - x.request.window.first_date).days + 1 > limits.max_date_span for x in m.series), 'limit_date_span'),
                 (s.steps >= limits.max_steps, 'limit_steps'),
                 (any(x.request.limit > limits.max_records_per_page for x in m.series), 'limit_records_page'),
                 (len(s.model_dump_json().encode()) > limits.max_checkpoint_bytes, 'limit_checkpoint_bytes'),
                 (progress.pages >= limits.max_pages_per_instrument, 'limit_pages'),
                 (len(progress.cursors) > limits.max_continuation_depth, 'limit_depth')]
        if supplied is not None:
            count = max(len(supplied.observations), len(supplied.page.observation_checksums))
            total = sum(b.observed_count for b in s.batches)
            tests += [(count > limits.max_records_per_page, 'limit_records_page'),
                      (total + count > limits.max_total_records, 'limit_total_records'),
                      (len(supplied.payload) > limits.max_checkpoint_bytes, 'limit_checkpoint_bytes')]
        for exceeded, code in tests:
            if exceeded:
                raise Rejected(code)

    def preflight(self, series_key: str):
        self._limits(next(p for p in self.snapshot.progress if p.series_key == series_key), None)

    def commit(self, series_key: str, supplied: SuppliedPage | None, *, token: int, expected_version: int,
               request: RequestParameters, retry_index: int, crash: str | None = None) -> CommitResult:
        """CAS + fencing + replay; one candidate assignment is the fake atomic commit."""
        with self._lock:
            self._guard(token)
            s, m = self.snapshot, self.snapshot.manifest
            series = next((x for x in m.series if x.key == series_key), None)
            if series is None:
                raise Rejected('identity_conflict')
            if supplied is not None and (not isinstance(supplied, SuppliedPage) or not isinstance(supplied.page, ProviderPage) or
                                         not isinstance(supplied.payload, bytes) or not all(isinstance(o, DailyObservation) for o in supplied.observations)):
                raise Rejected('invalid_contract')
            rid = request_identity(m, request)
            key = content_hash((m.content_hash, rid, retry_index))
            response = _response(supplied)
            existing = next((b for b in s.batches if b.batch_key == key), None)
            if existing is not None:
                if existing.response_identity != response:
                    raise Rejected('replay_conflict')
                return existing.result
            if expected_version != s.version:
                raise Rejected('version_conflict')
            progress = next(p for p in s.progress if p.series_key == series_key)
            expected = _replace(series.request, cursor_ref=progress.cursor_ref)
            if request != expected or retry_index != progress.retries or progress.done:
                raise Rejected('identity_conflict')
            self._limits(progress, supplied)
            if crash == 'before_artifact':
                raise SimulatedCrash()
            records, code = (), 'provider_unavailable'
            payload, page = (b'', None) if supplied is None else (supplied.payload, supplied.page)
            if supplied is not None:
                receipt = page.receipt
                if ((receipt.provider, receipt.feed, receipt.dataset, receipt.dataset_version) !=
                        (m.provider, m.feed, m.dataset, m.dataset_version) or receipt.parameters != request):
                    raise Rejected('identity_conflict')
                if receipt.responded_at > utc(self.clock()):
                    raise Rejected('invalid_contract')
                # The artifact fake can have orphans; no checkpoint refers to one until commit.
                digest = payload_digest(payload)
                if digest not in self._artifacts and sum(map(len, self._artifacts.values())) + len(payload) > m.limits.max_checkpoint_bytes:
                    raise Rejected('limit_checkpoint_bytes')
                self._artifacts[digest] = payload
                if crash == 'after_artifact':
                    raise SimulatedCrash()
                try:
                    receipt.verify_payload(payload)
                    if page.observation_checksums != tuple(o.semantic_checksum for o in supplied.observations):
                        raise ValueError('invalid_contract')
                    for observation in supplied.observations:
                        if observation.receipt != receipt or observation.instrument != series.instrument:
                            raise ValueError('invalid_contract')
                        eligible_days = {d.session_date for d in series.coverage.days if d.outcome in ('provider_gap', 'suspended', 'unsupported')}
                        if observation.session.session_date not in eligible_days or observation.session not in series.coverage.calendar.sessions or not observation.eligible(request.window.cutoff):
                            raise ValueError('invalid_contract')
                    previous = tuple(o for b in s.batches for o in b.records)
                    validate_revisions(previous + supplied.observations)
                    records = supplied.observations
                    code = {'complete': 'ok', 'partial': 'ok', 'unsupported': 'unsupported',
                            'quarantined': 'quarantined', 'retryable': 'retry_scheduled'}[page.outcome]
                except ValueError:
                    code = 'payload_mismatch' if payload_digest(payload) != receipt.evidence.payload_sha256 else 'invalid_contract'
            retrying = code in ('provider_unavailable', 'retry_scheduled')
            retries = progress.retries + 1 if retrying else 0
            if retries > m.limits.max_retries_per_page:
                raise Rejected('limit_retries')
            cursor = progress.cursor_ref
            cursors = progress.cursors
            done = not retrying
            pages = progress.pages
            if code == 'ok':
                pages += 1
                cursor = page.next_cursor_ref
                if cursor is not None:
                    if cursor in cursors:
                        raise Rejected('cursor_cycle')
                    if len(cursors) + 1 > m.limits.max_continuation_depth:
                        raise Rejected('limit_depth')
                    cursors += (cursor,)
                done = cursor is None
            updated = _replace(progress, cursor_ref=cursor, cursors=cursors, pages=pages, retries=retries, done=done, code=code)
            all_records = tuple(o for b in s.batches for o in b.records)
            before = len(validate_revisions(all_records))
            after = len(validate_revisions(all_records + records))
            result = CommitResult(batch_key=key, version=s.version + 1, accepted_revisions=after - before, cursor_ref=cursor)
            batch = CommittedBatch(series_key=series_key, page=page, request=request, retry_index=retry_index, request_identity=rid, response_identity=response,
                                   batch_key=key, payload_hex=payload.hex(), observed_count=0 if supplied is None else max(len(supplied.observations), len(supplied.page.observation_checksums)),
                                   records=records, code=code, result=result)
            candidate = _replace(s, version=s.version + 1, steps=s.steps + 1, batches=s.batches + (batch,),
                                 progress=tuple(updated if p.series_key == series_key else p for p in s.progress))
            if len(candidate.model_dump_json().encode()) > m.limits.max_checkpoint_bytes:
                raise Rejected('limit_checkpoint_bytes')
            if crash == 'during_commit':
                raise SimulatedCrash()
            self._snapshot = candidate
            if crash == 'after_commit':
                raise SimulatedCrash()
            return result

    def coverage(self) -> tuple[CoverageReport, ...]:
        reports = []
        for series in self.snapshot.manifest.series:
            progress = next(p for p in self.snapshot.progress if p.series_key == series.key)
            observations = validate_revisions(tuple(o for b in self.snapshot.batches if b.series_key == series.key for o in b.records))
            latest = {o.session.session_date: o for o in observations}
            days = []
            for day in series.coverage.days:
                if day.session_date in latest:
                    days.append(_replace(day, outcome='present', observation_checksum=latest[day.session_date].semantic_checksum))
                elif day.outcome == 'provider_gap' and progress.code == 'unsupported':
                    days.append(_replace(day, outcome='unsupported'))
                else:
                    days.append(day)
            reports.append(_replace(series.coverage, days=tuple(days)))
        return tuple(reports)

    def completion_state(self):
        if any(not p.done for p in self.snapshot.progress):
            return 'retryable'
        reports = self.coverage()
        complete = [((r.active_complete if self.snapshot.manifest.coverage_basis == 'active_lifetime' else r.requested_complete)
                     and p.code == 'ok') for r, p in zip(reports, self.snapshot.progress)]
        required_gap = any(s.required and not clean for s, clean in zip(self.snapshot.manifest.series, complete))
        any_gap = not all(complete)
        if required_gap and not self.snapshot.manifest.allow_partial:
            return 'failed'
        return 'completed_with_gaps' if any_gap else 'completed'

    def export(self) -> bytes:
        blob = self.snapshot.model_dump_json().encode()
        if len(blob) > self.snapshot.manifest.limits.max_checkpoint_bytes:
            raise Rejected('limit_checkpoint_bytes')
        return blob

    @classmethod
    def restore(cls, blob: bytes, manifest: Manifest, clock: Callable, expected_sha256: str):
        if len(blob) > manifest.limits.max_checkpoint_bytes:
            raise Rejected('limit_checkpoint_bytes')
        if payload_digest(blob) != expected_sha256:
            raise Rejected('identity_conflict')
        try:
            snapshot = Snapshot.model_validate_json(blob)
            if snapshot.manifest != manifest:
                raise ValueError('manifest_changed')
            if tuple(p.series_key for p in snapshot.progress) != tuple(s.key for s in manifest.series):
                raise ValueError('invalid_progress')
            validate_revisions(tuple(o for b in snapshot.batches for o in b.records))
            specs = {s.key: s for s in manifest.series}
            for batch in snapshot.batches:
                if batch.page is None:
                    if batch.code != 'provider_unavailable' or batch.records or batch.payload_hex:
                        raise ValueError('invalid_transport_failure')
                    continue
                receipt = batch.page.receipt
                if receipt.parameters != batch.request or (receipt.provider, receipt.feed, receipt.dataset, receipt.dataset_version) != (manifest.provider, manifest.feed, manifest.dataset, manifest.dataset_version):
                    raise ValueError('restored_receipt_mismatch')
                if batch.code in ('ok', 'retry_scheduled', 'unsupported', 'quarantined'):
                    receipt.verify_payload(bytes.fromhex(batch.payload_hex))
                if batch.code == 'ok':
                    if batch.page.observation_checksums != tuple(o.semantic_checksum for o in batch.records):
                        raise ValueError('restored_observation_mismatch')
                    spec = specs[batch.series_key]
                    eligible_days = {d.session_date for d in spec.coverage.days if d.outcome in ('provider_gap', 'suspended', 'unsupported')}
                    if any(o.receipt != receipt or o.instrument != spec.instrument or not o.eligible(batch.request.window.cutoff) or o.session not in spec.coverage.calendar.sessions or o.session.session_date not in eligible_days for o in batch.records):
                        raise ValueError('restored_record_ineligible')
        except ValueError:
            raise Rejected('invalid_contract') from None
        result = cls(manifest, clock)
        result._snapshot = snapshot
        if snapshot.state in ('completed', 'completed_with_gaps') and result.completion_state() != snapshot.state:
            raise Rejected('invalid_contract')
        result._artifacts = {payload_digest(bytes.fromhex(b.payload_hex)): bytes.fromhex(b.payload_hex) for b in snapshot.batches if b.page}
        return result

    def public_result(self) -> dict:
        s = self.snapshot
        records = tuple(o for b in s.batches for o in b.records)
        unique = validate_revisions(records)
        reports = self.coverage()
        counts = dict(request_count=len(s.batches), attempt_count=len(s.attempts),
                      page_count=sum(p.pages for p in s.progress), transport_response_count=sum(b.page is not None for b in s.batches),
                      raw_artifact_count=len({payload_digest(bytes.fromhex(b.payload_hex)) for b in s.batches if b.page}),
                      accepted_unique_record_count=len({o.observation_key for o in unique}), accepted_revision_count=len(unique),
                      duplicate_count=len(records) - len(unique),
                      quarantined_count=sum(b.code in ('quarantined', 'payload_mismatch', 'invalid_contract') for b in s.batches),
                      retryable_count=sum(b.code in ('provider_unavailable', 'retry_scheduled') for b in s.batches),
                      unsupported_count=sum(b.code == 'unsupported' for b in s.batches),
                      missing_session_count=sum(d.outcome in ('provider_gap', 'unsupported', 'suspended') for r in reports for d in r.days))
        series = []
        required_missing_codes = {'provider_gap', 'unsupported', 'suspended'}
        if s.manifest.coverage_basis == 'requested_window':
            required_missing_codes |= {'pre_listing', 'post_delisting', 'incomplete_session'}
        for spec, report, progress in zip(s.manifest.series, reports, s.progress):
            series.append(dict(series_hash=content_hash(spec.key), required=spec.required,
                               fully_covered=(report.active_complete if s.manifest.coverage_basis == 'active_lifetime' else report.requested_complete) and progress.code == 'ok',
                               active_lifetime_complete=report.active_complete, requested_window_complete=report.requested_complete,
                               partially_covered=any(d.outcome == 'present' for d in report.days) and not report.active_complete,
                               missing_required_sessions=sum(d.outcome in required_missing_codes for d in report.days) if spec.required else 0,
                               unsupported_optional=not spec.required and progress.code == 'unsupported',
                               quarantined=progress.code in ('quarantined', 'payload_mismatch', 'invalid_contract'),
                               retryable_gap=progress.code in ('provider_unavailable', 'retry_scheduled'),
                               unresolved_provider_gaps=sum(d.outcome == 'provider_gap' for d in report.days)))
        # Never expose page, worker, payload, request, provider metadata or exception strings.
        return dict(job_hash=content_hash(s.manifest.job_id), state=s.state, code=s.code, counters=counts, series=series,
                    next_retry_at=s.next_retry_at.isoformat() if s.next_retry_at else None)


def run(store: FakeStore, provider: SyntheticProvider, token: int, *, crash: str | None = None):
    """Bounded dispatch of typed fixture pages only; retry is scheduled metadata, no sleep."""
    while True:
        s = store.snapshot
        if s.state in TERMINAL:
            return store.public_result()
        pending = next((p for p in s.progress if not p.done), None)
        if pending is None:
            state = store.completion_state()
            store.transition(state, token, 'ok' if state == 'completed' else 'coverage_gap')
            return store.public_result()
        try:
            store._guard(token)
            store.preflight(pending.series_key)
            spec = next(x for x in s.manifest.series if x.key == pending.series_key)
            request = _replace(spec.request, cursor_ref=pending.cursor_ref)
            try:
                supplied = provider.fetch(request, pending.retries)
                if not isinstance(supplied, SuppliedPage):
                    supplied = None
            except Exception:
                supplied = None  # fixed public failure code; no provider text retained
            store.commit(pending.series_key, supplied, token=token, expected_version=s.version,
                         request=request, retry_index=pending.retries, crash=crash)
            progress = next(p for p in store.snapshot.progress if p.series_key == pending.series_key)
            if progress.code in ('provider_unavailable', 'retry_scheduled'):
                store.transition('retryable', token, 'retry_scheduled')
                return store.public_result()
        except Rejected as exc:
            if exc.code in ('stale_lease', 'version_conflict', 'identity_conflict', 'replay_conflict'):
                raise
            try:
                store.transition('failed', token, exc.code)
            except Rejected as terminal_error:
                if terminal_error.code != 'limit_checkpoint_bytes':
                    raise
                return store.public_result() | {'state': 'failed', 'code': 'limit_checkpoint_bytes'}
            return store.public_result()
