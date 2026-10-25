# Bounded SEC metadata and current SIC access

Issue [#22](https://github.com/VitalSheaths/MoodRing/issues/22). Implementation and
mocked validation only. **No real SEC request has been authorized or executed for
this increment.** The CLI is manual-only, network-off by default, and unconnected
to the API, trading service, scheduler, market jobs or any persistent store.

## Reuse and adapter decision

| Authority | Reused without replacing it |
| --- | --- |
| `sec/contracts.py`, `normalization.py` | CIK, filing/accession identity, raw hash, provenance, context, amendments, eligible records, strict bounded JSON and result ceilings |
| `universe/identity.py` | SIC string normalization, pinned `SicTaxonomy` and its content checksum |
| Existing universe manifest | Caller supplies explicit reviewed issuer membership; requested CIKs must appear in it. It is not fetched or expanded |
| Market/orchestration/PostgreSQL contracts | Remain synthetic-only, unchanged. SEC metadata is not relabeled as a market job or persisted through their guards |
| Existing ledger/decision engine | Untouched; no signal, feature, model, position or fill is generated |

`access.py` returns an in-memory HTTP receipt, a current SIC observation and the
**existing** normalization result per issuer. The receipt adds request/receive
and wire-content evidence, not a second filing identity or storage authority.
Current SIC deliberately is not a `SicAssignment`: no historical effective date,
historical availability, security/share-class mapping or membership is inferred.
It is validated against the supplied taxonomy subset, which must not be from the
future relative to the caller clock. Unknown codes are explicit unsupported values.

### Why an isolated standard-library transport

A socket read timeout alone does not bound DNS resolution or slow-drip responses.
A thread timeout would leave its network operation alive. Each explicitly enabled
attempt therefore runs in one owned child process using native `HTTPSConnection`.
The parent bounds the **whole attempt**, including process startup, DNS, TLS,
headers and body, to at most five seconds. This is stricter than a five-second
inactivity timeout. The child also uses a socket timeout no greater than five
seconds, checks its absolute deadline while streaming and closes its connection.
The parent terminates/kills/reaps an overdue child; failure to reap stops all later
dispatches. One second of the total budget is reserved for cleanup (two bounded
0.2-second waits). No thread or child may silently continue into the next attempt.

Trade-off: process overhead reduces usable request time and requires a normal local
Python executable. This is not a production worker pool or an OS sandbox. OS
scheduling/process creation cannot provide hard real-time guarantees; elapsed-time
checks refuse late results, and blocking network work is cancellable. The 30-second
budget is never extended to accommodate retries or slow reads. Data/CPU bounds also
apply during normalization, with deadline checks before and after it.

No dependency is added. The worker uses `-I -B`, a fixed source-file path and a
minimal environment, with stdin/stdout **private pipes** and stderr discarded.
Its bounded JSON/base64 response is internal transport, not console/public output;
it carries no operator or response headers. The parent accepts at most 2 MiB of
worker output. Public CLI output is only the sanitized summary. The worker protocol
is internal, requires explicit enablement/identity, and is not a second supported CLI.

## Executable controls and finite bounds

| Control | Enforced behavior / merge-gate evidence |
| --- | --- |
| Permission and identity | `allow_network` must be exactly true; operator User-Agent must be an ASCII product token plus contact email. Syntax is not verified human identity. Missing opt-in blocks even injected transports |
| Destination | Only generated `https://data.sec.gov:443/submissions/CIK##########.json`; one to ten ASCII CIK digits normalize through the existing validator; at most two unique reviewed CIKs |
| URL isolation | No user URL, extra port, query, redirect, Location, primary-document or historical-file traversal; native HTTPS does not consult proxies |
| TLS | Certificate and hostname verification always enabled. Compiled-in CA file/directory used explicitly; proxy, Python startup, SSL trust overrides and SSL key-log environment variables are not forwarded |
| Concurrency/attempts | Serial, at most four dispatch attempts total and one retry per issuer; failures/timeouts consume budget, no SDK retries; inability to reap a child prevents another dispatch |
| Pace | A full quiet second follows each finished transport before the next attempt, across issuers/retries. Variable child startup therefore cannot compress actual HTTP dispatches below one second. This is per invocation, not a multi-process quota coordinator; concurrent manual callers are outside scope |
| Time | At most five seconds for each whole transport attempt; 30 seconds overall including pacing, retries and normalization, with cleanup reserve |
| Body | At most 1 MiB **both on the wire and after decoding**. Read at most 8 KiB chunks or the remaining budget plus one; reject early oversize lengths, count actual bytes regardless of headers |
| Compression | Only identity or one gzip member; bounded `decompress(max_length)` output, no unbounded flush; reject incomplete/concatenated/trailing gzip and unsupported encodings |
| Headers | 8 KiB line and 50-header parser ceilings; 32 KiB aggregate check; reject duplicate lengths/encodings, invalid lengths and Content-Length with Transfer-Encoding |
| Retry | Only timeouts, 429, 500/502/503/504; no retries for 403/404, redirects, validation or size failures. Parse bounded Retry-After delta/date; defer rather than wait beyond the remaining budget |
| Normalization | Existing 1,000 rows/issuer and 4 MiB result/issuer; ceilings can only be reduced. Structural failures return no partial normalization result; row quarantines/retryable prefixes remain explicit |
| Output | One outcome per requested issuer, including unattempted/deferred issuers; no aggregate success that hides failure. Complete filing history is always false |

`attempts` counts adapter dispatches, including a child that fails before issuing
HTTP. It is an upper bound on actual requests, not evidence of network success.
`Limits` rejects extra fields, booleans, coercions and any increase above ceilings.
Unknown or expired publication evidence is not repaired with receipt timestamps.

HTTP/1.1 Content-Length defines message framing: a short declaration may cause the
standard reader to expose only that framed prefix. The connection is never reused;
any exposed oversize bytes or mismatch is rejected and malformed/truncated JSON
fails normalization. No assertion is made that bytes outside a framed response were
inspected. Mock tests also exercise misleading lengths with streamed input that
continues beyond the declaration, so length alone cannot bypass the byte counter.

## Eligibility, provenance and partial results

Raw SHA-256 means the exact **decoded JSON bytes before parsing**, including
whitespace and ignored source fields. A separate wire SHA-256 retains the compressed
representation's identity. The receipt binding includes CIK/generated URL,
request/receive times, both hashes and encoding. The existing normalization binding
also includes context, universe and limits. Hashes prove byte binding, not source
truth, historical possession, licensing or public availability.

Caller context can supply reviewed per-accession publication evidence and verified
amendment links. Its ingestion time always comes from actual receipt; no caller can
override it. The CLI supplies no publication evidence, so filings with unknown
availability remain quarantined/ineligible. Filing date, fiscal period, acceptance
or receipt are not silently substituted for publication. Unsupported/naive acceptance
times quarantine instead of guessing UTC or US/Eastern. Unresolved amendments retain
the existing retryable-prefix behavior; no base accession is discovered from the network.

Wrong issuer, malformed/duplicate-key/nonfinite JSON, mismatched arrays or excessive
rows fail closed. Missing/unsupported SIC, row quarantines and unresolved amendment
links produce visible subset/gap/deferred outcomes. A successful second issuer does
not erase a failed first one. Historical `filings.files` references are not followed;
root recent metadata never establishes complete history or point-in-time membership.

## Manual use: safe commands now

From the repository root, with the documented [local setup](PAPER_TRADING.md):

```bash
cd MoodRing/GloVe
source .venv/bin/activate
PYTHONPATH=src python -B examples/sec_metadata.py --offline-demo
PYTHONPATH=src python -B examples/sec_metadata.py
```

The first command uses committed, explicitly synthetic fixture bytes and a mocked
transport. It reports `completed_with_gaps`: current synthetic SIC is observed,
but publication is unknown and the filing is ineligible. The second reports
`network_disabled` and intentionally exits with code 2. Neither makes a request.
The tests and CI use no real SEC response or live endpoint.

For future review only, the manual live interface has these controls:

- `--allow-network` plus one or two explicit `--cik` arguments;
- `--universe` and `--taxonomy`: explicit bounded, pinned caller input files;
- `SEC_USER_AGENT`: operator product/contact identity from the environment, never
  a command-line contact argument, stored file or public summary;
- no output path, response dump, cache, publication override or automatic reuse.

**Do not run that live mode yet.** A separately approved smoke must name exactly
one CIK, one operator, one identified User-Agent and one request. The
`--one-request` flag enforces exactly one CIK, one attempt and zero retries.
Without that flag the generic ceiling permits retries; it must not be mistaken
for one-request smoke permission. Repeated runs, a second CIK or expanded comparisons
need later approval after that result is reviewed.

CLI exit codes: 0 = supported subset/gaps returned; 1 = at least one failed/deferred
issuer; 2 = disabled/configuration/input error. Summaries use issuer ordinal and
fixed codes/counts, not raw CIK, names, SIC codes, URLs, body, contact or headers.

## Persistence and disclosure review

There are no data-file/cache/database writers, migrations, loggers or output dumps.
Responses/normalization remain in memory; only fixed-code summary JSON goes to CLI
stdout. Caller universe/taxonomy files are read-only and limited to 1 MiB each. `-B`
disables Python bytecode writes in the documented commands and isolated worker;
ordinary Python import caching is not a provider-data store. Diagnostic exceptions
and raw provider bodies never go to stdout, stderr or application logs. No private
operator information is in argv, worker output, receipts or committed fixtures.

Tests prohibit external DNS/connect/connect_ex, mock HTTPS/child process dispatch,
check hostile proxy/SSL/Python environment variables, guard write APIs and capture
stdout/stderr/logging. The worker's private IPC is not a public response serializer.
The caller must not dump internal receipts/normalization to a public endpoint; no
such endpoint, database operation or downstream use is provided here.

[Provider/licensing review](PROVIDER_COMPATIBILITY_REVIEW.md) remains authoritative:
public API accessibility grants no blanket rights for filer-authored material,
public display, indefinite retention, historical membership or model/catalyst use.
No `DataRights` flags are enabled. No narrative, exhibit, logo, XBRL fact extraction,
market quote, index series or broad history is acquired. Capacity observations
remain environment-specific; WAL stays cluster-wide, 96/128 MiB remain experiment
values and no retention or scaling decision is made.

## Required merge evidence

The `tests` CI job explicitly runs:

```bash
python -m pytest -q tests/test_sec_access.py tests/test_sec_transport.py
python -m pytest -q
PYTHONPATH=src python -B examples/sec_metadata.py --offline-demo
```

It also runs the offline API smoke, both existing demos and bounded capacity runner
with disposable PostgreSQL. These controls must remain executable and green on the
reviewed head; do not skip them or substitute a live test. Maintainer approval is
required before merge. No real request is needed for implementation acceptance.

Local verification: **151 focused tests passed; 691 full tests passed with PostgreSQL
and no skips**, with one existing Starlette/AnyIO warning. Final/hosted results are
reported in the PR separately from local evidence. Mocking proves controls, not
current SEC schema compatibility or real-world success.

Separate reviewed scope is still required for any actual smoke, additional manual
runs, persistent SEC state, scheduling, broader CIK coverage, production universe,
public API, history/backfills or model/catalyst use. Public hosting/identity and live
market access are independent decisions and were not prerequisites here.

## Trust-store portability and smoke review

Only existing compiled-in CA files/directories are loaded. If neither exists,
Windows uses its system store; other platforms fail before connection creation
with `trust_store_unavailable`. TLS verification remains mandatory. Worker and
public outcomes preserve that fixed code without provider details.

See [one-request smoke plan](SEC_SMOKE_PLAN.md); execution requires separate approval.

Offline trust-setup microbenchmark (local Python 3.11/macOS, 100 sequential
iterations per version, real local CA loading, socket connections forbidden):
previous median 4.724 ms / p95 9.765 ms; path-checked median 4.761 ms / p95
5.179 ms. These are environment-specific observations, not network latency or a
production performance guarantee; the median difference was 0.037 ms. Windows
system-store selection is mocked in tests, not validated on a Windows host.
