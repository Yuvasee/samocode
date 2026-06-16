---
name: prcomments
description: Investigate GitHub pull request review comments, determine whether each comment is valid, propose fixes, and document the analysis in an active samocode session. Use when the user asks to analyze PR comments, run /prcomments, investigate review feedback, or triage pull request comments.
---

# PR Comments

Investigate pull request review comments and produce a session report. Do not fix anything until the user explicitly chooses which comments to address.

## Requirements

- An active samocode session must be known, including `SESSION_PATH`.
- A PR number, PR URL, or explicit review comments must be provided.
- If only a PR number is provided, detect `owner/repo` from `git remote get-url origin`; if that is unclear, ask the user.

## Fetch Comments

If the user provides a PR URL, parse owner, repo, and PR number from it.

If the user provides only a PR number, fetch inline review comments with:

```bash
gh api 'repos/[OWNER]/[REPO]/pulls/[PR_NUMBER]/comments'
```

If the request concerns top-level PR conversation comments too, also fetch:

```bash
gh api 'repos/[OWNER]/[REPO]/issues/[PR_NUMBER]/comments'
```

If GitHub CLI is unavailable or unauthenticated, report the blocker and ask the user to paste the comments.

## Workflow

1. Capture timestamps:
   - `date '+%m-%d-%H:%M'` for the report filename.
   - `date '+%H:%M'` for the session flow log.
2. Parse each comment:
   - reviewer name,
   - file path and line if present,
   - quoted comment or concise summary,
   - whether it is inline or top-level.
3. Investigate each issue:
   - Read the relevant code and nearby context.
   - Understand the reviewer's concern.
   - Determine whether the issue is valid, invalid, or partially valid.
   - If valid or partial, identify the smallest coherent fix.
   - Classify action using the triage principles below; do not label a valid architectural/code-quality issue as "defer" just because current behavior works.
4. Create `[SESSION_PATH]/[MM-DD-HH:mm]-prcomments.md`:

   ```markdown
   # PR Comments Analysis

   Date: [timestamp]

   ## Comment 1: [brief title]

   **Reviewer says:** [quote or summary]
   **Location:** [file:line or top-level]

   **Analysis:**
   [Is this valid? Why or why not?]

   **Verdict:** [Valid/Invalid/Partial]

   **Suggested fix:**
   [If valid or partial, how to address it]

   ---

   ## Summary

   | Issue | Severity | Validity | Code Volume | Risk |
   | ----- | -------- | -------- | ---------- | ---- |
   | ...   | ...      | ...      | ...        | ...  |

   | Comment | Verdict | Action |
   | ------- | ------- | ------ |
   | ...     | ...     | ...    |

   ## Recommended Actions

   1. [What to fix and how]
   ```

5. Update `[SESSION_PATH]/_overview.md`:
   - Flow Log: `- [HH:MM] PR comments analysis -> [filename].md`
   - Files: `- [filename].md - PR review analysis`
6. If the sessions directory is a git repo, commit session docs:

   ```bash
   cd [SESSION_DIR] && git add . && git commit -m "PR comments analysis"
   ```

7. Report:
   - A table for all issues with columns: Severity, Validity, Code Volume, Risk.
   - Which comments are valid, invalid, or partial.
   - The recommended fixes.
   - Ask which issues to address.

## Rules

- Do not start fixing review comments during this skill.
- Do not dismiss a comment without reading the relevant code path.
- If a reviewer cites stale code or the wrong path, say so and explain the live path.
- Preserve uncertainty: use `Partial` when the concern is directionally right but the proposed fix or cited location is wrong.
- Always include a compact triage matrix for every issue, both in the session report and in the final user-facing summary, with exactly these columns: `Severity`, `Validity`, `Code Volume`, `Risk`.

## Triage Matrix Columns

- **Severity**: impact if the issue ships. Use values like `Blocking`, `Important`, or `Suggestion`.
- **Validity**: investigation result. Use `Valid`, `Partial`, or `Invalid`.
- **Code Volume**: amount of code affected by the fix (implementation size). Use concise labels such as `Small`, `Medium`, or `Large`, optionally with a short reason.
- **Risk**: implementation/regression risk of the fix. Use concise labels such as `Low`, `Medium`, or `High`, optionally with a short reason.

## Triage Principles

Default to fixing review comments in the current PR when the PR introduced or amplified the issue.

Mark an issue **Fix now** when:
- The PR created code that worsens structure, consistency, layering, or architectural clarity.
- The PR expanded an existing weak pattern in a way that makes the codebase harder to maintain.
- The fix is reasonably scoped, behavior-preserving, and can be covered with focused tests.
- The concern is not a product redesign but a local ownership/layering/source-of-truth cleanup.

Mark an issue **Defer** only when at least one is true:
- The issue clearly predates the PR and the PR did not meaningfully touch or worsen it.
- The fix requires a broader design decision, migration, ownership change, or cross-service contract change that is not justified by the PR.
- The proposed fix is primarily an optimization and risks destabilizing a race-sensitive or recently fixed path.
- The reviewer explicitly filed or points to a follow-up ticket whose scope truly owns the work.

Avoid weak deferral rationales:
- Do not say "current behavior is correct and tested" as the main reason to defer if the PR made architecture or layering worse.
- Do not defer a service/controller/repository boundary violation introduced by the PR when extraction is local and testable.
- Do not defer duplication introduced by the PR when a small helper/table removes it without changing behavior.

When documenting deferred issues, include:
- Why the comment is valid or partially valid.
- Why it is not appropriate for this PR under the criteria above.
- The concrete follow-up owner/ticket if known; otherwise say what kind of follow-up is needed.
