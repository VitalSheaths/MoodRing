# MoodRing

Learn the basics of stock trading with virtual money.

**Work in progress. Not production-ready. Local use only.**

## What you can try

- Run separate trading scenarios with their own virtual balances.
- Open and close simulated stock positions.
- Follow cash, holdings, profit and loss over time.
- Compare scenarios and see how trading costs affect results.
- Replay historical prices without placing real orders.

The current version is an API and an offline demo. A beginner dashboard and
validated automated predictions are still planned. Market quotes may be delayed
or unavailable; continuous real-time updates are not guaranteed.

## Run locally

From the repository root, using Python 3.11:

```bash
cd MoodRing/GloVe
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env
# Set your own random secret in PAPER_API_USERS_JSON inside .env.
PYTHONPATH=src python -m api.server
```

Open [API docs](http://127.0.0.1:8000/docs) and authenticate with your bearer secret.
Keep the server local; public hosting is not supported yet.

## Try the demo and tests

With the same environment active:

```bash
PYTHONPATH=src python examples/paper_demo.py
python -m pytest -q
```

The demo works without internet access. Database integration tests need separate
setup and skip by default; see the verification guide below.

## Follow the project

- [Usage guide](MoodRing/GloVe/docs/PAPER_TRADING.md)
- [Verification results](MoodRing/GloVe/docs/REVIEW_FIXES.md)
- [Open issues](https://github.com/VitalSheaths/MoodRing/issues?q=is%3Aissue+is%3Aopen) and [completed issues](https://github.com/VitalSheaths/MoodRing/issues?q=is%3Aissue+is%3Aclosed)

![Open issues](https://img.shields.io/github/issues/VitalSheaths/MoodRing)
![Closed issues](https://img.shields.io/github/issues-closed/VitalSheaths/MoodRing)

- [SEC ingestion contracts issue](https://github.com/VitalSheaths/MoodRing/issues/4)
- [SIC and universe contracts issue](https://github.com/VitalSheaths/MoodRing/issues/6)
- [OHLC, index and provenance contracts issue](https://github.com/VitalSheaths/MoodRing/issues/8)
- [Offline ingestion jobs issue](https://github.com/VitalSheaths/MoodRing/issues/10)
- [PostgreSQL job persistence issue](https://github.com/VitalSheaths/MoodRing/issues/12)
- [Offline SEC metadata normalization issue](https://github.com/VitalSheaths/MoodRing/issues/14)

## Contributor documentation

- [Product scope](MoodRing/GloVe/docs/01_prd.md)
- [Technical design](MoodRing/GloVe/docs/02_architecture.md)
- [Data contracts](MoodRing/GloVe/docs/03_data_models.md)
- [Delivery rules](MoodRing/GloVe/docs/04_rules.md)
- [Contributor instructions](.github/copilot-instructions.md) and [Git workflow](MoodRing/GloVe/docs/GIT_WORKFLOW.md)

This is a learning simulator. It uses virtual money and does not submit real trades.
