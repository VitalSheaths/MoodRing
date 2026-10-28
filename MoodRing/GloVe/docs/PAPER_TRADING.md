# Paper trading guide

## Start here: disposable offline lesson

After installing dependencies, run from `MoodRing/GloVe`:

```bash
PYTHONPATH=src python examples/api_smoke.py --self-contained
```

For a clean checkout, first run:

```bash
cd MoodRing/GloVe
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
PYTHONPATH=src python examples/api_smoke.py --self-contained
```

The eight numbered outcomes walk through readiness, scenarios, BUY/PASS, entry,
refresh, exit, history and replay comparison. The helper supplies the local token,
IDs, timestamps and idempotency keys. It starts its own loopback server, writes
synthetic ledger records to temporary SQLite, then removes that owned ledger and
stops the server. Installation may need package access; the lesson needs no
external market data. Existing local ledgers are not modified.

Use this first; the persistent server below is for learners who want to retain
history and operate endpoints manually. The [capability index](CAPABILITIES.md)
links executable workflows and isolated research/ingestion components.

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

## Persistent local offline API

From a clean checkout, Python 3.11, at the repository root:

```bash
cd MoodRing/GloVe
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
export PAPER_QUOTE_MODE=offline
export PAPER_ENABLE_PRICE_CHECKER=false
export PAPER_DATABASE_URL=sqlite:///resources/offline-lesson.sqlite
# Generate a local token for both server and smoke client; never commit it.
export PAPER_SMOKE_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export PAPER_API_USERS_JSON="$(python -c 'import json,os; print(json.dumps({os.environ["PAPER_SMOKE_TOKEN"]: "learner"}))')"
PYTHONPATH=src python -m api.server
```

Installation requires package access; the running offline API and smoke client do
not need external data. The API persists normal **synthetic paper ledger records**
to the selected SQLite file. This is not a zero-persistence SEC test. Use a fresh
lesson database when you want an empty history; do not overwrite an existing ledger.
No research database or migrations are needed.

In a second terminal, activate the same environment and set `PAPER_SMOKE_TOKEN` to
the token generated in the server terminal (keep it out of shared logs). Run:

```bash
cd MoodRing/GloVe  # from repository root
source .venv/bin/activate
# export PAPER_SMOKE_TOKEN='the token from the server terminal'
PYTHONPATH=src python examples/api_smoke.py
```

For a one-command disposable server and ledger (no token setup needed):

```bash
PYTHONPATH=src python examples/api_smoke.py --self-contained
```

This starts a child API on a free loopback port, forces offline mode and a temporary
SQLite ledger, runs the same HTTP lesson, then stops the child and removes only
its own temporary directory. CI runs this command alongside the existing demos.

Alternatively run the server in the background and the smoke client in the **same**
shell so that they inherit the same token. The script verifies offline readiness,
creates two scenarios, publishes BUY/PASS signals, buys, refreshes, closes, verifies
idempotency/history, and saves/compares two replay runs. It prints numbered learning outcomes plus summary IDs
and counts. Each invocation creates new lesson records. It accepts only loopback
HTTP, disables environment proxies/redirects, and refuses a non-offline server.

Open [Swagger UI](http://127.0.0.1:8000/docs), click **Authorize**, and enter the
configured secret without the `Bearer ` prefix. ReDoc is at `/redoc` and the schema
at `/openapi.json`. Authentication is a local static token map, not public identity.
Unconfigured authentication returns 503; missing/invalid bearer authentication
returns 401. Every private operation enforces ownership.

## API workflow and schemas

Use `POST /api/v1/scenarios` with `{"name":"Offline learning","config":{"auto_exit":false}}`.
Use the returned string `id` as `scenario_id` when publishing a signal. Swagger's
signal example generates aware prediction/feature timestamps when the schema is
loaded. Reload `/docs` if the example expires. Supply genuine feature availability
for your own research inputs; the generated example is a synthetic lesson only.
Returns/risk inputs are decimal fractions: `mu=0.03` means 3%, `var` is squared-return
variance, and `drawdown_risk=0.02` means 2%. Availability must be at or before
prediction time, which must not be in the future.

Use the returned BUY signal `id` in `POST /api/v1/paper-trading/trade`:

```json
{"signal_id":"USE_RETURNED_SIGNAL_ID","dollars":"100"}
```

Supply an `Idempotency-Key` header, for example `entry-lesson-1`. Then close using
`POST /api/v1/paper-trading/close/{trade_id}` with `exit-lesson-1`. Repeating the same
successful request/key replays the existing trade. A changed payload under the same
key returns 409; a new close key on an already closed trade returns 409. Omit
`dollars` to use scenario sizing; an explicit amount is the total entry budget
including costs. PASS signals are recorded but cannot open a trade.

Responses serialize money, prices, quantities and portfolio ratios as **decimal
strings**. Prediction/decision model metrics remain JSON numbers. IDs are opaque
strings, timestamps are offset-aware ISO values, and absent exit/performance fields
are null. `config_json`, `prediction_json` and `decision_json` names match the
existing service; they are not renamed to the obsolete legacy models. All documented
operations now declare response schemas. Extension dictionaries are limited to
research metadata and driver evidence. Errors share `{"ok":false,"detail":"..."}`;
validation errors return 422 without echoing submitted values. See the schema for
400/401/403/404/409/422/500/503 responses.

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
| `POST /api/v1/market/refresh` | Refresh and update the requesting user's watched tickers |
| `GET /api/v1/market/quotes?tickers=AAPL&tickers=MSFT` | Timestamped indicative quotes |
| `GET /api/v1/market/metadata/{ticker}` | Cached company metadata |
| `POST /api/v1/backtest/run` | Replay timestamped prices and predictions; save the result |
| `GET /api/v1/backtest/runs` | This user's saved research runs |
| `POST /api/v1/backtest/compare` | Compare 2–20 saved run IDs |
| `GET /ready` | Local readiness; HTTP 503 when a dependency/configuration is unavailable |
| `GET /health` | Process/dependency status (HTTP 200) |

Histories are bounded recent records, not paginated exports: default 500, maximum
5,000 per collection. Saved runs list defaults to 100, maximum 1,000. Compare requires
2–20 distinct owned IDs. Backtest examples in `/docs` contain all required bars and
predictions; they perform no historical download. Entries use a later price event.

## Health and readiness

`GET /health` reports process/dependency state with HTTP 200. `GET /ready` returns
200 only when local auth is configured, the database answers `SELECT 1`, and an
enabled background checker task is running without a recorded refresh error;
otherwise it returns 503. Neither certifies production readiness or probes live
provider reachability. A disabled checker is valid for manual-only learning.

Both report `authentication_configured`, database backend/connectivity,
`quote_provider_mode` (`offline`, `live`, or injected `custom`), `offline`, checker
lifecycle, last attempt/success timestamps and sanitized refresh error. Last refresh
exposes counts only, not user symbols/IDs, tokens, provider exceptions or database
URLs. A failed refresh retains marks and last success; a successful refresh clears
the error. Readiness cannot guarantee a fresh price for every ticker: inspect quote
and valuation flags. Health can query the paper database but never initiates a
provider/SEC request. Custom injected providers are explicitly not claimed offline.

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

The API defaults to `PAPER_QUOTE_MODE=offline`. It serves fixed synthetic prices
AAPL=100, MSFT=50 and NVDA=75 USD, with `source=synthetic_offline`. These ticker labels
are lesson identifiers, not observed market prices. Repeated refreshes leave prices
unchanged; observation/fetch times are local simulator times. Unsupported symbols
return unavailable rather than fabricated prices. Metadata is explicitly synthetic,
with null industry/sector/market cap; offline mode creates no metadata JSON cache.

The existing external adapter is **opt-in** with `PAPER_QUOTE_MODE=live`. This is
indicative yfinance access, not approval of public redistribution, provider rights,
SEC access or a production feed. This increment tests it only with mocks. Keep the
offline setting for all documented lessons and CI. Unknown modes fail startup.

When enabled, `PriceChecker` runs every 300 seconds by default, batching tickers from open
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
entries; use the offline API, offline demo or supplied historical replay outside market hours.

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

## Token ownership and supported offline behavior

There is no signup or token-issuing service. For a persistent server the local
operator generates a secret, maps it to a user in `PAPER_API_USERS_JSON`, and gives
the client that same secret through `PAPER_SMOKE_TOKEN` or Swagger Authorize.
The token is a credential, not a public user ID; never commit it or share logs
containing it. The disposable lesson generates an unrelated temporary secret
automatically. Public provisioning requires separate identity/deployment design.

Keep `PAPER_QUOTE_MODE=offline` for supported offline learning. AAPL, MSFT and NVDA
have fixed synthetic prices: refresh updates marks but does not create a changing
market. Use the paper demo or caller-supplied replay events for movement and P&L.
Live quote mode is opt-in; failures are not replaced with synthetic quotes.

The running `/openapi.json` and `/docs` are authoritative for HTTP contracts,
including the endpoint order and returned-ID dependencies in the introduction.
This guide owns executable commands; approved specifications own intended scope.
SEC ingestion and synthetic universe jobs do not publish predictions or trades.
Prediction publication accepts caller-supplied inputs and is not an automated
research/model pipeline. SEC diagnostics remain separately reviewed in
[PR #26](https://github.com/VitalSheaths/MoodRing/pull/26); another real smoke
requires separate approval, and live metadata compatibility remains unproven.
