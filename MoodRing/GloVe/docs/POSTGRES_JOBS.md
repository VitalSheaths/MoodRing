# PostgreSQL job persistence

Issue #12: synthetic research jobs only, extending the reviewed job contracts.
No live ingestion, API startup migration, provider credentials or scheduler.

## Persistence mapping (recorded before implementation)

| Authority | Representation |
| --- | --- |
| Existing `bronze.ingestion_runs` | Run ID and lifecycle status remain here. Status capacity widens 16 → 32 and run IDs 64 → 100 to match the existing job contract, without rewriting values. Legacy unowned news runs retain their behavior. |
| `job_manifests` | Immutable typed manifest, hash and synthetic-only job type; FK to the existing run ID. |
| `job_coordination` | Version, step count, safe code, retry time and server-time lease; no second lifecycle state. |
| `job_attempts`, `job_checkpoints` | Attempt history and per-series cursor/progress using existing typed contracts. |
| `job_batches` | Immutable idempotency result, request/response identity and typed receipt metadata. |
| `job_artifacts` | Content-addressed bounded BYTEA; PostgreSQL verifies SHA-256. No external artifact service. |
| `job_revisions`, `job_record_appearances` | Unique accepted revision identities and per-batch typed observation appearances (including transport duplicates). |

SEC/universe/market/job validators remain authoritative for their data semantics.
The PostgreSQL adapter reconstructs those models from linked records; an unchecked
whole-snapshot JSON blob is not the source of truth. Shared fake reducers supply
state/coverage/replay behavior inside a database transaction; their process lock
is not PostgreSQL concurrency authority. One shared orchestration loop is retained.

Migration is explicit, versioned and transactional. Existing research metadata
creates its own baseline tables on a fresh schema; the new SQL only extends that
boundary. Preflight rejects incompatible legacy columns and partial job schemas.
Checksums bind migration bytes and the installed schema. A later rollout must be
approved separately before touching an existing maintainer database.

## Explicit migration and recovery

A maintainer supplies a SQLAlchemy PostgreSQL engine and calls
`ingestion.jobs.migrations.inspect_schema(engine)` before an explicitly approved
`migrate(engine)`. Inspection returns `pending`, `applied`, `incompatible` or
`checksum_mismatch`; migration returns `applied` or `already_applied`. The store
never migrates automatically. Concurrent migrations serialize through a
transaction advisory lock. DDL and migration history commit together, or roll back.
Existing legacy rows are preserved. The installed columns, constraints, indexes,
triggers and functions are fingerprinted; unexpected changes fail preflight.

DDL takes database locks: schedule an approved maintenance window for a real
research database. No maintainer database was migrated in this increment. There is
no destructive down migration. For rollback, stop the new writer and retain the
additive evidence tables; review database restoration separately. Legacy helpers
continue to handle unowned runs but cannot mutate job-owned run projections.

After migration, construct `PostgresStore(engine, manifest, create=True)` to create
an explicitly authorized synthetic job. Reopening uses `create=False` (the default)
and requires an exact manifest match. A fresh engine/process can reconstruct the
typed checkpoint and resume through the existing orchestration loop. Incompatible
or corrupted linked evidence is rejected rather than silently repaired.

## Maintainer migration checklist

Keep the writer stopped until all five steps pass. This checklist does not approve
rollout to an existing research database; that needs a separate reviewed issue.

1. **Inspect:** confirm the intended database and code revision without printing
   its connection URL. Run `inspect_schema(engine)` with the inspection role;
   record the status and review legacy compatibility and the migration diff.
   Stop on `incompatible`, `checksum_mismatch` or any inspection error.
2. **Approve:** obtain authorization for that exact target/revision, maintenance
   window, backup/restore plan and role grants. Rehearse on a disposable copy;
   preserve evidence and agree who can stop the writer.
3. **Migrate:** use the separate migration role to call `migrate(engine)` once.
   Accept only `applied` or `already_applied`; on failure keep the writer stopped.
   Never use application startup to apply DDL.
4. **Verify:** require `inspect_schema(engine) == 'applied'`, unchanged legacy
   sample rows and expected grants. Run the reviewed bounded synthetic smoke job
   with the normal writer role, reopen it through a fresh connection and verify
   its checkpoint, evidence and counters. Confirm the inspection role cannot write
   and the writer cannot perform DDL or delete evidence.
5. **Enable the writer:** only after verification and explicit target approval,
   enable the authorized synthetic caller with the normal role. Do not supply
   migration credentials to it. If verification or operation fails, stop the
   caller and retain evidence; investigate before retrying or restoring.

There is no installed scheduler or enable-writer command: enabling means allowing
only the separately authorized caller to invoke the existing store/orchestrator.

## PostgreSQL privilege separation

The following describes required access for the current adapter, not an installed
role/grant migration or a claim that restricted-role deployment was tested. Use
separate roles and account for inherited and `PUBLIC` grants. All roles need an
authenticated connection (`CONNECT` on the target database, permitted by server
connection policy). Keep normal roles out of owner/migration-role membership.

### Normal job operation

- `USAGE` on `bronze`; `SELECT` on the eight `job_*` tables in the mapping above
  and `bronze.ingestion_runs`, plus inspection access described below (the store
  inspects the schema when opening).
- `INSERT` on those eight job tables and `bronze.ingestion_runs` to create jobs,
  attempts and committed evidence.
- `UPDATE` only on `job_coordination` (all mutable coordination fields),
  `job_checkpoints(progress)`, `job_attempts(finished_at)` and
  `ingestion_runs(status, total_fetched, total_inserted, ended_at, error_message)`.
  Coordination writes include `write_xid` and `guard_expires_at`; row locking also
  requires update permission. Database triggers still enforce write ordering.
- Access to ordinary built-in functions used by the adapter (including advisory
  locks, hashes, clocks and transaction IDs); default PostgreSQL function grants
  normally provide this. Trigger functions run as the invoking role and query the
  same tables; no direct application call to a `job_*` trigger function is needed.

No job sequence privileges are needed: job keys are supplied, not sequence-backed.
No `DELETE`, `TRUNCATE`, `CREATE`, table ownership, trigger creation, grant option,
`CREATEDB`, `CREATEROLE` or superuser access is needed for normal operation.
Legacy news ingestion is separate: do not grant its raw-news table/sequence access
to the job writer unless that additional responsibility is separately approved.
These grants are not per-user row isolation; the current adapter assumes a trusted
research writer, not direct SQL access for public clients.

### Migration execution

Use a controlled owner role (or an authorized role able to act as that owner),
with inspection access, database `CREATE` for the migration's schema creation
statement, and `USAGE`/`CREATE` on `bronze` when it exists. It must own the existing
`ingestion_runs` table to widen columns and install its trigger. It needs privileges
to create the new tables/indexes/foreign keys/functions/triggers, `USAGE` on the
trusted `plpgsql` language and referenced built-in types/functions, and write/read
access to migration history. Newly created objects belong to this role; ownership
provides the required new-object rights. Existing baseline tables must remain
visible during inspection. No extension installation or superuser access is
required. `CREATEDB` is needed by the disposable test fixture, not by `migrate`.

Keep these credentials out of the writer. Object ownership inherently allows
altering/bypassing guards, so it cannot be replaced by ordinary writer grants.

### Schema inspection

For `inspect_schema(engine)`: `USAGE` on an existing `bronze`, read access to
migration history (`version`, `checksum`, `schema_sha` in
`bronze.ingestion_job_migrations` when present), and the normal readable PostgreSQL
catalogs plus schema-definition inspection functions used in `migrations.py`.
No job payload/record reads or application-table writes are required by this
inspection function. No object ownership, DDL or `pg_monitor` membership is needed
under standard PostgreSQL catalog permissions. A hardened server with revoked
catalog/function access needs a reviewed equivalent grant set; an access error
must never be treated as a successful inspection.

These requirements follow the adapter's SQL and PostgreSQL's
[privilege definitions](https://www.postgresql.org/docs/16/ddl-priv.html) and
[GRANT reference](https://www.postgresql.org/docs/16/sql-grant.html). Grant design
and verification on a real target remain part of its separately approved rollout.

## Transaction boundaries

Each mutation locks the run and coordinator, checks the expected version and
server-time lease, executes the shared reducer, then writes evidence, counters and
cursor in one transaction. A deferred constraint trigger rechecks lease expiry at
commit. A worker whose lease expires during the transaction cannot publish that
batch. Replacement claims fence older tokens; independent connections race against
the same database authority.

Before-commit failures expose no partial batch. After commit but before
acknowledgement, replay of the same batch key and content returns the original
result. Changed content under that key is rejected. Retries and duplicates retain
transport evidence without increasing unique accepted-record counts. These are
PostgreSQL transaction guarantees under its configured durability settings, not a
claim of cross-system transactions or tested power-loss recovery.

## Storage choices and trade-offs

| Choice | Benefit | Cost / boundary |
| --- | --- | --- |
| Existing run row plus linked job tables | One lifecycle authority; preserves research helpers | Requires guarded legacy updates and explicit compatible column widening |
| Typed JSONB within relational identities | Reuses reviewed contracts and validation | Reopening revalidates evidence; no SQL analytics API is provided |
| Bounded content-addressed BYTEA | Artifact and checkpoint commit atomically without another service | Suitable for synthetic fixtures; not bulk historical storage |
| Shared reducers with row locks and version checks | Fake/database parity with database concurrency control | Conflicts require caller retry; no production worker scheduler |
| Strict schema fingerprint | Detects unreviewed drift | Intentional schema changes require a new reviewed migration |

Checkpoint limits apply to the reconstructed logical snapshot, including artifact
bytes, even though SQL stores evidence separately. The adapter also bounds rows
and body bytes before loading evidence. Limits are per job: indexes, WAL, retained
terminal jobs and total database disk usage require a separate operational policy.
Artifacts and committed evidence are append-only; attempts may close once and
checkpoints may advance only through the coordinated transaction before terminal
state. No automatic retention deletion or external object store is introduced.

The migration installs no roles or grants. A trusted database writer is assumed;
restrict application privileges before deployment. An owner/superuser can alter
triggers or truncate tables, so these guards are not protection from a privileged
operator. Internal manifests/checkpoints remain private storage evidence. Public
results use the existing sanitized projection; driver exceptions are mapped to
fixed error codes without returning SQL, paths, credentials or raw provider text.

## Validation and approval boundary

`tests/test_postgres_jobs.py` uses the existing `PAPER_TEST_POSTGRES_URL` fixture,
which creates and drops a uniquely named disposable database for each test. Use
only a test server where database creation/deletion is authorized. Without that
environment setting, database tests skip; this is not PostgreSQL coverage.

Coverage includes fresh and legacy migration, idempotent application, checksum and
schema drift, failed DDL rollback, independent-connection conflicts, expiry during
commit, acknowledgement loss, changed-content replay, corrupted evidence,
resource limits, fake/store parity and subprocess restart recovery. One expiry
test deliberately waits just over one second inside a transaction; other timing
tests manipulate isolated fixture state. All provider pages remain synthetic.

This increment does not authorize live ingestion, provider credentials, public
ingestion endpoints, scheduling, backfills, production universe selection or
migration of an existing maintainer database. Those need separately scoped review.
