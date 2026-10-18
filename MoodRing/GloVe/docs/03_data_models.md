# Data, ledger and state contracts

Status: Phase 1 specification for review, 2026-10-13. Existing ledger definitions
are authoritative for current fields; future records below are proposals, not migrations.

## Accounting precision

`src/paper_trading/schema.py` stores money/prices as `Numeric(24, 8)` and quantity
as `Numeric(24, 10)`. Service calculations use Decimal conversion and reject
nonfinite amounts. Cash, notional, fees and monetary P&L are rounded to cents with
`ROUND_HALF_UP`. Fill prices are quantized to eight decimal places using the
Decimal context rounding; quantities are quantized down to eight places.
Storage capacity and effective service precision are different.

Prediction probabilities and risk estimates may use floats. They are not a license
to calculate cash balances with binary floating-point values. Preserve the existing
rounding path until a separately approved precision change has boundary tests.

Cash after entry = prior cash − rounded notional − entry fee.
Cash after exit = prior cash + rounded exit proceeds − exit fee.
Realized P&L = net exit proceeds − entry cost basis (including entry fee).
Equity = cash + rounded market value of open positions.
Unrealized P&L = open market value − open cost basis.

## Implemented entities

| Table | Identity and relationships | Current meaning |
| --- | --- | --- |
| `paper_scenarios` | UUID, user; policy/research JSON and model/dataset/feature versions | Independent scenario and configuration. |
| `paper_portfolios` | Unique scenario FK, user | Initial capital, cash, realized total P&L, win/close counters, peak equity. |
| `paper_signals` | Scenario FK, user, ticker | Explicit prediction, decision, version, cutoff, expiry and baseline quote price. |
| `paper_positions` | Portfolio FK | Positive quantity, entry/cost basis, mark/quote timestamp, stop/target, open/closed summary. |
| `paper_trades` | Unique position FK, portfolio/signal/user | Entry/exit, fees, realized P&L and exit reason; optional agent ID does not implement agents. |
| `paper_orders` | Trade/portfolio FK; unique `(user_id, idempotency_key)` | Accepted buy/sell request, quantity and immediate filled state. |
| `paper_fills` | Unique order FK | One simulated full fill, price/fee, source quote and observation/fill times. |
| `paper_equity_history` | Portfolio FK | Event-time cash, marked value, equity, P&L and drawdown snapshots. |
| `paper_quotes` | Ticker primary key | Latest observation only, not a historical tick archive. |
| `signal_performance` | Signal primary/FK | Horizon observations and extrema; not portfolio P&L. |
| `backtest_runs` | UUID, user and versions | Policy/research/result/risk JSON; independent persisted replay output. |

Each scenario has one portfolio. Each trade has one position; entry and exit each
have an order and fill. IDs and metadata make records attributable, but a caller's
`model_version` string is not proof of a registered or validated model artifact.
`ExperimentTracker` remains a separate local JSON writer, not this backtest table.

## Current state transitions

- Scenario creation → portfolio with positive initial capital and initial history.
- Signal publication → stored decision (`BUY` or `PASS`); validity is checked again
  at entry. There is no mutable signal-status column in the current schema.
- Valid entry → filled buy order + fill + open trade/position + debited cash.
- Price update → revised mark and equity snapshot; optional policy exit on observed
  stop/target conditions. A gap fills at the observed simulated price, not a guaranteed stop price.
- Full exit → filled sell order + fill + closed trade/position + credited cash and
  realized counters. Partial fills/exits, pending limit orders and cancellation
  states are not implemented.
- Same idempotency key and same request → original result; conflicting request →
  rejection. Failed mutations must not partially update the ledger.

Order status is presently `filled`; trade/position status is `open` or `closed`.
Replay outcome statuses (`traded`, `expired`, `position_exists`, `not_tradeable`,
`rejected`, `no_later_event`, `never_eligible`) are serialized run outcomes, not a
new persistent signal-state table. Original input indexes preserve accounting.

## Timestamp and missing-data contract

The ledger timestamp boundary is `src/paper_trading/market.py` (`timestamp`):
offset-aware input normalized to UTC and serialized as ISO timestamps. The API
accepts datetime fields but service validation must still enforce timezone/cutoff
rules. `src/utils/UniversalDateParser.py` is an existing research parser with a
US/Eastern default; do not assume its outputs are interchangeable with ledger UTC.
A future adapter must document conversion explicitly, without rewriting either parser here.

| Time | Definition / eligibility |
| --- | --- |
| Event/report period | What happened or which period a source value describes; not automatically availability. |
| Source availability | Earliest evidenced time the information could be known. |
| Ingestion | When this system received the source. |
| Feature creation | When a feature was computed. |
| Prediction | Decision cutoff; feature availability must be at or before this time, which cannot be in the future. |
| Publication | When a proposed advisory is released; not a substitute for prediction cutoff. |
| Expiry | Last eligible time; do not refill expired signals or silently renew advisories. |
| Quote observation/fetch | Market timestamp versus local retrieval; freshness uses the observation time. |

Missing optional inputs remain missing with explicit reason/flags. Some existing
research transformations use defaults; auditing their missing-value semantics is
future contract work, not evidence that every historical pipeline already complies.

## Proposed contracts for later phases

Before any migration, agree versioned fixtures and compatibility:

- Security master/universe: stable ticker/CIK mappings, membership version and as-of dates.
- Raw filing/news/market records: source ID/URL, accession, raw payload reference,
  event and availability times, ingestion, units, period and revision identity.
- Normalized catalyst features: EPS actual and separately sourced consensus,
  surprise, revenue momentum, operating margin and cash-flow measures; guidance
  direction and availability. SEC actuals alone do not supply consensus estimates.
- Sentiment/MD&A extraction: text/evidence spans, source references, probabilities,
  confidence, model/prompt version and extraction time; output is a feature, not truth.
- Model/dataset manifests: artifact and data snapshot IDs, feature schema, calibration
  and evaluation versions. Preserve current local experiment JSON until an explicit registry decision.
- Advisory input/output: immutable input references, cutoff, decision/risk result,
  uncertainty, evidence, versions and expiry; explanations cannot invent numbers.
- Delayed outcomes: label horizon, eligible future prices, observation time and
  evaluation version; outcome updates do not retrain a serving model immediately.

For top-100 ingestion, propose partitioned ticker/time history, bounded reads,
source/revision idempotency keys and per-ticker/date checkpoints. Retention durations,
indexes and restart semantics require an approved workload/contract; none are
introduced by this documentation commit.

Related: [PRD](01_prd.md), [architecture](02_architecture.md), [rules](04_rules.md).

## SEC contract increment

The reviewed issue #4 increment defines `sec-contract-v1` under
`src/ingestion/sec/`: versioned universe membership, filing and payload provenance,
optional supplied Decimal facts, revision links and bounded offline checkpoints.
See [SEC contracts](SEC_CONTRACTS.md) for field rules, deterministic replay,
source eligibility and the future adapter boundary. This adds no ledger or research
database migration. Live downloads and catalyst feature calculation remain planned.

## SIC and security-universe contract increment

The issue #6 increment adds `universe-contract-v1` in `src/ingestion/universe/`:
stable issuer/instrument identity, dated symbol mappings and SIC assignments,
explicit ranking-policy inputs, immutable membership snapshots and rights declarations.
It reuses SEC CIK/time validation. See [universe contracts](UNIVERSE_CONTRACTS.md)
for precision, provenance, coverage checks, fixture-only policy parameters and
limitations. A real selector, persisted master and live OHLC integration remain planned.

## Daily market-data contract increment

Issue #8 adds `market-contract-v1` in `src/ingestion/market/`, reusing universe
identity, exact values, evidence and rights. Daily observations separate series
identity, semantic value checksum and revision identity; supplied session calendars
and coverage reports distinguish missing data from nontrading days and inactive
security lifetimes. See [market contracts](MARKET_CONTRACTS.md) for adjustment,
provenance, index capability and revision rules. No persistence or live adapter
is introduced.
