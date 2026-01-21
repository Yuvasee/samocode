# Samocode

Autonomous session orchestrator for Claude Code. Python spawns Claude CLI in a loop; Claude reads session state, executes phase-specific work, signals next step.

## Project Structure

```
main.py              # Orchestrator entry point - main loop
workflow.md          # Master prompt template for Claude iterations
worker/              # Core package (~1,400 lines)
  config.py          # Configuration from .samocode + .env
  phases.py          # Phase enum, config registry, transition validation
  runner.py          # Claude CLI execution with retry
  signal_history.py  # Signal history tracking for debugging
  signals.py         # Signal file I/O (continue/done/blocked/waiting)
  logging.py         # Rotating file + console logging
  notifications.py   # Telegram notifications
agents/              # Phase-specific agent instructions (md files)
skills/              # Claude Code skills (9 total)
commands/            # Standalone Claude commands (14 total)
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

**Three layers**: Parent Claude -> Worker (Python) -> Child Claude instances

**Phase flow**: init -> investigation -> requirements -> planning -> implementation -> testing -> quality -> done

**Signal protocol** - Claude writes `_signal.json` to control flow:
- `continue` - Next iteration
- `done` - Workflow complete
- `blocked` - Needs human intervention
- `waiting` - Paused for human input (Q&A or plan approval)

**Stateless iterations** - Each Claude invocation reads `_overview.md` fresh, executes one action, signals, exits.

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
- `CLAUDE_PATH` - Path to Claude CLI
- `CLAUDE_MODEL` - Model name (default: opus)
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` - Optional notifications

## Key Files

- `worker/phases.py` - Phase enum, PhaseConfig registry, transition/signal validation (source of truth)
- `worker/runner.py` - Core execution logic, Claude CLI invocation
- `worker/config.py` - ProjectConfig, RuntimeConfig, SamocodeConfig dataclasses
- `worker/signals.py` - Signal dataclass, JSON parsing
- `worker/signal_history.py` - Records signals to `_signal_history.jsonl` for debugging
- `workflow.md` - Master prompt injected into each Claude run
- `TECH_DEBT.md` - Known architectural issues
