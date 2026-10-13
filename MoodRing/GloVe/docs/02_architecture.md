# Architecture and safeguards

Status: Phase 1 specification for review, 2026-10-13. The PRD controls scope.

## Implemented boundaries

| Layer | Existing location | Responsibility |
| --- | --- | --- |
| API | `src/api/server.py` | Local bearer authentication, ownership-scoped requests and HTTP error boundary. |
| Simulation service | `src/paper_trading/service.py` | Entry, marking, full exit, history, scenario isolation and atomic cash/position changes. |
| Deterministic decisions | `src/engine/decision_engine.py`, `src/paper_trading/policy.py` | Prediction eligibility, costs, sizing, risk gates and explainable rejection. |
| Portable ledger | `src/paper_trading/schema.py`, `storage.py` | Existing records in SQLite or PostgreSQL through SQLAlchemy; no second ledger. |
| Market adapter | `src/paper_trading/market.py`, `checker.py` | Batched indicative quotes, caching, freshness checks and polling. |
| Replay | `src/paper_trading/backtest.py` | Chronological price events using the same service and policy. |
| Research | `src/ingestion/`, `features/`, `macro/`, `models/` | Existing research components with their own data and dependency requirements. |
| Experiment metadata | `src/utils/experiment_tracker.py` | Local JSON records; no registry migration in this phase. |

Explicit prediction + quote → deterministic decision/risk checks → simulated
order/fill → portfolio and position updates → marked state/history. Research
models are not a verified automated input to this loop merely because they exist.

## Why two database paths

`PAPER_DATABASE_URL` selects the paper ledger; SQLite is the local default. It
needs no server and supports the offline learning path. The portable ledger also
supports PostgreSQL when configured, but configuration does not establish a
production operating model or migrate existing data automatically.

`DATABASE_URL` selects research storage. Existing SQL uses PostgreSQL schemas,
JSONB and database-side feature joins/upserts. It is not SQLite-compatible research
SQL. PostgreSQL is provisioned separately; a client driver is not a bundled server.
Data directories, credentials and local databases must not be committed.

`PAPER_TEST_POSTGRES_URL` selects a disposable integration-test server, with
permission to create/drop isolated test databases. Without it, those tests skip;
ordinary demo/tests use SQLite and mocked quotes. CI provisions a temporary
PostgreSQL service to check backend behavior, migrations and temporal research
queries. CI data is test data, not the application's research dataset. These
three settings are independent; there is no automatic synchronization between stores.

## Planned extensions, not installed infrastructure

Historical raw records and curated Parquet snapshots support reproducible training.
The research ingestion layers retain raw sources, normalize units/revisions, and
build features eligible at a prediction cutoff. For a top-100 universe, partition
history by ticker/time and use bounded reads with indicator warm-up windows,
checkpoints and idempotent writes. Exact partitioning is a measured-workload decision.

A future online store may hold bounded recent features and advisory expiries. Redis
is optional and requires evidence of need. A new registry, advisory audit tables,
SEC connector and calibrated FinBERT adapter each require their own approved contract.

Future production web replicas must share durable state, initially proposed as
PostgreSQL. Ingestion, training and heavy NLP run as separate workers rather than
inside every web replica. Railway is an option to assess at deployment time, not
an implemented deployment. A runtime volume/cache is not shared authoritative state.

## Safeguards: current enforcement and remaining gaps

| Required invariant | Current boundary / limitation |
| --- | --- |
| Simulation only | No real execution path is authorized. |
| User isolation and nonnegative cash | Service ownership checks, ledger transactions and cash constraint; production identity is deferred. |
| Decimal accounting | Service rounds money and quantities explicitly; storage precision is described in the data contract. |
| Auditable execution | Order/fill records and equity snapshots exist; a fully immutable event store and security audit trail are not implemented. |
| Idempotent writes | Per-user order keys and transactional replay exist; new adapters need their own keys. |
| Causal data | Service and research cutoff checks exist; every future adapter must prove its availability and revision semantics. |
| Freshness | Stale quotes cannot authorize entry/exit; existing marks remain visible as stale. Do not substitute fake timestamps or zero for missing sources. |
| Deterministic risk authority | Models and future explanations cannot override the decision engine. |
| Shared production state | Required for future replicas; SQLite remains the local default. |

Execution rows are intended to retain completed fill evidence. Current tables
have no comprehensive database-enforced append-only policy, and trade/position
summary rows change on close. Do not claim a fully immutable ledger.

## Phase 1 acceptance

No dependency, schema, runtime or model changes. Documentation identifies each
existing boundary, distinguishes planned services, explains the database split
and acknowledges enforcement gaps. Human approval is required before designing
or introducing a new adapter, storage boundary or deployment layer.

Related: [PRD](01_prd.md), [data contracts](03_data_models.md), [rules](04_rules.md).
