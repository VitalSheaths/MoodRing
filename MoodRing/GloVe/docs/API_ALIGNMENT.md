# Local API alignment and verification

Issue [#23](https://github.com/VitalSheaths/MoodRing/issues/23). This increment
makes the existing local learning workflow inspectable through HTTP and OpenAPI.
It does not change the ledger, decision engine, research database or SEC adapter.

## Audit and design decisions

All 16 previously documented operations existed. The failure was contract and
usability drift: no declared response schemas, unsupported legacy tier/options
models, no Swagger bearer security scheme, and an external quote provider selected
by default. The guide advertised a runnable workflow without an offline API mode.

| Decision | Benefit | Trade-off / compatibility impact |
| --- | --- | --- |
| Replace unused `api_models.py` legacy entities with wired requests/responses | One schema authority; validates actual service structures and nested history/replay records | Unsupported imports are removed; this is API version 0.2.0, not a compatibility promise for legacy models |
| Decimal-string response serialization | Preserves ledger decimal precision and gives OpenAPI a consistent type | Existing HTTP clients expecting numeric money/quantity/portfolio ratios must parse decimal strings; research JSON blobs already stored as numbers cannot gain lost precision |
| Default offline synthetic provider | Beginners can run the API without market availability or external data rights | Fixed lesson prices are not market evidence; refresh does not simulate changing returns. `live` remains explicit opt-in and indicative |
| Live metadata cache only | Offline path cannot contact yfinance or write a metadata JSON cache | Injected custom providers have no implicit metadata implementation and report `custom`, not `offline` |
| HTTP bearer dependency | Swagger Authorize matches the actual token-map authentication | Still local static secrets; no production identity, token rotation or hosting policy |
| Uniform JSON errors; fixed validation/unexpected error messages | No request echo or provider/database internals in error responses | Less client-side validation detail; schemas and private server logs assist diagnosis |
| `/health` versus `/ready` | Distinguishes a responding process from configured/connected local dependencies | Readiness is not a live quote probe or production certification; per-symbol freshness still matters |
| OpenAPI examples use the injected server clock | Signal examples work when loaded rather than committing expired dates | Reload Swagger if a prediction example expires; replace returned-ID placeholders with real IDs |
| Standalone smoke client and disposable `--self-contained` mode | Same full HTTP lesson works manually and in CI | Smoke creates synthetic ledger records; disposable mode removes its own temporary ledger after stopping its child server |

## Route audit and test coverage

All operations are covered in `tests/test_api_contracts.py` and the existing API
suite. The shared HTTP smoke workflow covers every business route; additional
cases check authentication, ownership, conflicts, PASS, provider failures and readiness.
Every private operation has a bearer security declaration, typed response, tags,
summary, description and common JSON error contracts. GETs have path/query inputs,
not invented request bodies. Request POST models forbid unknown fields/nonfinite inputs.

| Method / route | Request / response model | Exercise |
| --- | --- | --- |
| POST `/api/v1/scenarios` | ScenarioRequest / Scenario | Create two independently owned portfolios |
| GET `/api/v1/scenarios` | none / Scenarios | Owned listing and isolation |
| POST `/api/v1/scenarios/compare` | CompareRequest / ScenarioComparisons | Distinct owned IDs, missing/foreign IDs |
| GET `/api/v1/scenarios/{scenario_id}` | path / Scenario | State, decimal strings, ownership |
| GET `/api/v1/scenarios/{scenario_id}/history` | path/limit / History | Orders, fills, equity, signals, trades |
| POST `/api/v1/scenarios/{scenario_id}/signals` | SignalRequest / Signal | BUY/PASS, cutoff validation, ownership |
| GET `/api/v1/signals/{signal_id}` | path / SignalHistory | Decision and nullable performance horizons |
| POST `/api/v1/paper-trading/trade` | PaperTradeRequest + key / TradeResponse | Entry, replay/conflict, missing key, PASS rejection |
| POST `/api/v1/paper-trading/close/{trade_id}` | path + key / TradeResponse | Exit, same-key replay, new-key conflict, ownership |
| POST `/api/v1/market/refresh` | none / Refresh | Owned mark refresh, failure sanitization |
| GET `/api/v1/market/quotes` | repeated tickers / QuotesResponse | Synthetic source, missing/invalid symbols, failure |
| GET `/api/v1/market/metadata/{ticker}` | path / Metadata | Synthetic metadata and unsupported symbols |
| POST `/api/v1/backtest/run` | BacktestRequest / BacktestRun | Caller-supplied replay saved without market lookup |
| GET `/api/v1/backtest/runs` | limit / BacktestRuns | Owned saved results and isolation |
| POST `/api/v1/backtest/compare` | CompareRequest / BacktestRuns | Saved-run comparison and ownership |
| GET `/health` | none / HealthResponse | HTTP 200 status, no sensitive values |
| GET `/ready` (new) | none / HealthResponse | 200 or 503 on actual auth/DB/checker state |

## Security review boundary

Offline mode constructs no external provider/cache and has only three fixed lesson
symbols. The smoke client only accepts loopback HTTP URLs without credentials,
paths or query fragments, disables environment proxies and does not follow redirects.
It refuses any server not reporting `offline`. No SEC HTTP transport is introduced,
so SEC URL/redirect/TLS/body handling remains an unimplemented, separately reviewed
issue #22 boundary. The existing live quote library is not certified by this work.

Validation errors, provider failures, health and unexpected server errors use safe
messages. Health reports refresh counts instead of user symbols, IDs, tokens, DB URLs
or original exceptions. Diagnostic stack traces remain server-side. Database startup
still fails if the ledger cannot be initialized; runtime readiness probes connectivity.

**Persistence:** normal API calls intentionally persist synthetic paper state to the
configured ledger. Offline mode prevents external market access, not ledger writes.
No new schema/migration/persistence subsystem or SEC data storage is added. The
self-contained smoke owns and removes only its temporary ledger; it never touches an
existing caller-selected database. No claim of “zero persistence” applies to a paper
API workflow. The later SEC test still requires zero persistence separately.

## Reproduce

Follow [exact setup and commands](PAPER_TRADING.md). From `MoodRing/GloVe`:

```bash
python -m pytest -q
PYTHONPATH=src python examples/api_smoke.py --self-contained
PYTHONPATH=src python examples/paper_demo.py
PYTHONPATH=src python examples/synthetic_market_demo.py
```

PostgreSQL integration requires a disposable test server as described in
[database verification](REVIEW_FIXES.md). Hosted CI supplies PostgreSQL 16, runs the
full suite and all three offline API/demo workflows plus the existing bounded
capacity runner. Report local, fresh-environment and hosted results separately;
skips are not PostgreSQL evidence. No external market/SEC request is required.

Issue #22 implementation waits for review of this prerequisite. Actual SEC smoke
execution needs a later exact-CIK/operator/User-Agent/request-budget approval;
expanded testing, durable state and automation remain separately gated.

### Local evidence for this increment

Fresh Python 3.11 virtual environment installed the unchanged `requirements-dev.txt`.
Full PostgreSQL 16-enabled regression: **540 passed, no skips**, with one existing
Starlette/AnyIO deprecation warning. Focused API suites: **25 passed**. The actual
loopback HTTP `api_smoke.py --self-contained`, `paper_demo.py` and
`synthetic_market_demo.py` passed. Dependency installation used package access;
these workflows used no external financial data. Hosted CI remains separate evidence.

The first PR CI run exposed an in-memory SQLite concurrency race: the health probe
could overlap the background checker's transaction on the shared StaticPool
connection. The probe now uses the same service lock as ledger operations. A
regression checks that synchronization, and the checker lifecycle passed 100 local
repetitions. This correction preserves database and execution semantics; it does
not disable the checker or weaken readiness assertions. Final hosted CI results
must be checked on the updated head.
