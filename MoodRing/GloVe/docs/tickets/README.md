# Project tickets

YAML files are versioned ticket records, not live GitHub issue state. The supplied
`bug.yml` and `architecture.yml` become GitHub issue forms under `.github/ISSUE_TEMPLATE/`.
Creating an issue from a form does not automatically synchronize these records.

| Ticket | Priority | State | Work |
| --- | --- | --- | --- |
| [MR-001](MR-001.yml) | P1 | closed | Correct misleading readiness and setup documentation |
| [MR-002](MR-002.yml) | P1 | closed | Sanitize provider errors at API boundaries |
| [MR-003](MR-003.yml) | P1 | closed | Account for every backtest input signal |
| [MR-004](MR-004.yml) | P1 | closed | Verify clean installation and GitHub database CI |
| [MR-005](MR-005.yml) | P1 | open | Replace local token map before public hosting |
| [MR-006](MR-006.yml) | P1 | open | Define secure public deployment and request limits |
| [MR-007](MR-007.yml) | P2 | open | Add privacy-aware audit events |
| [MR-008](MR-008.yml) | P1 | open | Reproduce reported nonworking behavior |
| [MR-009](MR-009.yml) | P2 | open | Build beginner scenario dashboard |
| [MR-010](MR-010.yml) | P2 | open | Validate automated prediction input and research provenance |
| [MR-011](MR-011.yml) | P2 | open | Specify market-data freshness and update guarantees |
| [MR-012](MR-012.yml) | P2 | open | Adopt scoped changes and ticket-linked review |
| [MR-013](MR-013.yml) | P2 | closed | Keep public READMEs focused on learner use |

## Working rules

1. Report an observed defect with exact command, environment, expected and actual behavior.
2. Keep one independently reviewable outcome per ticket. Link its ID in commit/PR descriptions.
3. `open` means acceptance work or verification remains. `closed` requires evidence for every acceptance criterion.
4. Reopen when regression tests fail or reproduction contradicts the recorded evidence.
5. Preserve published commits. Use follow-up commits and reviewed reverts instead of rewriting shared history.
6. Close CI work only after a successful hosted run; local results alone do not establish hosted verification.

For interview demonstrations, run `examples/paper_demo.py`, show the scenario ledger,
then walk through MR-002/MR-003 regression tests and explain the remaining open scope.
Do not describe open tickets as delivered features. Update YAML and the table together.
