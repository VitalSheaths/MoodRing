# Provider compatibility and licensing review

Review date: **2026-10-24 (Pacific/Auckland)**. Scope: [issue #20](https://github.com/VitalSheaths/MoodRing/issues/20).
Status: documentation assessment for maintainer review; no provider approved for activation.

## Recommendation

Keep the public learning demo **synthetic/current-snapshot**. None of the three
reviewed options establishes a zero-cost, publicly redistributable, real-time
top-100 equity plus benchmark service with verified historical availability.
This is an assessment of the cited evidence, not a claim about every provider.

For a separately approved next increment, SEC filing metadata/SIC is the narrowest
candidate. Massive Basic is a conditional candidate for private end-of-day equity
research, subject to subscriber eligibility and storage/replay rights clarification.
Alpha Vantage is an alternative for small private experiments, not the preferred
top-100 source. No account, credentials, entitlement or live compatibility was tested.

Approval of this document would approve the assessment only. It would not select
a production provider or authorize implementation, access, downloads or rollout.
No replacement architecture is proposed; gaps below require a separate decision.

## Evidence and method

Only official public documentation, pricing and terms were read. Source identifiers
below link to the exact pages and name the relevant sections. **All were accessed
2026-10-24.** Pages can change; these are dated observations, not pinned contractual
copies. No terms were accepted, vendor contacted, API example executed, sample
response acquired or financial dataset downloaded. Documentation examples are not
verified payloads. This is engineering due diligence, not legal certification.

Rights labels:

- **P — documented permitted**, within the stated user/use conditions.
- **R — documented restricted** under the reviewed free/individual terms.
- **U — unresolved**: insufficient evidence for that specific use; block it pending
  authoritative clarification. Technical access, a public repository, endpoint
  isolation and simulation-only accounts do not confer rights.

No source license flags or existing `DataRights` declarations were changed.

## Bounded shortlist: three complementary options

| Option / reviewed scope | Documented capability | Constraint and consequence |
| --- | --- | --- |
| SEC EDGAR public submissions/XBRL and SEC SIC list | No-key REST, submissions history and facts; nightly bulk exists [S1]. Public access subject to fair access [S2]. | No stock OHLC, quote feed or benchmark prices. A CIK identifies a filer, not a share class. Cannot alone supply a ranked investable universe. |
| Massive **Stocks Basic**, individual plan | $0, five calls/minute, two years, end-of-day aggregates, reference and corporate-action access [M1]. | No free real-time bid/ask entitlement established. Paid streaming/bulk capabilities are not Basic permissions. Ten-year history is outside Basic. |
| Alpha Vantage free individual access | Daily raw compact: latest 100 observations; full daily history and Daily Adjusted are premium [A1]. Free quota: 25 requests/day [A2]. | Insufficient daily budget for even one request for each of 100 instruments; no quota multiplication. A documented education/open-source exception is not an entitlement granted to this project. |

A 100-symbol, one-request-each exercise would consume at least 20 minutes of
Massive Basic quota or four days of Alpha Vantage free quota, before retries,
identity, actions or references. This is quota arithmetic, not a proposed job,
throughput benchmark, schedule or permission to run it.

### Identity, history and causality

- **SEC:** submissions supply current names/symbols plus older-file references [S1].
  CIKs are not recycled; EDGAR indexes can be corrected or removed [S3].
  SIC stays the taxonomy: SEC describes codes as business classifications in
  disseminated filings [S4]. Capture classification evidence/vintages; current
  mappings are not historical security membership. Full issuer/class linkage,
  delisted-price coverage and a complete historical candidate population remain gaps.
- **Massive:** ticker details document CIK, FIGI and SIC fields and an as-of date;
  inactive lookup is separate. Crucially, SEC-derived fields may use report period
  rather than filing publication [M3]. **Inference:** dated reference lookup alone
  fails our knowledge-time requirement for fundamentals. Historical aliases,
  corrections and delisted availability need dataset-specific evidence, not a
  blanket survivorship-free claim.
- **Alpha Vantage:** listing-status documentation offers dated active/delisted
  lists after 2010-01-01 [A1]. That does not establish stable instrument identities,
  complete delisted prices, SIC vintages or historical fundamental availability.
  Those are unresolved requirements.

For every future source, distinguish market event/session time, evidenced public
availability, local response/receipt and ingestion. Financial period end is never
availability; a historical retrieval today does not prove local possession then.
Unknown availability stays unknown and fails causal eligibility. Original and amended
filings/facts and later price revisions must remain distinct.

### Market semantics and access boundaries

**Massive bars [M2]:** windows use Eastern Time; `t` is Unix milliseconds at bar
start. Split adjustment defaults on; `adjusted=false` requests raw bars. Empty
intervals can mean no qualifying trades. The limit counts base aggregates (default
5,000, maximum 50,000), not necessarily returned bars; `next_url` may continue.
No factor/vintage evidence satisfying our retained-adjustment contract is established.

**Alpha Vantage [A1, A2]:** raw daily OHLCV and adjusted-close semantics must remain
separate. The support methodology includes splits and cash dividends; it is not
interchangeable with Massive's split-only flag. Real-time/delayed US data requires
premium entitlement. No reviewed free streaming/bulk contract is established.

**SEC [S1, S2]:** older submissions use referenced files rather than market cursors;
nightly bulk exists but is outside this increment. Fair access caps aggregate user
requests at ten/second and requires identified automation. This ceiling is not a
target polling rate or an approved request budget.

**Indexes [M7]:** Massive lists indices as a separate subscription. Its Basic index
documentation excludes SPX, DJI, RUT and VIX; NDX/Composite inclusion does not prove
all reference slots are covered. Reference lookup success is not value entitlement.
No index plan is approved here. Preserve separate index/volatility units, calendars,
nontradable identity, price/return variants and rights; do not substitute an ETF/ETN
for the underlying index without a separately reviewed comparison definition.

**Existing project policy:** raw prices remain the immutable baseline. A future
chart may explicitly choose an adjusted view, but retained research requires factor
definition, hash and vintage evidence. An adjusted close must not relabel raw OHLC
or become a fill price. Even a transient view needs the correct adjustment semantics
and display rights. No chart default changes here. Account splits/dividends need a
separate ledger design; vendor adjustments do not update positions or cash.

## Rights matrix: dataset, audience and intended use

P entries describe the source grant, not MoodRing authorization. U is a blocker,
not implied consent. SEC entries concern public metadata/numerical facts; no blanket
public-domain claim is made for filer-authored exhibits, logos or narrative material.

| Intended use | SEC public metadata/facts | Massive Basic individual | Alpha Vantage free individual |
| --- | --- | --- | --- |
| Private individual research access | P: public developer access, fair-access conditions [S1, S2] | P: own personal, non-business use, eligible subscriber [M4, M5] | P: private individual research/testing, subject to EULA [A3] |
| Durable raw storage and historical replay | U: retention/corrections and exact material scope need review [S3] | U: access grant does not establish indefinite archive or post-termination reuse [M4] | U: durable retention/post-termination terms for intended archive not established [A3] |
| Private derived features/returns/model use | U: verify exact input/material scope; no stock returns supplied | U: intended model/training and retained-output scope needs confirmation | U: specific model/derived-output scope not established |
| Charts visible only to eligible individual | U: material-specific display review; no market charts supplied | P within own-use grant [M4, M5]; archival conditions remain U | P within personal display grant [A3]; archival conditions remain U |
| Paper-account valuation, private or client-facing | U / unavailable: no prices | U for private simulator; R for serving other users under individual plan [M6] | U private simulator; R for uses outside private individual scope without written agreement [A3] |
| Public beginner charts or API price redistribution | U: exact selected material/use needs review | R: individual subscription does not cover published charts or serving users [M6] | R under personal scope; separate agreement required [A3] |
| Public derived signals, returns or model outputs | U: review selected inputs and output | U: derived output can reveal underlying data; no automatic exemption [M6] | U: no established public derived-output grant |
| Proprietary index/exchange benchmark use/display | Unavailable | U for every proposed series/use; Stocks rights do not grant index rights [M7] | U; catalog presence is not a display/redistribution grant |

### Licensing conditions and unresolved questions

Massive's individual access grant is personal/noncommercial/nonbusiness; third-party
and additional terms apply, IP notices must not be removed, and termination ends the
grant [M4]. Nonprofessional eligibility must be checked for the actual subscriber,
not assumed from zero revenue [M5]. Redistribution requires appropriate commercial
rights [M6]; attribution alone cannot cure a restriction.

Alpha Vantage's license distinguishes private individual activity from broader use,
including use on behalf of an organization [A3]. An educational project label is
not a waiver. Its wrapper guidance asks preservation of original responses [A2];
a future design must reconcile internal diagnostic evidence with sanitized public
errors. It must never publish raw credentials or provider internals.

For each dataset, the next review must resolve attribution placement, permitted
cache/archive duration, termination/deletion duties, raw response retention,
derived/model-output rights, intended user classification and third-party exchange/
index entitlements. **No universal retention duration or attribution waiver was
established.** Preserve existing synthetic evidence; do not ingest material whose
terms conflict with immutable retention until that conflict is resolved separately.
No retention enforcement or archival mechanism is authorized.

## Existing contract reuse and compatibility gaps

| Existing authority | Required reuse in any separately approved adapter | Gap to resolve before implementation |
| --- | --- | --- |
| [SEC normalization](SEC_NORMALIZATION.md), `src/ingestion/sec/contracts.py`, `normalization.py` (#5/#16) | Filing/accession identity, provenance, availability, amendment links, bounded normalization and existing SEC checkpoint. | Real source schema, dissemination evidence, timezone handling and amendment discovery. Existing market job type does not accept SEC ingestion. |
| [Universe contracts](UNIVERSE_CONTRACTS.md), `universe/identity.py`, `ranking.py`, `snapshot.py` (#7) | Stable issuer/instrument IDs, dated aliases/SIC, exact ranking inputs, membership/config hashes and `DataRights`. | Cross-source/class identity, verified historical population, actual rights review and approved real selection policy. No parallel master. |
| [Market contracts](MARKET_CONTRACTS.md), `market/providers.py`, `bars.py`, `sessions.py`, `indexes.py` (#9) | Typed requests/pages/receipts, revision identity, raw/adjusted series, finite pinned calendars, coverage and reference-slot capabilities. | Vendor date inclusion/session semantics, publication evidence, adjustment factors/vintages, real calendars, index-specific rights and safe failure mapping. |
| [Offline orchestration](OFFLINE_JOBS.md), `jobs/contracts.py`, `offline.py` (#11) | Finite windows/pages/records/attempts/retries/depth/bytes/steps, required/optional series, atomic checkpoint and replay/fencing. | Current protocol coordinates synthetic pages. HTTP/auth/transport retries and live job types need separate approved design; no relabeling synthetic jobs. |
| [PostgreSQL jobs](POSTGRES_JOBS.md), `jobs/postgres.py`, `migrations.py` (#13) | Existing research-run identity, linked evidence/artifacts, transactional CAS/checkpoint and explicit migration preflight. | Current manifests and guards are synthetic-only. No evidence that a live job can be stored unchanged; no migration or real-database rollout approved. |
| [Synthetic adapter](SYNTHETIC_MARKET.md), `market/synthetic.py`, `universe/selector.py` (#18) | Deterministic selection/normalization lessons and explicit synthetic/current-snapshot labels. | Fictional fixtures do not establish observed top-100 membership, production policy, entitlement or current vendor compatibility. |

Paths in the last five rows are relative to `src/ingestion/` unless fully stated.
The paper ledger and deterministic decision engine remain authoritative for trading;
none of these research contracts connects a new quote source to orders automatically.

### Future transport assessment, not an implementation specification

1. A vendor continuation URL must be translated into a bounded local opaque cursor;
   credentials/private parameters never enter public pages, logs or checkpoints.
   URL origin, path and redirects would need validation before any future fetch.
2. A successful HTTP response does not prove complete sessions or entitlement.
   Map missing/suspended/unsupported/retryable/quarantined outcomes only with evidence.
   Unknown gaps stay unresolved; never manufacture zero bars, OHLC or index volume.
3. Vendor request IDs are provenance, not our idempotency keys. Preserve exact
   payload hash/receipt evidence and existing request/window/calendar/adjustment
   identity. A changed payload must not overwrite an accepted revision.
4. No reviewed endpoint establishes a complete correction feed, historical
   publication vintage or maintained full calendar. These gaps block PIT claims.
5. Any future retries must consume explicit finite budgets and retain the last
   committed checkpoint. Provider quotas cannot silently enlarge existing limits.
   Authentication denial is not an endlessly retryable coverage gap.

## Capacity and retention decision

[PR #19 findings](CAPACITY_RESULTS.md) are **environment-specific observations**:
three PostgreSQL 16.15 runs, each with 11 jobs, observed 409,600 bytes of relation
growth and 161,552 bytes of **cluster-wide WAL insertion activity**. PostgreSQL
version, page allocation, WAL settings, background activity and fixture composition
matter. These figures are neither per-database WAL attribution nor retained WAL,
physical I/O, throughput or deployment capacity.

The proposed **96 MiB warning and 128 MiB harness cap are experiment-only**.
No production limit, ten-year projection, index/partition tuning or scale increase
follows. Intended deployment sizing needs its own measured and approved workload.
All synthetic evidence remains protected; proposed deletion stays **zero**.

Local Parquet plus DuckDB/Polars could eventually simplify analytical reads, but
does not acquire source rights or solve PIT/retention. No dependency, file store,
object service or archival authority is introduced. Retaining raw evidence supports
replay but has storage and licensing costs; archival adds verification/recovery
work; deletion conflicts with current immutable evidence. Those decisions remain separate.

## Trade-offs and review decision

| Choice | Benefit | Cost / unresolved condition |
| --- | --- | --- |
| Keep synthetic public demo | Reproducible, no market-data entitlement dependency | Cannot claim current market realism or actual top-100 membership |
| Consider SEC first in a separate issue | Narrow filing/SIC scope reuses metadata work | Does not solve prices; still needs live-access, rights and availability review |
| Consider Massive Basic privately later | Useful documented daily-bar/reference interface | Individual-only rights, two-year depth, finite quota, archive/PIT gaps; no public price feed |
| Consider Alpha Vantage privately later | Simple small daily experiment | Free depth/quota and adjustment entitlement constraints; broader rights unresolved |

Maintainer choices now: accept this assessment, request corrections, or retain the
offline-only path. Acceptance opens no dependent issue automatically.
Any subsequent provider implementation needs a separately approved dataset, plan,
audience, rights evidence, finite workload, causal/coverage tests and compatibility
decision. Schema/index tuning, retention enforcement, archival design, deployment
measurements and real historical backfills each remain separate reviewed work.

## Validation and acceptance evidence

- Compared three options using the official sources below; no external dataset
  or provider SDK was used.
- Checked contract references against main at `9e7ffd3b51e0482ae18ca6d971eea7166588ea8d`.
  Existing PRD, architecture, data models and rules remain unchanged.
- Capability observations and rights decisions are separate; U entries explicitly block
  unsupported uses. No runtime, dependency, config, fixture, schema or database changes.
- No runtime tests or demos were run for this documentation-only assessment; no
  live compatibility, performance or rights grant is claimed.
- Final gate: maintainer review of this PR. Issue #20 closes only on approved merge.

## Official source register

All accessed 2026-10-24. Section names make observations reviewable without copying
terms or proprietary payloads. Recheck documents and actual applicable agreement
before a future approval; pricing and help pages do not override a contract.

| ID | Official page and relevant section |
| --- | --- |
| S1 | [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces): introduction, submissions, XBRL, bulk. |
| S2 | [SEC Developer Resources](https://www.sec.gov/about/developer-resources): Fair Access. |
| S3 | [Accessing EDGAR Data](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data): corrections/removals, indexes, CIK. |
| S4 | [SEC SIC list](https://www.sec.gov/search-filings/standard-industrial-classification-sic-code-list): classification description. |
| M1 | [Massive pricing](https://massive.com/pricing): Stocks Basic versus paid stock plans. |
| M2 | [Massive custom bars](https://massive.com/docs/rest/stocks/aggregates/custom-bars): plan recency/history, adjusted, limit, timestamp, continuation and empty intervals. |
| M3 | [Massive ticker overview](https://massive.com/docs/rest/stocks/tickers/ticker-overview): identifiers/SIC, date semantics, inactive lookup. |
| M4 | [Massive individual terms](https://massive.com/legal/individuals-terms-of-service): §§2, 5, 6.1, 8.4 and 15.2. |
| M5 | [Massive stock subscriber classification](https://massive.com/knowledge-base/article/what-are-pro-and-non-pro-classifications-for-massives-stock-data): Applies to / What it decides. |
| M6 | [Massive redistribution guidance](https://massive.com/knowledge-base/article/how-can-i-redistribute-massives-market-data): What counts as redistribution, derived values. |
| M7 | [Massive Basic index coverage](https://massive.com/knowledge-base/article/are-all-indices-available-through-the-basic-subscription-tier): listed indices, VIX on Stocks, reference versus values. |
| A1 | [Alpha Vantage documentation](https://www.alphavantage.co/documentation/): Daily, Daily Adjusted, Listing/Delisting Status. |
| A2 | [Alpha Vantage support](https://www.alphavantage.co/support/): request limits, realtime/delayed policy, adjustment methodology, wrappers. |
| A3 | [Alpha Vantage terms](https://www.alphavantage.co/terms_of_service/): §§2–5, personal scope and commercial classification. |
