# Sparse-channel and predictive-timing packets

The preparation and execution code is implemented. The experiments described here
are proposals requiring exact approval; no result is implied by this document.
`research_data.sparse_timing_experiments.plan` validates bound train/development
files and creates a deterministic SHA-256 run identity without fitting anything.
`execute` requires that identity, named approval, timestamp, reviewed-candidate
acceptance, and an unused bound destination. It reserves each attempt before fit.
Interrupted or failed destinations cannot resume or retry automatically.

## Separate experiments

| Scope | Train / development rows | Conditions | Maximum fits / attempts |
| --- | --- | --- | --- |
| Sparse channel | 4,920 / 4,900 | Six feature arms at zero extra delay | 6 / 6 |
| Predictive timing | 4,870 / 4,850 | Six delay conditions, two feature arms | 12 / 12 |

Both use the original baseline-selected NumPy tree configuration: 100 boosting
iterations, seven leaves, learning rate 0.03, 32 candidate bins, minimum leaf five.
There is one deterministic seed (17), two compute threads, one attempt per fit and
no parameter search. Limits are 600 and 1,200 total boosting iterations respectively.
These are algorithmic limits, not a guaranteed wall-clock duration.

Sparse arms all contain the original price, macro, technical and coverage controls:

- C: original coverage-controlled baseline.
- Q: C plus separate channel support factor, effective input count and maximum input weight.
- R: Q plus raw generic tone in each channel.
- W: Q plus support-weighted tone in each channel, replacing raw tone.
- I: Q plus unsigned intensity in each channel.
- WI: W plus unsigned intensity.

Direct, product and sector/competitor channels remain separate columns. A successful
exact input has one daily vote per stock/channel, with `1/n` weights across its
source occurrences. With `n` successful distinct inputs, support is `n/(n+5)`.
Five is a fixed diagnostic design choice, not calibrated reliability. Weighted
tone is the equal-day mean of support times daily tone, without renormalizing by
support. Intensity comprises mean absolute tone, positive-plus-negative probability
mass, and within-day tone variance, each averaged equally across nonmissing days.
Effective input count is `1/sum(w_h²)` over accumulated exact-input context weights;
repeated input text across days does not become independent evidence.

The predictive timing conditions are additional (macro, news) exchange-session
lags `(0,0), (1,0), (5,0), (20,0), (0,1), (0,5)`. Compare original coverage controls
with the same controls plus the three raw tone channels. No sparse support features
enter this experiment. Macro variants reconstruct eligible vintages and rolling
operands; news variants delay occurrence eligibility and rebuild daily groups and
contexts. Do not shift final context averages. Original observation times remain
unchanged. No cross-partition borrowing or missing-as-absent substitution is allowed.

The first five otherwise-matched anchors per stock/partition lack the full delayed
news support window. The separate common-row population excludes them for every
condition, including the newly fitted zero-delay reference. Original population,
predictions and extreme floor cases remain immutable. One-at-a-time delay checks
do not cover macro/news delay interactions or causal response horizons.

## Binding and review

A private descriptor binds every source/audit dependency, prepared feature,
context, label and anchor file; ordered row IDs; exact output destination; and
human-review overlay. The plan also binds feature/target/context hashes, runtime
versions, code files, limits, metric definitions and contrasts. Human review is
separate from classifier tone. Exact development mapping quarantines affect only
new candidate features; do not overwrite published features or model scores.
Any review, data or code change requires regeneration and fresh approval.

Run `plan(scope, descriptor, artifact_root)` locally, save its returned JSON and
an ungranted approval template, and independently reproduce the identity. Packet
preparation must prohibit calls to preprocessing fit and model fit. After explicit
approval, execution uses the local artifact paths supplied by the operator:

```sh
python -m research_data.sparse_timing_experiments \
  --plan "$PLAN" --root "$ARTIFACT_ROOT" --approval "$APPROVAL"
```

Set `OPENBLAS_NUM_THREADS`, `OMP_NUM_THREADS`, `MKL_NUM_THREADS`,
`VECLIB_MAXIMUM_THREADS`, and `NUMEXPR_NUM_THREADS` to `2` before Python starts.
Preparation does not supply credentials or fetch data.

Primary sparse contrasts are Q−C, R−Q, W−R, I−Q and WI−W. Timing contrasts are tone
versus coverage within condition and each delayed condition versus the common-row
zero-delay reference. QLIKE is primary; volatility errors and floor counts are
secondary. Paired uncertainty keeps all stocks of a date together: 20-session
blocks primary, 5/10/40 sensitivity, 2,000 draws, seed 17, no gap bridging. This
addresses overlapping outcomes without claiming independent stock/date windows.
Final-test work, full scoring, publication and any new MASTER run remain held.
