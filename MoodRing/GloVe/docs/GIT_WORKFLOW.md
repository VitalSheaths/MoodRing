# Git Workflow Guide

This guide outlines the standard Git operational flow for human and AI contributors to ensure consistent, safe, and easily reviewable contributions.

---

## 1. Branching Strategy

Always work on a dedicated topic branch isolated from default branches:

```bash
# Check current state and preserve existing worktree modifications
git status

# Create and switch to a dedicated topic branch
git checkout -b docs/llm-git-workflow
```

---

## 2. Managing Changes & Worktree Safety

- Inspect modified files with `git status` and `git diff`.
- Stage only the relevant files associated with your explicit goal:
  ```bash
  git add .github/copilot-instructions.md MoodRing/GloVe/docs/GIT_WORKFLOW.md README.md
  ```
- **Note on Untracked Files:** Leave unrelated untracked files (e.g., system `.DS_Store` files) intact in your working tree; do not stage or remove them.

---

## 3. Committing Work

Write concise, descriptive commit messages outlining the *what* and *why*:

```bash
git commit -m "docs: add LLM instructions and Git workflow guide"
```

---

## 4. Verification & Review

Before opening a Pull Request:
1. Validate that all cross-references and internal documentation links resolve correctly.
2. Confirm no sensitive credentials or temporary build artifacts were staged.
3. Push your topic branch to the remote:
   ```bash
   git push -u origin docs/llm-git-workflow
   ```

---

## 5. Opening Pull Requests

Submit a clean PR against the primary branch with:
- A clear explanation of the changes made.
- Validation steps performed.
- A request for human maintainer review before merging.

---

## 6. Recovery & Rollbacks

If a change needs to be reverted or re-organized into distinct, reviewable PRs:
- Revert commits to the last stable state or commit SHA:
  ```bash
  git revert <commit-sha>
  # OR reset local branch state if unpushed
  git reset --soft <stable-commit-sha>
  ```
- Re-introduce work in isolated, logical PRs (e.g., configs, core logic, tests, and documentation).