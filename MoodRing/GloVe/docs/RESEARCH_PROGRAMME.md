# Historical research programme: starting specification

2026-11-01. Audit base: `90ddca0becf4d6ef10606c7e406b3adb8eda75cb`.
This is the starting research specification and implementation sequence, not
evidence of completed historical modelling. The first executable increment is
the [synthetic no-LLM explanation lesson](EXPLANATION_BOT.md).

## Repository audit

| Component inspected at the audit base | Finding | Consequence |
| --- | --- | --- |
| `src/models/temporal_transformer.py` | Five heads exist; the forward pass returns direction, mean, variance, trade success and drawdown | Preserve the five-head destination; class existence does not validate any head |
| `src/experiments/run_two_stage_evaluation.py` | Its demo creates random inputs/targets and optimizes mean/variance loss | Historical demonstration tables are not chronological five-head validation |
| `src/models/calibration.py` | Brier/ECE and temperature-scaling helpers exist | No accepted dataset, disjoint calibration window or calibrated deployed model follows from this |
| `src/utils/experiment_tracker.py` | Writes local JSON, supplies defaults such as full news coverage, and permits overwriting an experiment ID | Keep the existing tracker intact; add a separately reviewed validated run contract before using its records as bot evidence |
| `src/features/technical/macd.py` | Pure pandas calculation with warm-up | Reused by the bot |
| `src/features/technical/rsi.py` | Fills missing/zero-loss outputs with 50 | Not used as bot evidence; the new pure card calculation uses explicit missingness and a documented Wilder seed |
| `src/features/sentiment/pipeline.py` | Aggregates supplied scores; contains defaults and hardcoded mappings | No FinBERT inference or validated sentiment study established |
| `src/ingestion/pipeline/run_alignment.py` | Missing ticker can become AAPL | News preservation/mapping contracts remain a prerequisite to integration |
| `src/macro/connectors/fred.py` | Reads a database; “surprise” is deviation from a trailing mean | Does not establish release vintages or consensus expectations |
| `src/ingestion/market/` | Exact daily observations, identity, explicit calendars, availability and revision validation | Reused by the synthetic bot rather than defining another OHLC schema |
| Paper API/ledger | Caller-supplied predictions and deterministic risk authority | No research-to-trade connection in this increment |

These are source-level findings, not new training or live-provider results.
No historical acquisition, new research database or validated run registry was
found for the proposed end-to-end study. HTTP routes are unchanged; running OpenAPI
remains their authority.

## Proposed study definitions

Defaults below are a reviewable starting point for the label/experiment milestone.
They are **not implemented labels or accepted training configuration** in this
increment. Calendar, adjustment basis, data permissions and dataset coverage must
be settled before training. No final test period has been opened or selected here.
Human acceptance is pending: record the maintainer's identity, date and exact
definition version before changing this status. An agent review or passing tests
cannot supply the human's sign-off.

| Head | Proposed target and numeric example | Acceptance before training |
| --- | --- | --- |
| Direction | Next-session close-to-close price return above zero. Close 100 → 102 gives return 0.02 and label 1; unchanged gives label 0 | Freeze threshold, basis, horizon and explicit zero-return convention; compare frequency and logistic baselines with Brier/log loss and calibration |
| Expected return | Same decimal return: 102/100 − 1 = 0.02 | Explicit provider-native price versus total-return basis; zero/linear baselines, MAE/RMSE/bias |
| Variance | Conditional variance of that return, in squared-return units. Forecast mean 0.01, observed return 0.02: squared residual 0.0001 is a scoring observation, not a directly observed “true conditional variance” | Rolling-volatility baseline; proper distribution score, interval coverage/width; training-only parameter choice |
| Trade success | Next-open entry, fixed +2% target/−1% stop, one-session horizon. Entry 100, high 103, low 99 touches both 102 and 99: outcome **ambiguous** | Define gap fills, entry/exit convention, costs and ambiguous-case policy; daily bars cannot supply first-touch order |
| Drawdown | One-session maximum adverse excursion from next open: max(0, (open − low)/open). Open 100, low 97 gives 0.03 | Keep distinct from portfolio drawdown and close-to-close return; multi-session definition needs a later review |

The information cutoff is after the selected session closes. Each source needs
evidenced availability at or before that cutoff. An after-close reconstruction is
computed today; its next-close return does not imply execution at yesterday's
close. Next-open trade labels are a separate convention. Unknown availability
remains ineligible, and unavailable inputs retain explicit missing reasons.

**Glossary.** Event time describes the source period; availability is when that
information could be known; ingestion is when this system received it; computation
is when the feature was calculated. A reconstruction uses historical evidence now.
Operational replay requires previously recorded system inputs. A model prediction
is not an observed outcome, a confidence score is not data quality, and attribution
is not market causation.

## Reviewable delivery sequence

1. **Specification and early bot scaffold (this increment):** audit, explicit
   synthetic context, indicator cards, deterministic templates, content evidence
   IDs, bounded owned tools, golden regression cases and adapter interface.
2. **Archive foundation:** implement and review the synthetic Parquet → verified
   DuckDB path under the existing OHLC pilot contract. The real-data pilot retains
   its own exact scope, permission, acquisition and acceptance gates. This bot
   scaffold does not accept the pilot or expand its universe/window.
3. **Labels/features and run contract:** turn the proposed definitions above into
   versioned builders with numeric-example, ambiguity and future-perturbation
   tests. Add a strict record over the existing local JSON tracker, recording code,
   seed, configuration, data/artifact hashes, windows, counts, failures and missing
   metrics without defaults. Immutable predictions, append-only matured outcomes
   and revisions need explicit tests before a results tracker consumes them.
4. **Baselines and chronology:** panel-wide time splits, purged overlapping labels,
   train/tune/calibration/final-test windows and frozen evaluation criteria; then
   walk-forward validation. Reproduce the same metrics from the same run manifest.
   Unsupported heads remain not_evaluated. Failed comparisons are retained.
5. **Deterministic results tracker:** manually invoked validation and report
   generation from accepted registry records, with maturity checks and reproducible
   report IDs. No scheduler, autonomous training, promotion or deletion.
6. **Independent source increments:** macro release/vintage contracts, preserved
   news identities/mappings and an evaluated sentiment baseline, then eligible
   company events. Each source requires its own coverage and permission evidence.
7. **Five-head candidates:** compare modalities on matched held-out rows, report
   sample attrition and uncertainty, retain additions only on predeclared evidence.
8. **Free-form explanation comparison:** use the same tools/cache/evidence through
   a pinned local or hosted adapter and the same golden suite. Retain templates
   until the model passes the [comparison gate](EXPLANATION_BOT.md#model-comparison-gate).

No baseline return, calibrated probability, model superiority or investment
performance is claimed by the synthetic bot. A broader fixed-survivor universe,
ten-year window, source acquisition, deployment and model promotion remain
separately scoped decisions.

## Proposal decisions resolved for the first increment

The no-LLM route takes precedence over proposed embedding/model-first retrieval.
Use versioned curated text and keyword lookup initially. Embeddings and reranking
need measured retrieval benefit before adding dependencies. The proposal's
conflicting suggestions to answer from model memory and never use model memory
are resolved as follows: verified numbers are always tool outputs; curated
explanations are B; an unsupported question receives an honest bounded response.
A future model may add explicitly unverified C commentary, subject to evaluation.
There is no requirement to invent an answer when the evidence is absent.
