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

   **Semantic profile assignment (required for every phase in a new plan):**

   Choose the profile only from the phase's work, uncertainty, blast radius, and
   recovery risk:

   | Profile | Use when |
   |---------|----------|
   | `light` | Mechanical, local, deterministic work with no meaningful design choice or state risk: narrow renames, documentation, boilerplate, or similarly contained edits. |
   | `standard` | Ordinary, well-defined implementation using established patterns, with contained impact and straightforward verification. This is the normal workhorse. |
   | `strong` | Architecture, persistence/schema/data changes, concurrency, retries/idempotency, security/auth, public contracts, failure recovery, or other cross-cutting multi-component work. |
   | `max` | Rare phases where high uncertainty, large blast radius, and difficult or costly recovery are all present. If only one or two apply, use `strong`; split an oversized phase before escalating it to `max`. |

   Immediately under every `### Phase N: [Name]` heading, write exactly one
   `**Profile:** \`light|standard|strong|max\`` line. It must be the first line after
   the heading. This applies to implementation, testing, and readiness phases alike.

   Profiles are semantic routing labels, not model specifications. Do **not** inspect
   provider configuration, `config.toml`, model catalogs, effort levels, token usage,
   or prices when assigning them. Do not name a concrete model/provider in the plan.
   Runtime routing resolves the selected provider, model, and effort when Samocode
   executes the phase.

   The parser still accepts missing `Profile` in untouched legacy plans and falls back
   to the implementation workflow default. That compatibility path is not an
   authoring option: every newly created or newly added phase must declare a profile.

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
   **Profile:** `standard`
   - [ ] [Step]
   - [ ] [Step]
   - [ ] Run pyright/ruff or tsc - fix errors

   ### Phase 3: Testing
   **Profile:** `standard`
   - [ ] [Test case]
   - [ ] Edge cases: all/partial/no validators, queue succeeds/DB fails, DB succeeds/queue fails, concurrent same-KI revalidation, large uploads, API-key user missing email/name where applicable
   - [ ] Final checks

   ### Phase 4: PR Readiness
   **Profile:** `standard`
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
