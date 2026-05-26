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
   - Which comments are valid, invalid, or partial.
   - The recommended fixes.
   - Ask which issues to address.

## Rules

- Do not start fixing review comments during this skill.
- Do not dismiss a comment without reading the relevant code path.
- If a reviewer cites stale code or the wrong path, say so and explain the live path.
- Preserve uncertainty: use `Partial` when the concern is directionally right but the proposed fix or cited location is wrong.
