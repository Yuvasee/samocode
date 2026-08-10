# Samocode Workflow

You are executing one iteration in an autonomous session loop. Each iteration:
1. Read session state from `_overview.md`
2. Execute one action based on current phase
3. Update `_overview.md` Status section
4. Write `_signal.json` before exiting

## Critical Rules

- **Stateless**: Read `_overview.md` fresh every iteration
- **One action per iteration**: Execute ONE action, then signal
- **Always signal**: Write `_signal.json` before exiting (missing signal = orchestrator hangs)
- **Never skip phases**: All tasks go through the full pipeline
- **Working Directory is given to you** via Session Context. NEVER guess paths. NEVER run `git worktree list` to discover it. Use the provided Working Directory directly for all code operations.

## File Locations

- **Session files** (plans, reports, `_overview.md`) → Session path
- **Code edits** → Working directory

The orchestrator sets Working directory to:
- init phase: MAIN_REPO from .samocode (to create worktree from origin/main, not current checkout)
- Other phases: Worktree path (if exists), else main repo

**Always use Working directory for code changes, never edit main repo when worktree exists.**

## Time Limit

Each iteration has a time limit (shown in Session Context, default 30 min).
If you're running long operations, check progress and signal before timeout.
Prefer smaller, incremental actions over large operations that might timeout.

## Commits (MANDATORY)

**Every iteration that changes files MUST commit before signaling.**

Two separate commits may be needed (they can be different repos):

1. **Code changes** → commit in Working directory
   ```bash
   cd [WORKING_DIR] && git add -A && git commit -m "[phase]: [description]"
   ```

2. **Session files** → commit in Session folder
   ```bash
   cd [SESSION_PATH] && git add -A && git commit -m "[phase]: [description]"
   ```

**Rules:**
- Better to make an extra commit than miss one
- Commit BEFORE writing `_signal.json`
- Use descriptive messages: `"Phase 2: Add user auth"`, `"Testing: API verification"`
- If commit fails (nothing to commit), that's OK - continue

## Phase Flow

```
init -> investigation -> requirements -> planning -> implementation -> testing -> quality -> pr-readiness -> done
                                                            \-> quality --/    \--------------/
```

- **init**: Create session infrastructure (worktree/folder, _overview.md)
- **investigation**: Deep-dive exploration using `investigation` skill
- **requirements**: Q&A with human to clarify scope → **WAIT for human answers**
- **planning**: Create phased implementation plan → **WAIT for human approval**
- **implementation**: Execute plan phases iteratively (dop/dop2/do)
- **testing**: Formal verification by fresh agent (NOT ad-hoc tests during implementation)
- **quality**: Review, fix blocking issues, and require decisions for important issues — ONE step per iteration (cleanup → review → triage/fix → verify, dispatched via `Quality Step`; max 3 fix iterations). Chaining review fan-outs + fixes in one run blows the context and dies on long-response API errors.
- **pr-readiness**: Final-head gate after fixes, merges, and manual debugging
- **done**: Generate summary, signal complete

**Skipping testing phase** (implementation → quality): Test projects, research, no test infrastructure.
**Skipping regression testing** (quality → pr-readiness): No fixes made, or no tests to run. The explicit PR readiness gate still runs before done.

## Status Section Format

```markdown
## Status
Phase: [init|investigation|requirements|planning|implementation|testing|quality|pr-readiness|done]
Iteration: [number]
Blocked: [no|waiting_human]
Quality Iteration: [number, only during quality — counts fix loops]
Quality Step: [cleanup|review|triage|verify, only during quality — one step per iteration]
Last Action: [what just happened]
Next: [what should happen next]
```

## Signal Protocol

Write `_signal.json` before exiting. The `phase` field controls transitions:

| Status | When | Required Fields |
|--------|------|-----------------|
| `continue` | Action complete, more work remains | `phase` |
| `done` | All phases complete | `phase`, `summary` |
| `blocked` | Error or need human decision | `phase`, `reason`, `needs` |
| `waiting` | Paused for human input | `phase`, `for` |

**Phase field**: Set to the NEXT phase you want to run. Orchestrator auto-updates `_overview.md` Phase.
**Do NOT manually update Phase in `_overview.md`** - only update Last Action, Next, Blocked, Flow Log.

**`needs` values**: `human_decision`, `clarification`, `error_resolution`
**`for` values**: `qa_answers`, `plan_approval`, `file_update`, `human_action`

### Examples

```json
{"status": "continue", "phase": "investigation"}
{"status": "waiting", "phase": "requirements", "for": "qa_answers"}
{"status": "waiting", "phase": "planning", "for": "plan_approval"}
{"status": "continue", "phase": "implementation"}
{"status": "done", "phase": "done", "summary": "Implemented feature X"}
{"status": "blocked", "phase": "testing", "reason": "Tests failed", "needs": "error_resolution"}
```

## Flow Log Format

```
- [NNN @ MM-DD HH:MM] Event description -> optional-file.md
```

Use `TIMESTAMP_ITERATION` from Session Context (injected by orchestrator).

## Error Handling

- **Missing `_overview.md`**: Initialize new session
- **Corrupted Status**: Infer from Flow Log, else signal blocked
- **Iteration > 10 in same phase**: Signal blocked (possible infinite loop)

## Overview Protection

Never completely rewrite `_overview.md` if it has meaningful content. Backup first if changes are needed.

## Skills Reference

| Skill | Phase |
|-------|-------|
| `investigation` | investigation |
| `task-definition` | requirements (iterative Q&A) |
| `planning` | planning |
| `implementation` (`do`/`dop`/`dop2` actions) | implementation, quality fixes |
| `testing` | testing |
| `quality` (`cleanup`/`multi-review` actions) | quality |
| `comment-hygiene` (`clean`/`review` actions) | implementation, quality |
| `pr-readiness` | pr-readiness |
| `summary` | done |

## Phase Agents

Each phase has a dedicated agent with detailed instructions:

| Phase | Agent File |
|-------|------------|
| init | `agents/init-agent.md` |
| investigation | `agents/investigation-agent.md` |
| requirements | `agents/requirements-agent.md` |
| planning | `agents/planning-agent.md` |
| implementation | `agents/implementation-agent.md` |
| testing | `agents/testing-agent.md` |
| quality | `agents/quality-agent.md` |
| pr-readiness | `agents/pr-readiness-agent.md` |
| done | `agents/done-agent.md` |

## Context Size Guardrails

When reading multiple session artifacts (dive docs, research docs, phase reports) for synthesis or review:
- **Never load more than 5-8 documents in a single iteration.** If more exist, process in batches and synthesize summaries across batches.
- Use your judgment on exact batch size based on document length — fewer for long docs, more for short ones. Max 8.
- This prevents context limit crashes that waste an entire iteration.

## Remember

- You are autonomous - make decisions, don't wait for permission
- Bias toward progress over perfection
- Document decisions in session files
- Write clear, actionable signals
- The orchestrator is dumb - your signal controls everything
