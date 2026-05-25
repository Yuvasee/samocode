# Samocode

Autonomous session orchestrator for Claude Code or OpenAI Codex. Python spawns the configured provider CLI in a loop; the child agent reads session state, executes phase-specific work, signals next step.

## Project Structure

```
main.py              # Orchestrator entry point - main loop
workflow.md          # Master prompt template for provider iterations
worker/              # Core package (~1,600 lines)
  config.py          # Configuration from .samocode + .env
  phases.py          # Phase enum, config registry, transition validation
  runner.py          # Provider CLI execution with retry
  signal_history.py  # Signal history tracking for debugging
  signals.py         # Signal file I/O (continue/done/blocked/waiting)
  timestamps.py      # Centralized timestamp formatting
  logging.py         # Rotating file + console logging
  notifications.py   # Telegram notifications
agents/              # Phase-specific agent instructions (md files)
skills/              # Claude/Codex skills
commands/            # Standalone Claude slash commands
tests/               # pytest suite - one file per worker module
```

## Tech Stack

- Python 3.10+ (uses `|` union syntax)
- Dependencies: python-dotenv, requests (for Telegram)
- Testing: pytest
- Linting: ruff, pyright

## Commands

```bash
pytest tests/                    # Run all tests
pytest tests/test_runner.py     # Run specific test file
ruff check .                    # Lint
ruff format .                   # Format
pyright                         # Type check
python main.py --help           # Run orchestrator
```

## Code Style

- Strict typing - no `any` types, use `|` for unions
- Main functions at top, utilities below
- Frozen dataclasses for config/data structures
- Enums for status values (ExecutionStatus, SignalStatus, Phase)
- Global imports at file top, no dynamic imports
- Section comments (`# ===`) for large module organization
- Short, context-independent comments

## Architecture

**Three layers**: Parent CLI -> Worker (Python) -> Child provider instances

**Phase flow**: init -> investigation -> requirements -> planning -> implementation -> testing -> quality -> done

**Signal protocol** - child provider writes `_signal.json` to control flow:
- `continue` - Next iteration
- `done` - Workflow complete
- `blocked` - Needs human intervention
- `waiting` - Paused for human input (Q&A or plan approval)

**Stateless iterations** - Each provider invocation reads `_overview.md` fresh, executes one action, signals, exits.

## Testing

- One test file per worker module (test_config.py, test_runner.py, etc.)
- Fixtures in conftest.py
- Test real functionality, not mocks
- All tests must pass

## Configuration

**CLI arguments:**
```bash
python main.py --config ~/project/.samocode --session my-task
```
- `--config` (required) - Full path to `.samocode` file
- `--session` (required) - Session name (not path)

**`.samocode` file** (per-project, all required):
```
MAIN_REPO=~/project
WORKTREES=~/project/worktrees/
SESSIONS=~/project/_sessions/
```

**Environment variables** (in .env) - runtime settings only:
- `SAMOCODE_PROVIDER` - `claude` or `codex` (default: claude)
- `CLAUDE_PATH`, `CLAUDE_MODEL` - Claude CLI settings
- `CODEX_PATH`, `CODEX_MODEL` - Codex CLI settings
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` - Optional notifications

## Key Files

- `worker/phases.py` - Phase enum, PhaseConfig registry, transition/signal validation (source of truth)
- `worker/runner.py` - Core execution logic, provider CLI invocation
- `worker/config.py` - ProjectConfig, RuntimeConfig, SamocodeConfig dataclasses
- `worker/signals.py` - Signal dataclass, JSON parsing
- `worker/signal_history.py` - Records signals to `_signal_history.jsonl` for debugging
- `workflow.md` - Master prompt injected into each provider run
- `TECH_DEBT.md` - Known architectural issues

## Learnings

- When rewriting git history with `filter-repo` (or `filter-branch`), stash or commit uncommitted working-tree changes first — the rewrite ends with `git reset --hard` followed by `git gc`, which destroys uncommitted work irrecoverably from git
- When working-tree edits made by Claude Code are lost, scan `~/.claude/projects/**/*.jsonl` for `Edit`/`Write`/`Read` tool calls on the affected paths and replay chronologically from the latest Read snapshot — Claude session logs are a non-git backup of recent file states
- After `filter-repo` finishes it removes the `origin` remote by design; re-add it, then `git fetch origin` to rebuild remote-tracking refs before any `--force-with-lease` push
- Pin repo-local git identity (`git config --local user.email …`) when the repo's intended author differs from your global config — worktrees inherit local config automatically
- Never accept secrets pasted into a chat as a working approach — Claude transcripts persist; treat any pasted token as compromised and rotate it immediately, then guide the user to env vars or a credentials file for future runs
- When a session uses a worktree, start the orchestrator from the worktree path (or pass the worktree as working dir) — otherwise commits land on the main-repo branch instead of the session branch and the PR ends up split between two locations
