---
name: learn
description: Extract durable lessons from the current conversation, classify them, and route each to the narrowest enforceable code, test, skill, or scoped instruction source without growing a generic CLAUDE.md memory dump.
---

# Learn

Turn repeated friction into durable, scoped improvements. Do not treat every
incident as standing prompt context.

## Workflow

1. **Extract candidates**
   - Look for repeated failures, non-obvious constraints, and corrections that changed the successful approach.
   - If nothing durable was learned, report `No new learnings to capture.` and stop.

2. **Load the instruction architecture**
   - Resolve the active repository from the session `Working Dir`, or use `git rev-parse --show-toplevel`.
   - Read applicable `AGENTS.md`, `CLAUDE.md`, rule-loading tables, scoped rules, and the active skill before choosing a destination.

3. **Qualify each candidate**
   - Keep only guidance that is recurring, non-obvious, actionable, and likely to remain true.
   - Reject one-off incidents, stale version facts, task state, specific PR history, advice obvious from source, and behavior better enforced mechanically.

4. **Choose exactly one destination**
   - **Code, test, validator, or script:** behavior can be enforced mechanically.
   - **Skill:** procedure belongs to a named workflow or tool.
   - **Scoped rule:** guidance applies only to a folder, domain, or trigger.
   - **Project instruction:** stable project-wide policy with no narrower home.
   - **Session artifact:** active task state, decision, or handoff detail.
   - **Drop:** no durable value after the task ends.

5. **Pass the mandatory duplicate gate before writing**
   - Do not write a learning until this gate is complete.
   - Search every applicable project, team, and personal instruction source, plus the active skill and proposed destination.
   - Search by the candidate's key concepts, synonyms, and expected behavior; an exact-text search alone is insufficient.
   - Classify each match as duplicate, partial overlap, conflict, or distinct.
   - For a duplicate, make no addition. For partial overlap, refine or replace the existing rule instead of appending another version. Resolve conflicts before writing.
   - Record the sources searched and the duplicate disposition for the final report.

6. **Merge instead of append**
   - Never create or append to a generic `## Learnings` section automatically.
   - Keep the destination concise; move details to on-demand references when necessary.

7. **Validate routing**
   - Confirm every changed scoped rule is reachable from its rule-loading table or skill.
   - Re-read the final text for duplication, stale specifics, and conflicting instructions.
   - Run any repository-provided instruction or skill validator.

8. **Commit only when appropriate**
   - Do not create a commit unless the user requested it or the active workflow explicitly requires it.
   - Never commit directly on a protected/default branch when project instructions require a feature branch.
   - Stage only the instruction, skill, test, or tooling files changed by this workflow.

9. **Report dispositions**
   - List each candidate as retained, merged, enforced, deferred, or dropped, with its destination.
   - Include the sources searched by the duplicate gate and whether the candidate was duplicate, overlapping, conflicting, or distinct.
