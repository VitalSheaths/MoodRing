"""Bounded synthetic job persistence, sharing the reviewed in-memory reducers."""
from __future__ import annotations

from functools import wraps

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import SQLAlchemyError

from ingestion.market.bars import DailyObservation
from ingestion.sec.contracts import payload_digest, utc
from .contracts import Attempt, CommittedBatch, Lease, Manifest, Progress, Snapshot, TERMINAL
from .errors import StorageError
from .migrations import inspect_schema
from .offline import FakeStore, Rejected, SimulatedCrash


def _safe(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except SQLAlchemyError as exc:
            original = getattr(exc, 'orig', None)
            code = getattr(original, 'pgcode', None)
            message = getattr(getattr(original, 'diag', None), 'message_primary', '')
            if message == 'job_lease_expired':
                raise Rejected('stale_lease') from None
            if code in ('40001', '40P01'):
                raise Rejected('version_conflict') from None
            if message in ('job_evidence_immutable', 'job_coordinator_required'):
                raise StorageError('evidence_immutable') from None
            if code in ('42P01', '42703'):
                raise StorageError('schema_mismatch') from None
            if code == '23505':
                raise Rejected('identity_conflict') from None
            if code in ('23503','23514'):
                raise StorageError('constraint_conflict') from None
            raise StorageError('database_unavailable' if code is None or code.startswith('08') else 'database_error') from None
    return call


def _sql(conn, query, values=None, json_fields=()):
    statement = text(query)
    if json_fields:
        statement = statement.bindparams(*(bindparam(name, type_=JSONB) for name in json_fields))
    return conn.execute(statement, values or {})


def _clock(conn):
    return utc(_sql(conn, 'SELECT clock_timestamp()').scalar_one())


class PostgresStore:
    @_safe
    def __init__(self, engine, manifest: Manifest, *, create=False):
        self.engine, self.manifest = engine, manifest
        if inspect_schema(engine) != 'applied':
            raise StorageError('schema_mismatch')
        if create:
            with engine.begin() as conn:
                # Serialize create/reopen of this identity, including before its row exists.
                _sql(conn, 'SELECT pg_advisory_xact_lock(hashtextextended(:id,12))', {'id': manifest.job_id})
                existing = _sql(conn, 'SELECT manifest_sha FROM bronze.job_manifests WHERE run_id=:id', {'id': manifest.job_id}).scalar_one_or_none()
                if existing is not None:
                    if existing != manifest.content_hash:
                        raise Rejected('identity_conflict')
                else:
                    if _sql(conn, 'SELECT 1 FROM bronze.ingestion_runs WHERE run_id=:id', {'id': manifest.job_id}).scalar_one_or_none():
                        raise Rejected('identity_conflict')
                    view = FakeStore(manifest, lambda: _clock(conn))
                    _sql(conn, '''INSERT INTO bronze.ingestion_runs(run_id,started_at,status,total_fetched,total_inserted)
                        VALUES (:id,clock_timestamp(),'pending',0,0)''', {'id': manifest.job_id})
                    _sql(conn, 'INSERT INTO bronze.job_manifests VALUES (:id,:data,:sha)',
                         {'id': manifest.job_id, 'data': manifest.model_dump(mode='json'), 'sha': manifest.content_hash}, ('data',))
                    _sql(conn, '''INSERT INTO bronze.job_coordination
                        (run_id,version,steps,code,write_xid) VALUES (:id,0,0,'ok',txid_current())''', {'id': manifest.job_id})
                    for progress in view.snapshot.progress:
                        _sql(conn, 'INSERT INTO bronze.job_checkpoints VALUES (:id,:key,:data)',
                             {'id': manifest.job_id, 'key': progress.series_key, 'data': progress.model_dump(mode='json')}, ('data',))
        # Opening does not create or migrate anything; it validates the stored graph.
        self._view()

    def _load(self, conn, lock=False):
        params = {'id': self.manifest.job_id}
        row = _sql(conn, '''SELECT r.status,r.total_fetched,r.total_inserted,m.manifest,m.manifest_sha,c.*
            FROM bronze.ingestion_runs r JOIN bronze.job_manifests m USING(run_id)
            JOIN bronze.job_coordination c USING(run_id) WHERE r.run_id=:id''' + (' FOR UPDATE OF r,c' if lock else ''), params).mappings().one_or_none()
        if row is None:
            raise StorageError('job_not_found')
        try:
            stored_manifest = Manifest.model_validate(row['manifest'])
            if stored_manifest != self.manifest or row['manifest_sha'] != self.manifest.content_hash:
                raise Rejected('identity_conflict')
            # Reject oversized/corrupt graphs before fetching their payloads/appearance JSON.
            limits = stored_manifest.limits
            sizes = _sql(conn, '''SELECT
                (SELECT count(*) FROM bronze.job_attempts WHERE run_id=:id) AS attempts,
                (SELECT count(*) FROM bronze.job_batches WHERE run_id=:id) AS batches,
                (SELECT count(*) FROM bronze.job_record_appearances WHERE run_id=:id) AS appearances,
                COALESCE((SELECT sum(octet_length(metadata::text)) FROM bronze.job_batches WHERE run_id=:id),0)
                + COALESCE((SELECT sum(octet_length(record::text)) FROM bronze.job_record_appearances WHERE run_id=:id),0)
                + COALESCE((SELECT sum(2*octet_length(a.payload)) FROM bronze.job_batches b
                    JOIN bronze.job_artifacts a ON a.sha=b.artifact_sha WHERE b.run_id=:id),0) AS bytes''', params).mappings().one()
            if sizes['attempts'] > limits.max_attempts or sizes['batches'] > limits.max_steps or sizes['appearances'] > limits.max_total_records or sizes['bytes'] > 4*limits.max_checkpoint_bytes:
                raise ValueError('stored_graph_exceeds_bounds')
            lease = None if row['token'] is None else Lease(worker=row['worker'],token=row['token'],
                heartbeat_at=row['heartbeat_at'],expires_at=row['expires_at'])
            attempts = tuple(Attempt.model_validate(dict(r)) for r in _sql(conn, '''SELECT number,worker,token,started_at,finished_at
                FROM bronze.job_attempts WHERE run_id=:id ORDER BY number''', params).mappings())
            progress_rows = {r['series_key']: Progress.model_validate(r['progress']) for r in _sql(conn,
                'SELECT series_key,progress FROM bronze.job_checkpoints WHERE run_id=:id', params).mappings()}
            if set(progress_rows) != {s.key for s in self.manifest.series}:
                raise ValueError('checkpoint_set_mismatch')
            records = {}
            seen_revisions = set()
            for r in _sql(conn, '''SELECT a.batch_sequence,a.ordinal,a.record,a.revision_id,v.observation_key,v.semantic_sha,v.supersedes
                FROM bronze.job_record_appearances a JOIN bronze.job_revisions v USING(run_id,revision_id)
                WHERE a.run_id=:id ORDER BY a.batch_sequence,a.ordinal''', params).mappings():
                observation = DailyObservation.model_validate(r['record'])
                if (observation.revision_id,observation.observation_key,observation.semantic_checksum,observation.supersedes) != (
                        r['revision_id'],r['observation_key'],r['semantic_sha'],r['supersedes']):
                    raise ValueError('revision_identity_mismatch')
                appearances = records.setdefault(r['batch_sequence'], [])
                if r['ordinal'] != len(appearances):
                    raise ValueError('appearance_order_mismatch')
                appearances.append(observation)
                seen_revisions.add(r['revision_id'])
            revision_count = _sql(conn, 'SELECT count(*) FROM bronze.job_revisions WHERE run_id=:id', params).scalar_one()
            if revision_count != len(seen_revisions):
                raise ValueError('unreferenced_revision')
            batches = []
            for r in _sql(conn, '''SELECT b.*,a.payload FROM bronze.job_batches b
                LEFT JOIN bronze.job_artifacts a ON a.sha=b.artifact_sha
                WHERE b.run_id=:id ORDER BY b.sequence''', params).mappings():
                if r['sequence'] != len(batches):
                    raise ValueError('batch_order_mismatch')
                payload = b'' if r['payload'] is None else bytes(r['payload'])
                if r['artifact_sha'] is not None and (r['payload'] is None or payload_digest(payload) != r['artifact_sha']):
                    raise ValueError('artifact_hash_mismatch')
                metadata = r['metadata']
                if metadata['batch_key'] != r['batch_key'] or metadata['response_identity'] != r['response_identity']:
                    raise ValueError('batch_identity_mismatch')
                if (metadata['page'] is None) != (r['artifact_sha'] is None):
                    raise ValueError('artifact_reference_mismatch')
                batches.append(CommittedBatch.model_validate(metadata | {'records': records.pop(r['sequence'], ()), 'payload_hex': payload.hex()}))
            if records:
                raise ValueError('dangling_appearances')
            snapshot = Snapshot(manifest=stored_manifest,state=row['status'],version=row['version'],steps=row['steps'],
                code=row['code'],next_retry_at=row['next_retry_at'],lease=lease,attempts=attempts,
                progress=tuple(progress_rows[s.key] for s in stored_manifest.series),batches=tuple(batches))
            blob = snapshot.model_dump_json().encode()
            view = FakeStore.restore(blob, self.manifest, lambda: _clock(conn), payload_digest(blob))
            counts = view.public_result()['counters']
            if row['total_fetched'] != sum(b.observed_count for b in batches) or row['total_inserted'] != counts['accepted_unique_record_count']:
                raise ValueError('legacy_projection_mismatch')
            return view
        except Rejected as exc:
            if exc.code == 'identity_conflict':
                raise
            raise StorageError('evidence_corrupt') from None
        except (ValueError,KeyError,TypeError):
            raise StorageError('evidence_corrupt') from None

    @_safe
    def _view(self):
        # Multiple SELECTs must share a consistent read snapshot while writers commit.
        with self.engine.connect().execution_options(isolation_level='REPEATABLE READ') as conn:
            with conn.begin():
                view = self._load(conn)
                now = _clock(conn)
                view.clock = lambda: now
                return view

    @property
    def snapshot(self):
        return self._view().snapshot

    def guard(self, token):
        self._view().guard(token)

    def preflight(self, series_key):
        self._view().preflight(series_key)

    def completion_state(self):
        return self._view().completion_state()

    def public_result(self):
        return self._view().public_result()

    def export(self):
        return self._view().export()

    def _write(self, conn, before, view, deadline):
        after = view.snapshot
        lease = after.lease
        params = dict(id=self.manifest.job_id,old=before.version,version=after.version,steps=after.steps,
            code=after.code,retry=after.next_retry_at,worker=lease.worker if lease else None,
            token=lease.token if lease else None,heartbeat=lease.heartbeat_at if lease else None,
            expires=lease.expires_at if lease else None,deadline=deadline)
        updated = _sql(conn, '''UPDATE bronze.job_coordination SET version=:version,steps=:steps,code=:code,
            next_retry_at=:retry,worker=:worker,token=:token,heartbeat_at=:heartbeat,expires_at=:expires,
            write_xid=txid_current(),guard_expires_at=:deadline WHERE run_id=:id AND version=:old
            AND (:deadline IS NULL OR clock_timestamp() < :deadline)''', params)
        if updated.rowcount != 1:
            raise Rejected('stale_lease' if deadline is not None and _clock(conn) >= deadline else 'version_conflict')
        for attempt in after.attempts:
            if attempt.number > len(before.attempts):
                _sql(conn, '''INSERT INTO bronze.job_attempts VALUES (:id,:n,:w,:t,:s,:f)''',
                    dict(id=self.manifest.job_id,n=attempt.number,w=attempt.worker,t=attempt.token,s=attempt.started_at,f=attempt.finished_at))
            elif attempt != before.attempts[attempt.number - 1]:
                _sql(conn, 'UPDATE bronze.job_attempts SET finished_at=:f WHERE run_id=:id AND number=:n',
                    dict(id=self.manifest.job_id,n=attempt.number,f=attempt.finished_at))
        for progress in after.progress:
            _sql(conn, 'UPDATE bronze.job_checkpoints SET progress=:data WHERE run_id=:id AND series_key=:key',
                dict(id=self.manifest.job_id,key=progress.series_key,data=progress.model_dump(mode='json')), ('data',))
        for sequence in range(len(before.batches),len(after.batches)):
            batch = after.batches[sequence]
            artifact = None
            if batch.page is not None:
                payload = bytes.fromhex(batch.payload_hex)
                artifact = payload_digest(payload)
                _sql(conn, 'INSERT INTO bronze.job_artifacts VALUES (:sha,:payload) ON CONFLICT(sha) DO NOTHING',
                    dict(sha=artifact,payload=payload))
            data = batch.model_dump(mode='json',exclude={'records','payload_hex'})
            _sql(conn, '''INSERT INTO bronze.job_batches VALUES (:id,:n,:key,:response,:data,:artifact)''',
                dict(id=self.manifest.job_id,n=sequence,key=batch.batch_key,response=batch.response_identity,data=data,artifact=artifact), ('data',))
            for ordinal, observation in enumerate(batch.records):
                _sql(conn, '''INSERT INTO bronze.job_revisions VALUES (:id,:revision,:observation,:semantic,:parent)
                    ON CONFLICT(run_id,revision_id) DO NOTHING''', dict(id=self.manifest.job_id,
                    revision=observation.revision_id,observation=observation.observation_key,semantic=observation.semantic_checksum,parent=observation.supersedes))
                _sql(conn, 'INSERT INTO bronze.job_record_appearances VALUES (:id,:batch,:ordinal,:revision,:data)',
                    dict(id=self.manifest.job_id,batch=sequence,ordinal=ordinal,revision=observation.revision_id,data=observation.model_dump(mode='json')), ('data',))
        counts = view.public_result()['counters']
        _sql(conn, '''UPDATE bronze.ingestion_runs SET status=:state,total_fetched=:fetched,total_inserted=:inserted,
            ended_at=CASE WHEN :terminal THEN clock_timestamp() ELSE NULL END,error_message=:error WHERE run_id=:id''',
            dict(id=self.manifest.job_id,state=after.state,fetched=sum(b.observed_count for b in after.batches),
                 inserted=counts['accepted_unique_record_count'],terminal=after.state in TERMINAL,error=None if after.code=='ok' else after.code))

    def _before_commit(self, conn):
        """Failure-injection seam for integration tests, not a scheduler callback."""

    @_safe
    def _operate(self, method, *args, **kwargs):
        crash = kwargs.get('crash')
        if crash == 'after_commit':
            kwargs['crash'] = None
        error, result = None, None
        with self.engine.begin() as conn:
            view = self._load(conn, lock=True)
            before = view.snapshot
            try:
                result = getattr(view, method)(*args, **kwargs)
            except Rejected as exc:
                error = exc
            after = view.snapshot
            if after != before:
                deadline = (after.lease.expires_at if method == 'claim' and after.state == 'running' else
                            before.lease.expires_at if before.state == 'running' and before.lease else None)
                self._write(conn,before,view,deadline)
                self._before_commit(conn)
        # A reducer may intentionally record a failed attempt budget before rejecting.
        if error is not None:
            raise error
        if crash == 'after_commit':
            raise SimulatedCrash()
        return result

    def claim(self, worker):
        return self._operate('claim',worker)

    def heartbeat(self, token):
        return self._operate('heartbeat',token)

    def transition(self, target, token=None, code='ok'):
        return self._operate('transition',target,token,code)

    def commit(self, series_key, supplied, **kwargs):
        return self._operate('commit',series_key,supplied,**kwargs)
