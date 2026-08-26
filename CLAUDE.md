# Samocode

Autonomous session orchestrator for Claude Code or OpenAI Codex. Python spawns the configured provider CLI in a loop; the child agent reads session state, executes phase-specific work, signals next step.

## Project Structure

```
main.py              # Checkout entry point (shim) -> worker/cli.py
workflow.md          # Master prompt template for provider iterations
ARCHITECTURE.md      # Runtime/configuration/routing design
worker/              # Core package
  cli.py             # CLI: orchestrator loop, install/approve/recover commands
  config.py          # Project paths + legacy/runtime env settings
  global_config.py   # User-global TOML, defaults, validation, bootstrap
  startup.py         # Load-once composition + process-wide provider selection
  phases.py          # Phase enum, profile defaults, transition validation
  plan_resolver.py   # Active implementation-plan phase parser
  routing.py         # Semantic profile -> immutable execution target
  adapters.py        # Claude/Codex command builders and provider registry
  runner.py          # Iteration resolution, context injection, retry execution
  escalation.py      # Testing environment-block escalation planner
  worktree_guard.py  # Read-only worktree snapshot + mutation guard
  signal_history.py  # Signal history tracking for debugging
  signals.py         # Signal file I/O (continue/done/blocked/waiting)
  timestamps.py      # Centralized timestamp formatting
  logging.py         # Rotating file + console logging
  notifications.py   # Telegram notifications
agents/              # Phase-specific agent instructions (md files)
skills/              # Claude/Codex skills
commands/            # Standalone Claude slash commands
docs/                # User-facing references (including model routing)
tests/               # pytest suite - one file per worker module
```

## Tech Stack

- Python 3.11+ (`tomllib` is required for the global model config)
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
uv run samocode --help         # Run orchestrator CLI
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

**Phase flow**: init -> investigation -> requirements -> planning -> implementation -> testing -> quality -> pr-readiness -> done

**Signal protocol** - child provider writes `_signal.json` to control flow:
- `continue` - Next iteration
- `done` - Workflow complete
- `blocked` - Needs human intervention
- `waiting` - Paused for human input (Q&A or plan approval)

**Stateless iterations** - Each provider invocation reads `_overview.md` fresh, executes one action, signals, exits.

**Testing safeguards** - Testing defaults to the `strong` profile. An `environment`
blocked signal escalates testing once to the next profile rung (provider fixed, one
attempt per phase entry, audited). Testing is worktree-readonly: the worker snapshots
HEAD + tracked status around each testing iteration and rejects a tracked-file mutation
as `Blocked: workflow_error` (`worktree_mutated`).

**Immutable routing per iteration** - Startup selects one provider for the process.
Before each iteration, the runner resolves the workflow/plan phase and semantic profile
into a concrete model/effort target. Retries reuse that exact target. The child receives
the target in Session Context and must not resolve or override it.

## Testing

- One test file per worker module (test_config.py, test_runner.py, etc.)
- Fixtures in conftest.py
- Test real functionality, not mocks
- All tests must pass

## Configuration

**CLI arguments:**
```bash
samocode run --config ~/project/.samocode --session my-task
```
- `--config` (required) - Full path to `.samocode` file
- `--session` (required) - Session name (not path)
- `--provider` (optional) - Process-wide `claude`/`codex` override

**`.samocode` file** (per-project, all required):
```
MAIN_REPO=~/project
WORKTREES=~/project/worktrees/
SESSIONS=~/project/_sessions/
```

**User-global model config:**

- `$XDG_CONFIG_HOME/samocode/config.toml` when XDG is absolute, otherwise
  `~/.config/samocode/config.toml`
- Created by `samocode install` only when absent; an existing file is preserved
- Loaded and validated once at process startup
- Contains provider executables, semantic profiles, concrete models/effort, and
  optional workflow overrides
- Provider precedence: CLI → `SAMOCODE_PROVIDER` → config default → legacy Claude

See `docs/model-routing.md` for the schema and canonical profile table.

**Environment variables** (in `.env`) - runtime/legacy settings:
- `SAMOCODE_PROVIDER` - optional process-wide provider override
- `CLAUDE_PATH`, `CODEX_PATH` - executable path overrides
- `CLAUDE_TIMEOUT`, `CODEX_TIMEOUT` - timeout overrides
- `CLAUDE_MODEL`, `CODEX_MODEL` - legacy only, used when global config is absent
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` - Optional notifications

## Key Files

- `worker/cli.py` - argparse CLI, orchestrator loop, bootstrap quarantine, lifecycle preflight
- `worker/global_config.py` - TOML schema, canonical defaults, bootstrap/load
- `worker/startup.py` - startup composition and provider precedence
- `worker/phases.py` - Phase enum, PhaseConfig/profile registry, transition validation
- `worker/plan_resolver.py` - deterministic active plan/phase selection
- `worker/routing.py` - profile resolution and immutable ExecutionTarget
- `worker/adapters.py` - provider registry and Claude/Codex argv construction
- `worker/runner.py` - per-iteration plan/context resolution and retry execution
- `worker/escalation.py` - `plan_escalation` decision service (testing environment blocks)
- `worker/worktree_guard.py` - HEAD/tracked-status snapshot + mutation description
- `worker/config.py` - project/runtime/SamocodeConfig dataclasses
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
- The CLI must live inside `worker/` (`worker/cli.py`): hatch `force-include` copies top-level files into site-packages even for editable installs, so a top-level `main.py` entry point silently goes stale after every pull — assets resolve through `resolve_asset_source_dir()` for the same reason
- When a session uses a worktree, start the orchestrator from the worktree path (or pass the worktree as working dir) — otherwise commits land on the main-repo branch instead of the session branch and the PR ends up split between two locations
- The worktree guard fires on tracked-file *content or HEAD* changes during testing, not on untracked files: a build step that regenerates a tracked lockfile/snapshot trips `worktree_mutated` even if it looks incidental, so a testing agent must `git checkout -- <path>` any tracked file a build touched before signaling — the fix is to restore, never to commit or route around it
