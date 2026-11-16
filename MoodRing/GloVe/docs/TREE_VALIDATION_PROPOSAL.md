# Training-fold tree comparison and QLIKE-tree proposal

Status: implementation and synthetic checks, not a real-data training release.
This is a separate experiment from the sparse-channel and timing packets; it does
not change their identities, configurations, source policy or authorization.

## Bounded MSE-tree comparison

Keep the original price + macro + technical feature arm, annualized five-session
realized-variance target, vendor-adjusted return convention, extreme outcomes and
positive prediction floor. Keep all news and coverage features out of this tuning
study so it does not mix feature selection with hyperparameter changes.

| Configuration | Leaves | Minimum leaf rows | Iterations | Learning rate |
| --- | ---: | ---: | ---: | ---: |
| C0 current reference | 7 | 5 | 100 | .03 |
| C1 | 3 | 5 | 100 | .03 |
| C2 | 11 | 5 | 100 | .03 |
| C3 | 7 | 10 | 100 | .03 |
| C4 | 7 | 20 | 100 | .03 |
| C5 | 7 | 5 | 50 | .03 |
| C6 | 7 | 5 | 150 | .03 |
| C7 | 7 | 5 | 100 | .05 |
| C8 | 7 | 5 | 200 | .01 |

All configurations use at most 32 candidate split thresholds per feature/node.
C8 changes two parameters. This small grid tests hypotheses; it is not an optimal
search and does not estimate parameter interactions. Minimum leaf rows are
correlated stock-date observations, not independent dates or events.

Use expanding fitting windows with validation halves 2021-H2, 2022-H1 and 2022-H2,
wholly inside the existing training partition. Exclude any fitting outcome reaching
the first validation date and any validation outcome extending past that fold.
Preserve past eight-session feature contexts at the boundary; do not discard known
past inputs merely because their outcome labels were purged. Preprocessing fits
once on unique fitting feature rows in each fold, never on its validation rows.
The original 2023–2024 development and 2025–2026 final-test tables are not loaded.

This is 27 fits: current reference plus eight candidates across three folds. One
attempt per fit, two compute threads, seed 17, 3,000 total boosting iterations,
maximum 11 leaves, no early stopping or automatic retry/resume. An interrupted
exclusive destination requires review. No full-training refit or subsequent
2023–2024 evaluation is included. Historical variance is an unfitted reference.

Select by pooled out-of-fold mean QLIKE. With the fixed complete stock grid, this
weights dates and stocks equally. Report each fold separately. Exact ties prefer
fewer leaves, fewer iterations, larger minimum leaf and then configuration ID.
Report volatility MAE/RMSE, floor counts, largest losses, dominant realized-return
date clusters, and worst fixed five-date blocks. Do not remove extremes or use
those diagnostic exclusions to silently choose a different winner. Paired intervals
use date means and 20-session blocks (5/10/40 sensitivity), without crossing gaps
or fold boundaries. They remain descriptive after selection.

`research_data.tree_validation.plan` binds data, ordered fold rows, code, runtime,
limits and destination without fitting. Execution requires a separate exact
approval through `research_data.tree_validation.execute` or its CLI. Private
artifacts and approval files are not included in Git.

## Why QLIKE training needs a separate implementation

MSE and QLIKE both target conditional expected realized variance in an unrestricted
population model. Their finite-sample weighting, regularization and optimization
behave differently. The mismatch is not a bug and matching losses does not guarantee
better forecasts. QLIKE can heavily punish underprediction near a variance floor.
Robustness to a noisy variance proxy is not robustness to arbitrary outliers or to
unverified historical data. See [Patton's analysis](https://public.econ.duke.edu/~ap172/Patton_vol_proxies_JoE_2011.pdf).

For log variance `f`, let `v=exp(f)`. The uncentered QLIKE objective and derivatives
are `L=f+y*exp(-f)`, `g=1-y/v`, `H=y/v`. The latter is nonnegative for `y>=0`.
Optimizing directly in variance has Hessian `(2*y-v)/v^3`, which can be negative.
The constant QLIKE optimum is the arithmetic mean of realized variance, subject to
the existing prediction floor. Fitting squared error to `log(y)` is a different
objective and cannot be substituted for QLIKE.

A proposed Newton implementation should reuse the bounded CART split-search
skeleton, accumulate gradients/curvature using float64, regularize near-zero leaf
curvature, bound proposed steps and check actual loss decrease with backtracking.
Zero outcomes must remain valid: their log-link QLIKE curvature is zero. Floor
constraints must be part of optimization, not a silently clipped training target.
Reject nonfinite loss/ratios and record failures; never discard extreme targets.
All-zero training targets have their constrained optimum at the existing floor.

`qlike_proposal_checks.py` implements only numerical primitives and synthetic
checks: finite-difference derivatives, zero outcomes, constant optimum, scale
behavior, finite bounds and bounded proposed leaf updates. It is not a tree
trainer. Constrained leaf optimization, split-gain behavior, backtracking and full
end-to-end fitting still require implementation and tests before any release.

A library gamma-loss comparator is possible, but must use the same library for
its MSE control to avoid mixing engine and objective effects. Scikit-learn documents
a log link for gamma loss and requires strictly positive targets; zero variances
cannot be silently altered or dropped. See the [official estimator documentation](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.HistGradientBoostingRegressor.html).

Before an objective comparison, freeze a separate packet with the same rows,
features, folds and a fixed configuration. Log-link positivity, Newton regularization
and the objective change together unless a positive-output MSE control is also
implemented. A direct comparison with the original additive-variance MSE tree
therefore measures a combined change, not a pure objective effect. Do not authorize
that experiment through the hyperparameter packet.

## Interpretation limits

Training eligibility starts in 2021 after technical warmup despite the nominal
2020–2026 data scope. Early folds have little history. The candidate grid was
motivated by earlier development results, so these folds are not a newly untouched
holdout. A fold winner remains a candidate until a separately authorized frozen
check; no result replaces the original baseline or the retained extreme loss row.
All retrospective timing and adjustment limitations still apply.
