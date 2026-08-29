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
   - Do not target or cap the total number of phases. Use as many as the task needs to preserve small, coherent iterations; a large plan may legitimately contain dozens.
   - 1–3 focused steps per phase (not counting lint/typecheck)
   - Split by logical boundary: one file/module/concern per phase when possible
   - Prefer more small phases over fewer large ones
   - Include explicit edge-case acceptance checks when the feature touches validators, queues, DB writes, background fan-out, uploads, auth/API-key users, or shared package boundaries
   - `## Implementation Phases` contains implementation and test-authoring work only. The outer `testing -> quality -> testing -> pr-readiness -> done` lifecycle is owned by the orchestrator and must never appear as plan phases.
   - Put final browser/API/E2E and regression criteria in a separate `## Verification Plan`. The testing agent consumes those criteria after implementation; the implementation agent does not execute that section.

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
   the heading. This applies to every executable implementation phase. Verification
   Plan items and orchestrator-owned lifecycle phases do not carry plan profiles.

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

   ## Verification Plan

   - [ ] [Feature behavior or test case for the outer testing agent]
   - [ ] Edge cases: all/partial/no validators, queue succeeds/DB fails, DB succeeds/queue fails, concurrent same-KI revalidation, large uploads, API-key user missing email/name where applicable
   - [ ] Browser/API/E2E and post-Quality regression expectations

   ## Notes
   [Important context from task definition]
   ```

   Never create implementation phases for Testing, Quality, Code Clarity, Comment
   Hygiene, regression gates, PR Readiness, Done, summary generation, or approval
   stops. Never instruct the implementation agent to spawn those lifecycle agents or
   to wait for final approval instead of handing control back to the orchestrator.

3. **Update session:**
   - Edit `[SESSION_PATH]/_overview.md`:
     - Status: `Phase: planning`, `Blocked: waiting_human`, `Last Action: Plan created`,
       `Next: Await plan approval (samocode approve)`. In an interactive session set
       `Phase: planning` yourself; under the Samocode worker it already is — never
       write any other phase value (`planned` is not a phase).
     - Add to Flow Log: `- [TIMESTAMP_ITERATION] Plan created -> [filename].md`
     - Add to Plans: `- [filename].md - [brief description]` — plain filename, no
       markdown link: the worker parses this line to select the active plan
     - Add to Files: `- [filename].md - Plan: [brief description]`
   - Write `[SESSION_PATH]/_signal.json`:
     `{"status": "waiting", "phase": "planning", "for": "plan_approval"}`
     This is the pending plan-approval gate. `samocode approve` validates the plan,
     consumes the signal, and advances the session to implementation; never advance
     `Phase` yourself.
   - Commit (if git repo): `cd [SESSION_DIR] && git add . && git commit -m "Plan: [title]"`

4. **Report back:** Plan summary, file location, and the handoff: after review, run
   `/samocode-implement` (it runs `samocode approve` and starts the worker).
