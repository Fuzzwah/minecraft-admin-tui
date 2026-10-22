---
name: shipit
description: Release signed-off Minecraft Admin TUI changes through a GitHub pull request to main.
---

# Minecraft Admin TUI release workflow

## Repository and scope

- Repository: `Fuzzwah/minecraft-admin-tui`, remote `origin` (`git@github.com:Fuzzwah/minecraft-admin-tui.git`).
- Components: Python/Textual admin TUI (`mc_admin_tui.py`), core catalogue/RCON helpers (`mc_admin_core.py`), and optional vanilla Minecraft hub tooling.
- Run from the current repository/worktree; resolve its root with `git rev-parse --show-toplevel`. Do not switch to another worktree.
- Read applicable `AGENTS.md`, contribution/deployment rules, and README guidance before release. No additional rule files or CI workflows existed when this workflow was created; recheck on each invocation.
- Release only the user's signed-off paths. Stop for unrelated changes. Exclude generated environments, caches, temporary smoke artifacts, and incidental lockfiles.
- This project-local workflow is tracked release tooling and may be included when created/refreshed by an explicit `/shipit` invocation.

## Branch flow and ownership

- Source: an existing topic branch, normally `Fuzzwah/<topic>` (this change: `Fuzzwah/books`). Never commit or push directly to `main` through this workflow.
- Target: `main`, the repository default branch. Verify the remote/default branch before publishing.
- Before each commit or push, run `git branch --show-current` and verify the topic branch.
- No `@committer` routing is required; perform commit/push/PR operations directly with the available Git/GitHub tools.
- Do not amend, force-push, reset, stash, discard user changes, or delete branches used by any worktree.

## Required validation

- Review the complete release diff, `git status --short --branch`, `git diff --stat`, remote configuration, and `git diff --check`.
- With dependencies installed from `requirements.txt` or `pyproject.toml`, run `.venv/bin/python -m unittest discover -s tests` (or the equivalent interpreter with the project dependencies).
- Run `.venv/bin/python mc_admin_tui.py --help` as an entry-point smoke check.
- For Give-pane/catalogue changes, exercise the actual Textual app headlessly: open Give, search an enchantment/level, select it, set quantity, and activate Give. Capture the RCON boundary instead of changing a live player's inventory; check the complete command and inspect rendered option labels. Include numeric/Roman levels and treasure/curse variants when relevant. Prior successful smoke evidence for an unchanged implementation may be reused.
- Report the boundary: captured RCON commands are not a live Minecraft server acceptance test. Do not issue production mutations for release validation.
- Existing verification may be reused if the implementation has not changed; rerun required checks if it has. Do not create dependency lockfiles merely to run validation.

## Commit and pull request

- Stage explicit signed-off paths only and use a concise imperative commit subject.
- Push the current topic branch to `origin`, setting upstream if missing.
- Reuse an existing open PR for that branch; otherwise create one targeting `main`.
- PR body must contain `## Summary`, `## Verification`, and `## Deployment`. Describe user-visible behavior, regression coverage, exact observed validation, and any live-server verification limit.
- Inspect the PR body after creation and correct malformed formatting. Inspect changed paths, head/base branches, checks, reviews, and merge state.
- No immediate branch synchronization is mandated. If GitHub requires an update, use a non-rewriting merge/update and rerun affected validation; never force-push.

## Gates and merge

- Inspect current branch protection/rules and PR status checks/reviews; do not assume the initial settings still apply. At creation, `main` was unprotected with no required checks/reviews and no repository Actions workflows.
- Required checks must succeed, pending checks must settle, and blocking review requests/changes must be resolved. If required checks are unavailable or a gate fails, stop and report the exact blocker.
- When no remote checks/reviews are configured, report that explicitly; the local validation remains required. User `/shipit` is release signoff, not a fabricated GitHub approval.
- Merge using a merge commit (`gh pr merge <number> --merge --match-head-commit <verified-head-sha>`), matching existing repository history. Do not bypass gates with admin flags.
- Retain source branches by default; multiple active worktrees use topic branches.
- Confirm PR state `MERGED`, merge commit SHA, PR URL, and final working-tree state. Do not switch to or update another worktree's `main`.

## Deployment

Deployment intentionally deferred: this release publishes source through GitHub only. No package registry release, installed TUI upgrade, Minecraft container restart/recreation, hub deployment, or systemd service change is authorized. Operators can install the merged checkout with `pip install .` and relaunch `mc-admin-tui` using the README installation instructions. The README's `python3 -m mc_hub_deploy` procedure is separate, infrastructure-changing work and is not part of a Give-pane release.
