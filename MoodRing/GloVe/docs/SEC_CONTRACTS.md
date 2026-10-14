# SEC ingestion contracts v1

This increment implements [issue #4](https://github.com/VitalSheaths/MoodRing/issues/4)'s **offline contracts**, not a live SEC adapter.
Product and architecture scope are unchanged. No dependency, database migration,
API, paper ledger, experiment tracker or timestamp-parser implementation is changed.

## Location and interfaces

`src/ingestion/sec/contracts.py` contains frozen Pydantic v2 contracts using the
existing dependency. `offline.py` exercises them on synthetic UTF-8 JSONL records.
Contract version is `sec-contract-v1`; unknown versions and extra fields fail validation.

| Contract | Meaning |
| --- | --- |
| UniverseMember / UniverseManifest | Versioned ticker/CIK mapping; UTC effective intervals are half-open and must not overlap for the same ticker. |
| Provenance | Source, raw payload reference and SHA-256, ingestion, optional public availability with basis and evidence. |
| Filing | CIK, accession, supported form, filing date, report period, acceptance, SEC archive URL and explicit amendment relationship. |
| NormalizedFact | Optional supplied value with unit, period and source span; missing value requires a reason. No value extraction/calculation is implemented. |
| Envelope | Filing plus optional normalized facts from that accession. |
| Outcome | Accepted, duplicate, quarantined or retryable with a stable reason and input line. |
| Checkpoint | Source/universe fingerprints, completed-line cursor, accepted records and completed outcomes. |

CIKs are positive ASCII digit strings normalized to ten digits; accession numbers
retain their dashed format. Accession submitter prefixes need not match the issuer
CIK. URLs must match the issuer/archive/accession/document path. The initial forms
are 10-K, 10-Q, 8-K and their `/A` amendments. Other forms are rejected in v1.

An amendment requires a known base accession of the same issuer and filing family.
Unresolved base relationships produce a retryable outcome. Original evidence is
retained alongside later amendments. No inferred replacement of an entire filing
or partial XBRL restatement is performed.

## Availability and precision

All contract datetimes require an explicit offset and normalize to UTC. A filing's
report period/date or acceptance alone is not a public-availability timestamp.
Availability is optional, but a known time requires a basis and evidence. Unknown
availability is quarantined for this exercise; no fake timestamp is generated.
Source availability cannot precede known acceptance, and ingestion cannot precede
known availability or acceptance.

`eligible_records(checkpoint, cutoff)` returns **all** accepted revisions whose
source availability is at or before the cutoff. It retains the original. This
is a historical source-eligibility rule, not a claim that the local system actually
possessed the filing then. A future online adapter must also enforce local receipt
at serving time and verify source timestamp semantics.

Optional normalized facts use finite Decimal values; zero is distinct from missing.
Initial supported units are USD, shares, USD/shares and pure. Unsupported currencies,
units and extension taxonomies need an approved contract version rather than guessing.
No EPS surprise, ratios, fiscal-calendar conversion or catalyst calculation occurs here.

The existing `UniversalDateParser` is a research parser with a US/Eastern default;
the new contracts require explicit aware datetimes. A future adapter must resolve
source-specific timezones before construction. The paper timestamp utility and
both existing parsers remain unchanged.

## Reproducible bounded fixture batches

Each JSONL line contains `envelope` and `payload_text`. The supplied UTF-8 payload
bytes must match the provenance SHA-256. Payload references are opaque metadata;
they are never fetched or resolved by this module.

```python
from pathlib import Path
from ingestion.sec.contracts import UniverseManifest
from ingestion.sec.offline import Checkpoint, run_batch

root = Path('tests/fixtures/sec')
universe = UniverseManifest.model_validate_json((root / 'universe.json').read_text())
state, events = run_batch(root / 'filings.jsonl', universe, batch_size=2)
# Caller persists the completed snapshot; parse it again after restart.
restored = Checkpoint.model_validate_json(state.model_dump_json())
state, events = run_batch(root / 'filings.jsonl', universe, restored, batch_size=2)
```

Batches contain 1–100 attempted records; each line is limited to 64 KiB and a
fixture run is limited to 1,000 completed records. SHA-256 is streamed in bounded
chunks. Checkpoints retain bounded fixture evidence, not an unbounded market-data
archive. Resuming scans the completed prefix again; it does not load an all-symbol
DataFrame. A production adapter needs a separately approved durable commit/cursor
protocol and scale evaluation.

The cursor counts completed accepted, duplicate and quarantined lines. Retryable
lines remain uncommitted. Missing-base input blocks until the upstream ordering
or manifest is corrected; this runner does not fetch a base or skip the blocked
line. Changed source bytes or universe fingerprint invalidate an old checkpoint.
Invalid cursor/evidence structure fails closed. Checkpoints are caller-trusted
snapshots, not cryptographically authenticated artifacts.

Deduplication uses SEC source/accession identity plus immutable metadata, facts and
raw-payload evidence. Later retrieval time or payload storage location may differ;
changed source evidence is quarantined instead of overwriting the original.
The cursor advances only in a returned checkpoint. Unexpected interruption discards
the unfinished batch; rerunning from the saved snapshot replays it deterministically.

## Existing components and future adapter boundary

`src/macro/connectors/sec.py` reads existing Form 4 rows from PostgreSQL and computes
insider-flow research. It is not a general live filings downloader. Form 4 is outside
this v1 filing family; preserve that reader and its schema. Future SEC ingestion
should map these raw/normalized contracts to existing research layers after storage
review, without introducing another trading ledger.

`JSONStateStore` currently tolerates failed reads and does not provide the strict
checkpoint transaction protocol required here; this exercise leaves persistence
explicit with the caller rather than silently reusing fail-open loading. Existing
experiment metadata remains local JSON.

Before live access, separately approve source identity/User-Agent configuration,
throttling/backoff, provider failures, availability evidence, versioned universe
selection, append-only persistence and atomic checkpoint commits. Relevant primary
references: [SEC developer resources](https://www.sec.gov/developer),
[SEC data API documentation](https://www.sec.gov/edgar/sec-api-documentation),
[SEC EDGAR glossary](https://www.sec.gov/submit-filings/filer-support-resources/edgar-glossary).

## Validation

```bash
python -m pytest -q tests/test_sec_contracts.py
python -m pytest -q
```

Fixtures are explicitly synthetic, with fictitious companies and payloads. They
cover normal and amended filings, repeat retrieval, unknown availability, conflicting
issuer/source identities, invalid units/periods and blocked amendments. Tests cover
serialized restart, interrupted batches, eligibility, precision, schema versions
and input limits. This evidence does not establish live SEC availability, production
checkpoint durability or feature/model quality.
