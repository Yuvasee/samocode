---
name: comment-hygiene
description: Curate source-code comments and docstrings — delete AI-slop and stale comments, keep only the non-obvious WHY, and never change executable code. Use when cleaning up comments/docstrings, during a samocode cleanup or quality-review step, when running /comment-hygiene, or when reviewing a diff for comment quality. Works in Claude Code and Codex.
---

# Comment Hygiene

Curate comments and docstrings so each one earns its place: delete the noise, keep
the load-bearing WHY the code cannot express. Validated across Claude Code and Codex —
bulk-stripping comments cuts agent comprehension ~30%, while curation costs nothing.
So curate; never bulk-strip.

## The one test

For every comment and docstring ask: **could a competent reader recover this by
reading the code itself?**

- Yes → delete it.
- No → keep it, reworded to the shortest plain-English line — the WHY, not the what.

## Always cut

- Narration restating the next line (`i += 1  # increment i`).
- Docstrings that restate the signature or name; `Args`/`Returns` boilerplate echoing the types.
- Field/schema descriptions that merely echo the field name.
- Ticket / issue / Q- / PR / phase / gate references ("ENG-1234", "moved verbatim from X", "accepted at the … gate") — state the real reason, never the process history.
- Commented-out code, decorative banners, section dividers, author/date stamps.
- AI-slop tells: step-by-step travelogue, emoji, multi-sentence narration of mechanics.

## Always keep (reword, don't delete)

The non-obvious WHY: a constraint, invariant, gotcha, workaround reason, surprising
ordering, or unit/precision requirement — one short line, essence only.

```
# Truncate to milliseconds — the datastore rejects microsecond precision.
# Snapshot is authoritative here; the live record may have drifted.
```

## Stale comments (the dangerous kind)

A comment that contradicts the current code is worse than none — it never fails a
test, so it lies silently. Fix it to match the code, or delete it. Never preserve a lie.

## Safety invariant (non-negotiable)

Change comments and docstrings ONLY. Executable code stays byte-identical.

- Python: after editing, verify code is unchanged — strip docstrings and compare the
  before/after AST (`ast.dump`), or at minimum `python -m py_compile` and confirm the
  diff touches only comment/docstring lines.
- Other languages: confirm the diff shows only comment-line changes; re-run the build/tests.
- If a cleanup would require changing code to stay correct, that is a separate task —
  stop and flag it, do not fold it in.

## Actions

### clean — apply the cleanup

**Scope:** $ARGUMENTS (a path/dir, or the current branch diff `git diff origin/main...HEAD` if unset).

1. Determine scope; read the target files or diff.
2. Apply the cut/keep rules above; reword every survivor to one plain line.
3. Run the safety check (code byte-identical). Revert any edit that fails it.
4. Report counts: comments removed / reworded / kept, plus any stale comments fixed.
   Note anything left in place because removing it would require a code change.

### review — flag without editing

Use as a review lens (e.g. inside a PR review). Do NOT edit.

**Scope:** the diff under review. For each comment issue, emit a finding — file, line,
type ∈ {slop, restates-code, stale-contradicts-code, over-stripped-why}, and the
recommended action. Flag BOTH directions:

- noise that should be removed, and
- load-bearing WHY that was deleted, or is missing where the code is non-obvious.

Do not flag a concise comment that genuinely carries non-obvious WHY — that is correct,
not a finding.
