# MoodRing — paper trading learning project

**Work in progress. Not production-ready. Local use only.**

MoodRing combines research code with a stock paper-trading API. The simulator
persists independent scenario portfolios, simulated orders/fills, positions,
fees, realized/unrealized P&L and equity history. No real orders are submitted.
There is no beginner dashboard or validated automated model-to-trade pipeline yet.

## Run the implemented simulator

Commands start at this repository’s root. Python **3.11** is the documented environment.

```bash
cd MoodRing/GloVe
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env
# Replace the example bearer secret in PAPER_API_USERS_JSON before starting.
PYTHONPATH=src python -m api.server
```

Open http://127.0.0.1:8000/docs. The API uses a static bearer-token map for local
learning. Public hosting requires managed identity, token rotation, TLS,
rate limiting and audit logging; these are open tickets. Quotes from yfinance
are indicative, polled and potentially delayed, with no guaranteed real-time SLA.
Provider outages can prevent entries/exits; existing positions retain their marks.

## Demonstrate and verify

From `MoodRing/GloVe`, with the environment active:

```bash
PYTHONPATH=src python examples/paper_demo.py
python -m pytest -q
# Optional: a disposable server with permission to create/drop test databases.
PAPER_TEST_POSTGRES_URL='postgresql+psycopg2://USER@HOST/postgres' python -m pytest -q
```

The deterministic demo exercises scenario creation, entry, price updates and exit
without network access. Ordinary tests mock market data. Five PostgreSQL integration
tests skip unless the test database URL is supplied. A green local run is evidence
for the tested behavior, not production readiness or verified market performance.

## Project status and navigation

- [Simulator setup](MoodRing/GloVe/README.md) and [API/accounting guide](MoodRing/GloVe/docs/PAPER_TRADING.md).
- [Review fixes and verification](MoodRing/GloVe/docs/REVIEW_FIXES.md).
- [YAML ticket backlog](MoodRing/GloVe/docs/tickets/README.md): open work, closed acceptance evidence and reopening rules.
- [Implementation notes](MoodRing/GloVe/docs/IMPLEMENTATION_CHANGES.md).
- Research modules live in `MoodRing/GloVe/src/`: ingestion, features, macro,
  sentiment, models and experiments. The paper subsystem is `src/paper_trading/`;
  HTTP routes are `src/api/server.py`. Research paths require additional data,
  PostgreSQL and ML dependencies beyond the simulator installation.
- Architecture and usage notes under `MoodRing/GloVe/src/` contain research goals
  and legacy descriptions. Historical metrics are not validated platform results.

The project is intended to teach cash, exposure, fees, risk gates and portfolio
lifecycles. It does not claim profitable trading, production security or deployment readiness.
