# Offline indicator cards and explanation bot

First increment, 2026-11-01. Local Python library and CLI with a standalone HTML
view. It consumes supplied **synthetic** `DailyObservation` and `SessionCalendar`
objects; it does not read historical provider archives, an experiment registry,
news, a database or the paper ledger. See [research sequence](RESEARCH_PROGRAMME.md)
and [commands](PAPER_TRADING.md#offline-explanation-bot).

## What works

The default template adapter answers a small topic vocabulary: indicator cards,
trend/RSI/MACD, volatility/ATR/Bollinger, volume/OBV, coverage and model status.
The calling application selects the snapshot, instrument ID, session and cutoff.
The example selects fictional `stock:example` on 2026-09-25. Conflicting explicit
dates or recognized uppercase symbols receive an unsupported response; this is
not a general natural-language entity/date resolver. Other questions receive
bounded help instead of an invented answer.

Computed cards include close-to-close return, high-low range divided by close,
relative volume against the preceding twenty sessions **excluding today**, RSI,
MACD/signal/histogram, ATR as a fraction of close, Bollinger %B/bandwidth, twenty
daily log-return sample volatility and twenty-session OBV change. Values are
descriptive indicators, not validated predictive features. Full ATR prices,
Bollinger band levels, ARCH/EGARCH/EVT fitting and portfolio calculations are later
increments. Volume profile is unsupported without intraday data. The five model
heads remain explicitly not_evaluated.

RSI and ATR use fourteen observations and an arithmetic seed followed by Wilder
smoothing. RSI needs fifteen closes; an entirely flat series is undefined and
stays missing, an all-gain series is 100 and all-loss is 0. MACD reuses the existing
helper's recursive EMA conventions and requires 34 closes for its signal.
Bollinger uses population standard deviation, realized volatility uses sample
standard deviation without annualization. Zero-width bands and zero-volume
baselines remain undefined. A measured zero return/volatility is a valid zero.

## Evidence and access contract

- `Snapshot` is an immutable **in-memory view**, not a new archive or acquisition
  contract. It reuses existing identity, exact prices, availability, session and
  revision types. One series, at most 512 rows/sessions, at most sixteen snapshots.
  Only the fictional `bot-fixture` source is accepted in this scaffold.
- The source keeps Decimal precision. Float conversion occurs only in the derived
  indicator calculation and every reported result must be finite. Methodology is
  versioned as `indicator-cards-v1`.
- Revisions must form a valid chain; conflicting evidence for the same revision is
  rejected. The latest eligible revision at the cutoff is selected. Missing or
  unknown-availability sessions do not disappear through filtering: a gap anywhere
  in the selected history makes computed cards missing. This conservative policy
  can later be refined per indicator, with tests.
- The query is read-only and fixed. There is no generated SQL, shell execution,
  network request, arbitrary path argument or model-controlled query. Snapshot
  permissions are checked before cache access, retrieval and renderer invocation.
  `owner_id` must come from a trusted caller; this library does not authenticate
  public clients or replace the API's token/ownership enforcement.
- Cache entries bind owner, snapshot content, query, computation time and tool
  version. Ingestion eligibility depends on computation time, so an earlier
  reconstruction cannot consume a later cached result. The default LRU limit is
  64, maximum 256. Reusing an explicit request timestamp shares the result across
  adapters. Evidence IDs exclude model selection and wall-clock execution latency.
- Content hashes bind the full snapshot and tool output. Exact receipt and source
  revision IDs remain inspectable through the snapshot/result objects. The
  computed-at timestamp is recorded in each answer; all output is labelled
  synthetic historical reconstruction, never operational replay.
- A statements reproduce the tool's derived values or missing statuses; A here
  means **computed from synthetic inputs**, not validated real-market evidence.
  B statements quote the curated versioned corpus with passage IDs. C statements
  are explicitly unverified. A small fixed possible-mechanism template is used
  only for the high-volume/small-return “why” example; it is not causal evidence.

The corpus lives in `src/explanation/knowledge.py`. Keyword retrieval returns at
most three passages; mandatory coverage/help passages may be appended to an
answer. Its content IDs change when the text changes. Source/model text is data;
the HTML renderer escapes every dynamic value and includes a restrictive CSP.

## Swappable adapter

`AnswerAdapter.render(AnswerContext)` accepts only the question, already-authorized
tool output, selected passages and deterministic statements. It receives no tool
handle or storage object. Templates are the default and require no model, API key,
download or new dependencies. An adapter must preserve every A/B statement, grade
and citation exactly. It may append C commentary beginning `Unverified:` or
`Hypothesis, unverified:`. Invalid outputs are discarded and the deterministic
answer returns with explicit `adapter_rejected` status.

This is a trusted in-process interface, **not a Python sandbox**. A future local or
hosted transport needs its own timeout, response-size, rate/cost and disclosure
configuration. No such transport or real model has been selected or executed in
this increment. A future provider should receive only the authorized minimal
context necessary for its task, under an explicit data-disclosure choice.

## Model comparison gate

Run `explanation.evaluation.evaluate(adapter)` against the same checked-in golden
questions for every candidate. The evaluator uses the same tool implementation and
contracts and includes numeric/status oracles, unsupported queries, missing
coverage, historical cutoffs, conflicting revisions, command injection and
cross-owner denial. Focused tests also cover numeric warm-up, future perturbation,
revision selection, computation-time cache isolation and output escaping.

The public dev regression suite has 30 cases (21 initially, nine added during
prepublication review). They are agent-authored and not human-approved or an
independent held-out benchmark. Its citation validity and grounded
grade-integrity metrics are structural checks, not a semantic accuracy estimate.
Retrieval recall@3 measures the keyword search before mandatory passages are
appended. Reported timing includes fixture construction and both candidate and
template-reference answers; it is not provider latency. Template inference cost is
zero; unknown model cost stays null. Free-form quality and model comparison stay
not_evaluated until a real comparison is performed.

Before selecting a model:

1. Pin model, adapter, prompt, corpus and suite versions. Extend the suite with
   real user phrasing and a held-out paraphrase set, including malicious retrieved
   headlines once a news reader exists. Do not claim that unimplemented news
   retrieval is covered by source-metadata injection tests.
2. Require zero unauthorized reads, zero fabricated A/B statements/citations and
   no regression on numeric or missing-evidence cases. A rejection counts as a
   failed answer even if the fallback is safe.
3. Blind-score paired answers for relevance, completeness and correctly qualified
   hypotheses on a predeclared 0–2 rubric per dimension. Require a higher mean
   score than templates and no critical factual/security failures; ties retain
   templates. Report case counts and score uncertainty instead of claiming
   superiority from a tiny set. Use a separate held-out set after tuning.
4. Record per-answer actual token/cost telemetry (or explicitly unknown), model
   latency median/p95 separately from tool latency, and operator-defined acceptable
   budgets. Select hosted/local on those measurements, not an assumed ranking.

The current test-only replacement adapter proves interchangeability; it is not an
LLM and provides no evidence of model quality. Embeddings, a scheduler, report
watcher, run/outcome joins and historical source integrations remain future work.

## Evidence and CI review — 2026-11-02

Scope is recorded in [issue #30](https://github.com/VitalSheaths/MoodRing/issues/30).
`Answer.resolve_evidence` resolves each citation to a record in the authorized
answer envelope, including after serialization. The answer contract rejects
unresolved IDs, foreign tool IDs, grade/source mismatches and invented A/B text.
Tests prove that a tool exception prevents an answer and renderer invocation;
successful A statements resolve to the actual invoked tool result. This is an
orchestration guarantee, not a cryptographic attestation against arbitrary trusted
Python code manually constructing objects.

The [CI workflow](../../../.github/workflows/paper-trading.yml) supplies PostgreSQL
16 and `PAPER_TEST_POSTGRES_URL` and runs the full pytest suite. The local skips
are existing database integration checks; this bot has no PostgreSQL persistence
path. Its owner checks, bounded in-memory cache and evidence resolution are covered
by ordinary local tests. Current-head CI results must still be checked on the draft
PR; workflow configuration alone is not proof that a particular run passed.

Broader concept/evaluation drafts remain separate from this public runtime corpus.
Tune on dev only. Do not build KB cards from held-out solutions; use independent
sources and keep the reviewed rubric/solutions out of the model-facing question
export. Human approval must bind exact IDs, versions and content hashes, reviewer,
date, decision, source checks and resolved flags. A script or AI score cannot
provide that approval. Proposed research definitions retain their separate T0 gate.
