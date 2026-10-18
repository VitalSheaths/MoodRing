# SIC, security identity and universe contracts v1

Implements the offline scope of [issue #6](https://github.com/VitalSheaths/MoodRing/issues/6).
Contract version: `universe-contract-v1`. No live provider, persistence migration,
production selection engine, data download or paper-trading route change is included.
The existing SEC contracts and ledger remain unchanged.

## Modules and reuse

| Module | Responsibility |
| --- | --- |
| `src/ingestion/universe/identity.py` | Immutable catalogue, dated aliases/SIC assignments, evidence, rights and canonical hashes. |
| `ranking.py` | Explicit policy/input contracts and exact earnings-yield/midrank primitives. |
| `snapshot.py` | Validation of supplied universe membership, eligibility, ranks, outcomes and hashes. |

Reuse `cik`, `explicit_time`, `utc` and the SHA-256 type from
`src/ingestion/sec/contracts.py`; do not introduce another timestamp guessing rule.
`IdentityCatalog.issuer_for_cik()` maps SEC issuer scope to a shared stable issuer.
The legacy SEC UniverseManifest remains compatible and is not a second persisted
securities master. There is no database or automatic migration in this increment.

## Identity and lifecycle

An issuer has a stable ID, normalized CIK, legal name, country and evidence. One CIK
maps to one issuer in a catalogue. An instrument has a distinct stable ID, issuer
link (none for indexes), type, name, country, units/currency and lifetime. Multiple
share classes may reference the same issuer, but universe membership counts it once.

Ticker/provider symbols are effective-dated mappings, with MIC and provenance;
changing a ticker or exchange does not create a new instrument. Overlapping mappings
for the same provider/instrument or provider/symbol are rejected. The conservative
v1 uniqueness is global within a provider namespace; the caller must use qualified
provider symbols when the provider otherwise has cross-market ambiguity.

Effective intervals are half-open. Inactive records need a lifetime end; their
historical lifetime can still be queried when the evidence was known at that cutoff.
Successor links reference distinct existing instruments with consistent lifetimes,
no ambiguous overlapping successor intervals and no cycles. An acquired instrument
ID is not reused for a successor. These are versioned catalogue snapshots, not a
claim that one record can encode all historical metadata corrections. Preserve old
catalogue vintages when an observation changes; do not overwrite history in place.

## Exact SIC source and primary assignment

Use the [SEC SIC list](https://www.sec.gov/search-filings/standard-industrial-classification-sic-code-list),
not GICS. Codes are ASCII strings, zero-padded to four characters; integers and
unknown codes are rejected at the boundary. Each source subset carries a version,
capture time, URL, scope and checksum over canonical code/description records.

`tests/fixtures/universe/seed.json` pins a curated ten-code public subset under
`sec-subset-20261018`. Its captured time is an illustrative fixture field, not
proof of an official historical full-list snapshot. The descriptions are SEC
labels; fictional companies and measurements are clearly marked synthetic. Source
SHA-256 references the committed seed bytes, while `codes_sha256` fingerprints the
normalized subset. No real issuer assignment or hottest-industry claim is made.

Exactly one primary assignment per issuer/effective interval is supported in a
catalogue; overlaps are rejected. Assignments reference a known issuer, taxonomy
version and code. Effective date is not availability: future/unknown evidence is
ineligible at cutoff. A historical corrected assignment needs the appropriate
catalogue vintage. Current SIC observations do not establish historical coverage.

## Policy and ranking contract

Every parameter is explicit: count (100), industry count (10), country/currency,
venues, valuation and liquidity definitions, component/industry weights, price/
liquidity/history/freshness thresholds, industry heat windows, allocation method,
primary-listing rule, missing-data policy, exclusions, reconstitution and tie rules.
There are no production policy defaults.

The fixture uses illustrative 40/30/30 company weights, 70/30 industry weights,
252-session history, 60-session liquidity, $5 price/$10m dollar-volume thresholds,
180-day fundamental freshness and quarterly reconstitution. Its policy is explicitly
`fixture_only` with a fixture review reference. **These are not approved production
ranking parameters.** Observed snapshots require a separately reviewed policy/ref.
A later selector needs an approved config/version and actual evidence before use.

Midrank percentiles are `(average_rank - 1)/(N - 1)`; a singleton and all-equal
population give 0.5. Earnings yield uses TTM common earnings / positive issuer-wide
market cap. Negative earnings are valid; missing earnings are not zero. Nonpositive
cap is invalid. Missing cap/earnings can be represented with a reason and absent
observation/period fields, then excluded without fabricated dates. Source values
use finite Decimal/string/integer inputs; binary floats are rejected. Percentiles
and scores use reduced exact fractions, avoiding Decimal-context rounding of ratios.

Optional company-equivalent and share-class counts remain distinct and require
provider basis, observation time and evidence. Do not infer free float, a fiscal EPS
denominator, or whole-company value from the wrong class count. Dollar volume must
have a declared measurement basis, provider and feed; mixed feeds or inconsistent
consolidated/single-venue definitions fail snapshot validation. Suspended or unknown
trading status is explicitly excluded.

The declared future policy is capacity-aware issuer-cap largest-remainder industry
allocation with one minimum seat, rather than equal industry quotas. A primary
listing uses declared liquidity/MIC/instrument-ID tie rules. This increment does
**not** implement industry heat, quota allocation, primary-listing discovery or
production candidate selection. It validates supplied snapshots and exact company
component ranks against their supplied eligible candidate population. It does not
prove that omitted candidates do not exist or that a provider population is complete.

## Snapshot validation and reproducibility

A snapshot pins catalogue/config/input hashes, version, source cutoff, publication
and effective time, dataset mode, coverage assertions, members, industry counts
and one selected/excluded outcome per candidate. Publication follows cutoff and
precedes/equal effective time. Collections are immutable tuples/frozensets and
set-like collections are canonically ordered. Hashes normalize UTC timestamps,
Decimal spelling, keys and explicit nulls; a pinned expected fixture digest catches
unintended schema/content drift. Changing the universe version changes its hash.

`complete` requires 100 unique issuers, ten SIC categories, matching positive
industry counts, unique ranks 1–100 and consistent instrument/SIC evidence. Component
ranks are checked within eligible industry groups; selected global ranks follow
score, cap, liquidity and issuer-ID ordering. The contract does not require ten
companies per industry. Fixture counts are **3, 5, 7, 8, 9, 10, 11, 13, 15, 19**.

`insufficient` publishes diagnostics and no partial membership. Candidate/category
insufficiency must agree with supplied eligible inputs; general selection-constraint
failure remains an explicit assertion until a selector is implemented. Invalid raw
records fail construction; missing valid observations receive explicit exclusion
reasons. Do not pad a roster or silently relax the policy.

Source availability gates historical eligibility. Local ingestion is separately
recorded: an offline as-of query does not prove a real serving process had the data
then. A PIT label requires declarations for historical candidates, delistings,
classification vintages and fundamentals plus a coverage reference. These are
caller-reviewed evidence assertions, not cryptographic verification of completeness.
`data_kind=synthetic` remains independent of research mode, so a synthetic PIT test
cannot be mistaken for a real PIT dataset. Current snapshots cannot simply be
relabeled PIT with missing coverage flags.

## Index identity and rights

Indexes have no fabricated issuer/CIK and cannot be tradable in these contracts.
They use index/volatility units, distinct from currency; calculation currency may
be null. This is identity validation only: no index feed or new execution guard
is wired into the existing API by this increment.

DataRights denies unknown, denied, unreviewed, not-yet-reviewed and expired rights.
A verified declaration requires a review ref/time; individual research, model use,
public display and redistribution are separate flags. This models intended-use
review, not a legal grant. No provider rights are assumed from endpoint separation,
a free software package, a public index name or a paper account.

## Tests and remaining scope

```bash
python -m pytest -q tests/test_universe_identity.py tests/test_universe_snapshot.py
python -m pytest -q
```

Tests expand the small committed seed into fictional catalogue/input/membership
fixtures. It includes share classes, a rename/exchange change, inactive predecessor,
separate successor and read-only synthetic index. Insufficiency/error fixtures
exercise missing/stale/future inputs, invalid identities, PIT labels and rights.

No new dependency, database, DuckDB/Polars, Parquet writer, OHLC adapter, model,
corporate-action accounting or cost change is introduced. Next steps require review:
approve numerical selection policy, implement and independently verify the selector,
then separately approve OHLC/fake-provider/job contracts. Live integration remains
outside this PR. Product and architecture scope are not silently changed.
