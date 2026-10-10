# MoodRing paper trading

**Work in progress. Not production-ready. Local learning use only.**

A beginner-oriented stock simulator built around independent strategy scenarios.
Each scenario keeps its own cash, open positions, trade history and equity curve.
Prices may be delayed. Trading costs are simulated, and no real orders are placed.

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
Bearer YOUR_SECRET`. The API starts on `127.0.0.1:8000`; your simulated
portfolio is saved across restarts. Keep the server local.

For a complete example without network access or market hours:

```bash
PYTHONPATH=src python examples/paper_demo.py
python -m pytest -q
```

See the [usage guide](docs/PAPER_TRADING.md) for a complete walkthrough.
See [verification results](docs/REVIEW_FIXES.md) for what has been checked.

Track limitations and acceptance evidence in [YAML tickets](docs/tickets/README.md).
Quotes are polled and potentially delayed; no real-time delivery guarantee is provided.
