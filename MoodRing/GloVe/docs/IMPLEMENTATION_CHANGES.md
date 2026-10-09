# Implementation changes — 10 October 2026

## What is implemented

This change follows the reference project's persistence, price-checking and
research-run concepts while keeping the scope to beginner stock simulation.
There is one portfolio ledger per scenario, rather than separate user/strategy/
agent ledgers with competing accounting rules.

| File | Change or role |
|---|---|
| `src/paper_trading/schema.py` | Scenario, portfolio, signal, position, trade, order, fill, quote, equity, signal-performance and backtest-run tables |
| `src/paper_trading/storage.py` | SQLite/Postgres engine setup and transactions |
| `src/paper_trading/policy.py` | Decimal arithmetic, percentage/confidence/fractional-Kelly sizing and portfolio risk limits |
| `src/paper_trading/service.py` | Ownership, entry/exit lifecycle, idempotency, accounting, valuation, stop/target exits, histories and comparisons |
| `src/paper_trading/market.py` | Timestamped quote provider, short quote cache and one-hour metadata cache with startup eviction |
| `src/paper_trading/checker.py` | Batch polling background loop with failure recovery |
| `src/paper_trading/backtest.py` | Chronological replay through the same service; next-event entries and overlapping positions |
| `src/api/server.py` | Runnable FastAPI server, local token auth, lifecycle startup/shutdown and routes |
| `examples/paper_demo.py` | Offline, deterministic-price lesson using a persisted ledger |
| `src/ingestion/storage/silver.py` | Actually joins the scored technical view and tracks every contributing row's latest availability |
| `src/macro/storage/postgres.py` | Silver schema ownership and conservative availability migration; no competing Gold table definition |
| `src/ingestion/storage/gold.py` | Canonical additive Gold migration; availability-aware macro joins and signed insider flows |
| `src/macro/pipeline/run_macro_ingestion.py` | Revised FOMC text/scores acquire current availability |
| `src/features/volatility/arch_models.py` | Rolling one-step forecasts; historical rows never fit parameters using later returns |
| `src/features/sentiment/pipeline.py` | Availability before dedup, independent repeated evaluations and optional target-ticker aggregation |
| `src/engine/decision_engine.py` | Calls its age-based stale-data guard and rejects nonfinite or invalid predictions |
| `src/config/data_io.py` | Fixes the `config.paths` import |
| `src/config/paths.py` | Resolves source/project/state directories inside this checkout |
| `src/config/config.py` | Removes a hardcoded database username/password default |
| `requirements-paper.txt`, `requirements-dev.txt`, `.env.example`, `.gitignore`, `pytest.ini` | Setup, sample configuration, generated-file exclusions and test discovery |
| `tests/` | Accounting, restart, concurrency, ownership, freshness, cache, replay, API and PostgreSQL migration checks |

## Feature timing contract

The model view keeps the original column layout for existing consumers and
identifies the timing semantics as `v2.0-asof`. It
references `returns_technical_features_scored`, with technical confidence from
that view. `features_created_at`, `predicted_at` and `prediction_time` now use
one conservative availability boundary: the greatest timestamp among price,
benchmark, technical and volatility inserts/updates and one second after that
session's 16:00 New York close. An explicit `America/New_York` timezone handles
DST independently of the database session's timezone.

A row produced by a backfill today is available today, not automatically at its
historical trade date. A correction also moves its availability forward. Existing
mutable Silver tables are not a complete historical version store; an older
revision cannot be reconstructed after replacement. Consumers must query with
an explicit cutoff:

```sql
SELECT * FROM silver.returns_model_features
WHERE prediction_time <= :prediction_cutoff
  AND features_created_at <= :prediction_cutoff
  AND feature_confidence = 1.0
ORDER BY ticker, trade_date;
```

Warmup confidence is separate from feature availability. Use forward labels,
chronological training/calibration/test windows and purging appropriate to the
label horizon. The older synthetic experiment scripts do not automatically gain
a real historical dataset from this view.

Volatility features now fit each row from at most 252 observations ending at
that row, requiring 60 usable observations before fitting. They are next-step
forecasts in percent-return units. This removes full-sample parameter leakage
but requires many more fits; plan incremental jobs for a large ticker universe.
The separate legacy EWMA MLE utility is not used by the paper service and still
needs a windowed training policy before historical inference use.

Macro joins use source release/filing time and `available_at`, never just an
observation month or report date. Upcoming earnings dates are eligible only if
the schedule was known by the cutoff. Purchase amounts count positively and
sales negatively. Weekly insider summaries add daily flows, rather than summing
repeated seven-day windows.

For existing macro records, `available_at` is conservatively backfilled from
`created_at`. FRED release vintages are not fetched or reconstructed. A database trigger moves `available_at` forward when macro row contents change,
so a corrected value cannot retain its old availability; a real historical FRED study requires preserved
release vintages. A single mutable observation row cannot supply them.

## Postgres migration and rollout

The app's paper tables are additive and created by `PaperStore` on startup in the
configured paper database. Use a separate `PAPER_DATABASE_URL` for an isolated
paper ledger if desired. The research database retains Bronze/Silver/Gold.

For the research schema, configure `DATABASE_URL`, then run:

```bash
PYTHONPATH=src python -c 'from config.data_io import get_engine; from ingestion.storage.gold import ensure_gold_schema; ensure_gold_schema(get_engine())'
PYTHONPATH=src python -m features.technical.pipeline
PYTHONPATH=src python -m features.volatility.pipeline
PYTHONPATH=src python -c 'from config.data_io import get_engine; from ingestion.storage.silver import ensure_model_features_view; ensure_model_features_view(get_engine())'
PYTHONPATH=src python -m macro.pipeline.build_macro_features
```

These commands assume existing returns and Bronze news tables from the research
setup. Run against a development copy before changing a populated research DB.
The Gold migration preserves table rows, renames the two legacy columns when
needed and adds missing columns. Running it twice is safe. If a dependent view
expects a legacy column name, update that consumer for the canonical name.
The feature view uses `CREATE OR REPLACE VIEW`, rather than cascading drops.

## Verification

```bash
python -m pytest -q
# Optional full database checks: tests create and remove their own fresh databases.
PAPER_TEST_POSTGRES_URL='postgresql+psycopg2://USER@HOST/postgres' python -m pytest -q
PYTHONPATH=src python examples/paper_demo.py
```

The full suite passed 40 tests on Python 3.11, including five tests against a
fresh disposable PostgreSQL server. The offline entry/mark/exit demonstration
also ran successfully. The installed scientific stack emitted Matplotlib/
Pyparsing deprecation warnings; no test failures remained.

The ordinary tests mock quotes; they do not need external APIs. The opt-in
Postgres tests cover both a fresh schema and migration from the legacy Gold
schema, availability-aware macro values, feature cutoffs and a full ledger
entry/mark/exit. The test URL must allow creating temporary test databases.

## GitHub notes and remaining scope

The [reference README](https://github.com/Mattbusel/Reddit-Options-Trader-ROT-/blob/222a6cd4/README.md) is design context, not a source of copied platform code,
performance figures or security/test badges. These changes do not include live
broker execution, a frontend dashboard, paid tiers, Monte Carlo or autonomous
agents. They provide a runnable API and learning/research lifecycle whose state
survives restarts. Yfinance polling is explicitly indicative and potentially
delayed. Automated prediction generation needs a separately validated checkpoint,
feature transformation and calibration bundle before it can feed this API.

Generated ledgers, market-cache files, environments and Python caches are ignored.
No git commit or remote push is performed by this implementation. The dependency
files cover the new subsystem and its checks; the original neural-network/news
research scripts may require additional packages such as Torch and feedparser.
