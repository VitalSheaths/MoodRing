# C/V timing sensitivity and local lineage

Status: implemented preparation and approval-gated runner; no real-data fits from
this experiment are authorized or reported here. Private packets, data, model
states and reports belong in ignored artifact storage, not Git.

## Changed design

The previous four-arm experiment shared news coverage, peer count/coverage and
eight candidate missingness flags. This experiment intentionally removes the
peer and signed controls. It is a changed-design sensitivity study, not a rerun
of that experiment. Prior C/V predictions cannot be reused.

Both arms use the same baseline price, macro and technical features over the
existing eight-session context, plus 240 news-coverage controls and three shared
missingness flags for the 1/5/22-session variance proxies. V additionally uses the
three variance values. There are no sentiment-tone, peer, signed-variance or new
support/intensity values. Shared variance masks make the availability information
identical between C and V; baseline-only here includes those explicit masks.

| Variant | Extra macro delay | Extra news delay |
| --- | ---: | ---: |
| m0-n0 | 0 sessions | 0 sessions |
| m1-n0 | 1 session | 0 sessions |
| m5-n0 | 5 sessions | 0 sessions |
| m20-n0 | 20 sessions | 0 sessions |
| m0-n1 | 0 sessions | 1 session |
| m0-n5 | 0 sessions | 5 sessions |

These six conditions are predefined, with no joint-delay grid or selection of a
best delay. Retained macro variants reconstruct vintage selection and derived
values; they do not shift an already-derived macro column. News delays move
eligible source observations by exchange sessions, then reconstruct complete
occurrence groups and daily/context counts. Original observation times and
company mappings remain unchanged. Zero delay must reproduce published coverage.
The source policy is the one bound to the prior C/V packet; a later candidate
headline-review overlay is not silently substituted into the timing experiment.

All 4,920 training and 4,900 development rows, ordering, cutoffs, targets and
eight-session membership stay fixed. An unsupported delayed news window retains
its row with null coverage counts and daily masks. It is not zero news. Supported
empty windows retain observed zero counts. Monthly variance retains 140 warm-up
nulls in each partition. No forward-fill or cross-partition context is introduced.
The known 2025 source gap remains a final-test limitation, outside these features.

## Fixed experiment and gates

Twelve proposed fits: C and V in each condition. Each uses the selected C5 NumPy
squared-error tree: seven leaves, learning rate 0.03, 50 boosting iterations,
32 threshold candidates, minimum leaf size five. Maximum twelve one-shot attempt
reservations and 600 iterations, two numerical-library threads, no retries or
automatic resume. Deterministic seed recording does not provide independent runs.

Training-only preprocessing uses unique training daily rows for the baseline and
training anchors for coverage/new values. Each condition fits its own training
statistics under the same rules; C/V share those blocks. Missingness is explicit,
including delay-induced nulls. Development changes cannot alter fitted training
parameters. Future five-session labels mature by label_asof; they need not exist
at prediction cutoff. Five-session split purging and extreme outcomes are retained.

Primary metric is QLIKE on positive annualized variance; volatility MAE/RMSE,
floor counts, coverage, ticker and worst-loss diagnostics remain separate. The
unchanged 20-session historical-variance reference is included. Six within-delay
V-minus-C contrasts and ten nonzero-delay-minus-zero contrasts are predefined.
Each uses paired date means, 20-session date blocks (5/10/40 sensitivities), 2,000
draws, seed 17, no gap bridging. Intervals are descriptive on reused development
data; sixteen contrasts do not constitute independent confirmation.

`research_data.cv_timing_experiment.plan` verifies bound files, reconstructs
prepared inputs and creates a content-derived run identity without fitting.
`execute` requires granted=true, exact scope/run identity, approver and timestamp,
reproduces the plan, and refuses an existing destination. Reservations precede
fits; interruption preserves failed evidence and blocks replay. A completed run
still needs independent result reconciliation before research release.

```sh
PYTHONPATH=src python -m research_data.cv_timing_experiment \
  --root "$RESEARCH_ROOT" --plan "$EXACT_PACKET" --approval "$EXACT_APPROVAL"
```

This is an execution command, not a preparation command. Do not run it without
exact approval. All five thread variables recorded in the packet must equal 2.
Private preparation also patches preprocessing/model fitting to raise, proving
the preparation path makes no fits. Final test, paid calls, acquisition, MASTER,
direct-QLIKE tree training, strict PIT promotion and trading remain separate gates.

## Metadata lineage

`ExperimentTracker.lineage()` exposes a read-only companion by default. An
explicit writer appends content-addressed JSON records referencing existing
artifact hashes, sizes and relative paths. It does not embed data, parameters,
predictions or credentials and does not modify existing experiment JSON.

Record kinds cover sources, datasets, features, splits, configuration, preparation,
approval, run/attempt, preprocessing, model, predictions, metrics, contrasts,
audits and reports. Parent links distinguish preparation from execution. Code and
configuration commit IDs, approval identity and stage are explicit. Execution
produces one-shot reservations and artifact references; reports query without
writing. A preparation status never grants execution authority.

```sh
PYTHONPATH=src python -m utils.research_lineage \
  --store "$LINEAGE_DIRECTORY" --run-id "$RUN_ID" --stage development_only
```

Appending uses a filesystem lock, exclusive atomic publication and content hashes;
changed records or duplicate reservations fail closed. This is application-level
append-only evidence on a trusted local filesystem, not tamper-proof storage or
a new model registry. Neither lineage nor an approved research result can promote
a model, generate a trading signal or replace the existing ledger. Such promotion
requires a later independent holdout and architecture review.
