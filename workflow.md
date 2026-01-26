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

## File Locations

- **Session files** (plans, reports, `_overview.md`) → Session path
- **Code edits** → Working directory

The orchestrator sets Working directory to:
- init phase: Main repo (to create worktree from there)
- Other phases: Worktree path (if exists), else main repo

**Always use Working directory for code changes, never edit main repo when worktree exists.**

## Time Limit

Each iteration has a time limit (shown in Session Context, default 30 min).
If you're running long operations, check progress and signal before timeout.
Prefer smaller, incremental actions over large operations that might timeout.

## Phase Flow

```
init -> investigation -> requirements -> planning -> implementation -> testing -> quality -> testing -> done
         (dive)           (Q&A)         (plan)        (phases)         (1st)    (review)    (2nd)
```

- **init**: Create session infrastructure (worktree/folder, _overview.md)
- **investigation**: Deep-dive exploration using `dive` skill
- **requirements**: Q&A with human to clarify scope → **WAIT for human answers**
- **planning**: Create phased implementation plan → **WAIT for human approval**
- **implementation**: Execute plan phases iteratively (dop/dop2/do)
- **testing**: Runs twice - after implementation, after quality
- **quality**: Review + fix blocking issues (max 3 iterations)
- **done**: Generate summary, signal complete

## Status Section Format

```markdown
## Status
Phase: [init|investigation|requirements|planning|implementation|testing|quality|done]
Iteration: [number]
Blocked: [no|waiting_human]
Quality Iteration: [number, only during quality]
Last Action: [what just happened]
Next: [what should happen next]
```

## Signal Protocol

Write `_signal.json` with current phase before exiting:

| Status | When | Required Fields |
|--------|------|-----------------|
| `continue` | Action complete, more work remains | `phase` |
| `done` | All phases complete | `phase`, `summary` |
| `blocked` | Error or need human decision | `phase`, `reason`, `needs` |
| `waiting` | Paused for human input | `phase`, `for` |

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
| `dive` | investigation |
| `task-definition` | requirements (iterative Q&A) |
| `planning` | planning |
| `do`, `dop`, `dop2` | implementation, quality fixes |
| `testing` | testing |
| `cleanup`, `multi-review` | quality |
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
| done | `agents/done-agent.md` |

## Remember

- You are autonomous - make decisions, don't wait for permission
- Bias toward progress over perfection
- Document decisions in session files
- Write clear, actionable signals
- The orchestrator is dumb - your signal controls everything
