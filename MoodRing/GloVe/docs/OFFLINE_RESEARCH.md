# Offline retrospective research

This package prepares and checks local research artifacts. It does not connect
forecasting models to the paper-trading API, create autonomous agents, fetch a
new dataset, or authorize an experiment. The product contracts remain unchanged.
Scope is tracked in [issue #32](https://github.com/VitalSheaths/MoodRing/issues/32).

Implemented components include manifest-bound Parquet inspection, retrospective
adjustment and target calculations, train-only imputation/scaling with missingness
masks, source-weighted sentiment aggregation, bounded forecasting runners, and
synthetic leakage/approval tests. Actual data, approvals, score caches, predictions,
review labels and run packets belong in private artifact storage, not Git.

The retrospective target is annualized realized variance:
`252/5 * sum(next five vendor-adjusted daily log returns squared)`. Report its
square root as volatility. QLIKE evaluates positive variance predictions; volatility
MAE/RMSE are separate metrics. Genuine extreme observations are retained. Outcomes
must mature by `label_asof`; features must satisfy the declared prediction cutoff.
Future outcomes are not required to exist at forecast time. Five-session outcomes
crossing a chronological partition boundary are excluded.

Eight-session contexts remain inside their own partition. Technical warmup can
use earlier price history; fitted imputation and scaling use training only. Fit
baseline preprocessing on unique training feature rows, not repeated overlapping
windows. Append missingness masks and record columns absent throughout training.
Never fill missing sentiment with neutral zero or carry it forward.

The twenty-session historical-variance reference has no fitted parameters. Ridge
and bounded NumPy CART boosting train with variance squared error. The small
MASTER-inspired model trains with QLIKE, so comparisons confound architecture and
training loss. Its stock attention is confined to each prediction date; stock
presence and label masks are separate. Deterministic seed repeats are not
independent observations. Development selection is exploratory evidence.

Strict point-in-time reconstruction remains separate. Current adjusted prices do
not establish historical price versions; macro vintage dates do not prove channel
receipt times; retained news observation times do not prove historical headline
versions. Source gaps remain explicit. A fixed current stock universe also limits
historical representativeness. No performance or production-readiness claim follows
from synthetic tests or module availability.

See [experiment packets](SPARSE_TIMING_EXPERIMENTS.md) and
[diagnostic contracts](DEVELOPMENT_DIAGNOSTICS.md).

## Validation and reproduction

Install `requirements-dev.txt`, then run `python -m pytest -q`. PostgreSQL tests
require the disposable `PAPER_TEST_POSTGRES_URL`; absent that setting they skip.
CI provisions PostgreSQL separately. Optional Torch tests run with a CPU Torch
installation using:

```sh
PYTHONPATH=src:tests python -m unittest test_master_model test_forecast_master test_positive_master_experiments
```

These tests create synthetic fixtures, not an authorization to train retained
financial data. No pretrained NLP weights are needed. The dedicated CI job installs
CPU Torch and runs those synthetic checks; the default suite reports optional
Torch skips when that package is absent.
