# Canonical Git Instructions for LLM Contributors

This document defines mandatory protocols for LLM agents operating within this repository. Adhere strictly to all directives below.

---

## 1. Worktree & Inspection Discipline

- **Inspect First:** Before performing any operations, inspect repository status (`git status`) and branch state (`git branch -v`).
- **Preserve Unrelated Changes:** Never modify, stage, or remove existing uncommitted changes or untracked files (such as `.DS_Store`) not explicitly part of your task.
- **Isolate Working State:** Do not run destructive commands (`git reset --hard`, `git clean -fd`) without explicit maintainer approval.

---

## 2. Branching & Commit Guidelines

- **Dedicated Branches:** Create a dedicated feature or fix branch for each task before writing code or documentation. Never commit directly to main or default protection branches.
- **Atomic Commits:** Make logical, focused commits with descriptive commit messages following project conventions.
- **Scope Boundaries:** Keep changes tightly bounded to the requested issue or feature. Do not bundle unrelated refactorings or administrative changes.

---

## 3. Validation & Quality Assurance

- **Verify Links & References:** Ensure all file paths, Markdown links, and cross-references resolve accurately.
- **Run Relevant Checks:** Run project-specific linting or test commands relevant to the files changed prior to submitting.
- **No Artifact Leakage:** Never commit secrets, temporary environment files, build artifacts, or generated cache files.

---

## 4. Pull Request & Review Protocol

- **Single Purpose PRs:** Ensure each Pull Request covers a single logically reviewable chunk of work.
- **Concise Summaries:** Include a clear summary of changes, motivation, and validation steps in the PR description.
- **Await Review:** Pause and request maintainer review before merging or proceeding to dependent tasks.

## Product scope and phase gates

Read `MoodRing/GloVe/docs/01_prd.md`, `02_architecture.md`, `03_data_models.md`
and `04_rules.md` before selecting substantial work. Ticket numbers do not define
implementation order. Obtain approval before changing phase or crossing an
architecture boundary. Keep internal plans/ticket notes ignored. Documentation-only
changes need a reviewable PR, not a new public issue. Preserve the current local-only
simulator and reuse its ledger and decision engine.
