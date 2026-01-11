# Samocode - Autonomous Session Orchestrator

A "dumb" Python orchestrator that runs Claude Code CLI in a loop. Claude reads session state, decides actions via skills, updates state, and signals flow control. Human intervention only via `_qa.md` files and Telegram notifications.

## Installation

### 1. Install Skills & Commands

```bash
# Run the install script (creates symlinks to ~/.claude/)
cd ~/samocode
./install.sh

# Restart Claude Code to apply changes
```

This installs:
- **9 skills**: session-management, investigation, planning, implementation, quality, testing, summary, task-definition, samocode-run
- **14 commands**: /dive, /task, /create-plan, /do, /dop, /dop2, /do2, /cleanup, /multi-review, /summary, /session-start, /session-continue, /session-sync, /session-archive

To uninstall: `./uninstall.sh`

### 2. Install Python Dependencies

```bash
cd ~/samocode
pip install -r requirements.txt
```

### 3. Configure Environment

```bash
cp .env.example .env
# Edit .env with your settings:
# - TELEGRAM_BOT_TOKEN
# - TELEGRAM_CHAT_ID
# - CLAUDE_PATH (path to claude CLI)
```

## Quick Start

```bash
# Start a new session with dive and task
python worker.py --session my-feature \
  --dive "understand existing code structure" \
  --task "Add user authentication"

# Continue after answering Q&A
python worker.py --session my-feature

# Run on a specific repo (creates worktree)
python worker.py --session api-redesign \
  --repo ~/my-repo \
  --dive "current API structure" \
  --task "Redesign REST API"
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Samocode Orchestrator                    │
│                      (Python - "dumb")                      │
└─────────────────────────────────────────────────────────────┘
         │                    │                    │
         ▼                    ▼                    ▼
    ┌─────────┐         ┌──────────┐         ┌──────────┐
    │ Claude  │         │ _signal  │         │ Telegram │
    │   CLI   │◄───────►│  .json   │         │   Bot    │
    └─────────┘         └──────────┘         └──────────┘
         │                                        │
         ▼                                        ▼
    ┌─────────────────────────────┐         ┌──────────┐
    │     Session Folder          │         │  Human   │
    │  _overview.md, _qa.md, ...  │         │          │
    └─────────────────────────────┘         └──────────┘
```

**Key principle**: Python is dumb. It just:
1. Invokes Claude CLI with workflow prompt
2. Reads signal file after Claude exits
3. Decides: continue loop, stop, or notify human

Claude decides everything by reading `_overview.md` and using skills.

## Usage

### Starting a New Session

**Repo-based session** (creates git worktree):

```bash
# With --repo: creates worktree in ~/samocode/worktrees/26-01-08-api-redesign
python worker.py --session api-redesign \
  --repo ~/my-repo \
  --dive "current API structure and pain points" \
  --task "Redesign the REST API to use consistent naming and add pagination"
```

**Standalone session** (creates new project folder):

```bash
# Without --repo: creates folder in ~/projects/26-01-08-prototyping
python worker.py --session prototyping \
  --dive "explore new framework capabilities" \
  --task "Build prototype of new feature"
```

**Path-based session** (session in project folder):

```bash
# Full path: session in ~/code/my-project/_samocode, working dir is ~/code/my-project
python worker.py --session ~/code/my-project/_samocode \
  --dive "understand existing code" \
  --task "Add new feature"
```

**Dry run** (show config without executing):

```bash
python worker.py --session test --dry-run
```

**Required for new sessions:** Both `--dive` and `--task` must be provided.

### Continuing After Q&A

When the agent signals `waiting` for Q&A answers:
1. You receive Telegram notification
2. Edit `_qa.md` in session folder with your answers
3. Re-run with same session name (and `--repo` if repo-based):

```bash
# Repo-based continuation
python worker.py --session my-task --repo ~/my-repo

# Standalone continuation
python worker.py --session my-task
```

**Note:** `--dive` and `--task` are only used on first run. On subsequent runs, agent reads state from `_overview.md`.

### Session Naming

Session name is auto-prefixed with date: `my-task` → `26-01-08-my-task`

## Configuration

Environment variables (or `.env` file):

| Variable | Default | Description |
|----------|---------|-------------|
| `SESSIONS_DIR` | `~/samocode/_sessions` | Where sessions are stored |
| `DEFAULT_PROJECTS_FOLDER` | `~/projects` | Where standalone projects are created |
| `WORKTREES_DIR` | `~/samocode/worktrees` | Where worktrees are created (repo-based) |
| `TELEGRAM_BOT_TOKEN` | - | Telegram bot token for notifications |
| `TELEGRAM_CHAT_ID` | - | Telegram chat ID for notifications |
| `CLAUDE_PATH` | `/home/dev/.nvm/.../claude` | Path to Claude CLI |
| `CLAUDE_MODEL` | `opus` | Model to use |
| `CLAUDE_MAX_TURNS` | `120` | Max turns per iteration |
| `CLAUDE_TIMEOUT` | `600` | Timeout in seconds |
| `SAMOCODE_MAX_RETRIES` | `3` | Retry attempts on failure |
| `SAMOCODE_RETRY_DELAY` | `5` | Delay between retries (seconds) |

## Signal File Format

Claude writes `_signal.json` to control flow:

### continue
```json
{"status": "continue"}
```
Orchestrator loops to next iteration.

### done
```json
{"status": "done", "summary": "Brief description of what was accomplished"}
```
Workflow complete. Orchestrator stops.

### blocked
```json
{"status": "blocked", "reason": "Clear description", "needs": "human_decision"}
```
Stop and notify human via Telegram.

`needs` values: `human_decision`, `clarification`, `error_resolution`

### waiting
```json
{"status": "waiting", "for": "qa_answers"}
```
Pause for human input. Check `_qa.md` for questions.

`for` values: `qa_answers`, `file_update`

## Workflow Phases

```
investigation → requirements → planning → implementation → testing → quality → testing → done
                     │                                        ↑          │         ↑
                     └── Q&A via _qa.md ──────────────────────┘          └─────────┘
                                                                      (fix loop if
                                                                       blocking issues)
```

| Phase | Skill | Description |
|-------|-------|-------------|
| investigation | `dive` | Understand the problem space |
| requirements | `task` | Q&A with human via `_qa.md` |
| planning | `planning` | Create implementation plan |
| implementation | `dop2` (default) | Execute plan phases |
| testing | `testing` | Verify feature works |
| quality | `cleanup`, `multi-review` | Clean up and review code |
| done | - | Generate summary |

## Session Structure

```
~/samocode/_sessions/26-01-08-my-task/
├── _overview.md          # Session state (Status section)
├── _qa.md                # Q&A questions/answers (temporary)
├── _signal.json          # Flow control signal
├── 01-08-10:00-dive-*.md # Investigation documents
├── 01-08-10:30-task-*.md # Task definition
├── 01-08-11:00-plan-*.md # Implementation plan
├── 01-08-11:30-*.md      # Implementation docs
└── ...
```

### _overview.md Status Section

```markdown
## Status
Phase: implementation
Iteration: 3
Blocked: no
Last Action: Completed Phase 2
Next: Execute Phase 3
```

## Telegram Notifications

Notifications sent for:
- **Blocked**: Workflow hit an issue requiring human decision
- **Waiting**: Q&A questions ready in `_qa.md`
- **Complete**: Workflow finished successfully
- **Error**: Orchestrator crashed or Claude failed

## Logging

Logs written to `logs/samocode.log`:
- Rotating file handler (1MB max, 5 backups)
- Also outputs to console
- Format: `[YYYY-MM-DDTHH:MM:SS] LEVEL - message`

## Skills Reference

Skills are in `./skills/` (installed via plugin):

| Skill | Actions | Description |
|-------|---------|-------------|
| `session-management` | `start`, `continue`, `sync`, `archive` | Session lifecycle |
| `investigation` | - | Deep dive on topics |
| `task-definition` | - | Interactive task definition with Q&A |
| `planning` | - | Create implementation plans |
| `implementation` | `do`, `dop`, `dop2` | Execute tasks/phases |
| `quality` | `cleanup`, `multi-review` | Code cleanup and review |
| `summary` | - | Generate PR descriptions |
| `testing` | - | Test implemented features |
| `samocode-run` | - | Run samocode orchestrator |

## Commands Reference

Commands are in `./commands/` (installed via plugin):

| Command | Description |
|---------|-------------|
| `/dive` | Start investigation on a topic |
| `/task` | Define task with Q&A |
| `/create-plan` | Create implementation plan |
| `/do` | Execute single task |
| `/do2` | Execute with dual-agent comparison |
| `/dop` | Execute plan phase |
| `/dop2` | Execute plan phase with dual-agent |
| `/cleanup` | Run code cleanup |
| `/multi-review` | Run multi-perspective code review |
| `/summary` | Generate PR summary |
| `/session-start` | Start new session |
| `/session-continue` | Continue existing session |
| `/session-sync` | Sync session state |
| `/session-archive` | Archive completed session |

## Project Structure

```
~/samocode/
├── install.sh            # Install skills/commands to ~/.claude/
├── uninstall.sh          # Remove installed skills/commands
├── .claude-plugin/       # Plugin manifest (for validation)
│   └── plugin.json
├── worker.py             # Main orchestrator loop
├── workflow.md           # Master prompt for Claude
├── config.py             # Configuration from environment
├── signals.py            # Signal file operations
├── claude_runner.py      # Claude CLI execution with retry
├── telegram.py           # Telegram notifications
├── logging_setup.py      # Logging configuration
├── skills/               # Claude Code skills (9 skills)
├── commands/             # Claude Code commands (14 commands)
├── _samocode/            # This repo's own session
└── _sessions/            # Other sessions (gitignored)
```
