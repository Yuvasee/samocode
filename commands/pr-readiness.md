# PR Readiness

Run the final PR readiness gate after implementation, review-fix loops, merges, and manual debugging.

**Scope:** $ARGUMENTS (branch name, PR URL, diff range; defaults to current branch vs `origin/main`)

## Execution

**IMPORTANT:** You MUST use the `pr-readiness` skill and follow the `gate` action exactly. Do NOT improvise; this is the final-head gate that checks `_review_debt.md`, shared package boundaries, queue/DB consistency, fan-out, stale deprecations, and unresolved important findings.

Use `pr-readiness` skill now.
