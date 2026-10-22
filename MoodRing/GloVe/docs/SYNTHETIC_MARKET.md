# Synthetic selection and supplied market pages

Issue #17: synthetic/current-snapshot only. Design recorded before implementation.

| Concern | Reused authority |
| --- | --- |
| Company identity, dated aliases, SIC and rights | Existing universe catalogue/evidence contracts |
| Eligibility, percentiles and scoring | `eligibility_reason`, exact ranking primitives and `UniverseSnapshot` |
| Daily data | Existing calendar, request, receipt, adjustment, observation and index reference contracts |
| Ingestion | Existing `SuppliedPage`, `Manifest`, `FakeStore`, `PostgresStore` and `run` |
| Persistence | Existing explicit migration only, on disposable test databases; no new tables |

Selector input is capped at 1,000 listing candidates, 100 industry signals and
8 MiB logical input/output. Only fixture-only policy is accepted. Choose one
eligible listing per issuer by dollar-volume descending, MIC, instrument ID.
Issuer-wide cap/earnings/SIC evidence must agree across listings. Missing earnings
exclude under existing policy; negative earnings remain rankable. All issuer
outcomes are retained, with ignored secondary listings reported separately.

Qualifying industries meet the policy's minimum candidates and have supplied
eligible momentum/volume evidence with matching declared lookback windows. Exact
midrank percentiles and momentum/volume weights choose ten industries, ties by SIC.
Each receives one seat. Allocate remaining seats proportionally to aggregate issuer
cap using floor quotas and largest fractional remainders, SIC ties; when an industry
hits capacity, redistribute the remaining seats over those with spare capacity.
No industry may exceed its candidate count. Rank within industry by exact weighted
cap/earnings-yield/liquidity percentiles and existing ties. Publish exactly 100
issuers/ten industries or an insufficient snapshot with no partial membership.
The complete selection-source hash contributes to the universe version, binding
industry signals, raw listing inputs, policy, catalogue, cutoff and bounds.

The provider adapter receives finite pinned synthetic pages and their exact bytes,
not URLs or credentials. Caps: 110 series, 31 days, four distinct request pages per
series (plus one supplied retry response each), 32 records/page, 1,000 total source
records, 256 KiB/page and 8 MiB aggregate bytes. It parses only its documented
synthetic `records` array into existing observations. No price, date, volume or
adjustment factor is generated when missing. Caller supplies receipts, mappings,
calendars, capabilities and rights. The adapter checks them before returning pages.
Existing orchestration/job budgets remain independent and cannot be increased here.

Index references remain read-only. Unknown/unsupported capabilities are explicit;
close-only references have no invented OHLC/volume. Split-adjusted values require
supplied factor hashes/vintages, distinct from raw. No price adjustment or account
corporate-action processing is performed. Source manifests bind exact payloads and
all page context; job manifests must verify the selected universe.

Issue #15 remains deferred. Capacity measurement is required before large-scale
persistence; this small synthetic exercise does not approve real data, backfills,
scheduler deployment, retention enforcement, live compatibility or database rollout.

## Interfaces and reproducibility

`select_synthetic(catalog, policy, candidates, signals, asof=..., published_at=...,
effective_at=..., limits=...)` returns a `Selection` containing the existing
snapshot, full source hash and ignored secondary-listing IDs. `IndustrySignal`
requires exact momentum/volume values, evidence, as-of and both policy lookbacks.
A source or measurement-basis conflict is rejected, not averaged. An insufficient
snapshot is an explicit result, not a provider exception or partial top-100 list.
Input sets are sorted before hashing; exact rational arithmetic avoids float drift.
Only the supplied pool is ranked: no candidate completeness or market-wide merit
is established. The caller must retain the source inputs to reproduce the selection;
job persistence stores its universe hash, not a new securities warehouse.

`FixtureAdapter(snapshot, series, capability, pages, index_mappings=..., limits=...)`
accepts existing `Series` contexts and `SourcePage` objects. A source page contains
an existing receipt and adjustment declaration, exact bytes, outcome, next cursor,
fixed failure code and retry index (0 or 1). JSON is strictly:

```json
{"data_kind":"synthetic","records":[{"session_date":"2026-10-19","open":"100","high":"105","low":"99","close":"102","volume":"1000"}]}
```

Optional row fields are `notional` and `supersedes`; price-index close-only rows
provide only session date/close. Decimal strings or integers are accepted; binary
JSON floats, nonfinite numbers, duplicate keys, unknown fields and nesting beyond
16 levels are rejected/quarantined. No source parser reads URLs, credentials or
files. Supplied receipt bytes must hash exactly; same bytes can be referenced by
multiple receipts but are not proof that those receipts describe observed markets.
Each record must resolve its pinned calendar session and dated symbol mappings.
The capability, local-research rights, index shape and adjustment vintage must allow
it. Missing active-session records remain gaps. Unsupported references preserve
explicit outcomes rather than becoming zero-valued prices.

`adapter.fetch(request, retry_index)` returns the existing `SuppliedPage`; unpinned
requests/retries produce fixed safe errors. Invalid bounded payloads become
quarantined pages, without raw exception text. `adapter.manifest(job_id, limits=...)`
binds source and selected-universe hashes and calls existing universe verification.
Run it with the existing fake or PostgreSQL store; no second orchestration loop,
migration or state machine was introduced. Retry timing/fencing/replay/coverage
remain the existing store's responsibility. Cyclic cursors or changed content under
a batch key are rejected by those same boundaries.

Constructor failures use fixed `limit_*`, identity/context, duplicate-source or
synthetic-scope codes. Public job reports use the existing safe projection; raw
payloads, catalogue/rights evidence and internal source metadata are not public
API responses. Normal Python callers remain trusted; these classes are not a public
hostile-client service or a cryptographic signature of provider truth.

## Demo and test boundary

```bash
PYTHONPATH=src python examples/synthetic_market_demo.py
python -m pytest -q
```

The standalone seed expands 191 illustrative companies across 11 SIC groups.
The selector publishes 100 companies across ten groups, with variable quotas.
Tests materialize all 100 selected equities plus two index-reference slots. A
full 100-equity/two-session fake run was also exercised during development, but
full-prefix validation is expensive; the routine demo and PostgreSQL recovery
exercise deliberately ingest a small subset selected from that same snapshot.
The normal demo ingests three equities and two references across two sessions;
its expected state is `completed_with_gaps` because the optional reference is
unsupported. PostgreSQL tests compare raw/split-adjusted jobs with fake results,
reopen fresh engines and replay acknowledgement loss. They use only the existing
uniquely named disposable test-database fixture. No real research database is touched.

The same CI job runs full PostgreSQL regressions, the existing paper demo and this
synthetic demo. Without `PAPER_TEST_POSTGRES_URL`, integration tests skip; skipped
checks are not persistence evidence. Receipt times are pinned in the past and
still checked against the actual store clock; no future-receipt guard is bypassed.

## Trade-offs

| Choice | Benefit | Cost / review boundary |
| --- | --- | --- |
| Supplied industry metrics and fixture policy | Tests priority and quota logic without a live feed | Not production selection or verified market momentum |
| Exact fractions and deterministic remainders | Reproducible scores, ties and capacity redistribution | No optimization for an unbounded candidate population |
| Existing typed market/job boundaries | Same validation, replay, coverage and recovery authority | Full-prefix validation becomes expensive as retained evidence grows |
| Tiny seeded source and subset persistence demo | Bounded repeatable CI and transparent gaps | Does not measure production throughput, WAL, retention or storage capacity |
| Existing explicit PostgreSQL migration | Real transaction/recovery regression coverage | No deployment, role provisioning or existing-database rollout |

Issue #15 should evaluate database/index/WAL growth and evidence-preserving
retention before scale is increased. Passing these fixtures grants no data rights
and does not authorize any live provider, real backfill or retention deletion.
