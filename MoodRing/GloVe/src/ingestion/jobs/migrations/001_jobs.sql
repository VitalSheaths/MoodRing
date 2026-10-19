ALTER TABLE bronze.ingestion_runs ALTER COLUMN status TYPE varchar(32);
ALTER TABLE bronze.ingestion_runs ALTER COLUMN run_id TYPE varchar(100);
CREATE TABLE bronze.job_manifests (
 run_id varchar(100) PRIMARY KEY REFERENCES bronze.ingestion_runs(run_id) ON DELETE RESTRICT,
 manifest jsonb NOT NULL CHECK (jsonb_typeof(manifest)='object' AND manifest ? 'job_type'
    AND manifest->>'job_type' = 'offline_market_fixture' AND manifest ? 'job_id' AND manifest->>'job_id'=run_id),
 manifest_sha varchar(64) NOT NULL CHECK (manifest_sha ~ '^[a-f0-9]{64}$')
);
CREATE TABLE bronze.job_coordination (
 run_id varchar(100) PRIMARY KEY REFERENCES bronze.job_manifests(run_id) ON DELETE RESTRICT,
 version bigint NOT NULL CHECK (version >= 0), steps bigint NOT NULL CHECK (steps >= 0),
 code text NOT NULL, next_retry_at timestamptz,
 worker text, token bigint, heartbeat_at timestamptz, expires_at timestamptz,
 write_xid bigint NOT NULL, guard_expires_at timestamptz,
 CHECK ((token IS NULL AND worker IS NULL AND heartbeat_at IS NULL AND expires_at IS NULL)
     OR (token IS NOT NULL AND token > 0 AND worker IS NOT NULL AND heartbeat_at IS NOT NULL AND expires_at IS NOT NULL AND expires_at > heartbeat_at))
);
CREATE TABLE bronze.job_attempts (
 run_id varchar(100) NOT NULL REFERENCES bronze.job_manifests(run_id) ON DELETE RESTRICT,
 number integer NOT NULL CHECK (number > 0), worker text NOT NULL, token bigint NOT NULL CHECK (token > 0),
 started_at timestamptz NOT NULL, finished_at timestamptz,
 PRIMARY KEY(run_id,number), UNIQUE(run_id,token), CHECK(finished_at IS NULL OR finished_at >= started_at)
);
CREATE TABLE bronze.job_checkpoints (
 run_id varchar(100) NOT NULL REFERENCES bronze.job_manifests(run_id) ON DELETE RESTRICT,
 series_key text NOT NULL, progress jsonb NOT NULL,
 PRIMARY KEY(run_id,series_key), CHECK (progress ? 'series_key' AND progress->>'series_key' = series_key)
);
CREATE TABLE bronze.job_artifacts (
 sha varchar(64) PRIMARY KEY CHECK (sha ~ '^[a-f0-9]{64}$'),
 payload bytea NOT NULL CHECK (octet_length(payload) <= 100000000),
 CHECK (encode(sha256(payload),'hex') = sha)
);
CREATE TABLE bronze.job_batches (
 run_id varchar(100) NOT NULL REFERENCES bronze.job_manifests(run_id) ON DELETE RESTRICT,
 sequence integer NOT NULL CHECK(sequence >= 0), batch_key varchar(64) NOT NULL,
 response_identity varchar(64) NOT NULL, metadata jsonb NOT NULL,
 artifact_sha varchar(64) REFERENCES bronze.job_artifacts(sha) ON DELETE RESTRICT,
 PRIMARY KEY(run_id,sequence), UNIQUE(run_id,batch_key),
 CHECK (batch_key ~ '^[a-f0-9]{64}$' AND response_identity ~ '^[a-f0-9]{64}$'),
 CHECK (metadata ? 'batch_key' AND metadata->>'batch_key' = batch_key),
 CHECK(metadata ? 'response_identity' AND metadata->>'response_identity' = response_identity),
 CHECK(metadata->'result' ? 'batch_key' AND metadata->'result'->>'batch_key'=batch_key)
);
CREATE TABLE bronze.job_revisions (
 run_id varchar(100) NOT NULL REFERENCES bronze.job_manifests(run_id) ON DELETE RESTRICT,
 revision_id varchar(64) NOT NULL, observation_key varchar(64) NOT NULL,
 semantic_sha varchar(64) NOT NULL, supersedes varchar(64),
 CHECK(revision_id ~ '^[a-f0-9]{64}$' AND observation_key ~ '^[a-f0-9]{64}$' AND semantic_sha ~ '^[a-f0-9]{64}$'),
 CHECK(supersedes IS NULL OR supersedes ~ '^[a-f0-9]{64}$'),
 PRIMARY KEY(run_id,revision_id),
 FOREIGN KEY(run_id,supersedes) REFERENCES bronze.job_revisions(run_id,revision_id) ON DELETE RESTRICT
);
CREATE INDEX job_revisions_observation_idx ON bronze.job_revisions(run_id,observation_key);
CREATE TABLE bronze.job_record_appearances (
 run_id varchar(100) NOT NULL, batch_sequence integer NOT NULL, ordinal integer NOT NULL CHECK(ordinal >= 0),
 revision_id varchar(64) NOT NULL, record jsonb NOT NULL,
 PRIMARY KEY(run_id,batch_sequence,ordinal),
 FOREIGN KEY(run_id,batch_sequence) REFERENCES bronze.job_batches(run_id,sequence) ON DELETE RESTRICT,
 FOREIGN KEY(run_id,revision_id) REFERENCES bronze.job_revisions(run_id,revision_id) ON DELETE RESTRICT
);
CREATE INDEX job_record_revision_idx ON bronze.job_record_appearances(run_id,revision_id);
CREATE FUNCTION bronze.job_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'job_evidence_immutable' USING ERRCODE='23514'; END $$;
CREATE FUNCTION bronze.job_attempt_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='UPDATE' AND OLD.finished_at IS NULL AND NEW.finished_at IS NOT NULL
 AND (NEW.run_id,NEW.number,NEW.worker,NEW.token,NEW.started_at) IS NOT DISTINCT FROM
     (OLD.run_id,OLD.number,OLD.worker,OLD.token,OLD.started_at) THEN RETURN NEW; END IF;
 RAISE EXCEPTION 'job_evidence_immutable' USING ERRCODE='23514';
END $$;
CREATE FUNCTION bronze.job_mutation_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE job text;
BEGIN
 job := OLD.run_id;
 IF EXISTS(SELECT 1 FROM bronze.job_manifests WHERE run_id=job) THEN
  IF TG_TABLE_NAME='ingestion_runs' THEN
   IF OLD.status IN ('completed','completed_with_gaps','failed','cancelled')
   OR NOT EXISTS(SELECT 1 FROM bronze.job_coordination WHERE run_id=job AND write_xid=txid_current()) THEN
    RAISE EXCEPTION 'job_coordinator_required' USING ERRCODE='23514';
   END IF;
  END IF;
  IF TG_TABLE_NAME='job_coordination' AND EXISTS(SELECT 1 FROM bronze.ingestion_runs
   WHERE run_id=job AND status IN ('completed','completed_with_gaps','failed','cancelled')) THEN
   RAISE EXCEPTION 'job_evidence_immutable' USING ERRCODE='23514';
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION bronze.job_deferred_lease_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.guard_expires_at IS NOT NULL AND clock_timestamp() >= NEW.guard_expires_at THEN
  RAISE EXCEPTION 'job_lease_expired' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER job_owned_run_guard BEFORE UPDATE ON bronze.ingestion_runs
 FOR EACH ROW EXECUTE FUNCTION bronze.job_mutation_guard();
CREATE TRIGGER job_coordination_guard BEFORE UPDATE ON bronze.job_coordination
 FOR EACH ROW EXECUTE FUNCTION bronze.job_mutation_guard();
CREATE CONSTRAINT TRIGGER job_lease_commit_guard AFTER INSERT OR UPDATE ON bronze.job_coordination
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION bronze.job_deferred_lease_guard();
CREATE TRIGGER job_attempt_immutable BEFORE UPDATE OR DELETE ON bronze.job_attempts
 FOR EACH ROW EXECUTE FUNCTION bronze.job_attempt_guard();
CREATE TRIGGER job_manifest_immutable BEFORE UPDATE OR DELETE ON bronze.job_manifests
 FOR EACH ROW EXECUTE FUNCTION bronze.job_immutable();
CREATE TRIGGER job_artifact_immutable BEFORE UPDATE OR DELETE ON bronze.job_artifacts
 FOR EACH ROW EXECUTE FUNCTION bronze.job_immutable();
CREATE TRIGGER job_batch_immutable BEFORE UPDATE OR DELETE ON bronze.job_batches
 FOR EACH ROW EXECUTE FUNCTION bronze.job_immutable();
CREATE TRIGGER job_revision_immutable BEFORE UPDATE OR DELETE ON bronze.job_revisions
 FOR EACH ROW EXECUTE FUNCTION bronze.job_immutable();
CREATE TRIGGER job_record_immutable BEFORE UPDATE OR DELETE ON bronze.job_record_appearances
 FOR EACH ROW EXECUTE FUNCTION bronze.job_immutable();

CREATE FUNCTION bronze.job_checkpoint_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' OR EXISTS(SELECT 1 FROM bronze.ingestion_runs WHERE run_id=OLD.run_id
   AND status IN ('completed','completed_with_gaps','failed','cancelled')) THEN
  RAISE EXCEPTION 'job_evidence_immutable' USING ERRCODE='23514';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM bronze.job_coordination WHERE run_id=OLD.run_id AND write_xid=txid_current()) THEN
  RAISE EXCEPTION 'job_coordinator_required' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER job_checkpoint_mutation_guard BEFORE UPDATE OR DELETE ON bronze.job_checkpoints
 FOR EACH ROW EXECUTE FUNCTION bronze.job_checkpoint_guard();
