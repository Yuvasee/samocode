---
name: merge
description: Merge a source branch, usually origin/main, into the current working branch with preflight checks, explicit conflict handling, optional samocode session documentation, and targeted validation. Use when the user asks to merge main, run /merge, update the current branch from main, or follow samocode's guarded merge workflow.
---

# Merge

Merge a source branch into the current branch. Default source is `origin/main`.

## Working Directory

Resolve the repository before running git commands:

1. If an active samocode session is known, use `Working Dir:` from its `_overview.md`.
2. Else, if the project `CLAUDE.md` or `.samocode` defines `MAIN_REPO`, use that.
3. Else, use `git rev-parse --show-toplevel` from the current directory.
4. If the repo is unclear, stop and ask.

Do not run the merge from `main`, `master`, or another protected default branch unless the user explicitly asked for that.

## Workflow

1. Capture timestamps:
   - `date '+%m-%d-%H:%M'` for session filenames.
   - `date '+%H:%M'` for flow-log entries.
2. Preflight:
   - `git branch --show-current`
   - `git status --porcelain`
   - If there are uncommitted changes, stop and ask the user to commit or stash first.
   - `git fetch origin`
3. Merge without committing:
   - `git merge [source-branch] --no-ff --no-commit`
4. If the merge is clean:
   - Inspect `git status --short` and the merged file list.
   - Commit with `git commit -m "Merge [source-branch] into [current-branch]"`.
5. If there are conflicts:
   - List conflicted files with `git diff --name-only --diff-filter=U`.
   - Read conflict markers and explain, for each file:
     - current branch behavior,
     - incoming branch behavior,
     - conflict reason,
     - suggested resolution.
   - Resolve automatically only when the intent is clear; otherwise ask the user.
   - Stage resolved files, verify no conflict markers remain, then commit.
6. If an active samocode session is known, document the merge:
   - Create `[SESSION_PATH]/[MM-DD-HH:mm]-merge-[branch].md`.
   - Update `_overview.md` Flow Log and Files.
   - Commit the session docs if the sessions directory is a git repo.
7. Validate the merge with targeted checks based on changed files:
   - Python: run relevant pytest/ruff/pyright commands available in the repo.
   - TypeScript/frontend: run relevant typecheck/test/lint commands available in the repo.
   - If a check cannot run, report why.

## Conflict Resolution Rules

- Preserve both sides when they are compatible.
- For package moves or renamed directories, prefer the current branch's new location/import style and adapt incoming code to it.
- Do not discard incoming tests unless they are obsolete after the resolution.
- Do not revert unrelated user changes.
- Never use destructive reset/checkout commands unless the user explicitly asks.

## Report

Finish with:

```text
Merge complete
[X] files changed
Conflicts: [Y resolved / none]
Checks: [commands and results]
```
