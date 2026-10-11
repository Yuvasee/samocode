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
   - Each phase is executed as a **single samocode iteration** by a highly capable agent. Size a phase as an outcome-level milestone that such an agent finishes in one iteration: a whole subsystem or vertical slice with its tests, not a single file, function or test.
   - Use the fewest phases that keep dependencies and risk boundaries clean. Most tasks need 1–6 phases. More than about 8 means the plan is too granular, or the task should be split into separate sessions.
   - Split only at real boundaries: work that a later phase builds on (for example, a schema before its consumers), a clearly different risk profile, or independently reviewable subsystems. Do not split by file, function or test.
   - Write each phase as 2–5 outcome and acceptance bullets, not step-by-step instructions. Leave design choices inside a phase to the implementing agent unless the requirements fix them.
   - Plan the simplest design that meets the requirements. Reuse existing code. Do not plan speculative abstractions, extra subsystems, or hardening that no requirement or acceptance check needs.
   - Tests belong in the same phase as the code they verify.
   - Include explicit edge-case acceptance checks when the feature touches validators, queues, DB writes, background fan-out, uploads, auth/API-key users, or shared package boundaries
   - `## Implementation Phases` contains implementation and test-authoring work only. The outer `testing -> quality -> testing -> pr-readiness -> done` lifecycle is owned by the orchestrator and must never appear as plan phases.
   - Put final browser/API/E2E and regression criteria in a separate `## Verification Plan`. The testing agent consumes those criteria after implementation; the implementation agent does not execute that section.

   **Semantic profile assignment (required for every phase in a new plan):**

   Choose the profile only from the phase's work, uncertainty, blast radius, and
   recovery risk:

   | Profile | Use when |
   |---------|----------|
   | `light` | Mechanical, local, deterministic work with no meaningful design choice or state risk: narrow renames, boilerplate, or similarly contained edits. |
   | `standard` | Ordinary, well-defined implementation using established patterns, with contained impact and straightforward verification. This is the normal workhorse. |
   | `strong` | Architecture, persistence/schema/data changes, concurrency, retries/idempotency, security/auth, public contracts, failure recovery, or other cross-cutting multi-component work. |
   | `max` | Rare phases where high uncertainty, large blast radius, and difficult or costly recovery are all present. If only one or two apply, use `strong`; do not escalate a phase to `max` only because it is large. |

   **Documentation authoring is `max`-only.** Any phase that authors or substantively
   rewrites documentation content — README, ARCHITECTURE.md, CHANGELOG, workflow.md,
   CLAUDE.md, CONTRIBUTING.md, or files under `docs/` — must be split into its own phase
   carrying a `**Profile:** max` line, even when the edit looks mechanical. This overrides
   the general "max is rare" guidance above. It does not apply to reading documentation
   for context, or to source comments and docstrings, which keep whatever profile their
   surrounding work warrants. `worker/plan_resolver.py` enforces this and rejects a
   pending documentation-authoring phase whose profile is missing or not `max`.

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

   ### Phase 1: [Outcome-level milestone]
   **Profile:** `strong`
   - [ ] [Outcome the phase delivers]
   - [ ] [Acceptance check, including the tests that prove it]
   - [ ] Type checks and linters pass for the changed code

   ### Phase 2: [Outcome-level milestone]
   **Profile:** `standard`
   - [ ] [Outcome the phase delivers]
   - [ ] [Acceptance check, including the tests that prove it]

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
