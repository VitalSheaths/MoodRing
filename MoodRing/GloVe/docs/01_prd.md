# Product requirements

Status: Phase 1 specification for review, 2026-10-13. This document defines scope;
it is not evidence that planned features are implemented.

## Product and users

MoodRing is a local-first equity paper-trading and research platform. Beginners
learn orders, cash, holdings, equity, realized/unrealized P&L, costs and risk
limits without real money. Practitioners investigate swing-strategy signals and
compare reproducible historical and paper scenarios.

The current product is an API and deterministic offline demonstration. A public
service and beginner dashboard are separate future decisions.

## Capabilities and acceptance

| Capability | Status | Acceptance boundary |
| --- | --- | --- |
| Independent scenarios | Implemented | Each user-owned scenario has a separate balance, policy and portfolio history. |
| Long fractional stock simulation | Implemented | Entry and full exit record orders, fills, costs and positions atomically; retries cannot double-spend. |
| Portfolio accounting | Implemented | Cash and cost basis reconcile with fills; realized and unrealized P&L remain distinct; stale marks are visible. |
| Risk gates | Implemented | Ownership, freshness, available cash, sizing, exposure and drawdown constraints can reject an entry. |
| Quote updates | Implemented | Indicative polling updates watched symbols; outages retain marks and cannot be described as guaranteed real-time service. |
| Manual/explicit predictions | Implemented | Inputs include prediction time, feature availability and version metadata; no validated automated feed is implied. |
| Historical replay | Implemented | Entries use a later event; concurrent open positions remain marked; every input has a recorded outcome. |
| Research modules | Existing research code | Presence of a module or historical result does not establish a validated end-to-end production workflow. |
| Top-100 universe and bounded OHLC processing | Planned | Versioned membership, bounded per-ticker/date reads, checkpoints and causal feature construction. |
| SEC/earnings catalysts | Planned extension | Source IDs, accession, units, reporting periods, revisions and availability are retained and validated. |
| FinBERT benchmark | Planned evaluation | Compare with the existing sentiment baseline on labeled, time-separated data before claiming improvement. |
| Structured advisory | Planned | Versioned evidence, uncertainty, cutoff, expiry and deterministic risk results must be replayable. |
| Public deployment | Deferred | Requires an approved operating model, shared state and separately reviewed identity/security controls. |

## Learning workflow

Create a scenario → publish an eligible explicit prediction → inspect the decision
→ request a simulated entry → observe price updates and costs → close the position
→ compare state and history. Rejections must remain understandable. The offline
demo is the first reviewable path; live providers and model quality require their
own evidence.

## Research and advisory scope

Extend the existing ledger with one validated adapter at a time. Start with a
bounded SEC filing-ingestion contract, subject to phase approval, then catalyst
features; qualitative extraction and FinBERT evaluation follow separate reviews.
Existing technical and sentiment research is reused, not replaced by duplicate stores.
An LLM may eventually explain structured evidence. It must not invent authoritative
prices, probabilities, confidence, risk limits or trade instructions.

## Excluded in the current phase

Real-money execution, autonomous agents, unsupported asset classes, promised
profitability, guaranteed predictions, production identity, required dashboards
and WebSockets. Redis, Railway and new research dependencies are not approved
requirements for the local simulator.

Calendar/analyzer classes described in [legacy usage notes](../src/USAGE_EXPLANATIONS.md)
are roadmap concepts. Metrics in [research notes](../src/Architecture4Objectives.md)
are not product acceptance evidence.

## Delivery sequence and approval gates

1. **Specification alignment now:** approve these four documents and their status labels.
2. **Contracts for one approved feature:** resolve the concrete local workflow failure,
   if reproducible; define fixtures, timestamp eligibility, provenance and manifests
   for SEC ingestion. No broad ledger migration or authentication rewrite.
3. **One research adapter:** bounded SEC filing ingestion first as the proposed next
   research increment; agree universe scope and provider/data requirements before implementation.
4. **Validated features and NLP:** catalyst features, qualitative extraction and a
   separate FinBERT benchmark; assess incremental value before combining outputs.
5. **Advisory composition:** stable structured outputs and risk results, then audit
   records and optional evidence-linked explanation.
6. **Deployment hardening last:** worker/web separation, shared database operating
   model, identity, TLS, request limits and deployment review.

Only the current phase is authorized by this specification. Before each next
phase, agree acceptance criteria and one substantial issue. Existing deployment
issues are later-phase backlog, not the immediate implementation queue.

## Definition of done

A feature needs a scoped issue, accepted contract, evidence of causal inputs and
provenance, targeted validation, documented limitations, reviewed PR and approved
merge. Documentation-only work needs a focused PR, not a new issue ticket.

Related: [architecture](02_architecture.md), [data contracts](03_data_models.md),
[execution rules](04_rules.md), [current usage](PAPER_TRADING.md).

## Reviewed contract increments

SEC filing contracts are implemented separately from live filing ingestion. Issue #6
adds offline [SIC/security-universe contracts](UNIVERSE_CONTRACTS.md) and synthetic
validation. These contracts do not claim real top-100 selection, a complete PIT
archive, public data rights or live market coverage. The next selector/provider
increment requires its own reviewed policy and scope.

Issue #8 adds offline [daily market-data contracts](MARKET_CONTRACTS.md): OHLC,
read-only index-reference slots, explicit coverage and provider evidence. It does
not add a live feed, real historical dataset or account corporate-action processing.

Issue #10 adds [offline ingestion coordination](OFFLINE_JOBS.md): bounded synthetic
pages, in-memory atomic commits, replay, leases and explicit coverage results.
Durable persistence, live providers, schedulers and backfills remain excluded.
