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

## Phase Flow

```
init -> investigation -> requirements -> planning -> implementation -> testing -> quality -> testing -> done
         (dive)           (Q&A)         (plan)        (phases)         (1st)    (review)    (2nd)
```

- **init**: Create session infrastructure (worktree/folder, _overview.md)
- **investigation**: Deep-dive exploration using `dive` skill
- **requirements**: Q&A with human to clarify scope
- **planning**: Create phased implementation plan
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
**`for` values**: `qa_answers`, `file_update`, `human_action`

### Examples

```json
{"status": "continue", "phase": "implementation"}
{"status": "done", "phase": "done", "summary": "Implemented feature X"}
{"status": "blocked", "phase": "testing", "reason": "Tests failed", "needs": "error_resolution"}
{"status": "waiting", "phase": "requirements", "for": "qa_answers"}
```

## Flow Log Format

```
- [MM-DD HH:MM] Event description -> optional-file.md
```

Capture timestamp once at start of skill execution, reuse throughout.

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
| `task` | requirements (Q&A generation) |
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
