# Offline ingestion jobs

Issue #10 boundary: version `job-contract-v1`, `src/ingestion/jobs/`.
This is a deterministic in-memory coordination exercise, not persistent ingestion.

## Reuse matrix

| Merged contracts | Reuse / ownership |
| --- | --- |
| SEC, PR #5 | `utc`, explicit availability/precision boundaries and payload digest are reused through market/universe types. SEC filing/accession identity, provenance, amendments, eligibility and the existing SEC checkpoint runner remain authoritative and unchanged. This increment does not process or re-model filings. |
| Universe, PR #7 | `Instrument`, dated aliases embedded in observations, `Time`, `Id`, canonical hashes, universe snapshot hashes and rights declarations remain authoritative. No replacement master, ranking or rights representation. |
| Market, PR #9 | `RequestParameters`, `ProviderPage`, `ProviderReceipt`, `DailyObservation`, revision validation, `CoverageReport`, calendars and `IndexReference` are coordinated directly. No second OHLC/page/coverage schema. |
| Existing research run tables | Remain unchanged. New job models are offline coordination contracts only; any later schema mapping/migration must be reviewed separately. |

No architecture departure is proposed. The module supplies only job/attempt/lease,
limits and batch/checkpoint coordination that these existing modules do not supply.
The fake may obtain synthetic pages and commit validated results. It contains no
HTTP, provider auth/parser, quota discovery, scheduler, filesystem or database I/O.

## Models, identity and state

`contracts.py` defines immutable job/series manifests, required finite `Limits`,
attempts, leases, progress, committed batches/results and a snapshot. `offline.py`
defines a typed synthetic-provider protocol, `FakeStore` and `run`. The test provider
is in `tests/job_fixtures.py`; it builds existing market models, not vendor responses.

A manifest binds source/universe/config hashes, provider/feed/dataset/version,
request windows/calendars/adjustment, required versus optional series and code
version. Its config digest is verified against limits, partial-success, lease and
backoff and required coverage-basis settings. `verify_universe` checks the supplied snapshot hash and equity
membership/identity; the fixture calls it against the existing synthetic universe.
Indexes reference the existing `IndexReference` contract. No universe is selected.

Request identity hashes provider/feed/dataset/version and the complete typed request,
including window, cutoff, calendar/version, adjustment, cursor and record limit.
Response identity hashes the complete typed page, supplied observations and actual
payload digest. Batch identity hashes manifest identity, request identity and retry
ordinal. Exact key/content replay returns the original `CommitResult`, even with
an old expected version. Changed content under that key rejects. Lease/fencing checks
precede replay, so the original worker cannot use replay to bypass takeover.
Responses are exact fixture evidence: changed transport timestamps count as changed
batch content, while accepted market revision identity still deduplicates semantic
retrievals according to the existing market contract.

Lifecycle:

- `claim`: pending/paused/retryable → running, or takeover of an expired running
  lease. Every claim adds an attempt and a higher fencing token. An unexpired
  competitor is rejected; a retry cannot be claimed before its due time.
- running → paused/retryable/completed/completed_with_gaps/failed/cancelled.
- paused/retryable → cancelled; resumption uses `claim`, not a free state mutation.
- Terminal states are immutable. Read-only `run` on a terminal snapshot returns
  its result unchanged. A recovery has a different job ID and a `recovery_of`
  reference; history is not rewritten. References are metadata, not a durable
  cross-job registry or proof of a predecessor's state.

UTC comes from an injected clock. No sleeping or real scheduler is present.
Heartbeat renews a live lease; expiry rejects heartbeat/commit. A later production
implementation needs authoritative database time, transactional CAS and fencing,
not this process-local `RLock`. Expected checkpoint version and worker token are
checked under that lock. Snapshots validate batch/request identities, cursor/retry
progress derived from commits, versions, attempt sequence and lease ownership.

## Exact fixture limits

All values are required positive integers, except retries/depth which may be zero.
There are no production defaults. `tests/fixtures/jobs/policy.json` pins:

| Bound | Fixture maximum |
| --- | ---: |
| Instruments or reference slots | 6 |
| Inclusive requested calendar-date span | 31 days |
| Successful pages per instrument | 3 |
| Records per requested/returned page | 2 |
| Total observed records, including duplicates/quarantine candidates | 12 |
| Claims/attempts | 5 |
| Retries per page/cursor | 2 |
| Continuation references per series | 2 |
| UTF-8 serialized checkpoint | 2,000,000 bytes |
| Committed orchestration dispatch steps | 20 |

A retry response consumes a dispatch step but not a successful page. Request limits
must fit the page record bound before dispatch. Total records uses the larger of
supplied observation count and declared checksums. The checkpoint size includes
manifest, attempts, receipts, records and embedded payload hex, so it is deliberately
conservative. Initialization, lease/state updates and staged batches all respect it.
The fake artifact cache, including orphans, also cannot exceed this byte budget.
A too-small initial budget rejects construction with a fixed code.

A limit rejects the candidate and preserves the last committed records/cursor.
`run` normally records a failed state with that code. If even the terminal metadata
would exceed the byte budget, the returned failure is explicit and the last valid
snapshot is retained unchanged. No oversized checkpoint is published.
Pre-commit injected crashes exit the invocation; they do not run an unbounded loop.
The bounds describe this offline exercise, not network quotas, physical HTTP
attempt accounting or protection from arbitrary hostile Python provider code.

## Atomic boundary and crash outcomes

The store stages a complete immutable candidate before replacing its snapshot.
Raw bytes are digest-addressed in an in-memory artifact cache. Accepted/quarantined
outcomes, counters (derived from commits) and cursor become visible together.
Corrupt or unknown-availability evidence is quarantined without advancing its
cursor. Its series stops with a gap. Orphan bytes are not accepted evidence.

| Injection point | Required visible result |
| --- | --- |
| Before artifact verification/creation | No batch, counter or cursor change. |
| After artifact creation, before commit | Same checkpoint; an unreferenced orphan may remain in the fake cache. |
| During candidate commit | All-or-nothing; staged records/counters/cursor remain invisible. |
| After commit, before acknowledgement | Commit remains visible; replay returns its original result without increments. |
| After acknowledgement | Serialized/reconstructed state contains the same committed checkpoint. |

`export()` returns bytes; it writes no file. `restore()` requires the expected
manifest and an externally supplied SHA-256, enforces size and revalidates structure,
progress, revisions and completion. The digest detects changed bytes, not authenticity
against someone who controls both state and its expected digest. Embedded payloads
allow in-memory reconstruction; they are private checkpoint evidence, not public
results or a proposed production artifact-storage layout. Missing/corrupt bytes
cannot authorize an accepted batch. No filesystem durability or multi-process
transaction claim follows from this fake.

## Counter definitions

Counters are derived from the immutable committed prefix; they are never incremented
independently of records/checkpoints. Attempt count comes from explicit lease claims.

| Counter | Unit |
| --- | --- |
| request_count | Committed synthetic dispatch outcomes, including safe provider failures. |
| attempt_count | Worker claims, including resumed/takeover attempts. |
| page_count | Successfully committed complete/partial pages. |
| transport_response_count | Committed typed responses, including retry/unsupported/quarantine responses. Exceptions have no typed response. |
| raw_artifact_count | Distinct actual payload digests referenced by committed responses; excludes orphans. |
| accepted_unique_record_count | Distinct market observation keys across accepted revisions. |
| accepted_revision_count | Distinct accepted market revision IDs, including originals. |
| duplicate_count | Accepted observation appearances minus distinct revision IDs. |
| quarantined_count | Committed rejected responses/batches, including a corrupt unparseable payload; not an invented count of rows inside corrupt bytes. |
| retryable_count | Committed retryable responses or safe provider-failure outcomes. |
| unsupported_count | Committed unsupported responses. |
| missing_session_count | Remaining active-session provider gaps, unsupported or suspended outcomes across all series. |

Replay adds nothing to these totals. Actual pre-commit fixture calls can be inspected
in the test provider's `calls` trace; they are not falsely claimed as committed
checkpoint telemetry. A real transport attempt journal cannot be rolled back with a
record transaction and requires its own later design. This distinction keeps the
specified pre-commit atomic counter semantics precise.

## Coverage and required/optional example

Each series retains the existing `CoverageReport`. The required `coverage_basis`
chooses active-lifetime or entire-requested-window completeness; the fixture
explicitly chooses active lifetime. Full-window policy cannot treat pre-listing
or post-delisting dates as a completed ten-year history. Validated accepted observations
fill its supplied completed sessions; source eligibility, adjustment vintages and
revision checks still apply. Unsupported responses label unfilled sessions explicitly.
Series outcomes are aggregated only after each series stops or reports retryable.

The pinned example includes required equity/index, an optional reference, an
unsupported optional reference, a series that retries once and a corrupt-payload
series. Stocks reuse members from the existing synthetic universe; indexes remain
nontradable with unknown public rights. The universe remains labeled
`current_snapshot`: using its membership for an earlier historical window is not
PIT universe selection or evidence against survivorship bias.

- Clean required coverage under the chosen basis, with no optional gaps → completed.
- Required gaps or quarantined results with `allow_partial=false` → failed.
- Any known optional gaps/quality failures, or required gaps explicitly allowed by policy →
  completed_with_gaps. Unsupported optional references are never silently omitted.
- A retryable dispatch → retryable with an explicit next-retry time; no real wait.
  Budget exhaustion → failed with a fixed limit code, without cursor advancement.
- One completed instrument cannot finish a job while another series is pending.

Public summaries identify full/partial required coverage, missing required sessions,
unsupported optional series, quarantine, retryable gaps and unexplained provider
gaps. They contain hashed job/series references, fixed states/codes, counts and due
time only. Raw pages, payloads, aliases, worker names, parameters, source URLs and
provider exceptions are excluded. Security tests use inert sentinels, not credentials.
The committed JSON fixture contains no headers, signed URLs or private metadata.

## Trade-offs and approval gate

| Decision | Benefit | Limit / later review |
| --- | --- | --- |
| Reuse existing data contracts | Identity, revisions and coverage have one owner. | This orchestrator does not implement SEC parsing or universe selection. |
| Immutable snapshot + single locked assignment | Simple atomicity and replay tests, including concurrent CAS. | Process-local only; no durable transaction or distributed lease guarantee. |
| Retain typed evidence/payloads in memory | Deterministic reconstruction and byte checks without I/O. | Space-heavy; later persistence must review artifact retention, rights and schema mapping. |
| Separate committed telemetry from physical calls | Pre-commit counters cannot drift from accepted evidence. | Production transport observability needs a separate attempt journal. |
| Explicit optional-gap policy | Unsupported references cannot silently imply full coverage. | No real market coverage is established by the fixture. |
| Retry due-time metadata | Tests use a deterministic clock without sleeping. | No scheduler or real rate-limit discovery. |

The existing research `src/ingestion/storage/postgres.py` run table has start/end,
status and fetched/inserted/error fields. It is neither replaced nor extended here.
A later reviewed persistence issue must map these contracts to that operating model
and test real transaction/restart behavior before claiming durability.

Completion authorizes only these contracts, in-memory fake store/orchestrator,
fixtures, tests and documentation. PostgreSQL migrations, durable job tables,
object storage, production schedulers, live credentials, ten-year downloads,
production universe selection and public ingestion endpoints remain excluded and
require a separate issue and review based on the measured fake behavior.

## Verification

```bash
python -m pytest -q tests/test_offline_jobs.py
python -m pytest -q
PYTHONPATH=src python examples/paper_demo.py
```

The focused suite passed **55 tests**. The full local suite passed **236 tests**,
with **5 optional PostgreSQL tests skipped** because no disposable server was
configured, and the existing Starlette/AnyIO deprecation warning. The offline
paper demo, Python syntax, documentation file links and diff whitespace checks
also passed. Hosted PostgreSQL coverage must be reported from its actual CI result.
No test here proves production recovery, live provider availability or real coverage.
