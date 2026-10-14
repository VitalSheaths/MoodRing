# Review fixes

Reviewed source commit: `fbafb36`. The root README overstated readiness and its
commands assumed a nonexistent root `src/` layout. Documentation now directs
users to `MoodRing/GloVe` and labels the simulator as work in progress/local-only.
Unsupported performance/readiness claims and external project references were removed.

## Behavior changes

Provider lookup, refresh and quote-validation failures return the generic 503
message `Market data temporarily unavailable`. Original exceptions remain in
server logs; operators must restrict access to those logs. Metadata lookup follows
the same error boundary. API OpenAPI metadata and health explicitly identify local
scope and `production_ready: false`.

Replay adds `signal_outcomes` with the original `input_index`, ticker, terminal
status, reason and signal/trade IDs when available. Statuses are `traded`,
`expired`, `position_exists`, `not_tradeable`, `rejected`, `no_later_event` and
`never_eligible` (prediction after final event). Missing prices and risk rejections
are terminal rejected attempts in this replay; they are not retried automatically.
`signal_status_counts` counts these outcomes and `unfilled_signal_count` counts
all statuses other than traded, including deliberately rejected inputs. This changes
the interpretation of the old incomplete count. Final open positions remain marked;
no forced final exit or same-event entry has been introduced.

## Verification and remaining work

Commands are run from `MoodRing/GloVe`:

```bash
python -m pytest -q
PAPER_TEST_POSTGRES_URL='postgresql+psycopg2://USER@HOST/postgres' python -m pytest -q
PYTHONPATH=src python examples/paper_demo.py
```

The initial review baseline produced **35 passed, 5 skipped** on Python 3.11.
Database checks are optional locally and require a disposable database with create/drop
permissions. The hosted workflow configures PostgreSQL explicitly. Its existence does
not mean a hosted run has passed. New regression tests cover provider redaction,
API readiness and exhaustive replay accounting. See ticket evidence and the final
verification log below for results from this revision.

The reported runtime failure remains an open reproduction ticket. Mocked provider
tests and an offline demo cannot establish live provider availability, automated
model quality, public-hosting safety or production readiness. Remaining work is
tracked in [GitHub issues](https://github.com/VitalSheaths/MoodRing/issues).

### Local verification for this revision (2026-10-10)

- Existing Python 3.11 environment: **44 passed**, including all five PostgreSQL tests.
- Fresh Python 3.11 virtual environment installed from requirements-dev.txt:
  **44 passed**, including all five PostgreSQL tests. One dependency deprecation warning.
- Real localhost HTTP smoke: documented `python -m api.server` starts; health reports
  local scope and authenticated scenario creation returns 201. Temporary ledger only.
- Offline demo: two independently sized positions mark upward and close with net
  profits $9.37 and $18.74 after simulated costs. This is deterministic demonstration data.
- GitHub workflow for `c3975d8` completed successfully, including the configured
  PostgreSQL checks and offline demo: [hosted run](https://github.com/VitalSheaths/MoodRing/actions/runs/38018685001).
  MR-004 is closed with this revision-specific evidence.
