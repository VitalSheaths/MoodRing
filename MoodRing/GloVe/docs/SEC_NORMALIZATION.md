# Offline SEC metadata normalization

Issue #14 scope: supplied synthetic submissions-style JSON only. This is a
metadata adapter, not a live SEC client or verified provider implementation.

## Design and reuse (recorded before implementation)

| Concern | Reused authority / adapter responsibility |
| --- | --- |
| Filing identity and provenance | Existing `Filing`, `Provenance`, `Envelope`; adapter maps source fields and computes exact byte hash. |
| Universe eligibility | Existing SEC `UniverseManifest` and dated CIK membership; no new security master. |
| Duplicate/amendment rules | Extract the existing offline runner's classification into one shared pure function. |
| Accepted prefix and eligibility | Existing `Checkpoint` and `eligible_records`; no new checkpoint engine or durable storage. |
| Field evidence | Adapter result holds JSON pointers; no facts or alternative filing records. |
| Market jobs | Unchanged: `offline_market_fixture` is not an SEC job type. PostgreSQL is regression coverage only. |

The supported synthetic subset is `submissions-subset-v1`: root `cik` (ASCII digit
string or positive integer), `filings.recent` with aligned string arrays for
`accessionNumber`, `form`, `filingDate`, `reportDate`, `acceptanceDateTime`, and
`primaryDocument`. Empty report/acceptance strings mean unknown. Dates are strict
ISO dates; acceptance requires an explicit UTC offset. No timezone is inferred.
Accession prefixes may identify a filing agent and need not equal issuer CIK.

Other source keys are ignored after bounded JSON validation. They cannot override
recognized fields. Caller context separately supplies ingestion time, per-accession
public availability with basis/evidence, and verified amendment links. No timestamp
is inferred from filing date or acceptance. Missing availability is quarantined;
an amendment without a supplied base link is unresolved/retryable. A supplied link
still needs the existing base identity/family/period/time checks.

Limits: 1 MiB raw input, 1,000 rows, 64 KiB UTF-8 strings, nesting depth 64 and
4 MiB serialized result. Caller limits may only reduce these ceilings. Raw size,
string/nesting limits are checked before JSON decoding; duplicate keys and nonfinite
numbers are rejected. Context collections and outcome collections are bounded too.
A result-budget failure returns a fixed error, never a partial result.

Normalization is pure and bounded. Replay recomputes from the supplied bytes and
context; an optional previous result is only a binding check, not a resume cursor.
The binding includes raw hash, universe, context, adapter version and limits.
Results retain the existing committed prefix and stop on the first retryable row.
No later rows are silently counted as processed. Original row order is preserved.

Public summaries include only fixed codes/counts, never raw bytes, provider text,
source identifiers or private context. Internal envelopes/evidence are trusted
research data and are not public summaries. No dependencies, migrations, HTTP,
credentials, database rollout, scheduler, backfill or retention enforcement.

## Interface and replay

```python
from ingestion.sec.normalization import normalize, eligible

# payload_bytes and receipt_context are supplied by the caller, not fetched here.
result = normalize(payload_bytes, receipt_context, fixture_universe)
public_counts = result.public_summary()
research_records = eligible(result, aware_cutoff)
locally_received = eligible(result, aware_cutoff, require_local_receipt=True)
assert normalize(payload_bytes, receipt_context, fixture_universe,
                 previous=result) == result
```

`Limits` accepts strict positive integers only; boolean/coerced/increased limits
fail. Context requires an explicit aware `ingested_at`, optional per-accession
`availability` (`available_at`, `basis`, `evidence`) and optional `amendments`
(accession → verified base accession). All fields are validated. Unknown context
keys and versions fail; unlike source extras, these are caller configuration errors.
Context collection lengths may not exceed `max_rows`; individual evidence strings
have the existing 1,000-character contract bound. No clock or environment lookup
occurs. Optional source dates use the empty string for unknown, not epoch values.

`Result` contains a raw-byte hash, context hash, combined replay binding, original
row count, typed row evidence, bounded outcomes and the existing SEC checkpoint.
Rows retain seven JSON pointers: issuer and each recognized source field. Context
availability evidence remains inside existing provenance; amendment relationships
are supplied separately and never inferred from row adjacency. Rows are internal
metadata evidence even when quarantined; only accepted records enter the checkpoint.
A retryable event is visible but does not advance the committed cursor. Public
`remaining_rows` excludes the attempted retryable row; `committed_rows` reports the
accepted/quarantined/duplicate prefix. There is no claim that unvisited rows passed.

Replay verifies the complete input binding and recomputes the result rather than
trusting prior rows or counters. Changed whitespace/ignored fields change the raw
hash and binding too. Source IDs are accession-based, so conflicting metadata under
one accession within a payload quarantines rather than replacing accepted evidence.
Cross-payload reconciliation/durable SEC jobs are not added. The older JSONL fixture
runner can consume suitably small normalized rows with the same raw payload text;
its own file/line limits still apply independently.

## Safe failures

Structural failures raise `NormalizationError` with a fixed code: `invalid_input`,
`invalid_utf8`, `invalid_json`, `duplicate_json_key`, `nonfinite_json_number`,
`invalid_schema`, `invalid_issuer`, `misaligned_arrays`, `invalid_context_or_contract`,
`replay_context_mismatch`, or a `limit_*` code for the declared bound. Invalid
eligibility cutoffs raise `invalid_cutoff`. No original validation exception is
returned. Row outcomes reuse the SEC classifier's fixed reasons, with explicit
`unsupported_form` and retryable `missing_amendment_link` additions. The safe
summary contains counts only, not internal row IDs, raw strings, URLs or context.

## Trade-offs and validation

| Decision | Benefit | Limitation |
| --- | --- | --- |
| Supplied synthetic JSON subset | Reproducible parsing without external services | Does not prove current SEC schema compatibility or real data rights |
| Shared pure SEC classification | Existing duplicate/amendment/eligibility semantics stay authoritative | Does not generalize market job types or persist SEC jobs |
| Recompute bounded replay | No second resume engine or trusted mutable snapshot | Reprocesses up to the finite row cap; unresolved base stops the prefix |
| Exact raw-byte hashes and pointers | Evidence is attributable without inventing facts | Hashes do not establish publication, truth, licensing or local historical possession |
| Byte/string/depth limits before decode plus bounded result | Rejects large/deep inputs and avoids partial output | Bounds are logical input/output budgets, not a measured process-RSS guarantee |

Tests use the explicit synthetic manifest in `tests/fixtures/sec/submissions/`.
They cover supported forms, availability, dated membership, original/amendment
retention, conflicting source identity, unknown fields, strict finite limits,
pre-decode rejection, sanitized summaries/errors and existing JSONL checkpoint
parity. Network operations are forbidden in the normalization tests. Full regression
coverage includes the existing disposable PostgreSQL migration/recovery tests;
PostgreSQL is not accessed by this adapter.

Live SEC access, real market-data access, production backfills, scheduler deployment,
real-database rollout, retention enforcement and production universe selection each
remain separately reviewed increments. Issue #15 remains deferred operational work.

## Manual transport boundary

Issue #22 adds [bounded manual metadata/SIC access](SEC_ACCESS.md) and mocked
transport security tests. This normalizer remains pure and uses the same evidence
contracts. No real SEC execution or persistent ingestion is authorized by its PR.
