# MoodRing paper trading

A beginner-oriented stock simulator built around independent strategy scenarios.
Each scenario keeps its own cash, open positions, trade history and equity curve.
Orders use indicative market quotes and simulated fees and slippage. Existing
news, technical, volatility and macro research pipelines remain under `src/`.

## Run locally

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
# Edit PAPER_API_USERS_JSON in .env to use your own random secret.
PYTHONPATH=src python -m api.server
```

Open [API docs](http://127.0.0.1:8000/docs). Authenticate with `Authorization:
Bearer YOUR_SECRET`. The API starts on `127.0.0.1:8000`; the paper ledger defaults
to SQLite and is persisted across restarts. The research pipelines need PostgreSQL.

For a complete example without network access or market hours:

```bash
PYTHONPATH=src python examples/paper_demo.py
python -m pytest -q
```

See [paper trading guide](docs/PAPER_TRADING.md) for the routes, accounting,
risk rules, quote timestamps, historical replay, and deployment limitations.
See [implementation changes](docs/IMPLEMENTATION_CHANGES.md) for the changes to
existing files and the Postgres migration procedure.
