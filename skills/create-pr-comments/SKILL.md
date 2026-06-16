---
name: create-pr-comments
description: Create GitHub PR review comments from review findings. Use after running a code review to post findings as line-bound comments.
---

# Create PR Comments

Create GitHub PR review comments bound to specific code lines based on review findings.

## Requirements

- `gh` CLI authenticated with repo access
- Review findings (from `/multi-review`, `/cleanup`, or manual review)

## Input

`$ARGUMENTS` can contain:
- PR URL (e.g., `https://github.com/org/repo/pull/123`)
- PR number (e.g., `123`) - will detect repo from git remote
- Severity filter: `--all`, `--blocking`, `--important`, `--nice-to-have`
- Default: blocking + important issues only

## Steps

### 1. Parse Arguments

```
PR_URL or PR_NUMBER = extract from $ARGUMENTS
SEVERITY_FILTER = extract from $ARGUMENTS (default: blocking + important)
```

If PR not specified, ask user for PR URL or number.

### 2. Get PR Information

```bash
# If URL provided, extract owner/repo/number
# If only number, detect from git remote:
gh api repos/{owner}/{repo}/pulls/{number} --jq '{headRefOid: .head.sha, headRefName: .head.ref}'
```

Store:
- `COMMIT_SHA` = head commit SHA
- `BRANCH_NAME` = head ref name

### 3. Collect Review Findings

Check conversation context for review findings from:
- Recent `/multi-review` output
- Recent `/cleanup` output
- Manual review findings shared by user

If no findings in context, ask user to provide them or run a review first.

### 4. Filter by Severity

Based on `SEVERITY_FILTER`:
- `--all`: Include all findings
- `--blocking`: Only blocking issues
- `--important`: Only important issues
- `--nice-to-have`: Only nice-to-have issues
- Default (no flag): blocking + important

### 5. Map Findings to Lines

For each finding:
1. Identify the file path
2. Get the diff to find exact line numbers:
   ```bash
   gh api repos/{owner}/{repo}/pulls/{number}/files --jq '.[] | select(.filename == "PATH") | .patch'
   ```
3. Map the finding to specific line in the RIGHT side of diff

### 6. Create Comments

For each finding, create a line-bound PR comment:

```bash
gh api repos/{owner}/{repo}/pulls/{number}/comments \
  -f body='COMMENT_BODY' \
  -f commit_id="COMMIT_SHA" \
  -f path="FILE_PATH" \
  -F line=LINE_NUMBER \
  -f side="RIGHT"
```

**Comment format by severity:**
- Blocking: Start with `🚫 **BLOCKING: [Title]**`
- Important: Start with `⚠️ **IMPORTANT: [Title]**`
- Nice-to-have: Start with `💡 **Nice-to-have: [Title]**`

Include:
- Clear description of the issue
- Code example or fix suggestion if applicable

**Exclude from comments:**
- Reviewer attribution lines like "Flagged by: 3/4 reviewers (Future Maintainer, System Architect, Product Advocate)" — these are internal metadata and should not appear in PR comments

### 7. Report Results

Output summary:
```
Created X PR comments on PR #N:

| File | Line | Severity | Issue |
|------|------|----------|-------|
| ... | ... | ... | ... |

PR: [URL]
```

## Error Handling

- If `gh` not authenticated: "Please run `gh auth login` first"
- If PR not found: "PR #N not found in {owner}/{repo}"
- If line not in diff: Skip comment, note in summary that finding couldn't be mapped
- If comment creation fails: Log error, continue with remaining comments

## Examples

```
# After running /multi-review
/create-pr-comments https://github.com/org/repo/pull/123

# Only blocking issues
/create-pr-comments 123 --blocking

# All issues including nice-to-have
/create-pr-comments 123 --all

# From current repo
/create-pr-comments 123
```
