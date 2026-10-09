# Paper trading guide

## The learning workflow

Create a scenario with starting cash, publish a timestamped prediction, and buy
an allocation from that signal. A scenario is an independent experiment: two
scenarios can trade the same stock while retaining separate balances and results.
Use the portfolio endpoint to see cash, current equity and unrealized P&L. Closing
a trade converts its net profit or loss into realized P&L and returns its proceeds
to cash. The history endpoint explains what happened through orders, fills and
recorded equity snapshots.

The working scope is fractional long stock positions in USD. Multiple tickers
can stay open together. Each scenario permits one open position per ticker and
one entry per signal. Stops and targets close the entire position at the next
observed eligible price. There are no option legs, short selling, leverage,
partial fills, partial exits or real broker orders in this release.

## API workflow

The server reads `.env` through the existing configuration module. Map secret
Bearer tokens to stable user IDs in `PAPER_API_USERS_JSON`; each route checks
ownership. No token configuration means all private routes return 503. Use a
random secret rather than the placeholder in `.env.example`.

```bash
export TOKEN='your-secret-from-env'
curl -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"Learning: 2% allocation","initial_capital":10000,"model_version":"manual-v1","config":{"position_pct":2,"auto_exit":true}}' \
  http://127.0.0.1:8000/api/v1/scenarios
```

Use the returned scenario ID to post a signal. `prediction_time` and
`feature_available_at` require timezone offsets; availability must be at or
before prediction time, and prediction time cannot be in the future. Supply
real timestamps from the data that produced the prediction. Returns and risk
inputs are decimal fractions: `mu=0.03` means an expected 3% return and
`drawdown_risk=0.02` means a modeled 2% drawdown; `var` is variance in squared
decimal-return units. All prediction heads must refer to the same forecast horizon. The signal POST is
an adapter for manual or external model predictions. This app does not invent
trained checkpoints or claim that submitted probabilities are calibrated.

```json
{
  "ticker": "AAPL",
  "prediction": {
    "prob_up": 0.8,
    "mu": 0.03,
    "var": 0.0004,
    "trade_success_prob": 0.75,
    "drawdown_risk": 0.02,
    "prediction_time": "REPLACE_WITH_CURRENT_OFFSET_TIMESTAMP",
    "feature_available_at": "REPLACE_WITH_ACTUAL_FEATURE_OFFSET_TIMESTAMP"
  }
}
```

Post this to `/api/v1/scenarios/{scenario_id}/signals`. The existing decision
engine returns BUY or PASS; all signal decisions are stored, including PASS.
Only BUY signals can open positions, and the engine reevaluates the prediction
with the entry quote and scenario costs at execution time.

```bash
curl -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: entry-lesson-1' \
  -d '{"signal_id":"RETURNED_SIGNAL_ID","dollars":100}' \
  http://127.0.0.1:8000/api/v1/paper-trading/trade

curl -X POST -H "Authorization: Bearer $TOKEN" -H 'Idempotency-Key: exit-lesson-1' \
  http://127.0.0.1:8000/api/v1/paper-trading/close/RETURNED_TRADE_ID
```

Omit `dollars` to use scenario sizing. A supplied dollar amount is a total entry
budget including commission; exceeding the permitted allocation produces an
error. Repeating the same successful request with the same idempotency key
returns the existing trade; changing its payload produces 409. Retrying an
entry with another key cannot trade the same signal again. A close with a new
key after closure also returns 409.

| Route | Purpose |
|---|---|
| `POST /api/v1/scenarios` | Create a scenario and its independent portfolio |
| `GET /api/v1/scenarios` | List this user's scenarios |
| `GET /api/v1/scenarios/{id}` | Cash, equity, exposure, drawdown and open positions |
| `GET /api/v1/scenarios/{id}/history?limit=500` | Recent trades, orders, fills, equity observations and signals |
| `POST /api/v1/scenarios/compare` | Compare 2–20 scenario IDs using `{"ids":["...","..."]}` |
| `POST /api/v1/scenarios/{id}/signals` | Record an explicit prediction and decision |
| `GET /api/v1/signals/{id}` | Prediction, decision and observed price horizons |
| `POST /api/v1/paper-trading/trade` | Open a position from a BUY signal |
| `POST /api/v1/paper-trading/close/{trade_id}` | Close the whole position |
| `POST /api/v1/market/refresh` | Poll and update the requesting user's watched tickers |
| `GET /api/v1/market/quotes?tickers=AAPL&tickers=MSFT` | Timestamped indicative quotes |
| `GET /api/v1/market/metadata/AAPL` | Cached company metadata |
| `POST /api/v1/backtest/run` | Replay timestamped prices and predictions; save the result |
| `GET /api/v1/backtest/runs` | This user's saved research runs |
| `POST /api/v1/backtest/compare` | Compare 2–20 saved run IDs |
| `GET /health` | Process and background refresh status |

The generated `/docs` page contains full request schemas. Histories are limited
recent records, not paginated exports; default limit is 500, maximum 5,000.

## Cash and P&L accounting

Every entry atomically creates a trade, position, order and fill, debits cash,
and records equity. Every exit records a sell order/fill, closes the position,
credits proceeds and updates realized P&L and win counts. Transactions roll back
all those writes together on failure. Postgres locks the portfolio row; local
SQLite uses `BEGIN IMMEDIATE` to serialize ledger writes across processes.
Amounts are calculated with Decimal; cash and fees round to cents, quantities
to eight decimals and fill prices to eight decimals.

- Entry fill price = observed quote × (1 + slippage rate).
- Cost basis = quantity × entry fill price, rounded to cents, plus entry fee.
- Market value = quantity × most recent recorded quote, rounded to cents.
- Unrealized P&L = market value − cost basis.
- Equity = cash + open market values.
- Exit fill price = observed quote × (1 − slippage rate).
- Net realized P&L = rounded exit proceeds − exit fee − cost basis.

`paper_portfolios.total_pnl` means **realized** P&L. Responses expose realized
and unrealized P&L separately. A gap through a stop fills at the observed price
with sell slippage, not an assumed stop price. An observed mark, not an intrabar
high or low, can trigger an automatic exit.

## Sizing and controls

Defaults: 2% of current equity per entry, 10% maximum position, 50% maximum gross
exposure, 10% cash reserve, 1% per-trade modeled risk and a 20% portfolio drawdown
pause. Commission is 10 bps on each side and slippage 5 bps on each side. The
engine's expected net-edge gate includes an estimate of both sides' costs.

The allowed entry is the smallest of sizing allocation, available cash after
reserve, gross-exposure headroom, modeled risk budget and the decision engine's
allocation. Risk uses the larger of the stop distance and predicted drawdown,
plus estimated round-trip costs. A drawdown above the scenario limit blocks new
entries; it never blocks a manual exit. Missing/stale quotes or stale portfolio
marks block new entries. Stops are simulation rules and do not guarantee a
maximum loss through gaps.

`fixed_pct` is the beginner default. `confidence_weighted` scales the base
allocation from 0.5× to 2×, still within all caps. `fractional_kelly` requires
explicit `kelly_win_probability` and `kelly_payoff_ratio`; it uses
`fraction × max(0, p − (1−p)/b)`, capped by scenario risk. The default fraction is
one quarter and the maximum is one half. Direction probability alone is not a
win-rate/payoff estimate. No guaranteed performance or drawdown reduction is
implied by choosing a Kelly fraction.

## Quote updates and caching

`PriceChecker` runs every 300 seconds by default, batching tickers from open
positions and recently tracked signals in a single provider call. The yfinance
adapter requests one-minute regular-session bars and preserves the source's
bar timestamp and fetch timestamp. This is polling of potentially delayed,
indicative data, not a guaranteed exchange stream or bid/ask execution feed.

The default provider verifies USD currency through the metadata cache; missing
or non-USD currency is excluded rather than treated as dollars.
Execution requires a quote no older than 900 seconds by default. Cache refreshes
do not reset the source timestamp. A source failure never silently replaces a
price with zero or invents a current timestamp. Last marks remain visible and
become explicitly stale. Closed markets may therefore prevent new simulated
entries; use the offline demo or historical replay outside market hours.

`CachedQuoteProvider` keeps prices in memory for 60 seconds. `MarketEnricher`
keeps company metadata in a JSON file for one hour and evicts stale or malformed
entries during startup. That metadata cache never supplies execution prices.
`paper_quotes` stores the latest accepted provider observations; fills preserve
the exact quote used for each execution. Out-of-order observations cannot
rewind positions. The background loop survives provider failures and reports
failure state through `/health`.

`signal_performance` records the first eligible observed price at or after
1h/4h/1d/1w, with an eight-day tracking window, plus maximum observed gain/loss
percentages. It does not reconstruct missed intraday highs/lows. Signal movement
statistics are distinct from account unrealized P&L, which includes position
quantity and entry costs.

## Research metadata and replay

`model_version` identifies the exact checkpoint or manual policy that produced
a signal. Use a checkpoint hash or immutable release ID, not a mutable name.
Scenarios and saved backtest runs also record dataset/feature versions, configuration
and a research JSON object for seed, loss, calibration window, split dates and
checkpoint path. These tags are provenance; saving a tag does not validate the
training methodology.

A backtest request extends the scenario request with `bars` and `signals`:

```json
{
  "name": "Replay example",
  "config": {"auto_exit": false},
  "bars": [
    {"timestamp": "2026-10-06T15:00:00+00:00", "prices": {"AAPL": 100}},
    {"timestamp": "2026-10-06T15:01:00+00:00", "prices": {"AAPL": 101}}
  ],
  "signals": [{"ticker": "AAPL", "prediction": {
    "prob_up": 0.8, "mu": 0.03, "var": 0.0004, "trade_success_prob": 0.75,
    "drawdown_risk": 0.02, "prediction_time": "2026-10-06T15:00:00+00:00",
    "feature_available_at": "2026-10-06T14:59:00+00:00"
  }}]
}
```

Replay sorts events chronologically. Predictions observed at an event become
eligible to fill at a **later** event; price updates and exits precede new entries.
Open positions carry forward and remain open at the end. Each price dictionary
represents all available observations at that timestamp. Missing/stale prices
cause skipped entries rather than hindsight fills. Daily replay needs a longer
signal lifetime; even its maximum 24-hour lifetime expires over weekends.

`backtest_runs` persists configuration, research metadata, results and maximum
observed drawdown. Comparison returns cash/exposure/profit metrics and provenance
rather than asserting one run is better from a tiny sample. Monte Carlo,
Sharpe-ranked discovery and learned Kelly payoff estimates are not implemented.
The older synthetic research scripts remain separate from this replay engine.

## Deployment scope

The default service is a local API with generated docs and polling endpoints.
There is no dashboard or WebSocket broadcast in this release. Run a single API
worker for the background checker and JSON metadata cache. Multiple workers
would duplicate polling and cache writes. PostgreSQL supports shared ledger
transactions, but distributed scheduling is still needed before scaling workers.

The token map provides a local ownership boundary. Public hosting needs managed
identity, token rotation, HTTPS and operational resource limits. Posting valid
predictions is explicit/manual; there is no unattended model-training or trading
agent loop. An optional `agent_id` on a trade attributes a future paper agent's
order to the same ledger, rather than maintaining a second `agent_trades` balance
that could diverge. Brokers are outside this implementation.
