# Disposable synthetic capacity measurements

Issue #15 design, recorded before implementation. This is a measurement and dry-run
retention increment, not a deployment or capacity certification.

## Approved experiment

One newly created random `moodring_measure_<uuid>` database per run, explicit
`PAPER_TEST_POSTGRES_URL` plus an acknowledgement. No fallback to `DATABASE_URL`
or an existing application database. Use the existing migration and synthetic
selector/adapter/store. The only cleanup is dropping the newly owned disposable
database after connections close; no evidence rows or triggers are removed/disabled.

Limits: 12 jobs, three equities/two reference slots per job, two sessions, 128
source records, 100 committed orchestration steps, eight heartbeats, 120 seconds
cooperative deadline, five-second SQL statement timeout, 1 MiB report. The runner
stops at its finite caps. No VACUUM, statistics reset/flush, replication/archival
configuration, index changes or retention execution is included.

Phases: schema baseline; create/claim/heartbeat/commit/exact replay/completion;
a larger completed job; completed-with-gaps, failed, cancelled and protected active
jobs; retryable dispatch; duplicates and revisions; compressible and varied
quarantined artifact evidence. Sources remain synthetic and bounded.

## Measurement authority

Use read-only transactions to read exact row counts, database bytes and heap,
TOAST, index/all-fork allocation. Live/dead tuple statistics are estimates and may
lag; they are not exact counts or a bloat measurement. Measure per-phase cluster
WAL insertion-LSN deltas; unrelated activity/checkpoints/full-page images may
contribute. Do not claim zero WAL for an idempotent replay or attribute all cluster
WAL to this database. Retained WAL/archive/slot bytes and free disk space are not
measured and must remain explicitly unsupported.

Retention planning is a pure bounded function over an inventory. State-specific
illustrative ages are completed 30d, completed-with-gaps 60d, failed 90d, cancelled
14d. Nonterminal jobs, recent terminal jobs, explicit holds and ancestors of kept
jobs are protected. Missing/cyclic dependency evidence forces a conservative hold.
All referenced/shared artifacts remain protected, even when all referencing jobs
are review candidates. Unreferenced bytes are inventory only, never deletion
permission. Report both current age and a clearly labeled 120-day what-if without
changing any database timestamps. Proposed deletion bytes always equal zero.

## Review gates

Review actual repeat-run measurements and uncertainty before setting any real size
or retention policy. Any schema, index, partitioning, archival or retention-enforcement
change needs a new issue and approved migration/design. Provider/licensing review,
real data/backfills, schedulers and existing-database rollout remain separate.

## Running and reviewing

```bash
# Explicitly set PAPER_TEST_POSTGRES_URL to an authorized disposable test server.
PYTHONPATH=src python examples/measure_synthetic_capacity.py \
  --ack-disposable --output resources/capacity-report.json
```

The URL must address the test server's `postgres` maintenance database and use the
existing synchronous PostgreSQL driver. The helper creates a UUID target from
`template0`, requires a process-owned target created by that helper, verifies
`current_database()` before migration, and never accepts an
existing application database as the workload target. Creation must succeed before
that name can be cleaned up. No `DROP ... FORCE` or backend termination is used. An interrupted process may
leave an owned test database; inspect it manually before any separately authorized
cleanup rather than deleting all names with the prefix.
Connection/statement/lock timeouts are session-local. The deadline is cooperative
between bounded phases, not a process-kill or hard real-time guarantee.

Raw JSON reports stay local/ignored; CI does not upload them. Commit only reviewed,
sanitized findings such as [the measured results](CAPACITY_RESULTS.md). Report hashes
bind the workload, seed, bounds and source files. Numeric byte values and timings
are observations, not golden test constants. CI reruns the workload and validates
invariants; allocator/WAL results may differ across PostgreSQL versions/settings.

### Privileges

- **Disposable lifecycle:** a dedicated test-server role needs `CONNECT` on the
  maintenance database and `CREATEDB`. It owns only its newly created database and
  uses the already documented migration privileges there. Dropping that owned
  target is test teardown, not a retention operation. Never give these credentials
  to the normal application writer.
- **Collector/inventory reader:** `CONNECT`, schema `USAGE`, read access to the
  allowlisted research/job tables and their referenced metadata/artifact lengths,
  readable PostgreSQL catalogs/statistics, and ordinary size/clock/setting-function
  execution. No DML, DDL, ownership, `CREATEDB`, `pg_monitor` or superuser access is
  needed for these SELECTs under standard PostgreSQL permissions. A hardened target
  may need narrowly reviewed equivalent grants. These requirements describe the SQL;
  this increment does not provision roles or certify a production grant policy.
- **Optional WAL sample:** execution of `pg_current_wal_insert_lsn()` on a primary.
  If unavailable by permissions/recovery state, report null/unsupported rather than
  asking for broad privileges. No directory/replication/archive monitoring role is
  requested. Normal job-operation privileges remain unchanged.

The collector uses read-only repeatable-read transactions; its output exposes no
connection URL, database UUID, payload body or private job identifiers. Public job
labels are local ordinals. Database errors use fixed codes. Repeatable read stabilizes
logical rows, not simultaneous physical file sizes or asynchronously updated stats.

### Growth arithmetic

For each terminal state, proposed retained population is arrival rate × proposed
retention days; add active jobs separately. The report includes an illustrative
10 jobs/day × 30 days scenario and logical sensitivity to artifact sharing and extra
duplicate/revision metadata. These are arithmetic outside the 11-job observed range,
not permission to run 300 jobs. The physical/WAL extrapolation holds the mixed
fixture and compressibility constant; it deliberately does not claim to predict
physical changes from the logical sensitivities. WAL generation does not determine
WAL retained by checkpoints, archival failure or replication-slot consumers.

### Measurement references

PostgreSQL documents [size and WAL functions](https://www.postgresql.org/docs/16/functions-admin.html)
and [cumulative statistics](https://www.postgresql.org/docs/16/monitoring-stats.html).
Size categories, insertion-LSN deltas and tuple estimates have different meanings;
this collector does not turn them into disk-write I/O or exact-bloat measurements.

### Separate review required

Do not enable automated deletion, archive evidence, disable immutable triggers,
change an index/partition/schema, tune vacuum/replication settings, or apply a
migration on an existing database based only on this report. Each proposed change
needs a new scoped issue and review after these results are accepted. Provider and
licensing design is a subsequent, independently approved issue.
