# Daily market-data contracts

Issue #8 boundary: `src/ingestion/market/`, version `market-contract-v1`.
This increment adds offline models and pure validation helpers only. It reuses
`Contract`, `Exact`, `Time`, `Id`, `Evidence`, `Instrument`, `SymbolMapping`,
`DataRights` and canonical hashing from the universe contracts, and SEC UTC rules.
There is no new ledger, database, dependency, provider connection or job runner.

## Module and identity decisions

- `sessions.py`: pinned session schedules, explicit date windows and coverage.
- `providers.py`: capabilities, sanitized typed requests, receipts and fake pages.
- `bars.py`: immutable observations, adjustment metadata, revisions and deduplication.
- `indexes.py`: finite reference registry and capability declarations.

Series identity includes stable instrument ID, provider/feed/dataset, daily interval,
calendar/version/session mode, units/currency and adjustment basis. Observation
identity adds session date. Semantic checksum adds values, session boundaries and
adjustment factor/vintage. It excludes transport timestamps, alias spelling,
payload packaging, revision pointers and source availability. Those remain separately
hashed provenance evidence. Repeated revision identity means duplicate; differing values
need a supersession link. No old observation is overwritten by a pure helper.

Raw OHLC is the immutable baseline. Adjusted views need reproducible factor and
vintage references. Price, split-adjusted, dividend-adjusted and total-return bases
are explicit. These declarations do not calculate factors or apply account actions.
Close-only index observations do not manufacture open/high/low or volume.

Implementation details, fixtures and measured validation results follow in this
same document. Live ingestion, persistence, market-data rights review, real calendar
coverage and corporate-action accounting remain separately approved increments.

## Record rules and precision

`DailyObservation` embeds the approved `Instrument` and `SymbolMapping` types.
Stocks also carry a separate canonical mapping; provider aliases can differ from
the canonical ticker. Both require a matching MIC. The record verifies
instrument/provider/mapping/symbol consistency, lifetime and known alias
availability at session close. Full-catalog uniqueness is still the responsibility
of `IdentityCatalog`; an embedded record alone cannot prove global identity coverage.
Prices/volume/notional use the existing finite `Exact` Decimal boundary (40 digits,
12 decimal places), rejecting binary float and bool input. No rounding or fill
simulation is performed. Prices and supplied volume/notional are nonnegative;
zero is a reported value, never a missing-data substitute. Stock observations need
OHLC and matching currency units. Index observations use index/volatility points,
may be close-only, and must have null volume/notional. Stock OHLC ordering is
`low <= min(open, close) <= max(open, close) <= high` (also applies to full index bars).

Timestamps require explicit offsets and normalize to UTC through existing SEC
validation. Session dates are local to the declared IANA timezone; same-day session
boundaries must agree with that date. Overnight-session instruments are outside
this daily U.S. reference increment. The session mode distinguishes regular,
extended and index-observation windows; an index calendar is not assumed to be NYSE.

Raw, split-adjusted, dividend-adjusted and total-return bases are separate series.
Nonraw observations require a factor reference, its SHA-256 and an available vintage.
The contract distinguishes the labels but does not certify a vendor's adjustment
formula. Optional `adjusted_close` has its own basis and metadata: it cannot relabel
the raw open/high/low/close. Factor payloads, definitions and index price-versus-return
variants must be reviewed before retained research uses them. A chart consumer may
choose split-adjusted views explicitly; this increment adds no chart default or
adjustment computation. Corporate-action ledger transactions remain separate.

## Checksums, evidence and revisions

| Identifier | Contents / purpose |
| --- | --- |
| `series_key` | Schema version, stable instrument, provider/feed/dataset/dataset version, interval, calendar/version/mode/timezone, units/currency and adjustment basis. |
| `observation_key` | Series key plus local session date. |
| `semantic_checksum` | Observation key, pinned session, shape, OHLC/volume/notional and adjustment/adjusted-close metadata. |
| `revision_id` | Observation key, semantic checksum and predecessor revision ID. Distinguishes a later correction returning to an earlier value. |
| `receipt.content_hash` | Complete normalized receipt, including request, source availability, transport/ingestion evidence and payload hash. |

`validate_revisions` consumes supplied append order. An initial observation cannot
have a predecessor. A correction must point to the latest revision for that exact
observation key and have nondecreasing ingestion and known source-availability
times. Same revision on another retrieval deduplicates. Reverting to an earlier
value is valid with a new predecessor; attaching a new revision link to unchanged
latest values is rejected. The helper returns originals and corrections without
mutating them; it is not a database append-only constraint or an as-of query engine.
`validate_series` additionally rejects mixed feeds/calendars/adjustment modes.

Source availability is intentionally outside the semantic checksum, so acquisition
evidence can vary without inventing a price revision. Retain receipts independently
in a future evidence store; the deduplication helper returns the first observation
for each revision, not every acquisition receipt. Unknown source availability is
valid metadata but fails `eligible(cutoff)`. Known availability for daily observations
cannot precede session completion. Eligibility uses evidenced source time and factor
vintage, not the time of a later historical download. This does not manufacture
historical source availability or establish a complete PIT archive.

A receipt requires request <= response <= ingestion and known source availability
<= response. Payload references are `sha256:<digest>` and must match the declared
payload hash. `verify_payload(bytes)` verifies bytes using the shared SEC digest
helper; model construction alone cannot prove the object exists. No files are
opened by the contracts. Request, run and cursor references are bounded opaque local
IDs, never raw vendor cursors, signed URLs or filesystem paths. Parameters are typed
and allowlisted, with no arbitrary headers or credential dictionary. Adapters must
sanitize their source references before construction; there is no claim to detect
a secret deliberately disguised as a normal symbol/ID. Validation failures use fixed
codes; no raw provider exception is carried in the page envelope.

## Windows, calendars and coverage

`DateWindow` states both endpoint inclusion flags, an explicit cutoff, calendar
ID/version and leap-day policy. `ten_years` defines `(end minus ten calendar years,
end]`; Feb 29 maps to Feb 28 or March 1 as explicitly requested. This is a calendar
interval, not 2,520 assumed trading days. `completed_sessions` excludes sessions
closing after cutoff. It refuses to extrapolate beyond the supplied calendar range.
`last_completed` is only the last supplied completed session, not a promise that the
fixture contains the latest actual exchange session.

`SessionCalendar` is a finite pinned schedule: every date is either an explicit
session or non-session, without duplicates. The JSON fixtures cover a DST weekend
and a holiday/early close. They are illustrative schedules, not a maintained exchange
calendar or proof of historical session completeness.

A `CoverageReport` supplies each requested date exactly once. Non-sessions,
incomplete sessions and pre-listing/post-delisting dates must match the supplied
calendar and lifetime. Active completed sessions are present (checksum required),
suspended, unsupported or unexplained provider gaps. Requested completeness and
active-lifetime completeness are separate. Suspension/unsupported classifications,
lifetimes and observation checksums are caller assertions; these models validate
consistency, not external truth or a stored observation join. Never turn missing
sessions into zero bars. A real manifest/calendar comparison remains adapter work.

## Index references and provider pages

`INDEX_SLOTS` defines 22 reference slots: eleven broad-market/volatility references
and eleven sector references. Names/administrators identify intended registry slots;
all provider aliases, actual history, availability and entitlement remain unverified.
Sector slots are benchmark references, not a new company taxonomy or a mapping from
SIC to proprietary sector classifications. No licensed constituents or index values
are included. Price and total-return variants require separate instrument IDs;
VIX requires the volatility variant/unit. Every instrument remains nontradable,
without a fabricated issuer/CIK. All default fixture capabilities are unknown and
all public-use rights deny access. A registry entry does not enable API access.

A supported declaration requires coverage start, shape, mapping reference and
matching provider evidence. These are declarations, not external verification.
`ProviderCapability.supports` checks declared kind/interval/basis/request limits,
UTC-date coverage bounds and evidence eligibility. Coverage bounds are half-open
UTC-date labels; a future adapter must normalize vendor date semantics. Technical
support and `DataRights.permits` are separate checks: a supported capability never
grants local-research, public-display, redistribution or model-use rights.

`ProviderPage` references observation checksums, not arbitrary response dictionaries.
Complete pages cannot continue. Partial pages may have a continuation, or none when
known gaps cannot be paged away. Unsupported, quarantined and retryable outcomes
have finite safe failure codes and cannot publish observations or continuations.
`validate_page_sequence` checks a supplied chain starts without a cursor, request
identity remains stable, continuations match, cursor cycles do not occur and receipt
times are ordered. It deduplicates repeated observation checksums across pages.
It does not fetch pages, resolve checksums, infer gap causes, retry or persist a cursor.

## Trade-offs and later approval boundaries

| Choice | Benefit | Cost / limitation and next gate |
| --- | --- | --- |
| Reuse universe/SEC types | One identity, precision and time boundary; no competing master. | Coupled to their versions; changing shared semantics needs compatibility review. |
| Embedded identity and receipt evidence | A fixture/record is inspectable without a database. | Repetition increases record size; normalized storage references need a later persistence contract. |
| Raw baseline plus explicit adjusted series | Protects original evidence and prevents mixing split/dividend meanings. | No factors are calculated; adjusted analysis requires reviewed source factors and vintages. |
| Separate semantic and revision hashes | Transport retries deduplicate and corrections/reversions remain attributable. | Acquisition receipts need separate retention; hashes alone do not prove source truth or storage immutability. |
| Finite supplied calendars | Deterministic offline tests without another dependency or hidden holiday assumptions. | No full calendar coverage; actual schedules, overnight needs and updates require separate integration review. |
| Explicit per-date coverage | IPO gaps, holidays and unexplained misses cannot silently collapse into zeros. | Larger manifests; later storage may compress ranges while preserving these semantics. |
| Unknown index capabilities and denied rights | Can name required references without inventing vendor support or permission. | No benchmark data is usable publicly until coverage and rights are separately verified. |
| Typed page/receipt envelopes | Bounded request semantics and safe failure codes without provider internals. | No resilience claim: retries, durable jobs, rate limits, storage atomicity and checkpoints are later work. |
| No new database/analytics dependency | Current offline demo and tests remain runnable with existing setup. | Does not deliver a history store; local Parquet + DuckDB remains a separately reviewed increment. |

No departure from the approved architecture/product scope was needed. These choices
implement the reviewed contract boundary; they do not authorize live data, a chart,
ledger corporate actions, public benchmark endpoints, models or deployment.

## Verification for this increment

Using the existing Python 3.11 development environment:

```bash
python -m pytest -q tests/test_market_bars.py tests/test_market_sessions.py tests/test_market_providers.py tests/test_market_indexes.py
python -m pytest -q
PYTHONPATH=src python examples/paper_demo.py
```

The 72 new offline checks cover values/units, adjustment modes, source timing,
aliases/MIC, checksums and correction reversions, finite calendars and coverage,
reference slots/variants/rights, raw payload bytes and fake page chains/failures.
The local full suite passed **181 tests**, with **5 optional PostgreSQL tests
skipped** because no disposable PostgreSQL server was configured. The existing
Starlette/AnyIO deprecation warning remains. The offline paper demo also passed.
Hosted PostgreSQL coverage must be reported from its actual workflow result;
these local results do not establish that coverage.
