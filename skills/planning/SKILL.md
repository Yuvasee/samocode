---
name: planning
description: Create implementation plans with phase management.
---

# Planning

Creates detailed implementation plans with phases, stored within the session folder.

## Requirements

- Active session must exist (session path in working memory)
- If no active session: **STOP and ask user** for session path

## Execution

**Session path:** [SESSION_PATH from working memory]
**Context:** $ARGUMENTS

### Steps

1. **Gather context:**
   - Read recent dive/task documents from session
   - Review project documentation if available
   - Understand current codebase state

2. **Create plan file:**
   - Location: `[SESSION_PATH]/[TIMESTAMP_FILE]-plan-[plan-slug].md`

   **Phase design principles:**
   - Each phase is executed as a **single samocode iteration** — keep phases bite-sized and independently completable
   - 1–3 focused steps per phase (not counting lint/typecheck)
   - Split by logical boundary: one file/module/concern per phase when possible
   - Prefer more small phases over fewer large ones
   - Include explicit edge-case acceptance checks when the feature touches validators, queues, DB writes, background fan-out, uploads, auth/API-key users, or shared package boundaries
   - **Do NOT label any phase "Manual browser verification" or similar.** Samocode's testing-agent runs browser E2E autonomously (container restart, data seeding, screenshots included). Carving it out as a "manual" phase gives the testing-agent permission to defer. Put browser verification criteria in the standard Testing phase or as acceptance checks on the UI-touching phases.
   - **Model profile per phase (optional):** Immediately under a `### Phase N: [Name]` heading, add `**Profile:** \`name\`` (backtick-quoted) to route that phase through a non-default model profile — e.g. `strong` for architecture-heavy phases, `max` for the highest-stakes ones. Omit the line to inherit the workflow `implementation` default. The value must be a single non-empty backtick-quoted name and appear at most once per phase; an empty, unquoted, or duplicated line fails the session before any model call. Built-in names: `light`, `standard`, `strong`, `max` (plus any custom profiles in the user's global config). When unsure, omit it.

   Structure:
   ```markdown
   # Plan: [Title]
   Created: [TIMESTAMP_LOG]

   ## Task Definition
   [Concise summary]

   ## Requirements
   - [ ] [Requirement 1]
   - [ ] [Requirement 2]

   ## Context
   [Key files, current state, constraints]

   ## Implementation Phases

   ### Phase 1: [Name]
   **Profile:** `strong`
   - [ ] [Step]
   - [ ] [Step]
   - [ ] Run pyright/ruff or tsc - fix errors

   ### Phase 2: [Name]
   - [ ] [Step]
   - [ ] [Step]
   - [ ] Run pyright/ruff or tsc - fix errors

   ### Phase 3: Testing
   - [ ] [Test case]
   - [ ] Edge cases: all/partial/no validators, queue succeeds/DB fails, DB succeeds/queue fails, concurrent same-KI revalidation, large uploads, API-key user missing email/name where applicable
   - [ ] Final checks

   ### Phase 4: PR Readiness
   - [ ] Enter the `pr-readiness` phase after all fixes/merges/manual debugging
   - [ ] Resolve `_review_debt.md` rows: fix, defer with ticket/reason, or reject with evidence

   ## Notes
   [Important context from task definition]
   ```

3. **Update session:**
   - Edit `[SESSION_PATH]/_overview.md`:
     - Add to Flow Log: `- [TIMESTAMP_ITERATION] Plan created -> [filename].md`
     - Add to Plans: `- [filename].md - [brief description]`
     - Add to Files: `- [filename].md - Plan: [brief description]`
   - Commit (if git repo): `cd [SESSION_DIR] && git add . && git commit -m "Plan: [title]"`

4. **Report back:** Plan summary and file location
