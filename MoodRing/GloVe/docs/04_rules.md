# Operational and delivery rules

Status: Phase 1 specification for review, 2026-10-13.

## Read before changing scope

Read [PRD](01_prd.md), [architecture](02_architecture.md) and
[data contracts](03_data_models.md), then inspect the repository, relevant code,
configuration, tests and working-tree state. Follow
[canonical contributor instructions](../../../.github/copilot-instructions.md)
and [Git workflow](GIT_WORKFLOW.md). Preserve unrelated user work and untracked files.

These four documents govern scope and contracts after approval. Existing
`PAPER_TRADING.md`, `IMPLEMENTATION_CHANGES.md` and `REVIEW_FIXES.md` document
implementation/evidence; they must not silently redefine approved scope.

## Select work by dependencies, not ticket number

A ticket ID or priority label does not determine execution order. Map a substantial
issue to the approved phase, prerequisites, acceptance checklist and evidence
before implementing it. Publish issues one at a time for maintainer review.
Do not create issues for documentation-only changes, README edits or small refactors.
Do not publish internal ticket YAML or scratch plans as public issue attachments.

Existing public authentication work belongs to deployment hardening. Its presence
in the issue list does not authorize it ahead of the local workflow, contracts
and validated research adapters. Do not close completed internal notes as public
issues retroactively merely to increase completed-issue counts.

## Incremental phases

| Phase | Deliverable | Gate before advancing |
| --- | --- | --- |
| 1 — specifications | Four consistent living documents, implemented/planned/excluded labels, links and public/private boundary | Maintainer reviews documentation PR. |
| 2 — approved contracts | One feature's fixtures, schemas/manifests, provenance, cutoff and restart rules; resolve any concrete prerequisite failure | Agree feature scope and acceptance; no broad migration. |
| 3 — adapter | Proposed first increment: bounded SEC filing ingestion; evaluate separately from FinBERT | Evidence of deduplication, availability, revision handling and bounded resources. |
| 4 — feature/NLP validation | Catalyst features and separately evaluated qualitative/FinBERT outputs | Labeled benchmarks and out-of-time comparison; no assumed trading benefit. |
| 5 — advisory | Compose validated structured outputs behind existing risk authority; explanations follow stable evidence contracts | Reproducible output, expiry and replay/validation evidence. |
| 6 — deployment | Worker separation, shared state and hosting/security controls | Explicit architecture/deployment approval before public exposure. |

This phase table does not grant permission to perform later phases. Require human
approval at architecture boundaries, before changing phase and before merging.

## Change boundaries

Reuse the ledger, decision engine and feature modules. No duplicate trading state,
competing feature store or simultaneous experiment-registry migration. Substantial
work is separated into reviewable dependency/config, storage/contract, safeguards,
adapter/API, research/evaluation and test/documentation changes when needed.

Keep `ExperimentTracker` as local JSON in the current phase. Reference
`UniversalDateParser` when changing research timestamps; add focused parsing/timezone
checks only for an approved timestamp change. Legacy calendar/analyzer concepts
and historical model metrics are roadmap/research notes, not implemented features
or acceptance evidence. Do not implement them while editing specifications.

## Validation and public/private boundary

- Check precision, ownership, idempotency, causal eligibility, quote freshness,
  provenance and versioning for the specific approved behavior being changed.
- Tests with mocked prices do not prove live-provider availability or ML quality.
  SQLite tests do not establish PostgreSQL behavior; optional database checks and
  hosted CI must be reported separately with actual evidence.
- For documentation-only changes, validate links, code-to-doc contracts and diff
  scope; do not claim runtime verification from a document review.
- Keep secrets, weights, local databases, generated datasets/caches, internal ticket
  notes and scratch plans ignored. Public specs and contributor rules must contain
  no credentials or private operational details.
- Use a dedicated branch and focused commits, then PR, CI, review and approved merge.
  Report remaining work honestly; a document containing a requirement does not
  mean the requirement is implemented.
