# Fixed variance and peer comparison

This release adapter prepares four deterministic NumPy boosted-tree fits on
supplied, previously approved train/development inputs. Preparation performs
hash, row, calendar, target-maturity, feature and coverage checks; it does not fit
preprocessing or a forecasting model. Execution requires the exact run identity.

## Fixed design

All arms share the unchanged price/macro/technical eight-session design. Add
published news coverage counts/masks (no tone), peer count/coverage and every
new candidate's missingness flag as common anchor controls. New variance values
are appended once at the prediction anchor, not broadcast into earlier sessions.

| Arm | Additional values beyond common controls |
| --- | --- |
| C_controls | None |
| V_horizons | Annualized 1/5/22-session mean squared log returns |
| S_signed | V plus negative and positive 22-session squared-return components |
| P_peers | S plus same-date leave-one-stock-out 22-session peer variance |

The configuration is fixed at seven leaves, learning rate 0.03, 50 boosting
iterations, 32 candidate thresholds and minimum leaf size five. This is the
C5 configuration from the prior chronological training-fold study. It is not
the older 100-iteration timing-packet configuration; that packet is unchanged.

At most four fits, four reserved attempts and 200 total boosting iterations.
Two numerical-library threads, recorded seed 17, no stochastic seed repetitions,
tuning, early stopping, automatic retries or automatic resume. A failed partial
directory remains inspectable and cannot be reused.

## Feature overlap and interpretation

Existing realized_vol_5/20/63 are daily sample standard deviations, whereas the
new proxies are annualized, non-demeaned mean squares. The baseline already has
a 20-session mean-square benchmark and a 20-session downside measure. New
horizons and representations are not independent new source information.

The one-session proxy is 252 times the squared existing log return. Negative
plus positive 22-session components equal the total proxy; the peer support
fraction is count divided by nine in a ten-stock cohort. Keep these identities
visible rather than claiming independent signals. A tree can exploit different
axis-aligned representations despite deterministic relationships. No correlation
or outcome-based feature pruning is performed.

The new monthly fields require partition-local history. Preserve their warm-up
nulls and the exact approved row population. Fit baseline preprocessing on unique
training daily rows, anchor preprocessing on training anchors only, and reuse
shared transformations across arms. Missingness flags are shared even for arms
that omit the corresponding values; new value blocks do not append them again.

## Evaluation and release

Primary metric: QLIKE on annualized variance. The tree still fits variance MSE.
Report volatility MAE/RMSE separately, plus floor counts, ticker/coverage subsets
and large row losses. Keep the 20-session historical-variance reference and
extreme outcomes. Paired date-block uncertainty uses 20 sessions primarily,
5/10/40 as sensitivities, 2,000 replicates and seed 17; stocks on a date remain
together and blocks cannot bridge missing-date gaps.

Predeclare V minus C, S minus V, P minus S and P minus C contrasts. No per-arm
configuration choice or automatic winner publication. Development has been used
repeatedly: this is exploratory evidence, not a fresh holdout or causal finding.

A packet binds source files, ordered anchor/context keys, cutoffs, targets,
candidate reconstruction, coverage publication receipts, code, runtime and the
exclusive output destination. Execution revalidates the packet, reserves each
attempt before fitting, and writes model/preprocessing states and result hashes.

The pasted stock-identity/SEC design is separate from this adapter. No universe
expansion, SEC/OpenFIGI/Schwab access, new acquisition, sentiment inference,
final-test evaluation, MASTER fit, QLIKE-tree trainer or paid call is included.

Tests use synthetic fixtures for changed inputs, exact approval, row/time/value/
mask binding, shared controls, development isolation, interruption and replay
refusal. They do not establish predictive performance.

