---
name: pr-readiness
description: Final PR readiness gate after fix loops, merges, and manual debugging.
---

# PR Readiness

Review final `HEAD` after implementation, fix loops, merges, and manual debugging. This is a gate, not a general brainstorming review: pass only when important issues are fixed, explicitly deferred, or rejected with evidence.

## Requirements

- Run from the project working directory or resolve it from the active session `_overview.md`
- Use final `HEAD`; do not rely on earlier review summaries alone

## Action

### gate - Final PR Readiness Gate

**Scope:** $ARGUMENTS (or current branch vs `origin/main` if not specified)

#### Steps

1. **Resolve final scope:**
   - Confirm current branch and `HEAD`
   - Fetch if needed
   - Use `git diff origin/main...HEAD` unless $ARGUMENTS names a PR, branch, or explicit diff range
   - Read the latest commits after any merges or manual debugging

2. **Load review debt:**
   - Locate `_review_debt.md` in the active session path when known; otherwise use the invocation directory
   - If the file exists, read all open blocking/important rows
   - Fail the gate for any open row whose `Decision` is `undecided`
   - Fail the gate for any `defer` row missing a ticket/link or concrete owner/reason
   - Fail the gate for any `reject` row missing evidence
   - Verify fixed rows against final `HEAD`; reopen the row if the claimed fix is absent

3. **Inspect final HEAD for high-risk surfaces:**
   Focus on changed files that are shared, public, background, or cross-boundary:
   - Shared/public Python modules and package exports, especially `avoncore`
   - Background jobs, queue consumers/producers, fan-out, retries, and scheduled work
   - Controller/service/repository boundaries
   - Queue + DB consistency and source-of-truth ordering
   - Concurrency and bounded fan-out
   - Config/default definitions and stale deprecations
   - Code touched after the last quality run

4. **Run deterministic checks:**
   - New `avoncore` promotion requires **2+ current Python service consumers**; frontend mirrors do not count
   - Future consumers require a ticket/TODO, not premature promotion
   - Search for name collisions before accepting new shared symbols
   - Search changed controllers for repository/raw DB usage and changed repositories for queue/user-flow orchestration
   - Trace queue + DB paths for queue succeeds/DB fails, DB succeeds/queue fails, duplicate delivery, and retry after partial failure
   - Search fan-out paths for unbounded `gather`, task creation, loops over user data, and missing durable retry assumptions
   - Search for duplicated env vars/defaults across settings, constructors, frontend config, tests, and docs
   - Search for stale deprecation shims, warnings, compatibility kwargs, or TODOs introduced by the branch

5. **Walk feature edge cases when applicable:**
   - All validators enabled, partial validator set enabled, and all validators disabled
   - Queue succeeds but DB write fails; DB write succeeds but queue publish fails
   - Two users revalidate the same KI or shared entity concurrently
   - Large uploads, empty uploads, and unsupported file/content types
   - API-key user missing email/name or other human-profile fields
   - Retry after partial failure, refresh after background completion, and stale UI state

6. **Produce gate result:**
   Use this format:

   ```markdown
   # PR Readiness Gate
   Scope: [branch/range/PR]
   HEAD: [sha]

   ## Result
   PASS | FAIL

   ## Blocking Readiness Issues
   - [issue, evidence, required action]

   ## Important Open Issues
   - [issue, evidence, required decision/fix]

   ## Review Debt Ledger
   - [rows checked, rows updated, unresolved rows]

   ## Checks Run
   - [deterministic checks and targeted tests/commands]

   ## Residual Risk
   - [known accepted risks with ticket/evidence]
   ```

#### Pass/Fail Rules

Fail if any of these are true:
- A blocking or important quality finding remains undecided
- `_review_debt.md` has open important rows without a valid fix/defer/reject decision
- Final `HEAD` introduces a new important issue in a shared/public module, background job, controller/service/repository boundary, concurrency/fan-out path, queue/DB consistency path, or config/default source
- A deferred item has no ticket/link or concrete owner/reason
- A rejected item has no evidence

Pass only when all important issues are fixed, explicitly deferred, or rejected with evidence, and final `HEAD` has been checked after the last merge/fix/debug loop.
