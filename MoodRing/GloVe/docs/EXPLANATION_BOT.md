# Evidence-linked indicator lessons

This read-only increment reuses the earlier synthetic explanation library and adds
a separate bridge to approved retrospective research features. It does not connect
a forecasting model to paper trades or publish forecasts.

## Two explicit data contracts

- Synthetic Snapshot: supplied fictional DailyObservation/calendar objects; the
  existing cutoff/revision rules and tests remain.
- ResearchSnapshot: only explicitly bound train/development feature and context
  files. No future labels, final-test tables, provider requests or sentiment
  inference. Values are imported from the frozen feature calculation; this is not
  a new independent OHLC calculation or historical-version reconstruction.

A trusted local operator pins the descriptor SHA-256 and supplies the owner.
The descriptor binds the partition manifest, feature file, context file and
original research contract metadata. File bytes are verified before parsing;
symlink paths, altered bindings, nonfinite values, duplicate keys, unsupported
partitions and context gaps fail closed. A digest establishes integrity, not
human authorization: the approval reference must be reviewed separately.

Only matched anchors are selectable. Each retains its eight context sessions;
those sessions are not the full technical-indicator warm-up history. The approved
cutoff must match exactly. Loaded-at records today's local import, never a
fabricated historical availability time. The strict PIT machinery is unchanged.

## Questions, tools and evidence

Default answers use TemplateAdapter, with no model, key, network, SQL or shell.
Questions cannot select files. Owner checks occur before cache access; cache
identity includes owner, snapshot, query, computation time and tool version.

The tool returns values or explicit missing/not-evaluated states. Grade A resolves
to that tool result; grade B resolves to a versioned methodology passage. A is
not a claim of predictive accuracy or historical knowability. Model commentary
cannot rewrite A/B statements or citations. A failed tool prevents an answer;
a rejected adapter falls back explicitly. This is a trusted Python interface,
not a sandbox for arbitrary third-party adapter code.

The vocabulary is bounded. Use exact indicator names or common aliases (RSI, ATR,
MACD, OBV, volatility, log return). Conflicting recognized stock symbols or dates,
trade requests and unsupported questions receive bounded help. This is not general
natural-language entity resolution. The bot can describe evidence and uncertainty;
it cannot establish why markets moved or guarantee a user's decision.

## Dictionary and education

[Indicator dictionary](INDICATOR_DICTIONARY.md) and its
[machine-readable source](../src/explanation/indicator_dictionary.json) define
32 cards: 23 supplied technical measures and nine unavailable model/advanced
outputs. Formulas, terms, units, minimum history, code locations and limitations
are visible. Beginner copy leads with meaning; intermediate users can expand
calculations and evidence. Sample daily volatility, annualized mean-square
variance and future five-session variance remain distinct.

## Local preview

From the project directory, use operator-provided private paths and digest:

```sh
PYTHONPATH=src python -m explanation.research_preview \
  --root "$ARTIFACT_ROOT" --descriptor "$DESCRIPTOR" \
  --descriptor-sha256 "$DESCRIPTOR_SHA256" --port 8018
```

The listener binds 127.0.0.1, serves only its fixed view, rejects cross-site
requests/rebinding hosts and logs no question text. Stock/session selectors use
preloaded approved anchors. No directory browsing or browser API key is exposed.
This is a local preview, not production authentication or a mobile-hosted service.
The already shared progress portal is a different deployment.

## Evaluation and release gates

Run the synthetic tests with pytest. Existing 37 golden cases are agent-authored
development regressions, not population accuracy. The private preparation packet
adds mechanical reconciliation against approved train/dev values and a 40-case
development convenience review spanning all ten companies. It has not been
human approved. Review correctness, attribution, clarity, missingness and usefulness
for beginners; retain reviewer, date and exact content hashes.

Before a free-form model: freeze prompts/corpus/adapter/model, ensure account
entitlement and data-use permission, and compare with templates on the same
questions. Tune development only. An independently authored held-out question set
must remain separate from model-facing knowledge and cannot be filled by copying
solutions. Zero unresolved citations/unsupported numeric claims/security failures
is a gate, not proof of semantic accuracy. Measure answer quality, failure rate,
latency and cost. Final-test research, forecasting training and paid API calls are
not authorized by this module.
