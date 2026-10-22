# Synthetic PostgreSQL measurement results

## Decision for review

Keep current synthetic bounds. These small runs justify further measured review,
not a production size budget, index change or retention implementation. No schema,
index, partitioning, vacuum or immutable-evidence policy was changed.

## Evidence

Three sequential fresh disposable databases on PostgreSQL **16.15**, 8,192-byte
blocks, `wal_level=replica`, full-page writes on, WAL compression off, tuple counting
on. The existing explicit migration ran only in those new databases. There were
11 jobs, 17 supplied source rows and 18 committed batch outcomes per run. Two jobs
completed, one completed with gaps, five failed, one was cancelled, one remained
running and one retryable. Duplicate/revision cases deliberately left a required
session missing, so their evidence does not imply complete coverage.

Workload SHA-256:
`6f8ebe9f30ea6517094537dff95256b9541a9f127bceaf9039952ed75d29ad57`.
Source-code SHA-256:
`a693c54b91d1de1dd7769e31ab9bf95610eaedba2c57218dbb2439a016fa33f4`.
All three runs used the same hashes. The runner records exact settings/bounds and
sample timestamps; local full JSON reports are not committed. The figures below
exclude migration WAL and baseline database creation. Runtime was 1.92–2.03 s for
the instrumented workload; this is not a throughput benchmark or service target.

## Byte observations

All three runs produced the same allocation/logical totals in this environment.

| Metric (bytes) | After migration | After workload |
| --- | ---: | ---: |
| Whole database | 8,240,151 | 8,649,751 |
| Allowlisted relations, total allocation | 221,184 | 630,784 |
| Main heap forks | 8,192 | 131,072 |
| TOAST totals, including TOAST indexes/forks | 81,920 | 188,416 |
| Ordinary table indexes | 131,072 | 237,568 |
| Selected logical column bytes | 0 | 180,848 |
| Unique raw artifact bytes | 0 | 33,606 |
| Raw bytes counted once per batch reference | 0 | 34,893 |

Aggregate serialized logical checkpoints were **213,711 bytes**, a different unit
from SQL column bytes and disk allocation. Deduplication saved **1,287 logical raw
bytes** versus storing the payload per reference. It does not imply the same number
of reclaimed disk bytes. Allocated relation growth was **409,600 bytes**; ordinary
index allocation grew **106,496 bytes**, chiefly when initial B-tree pages were
first populated. Page reuse means a phase can add evidence without growing files.

The 16 KiB compressible and varied invalid-payload fixtures were retained as failed
synthetic evidence, not accepted market observations. Their complete-job phases
grew relation allocation by 0 and 90,112 bytes respectively; the latter grew TOAST
allocation by 57,344 bytes. Those phase differences include job metadata as well
as raw evidence, so they do not isolate a universal artifact compression ratio.

## WAL and update observations

Sum of phase insertion-LSN deltas: **161,552 bytes in every run** across the three runs.
This is cluster-wide insertion activity observed during the phases, not per-database
attribution, retained WAL or physical disk writes. Background activity and full-page
images can contribute. The roughly 0.893 ratio of observed WAL bytes to selected
logical column bytes is an experiment-specific ratio, not a storage-write amplification
model. Physical index-write I/O was not measured; index allocation alone cannot
establish it.

| Phase | Observed WAL bytes | Relation allocation growth |
| --- | ---: | ---: |
| Five heartbeats | 2,512 | 0 |
| First committed observation | 6,240 | 90,112 |
| Exact replay | 152 | 0 |
| Larger, six-observation completed job | 39,816 | 81,920 |
| Duplicate-evidence job | 14,744 | 8,192 |
| Revision-evidence job | 16,152 | 40,960 |
| Compressible-payload failed job | 7,912 | 0 |
| Varied-payload failed job | 25,176 | 90,112 |

Replay preserved records, counters and logical evidence; its row-locking transaction
still generated WAL here. Heartbeats update coordination without adding accepted
records. Do not equate those operations with zero database cost.

The cumulative tuple views lagged exact rows: one sample reported 3 live manifests
while exact counting found 11; coordination/checkpoint update counts also reflected
only part of the workload. These are visibility/timing limits, not evidence of lost
rows. No statistics flush/reset or VACUUM was used. Exact bloat, retained WAL,
archive/slot retention and free filesystem capacity remain explicitly unmeasured.

## Retention dry run

At current measured age: **11 jobs held, zero candidates**. Under a clearly labeled
120-day what-if and illustrative state ages (completed 30d, gaps 60d, failed 90d,
cancelled 14d): **8 archive-review candidates, 3 held**. The running and retryable
jobs stay held; their completed parent is also held for dependency/replay evidence.
One artifact is shared. All **33,606 artifact bytes** remain protected, including
**172 bytes** referenced by retained jobs and **33,434 bytes** referenced only by
review candidates. No orphan bytes were found in this fixture. Proposed deletion
bytes are **zero**. Candidate status does not grant permission to archive/delete.

Malformed/missing/cyclic dependency inventories force a conservative hold-all.
Current versus what-if planning does not update timestamps or database state.
Inventory equality before/after planning is checked, and the owned disposable
database is dropped only as test teardown after measurements finish.

## Proposed limits and trade-offs

These are review proposals, not production configuration or an enforcement rollout:

- Keep the harness at 12 jobs, 128 source records, 100 steps, eight heartbeats,
  two sessions and its 120-second cooperative deadline. Warn during review if a
  run needs larger bounds; stop instead of silently increasing them.
- The harness's absolute database stop is 128 MiB. A proposed 96 MiB warning leaves
  headroom for that experiment only; neither value is a tested capacity of PostgreSQL.
  Free disk space is unmeasured, so an operator still needs a dedicated disposable
  server with enough independently verified space. No production alert was installed.
- Review logical checkpoint bytes, per-relation/TOAST/index allocation, exact rows,
  WAL deltas and deduplication together. With only 11 jobs, do not tune indexes,
  partitions or autovacuum from these measurements.
- Keep all terminal evidence until a separately approved retention design resolves
  dependencies, audit/replay requirements, shared artifacts, archive verification and
  recovery. This preserves reviewability at a storage cost. Proposed ages here are
  planning examples, not a cleanup policy.
- Retaining everything is simplest and preserves replay, but grows without a global
  quota. Archival could reduce database load but adds another evidence authority and
  verification process. Deletion conflicts with current immutable guards and requires
  its own design, migration, permissions and recovery review. No choice was implemented.

The report's 300-job arithmetic is outside the observed range. For example, holding
this mixed fixture constant gives about 4.97 MB selected logical columns and 11.39 MB
relation allocation including baseline. Sharing and duplicate/revision sensitivities
change logical estimates only. These numbers do not validate 300 jobs, real prices,
ten years of history, production throughput or future WAL retention.

## Next gate

Review this evidence and its limits first. Any schema/index/partitioning or retention
enforcement proposal gets a separate issue. Provider/licensing work follows its own
approval; real history, backfills, scheduler deployment and real-database rollout
remain excluded. See [the measurement runbook](CAPACITY_MEASUREMENTS.md).
