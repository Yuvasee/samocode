<div align="center">

# samocode

**Walk-away AI coding sessions, locally orchestrated.**
Drives Claude (primary) or Codex through full SDLC phases with human gates,
so you can hand off multi-hour engineering work and walk away.

[![PyPI](https://img.shields.io/pypi/v/samocode.svg)](https://pypi.org/project/samocode/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![CI](https://github.com/Yuvasee/samocode/actions/workflows/ci.yml/badge.svg)](https://github.com/Yuvasee/samocode/actions/workflows/ci.yml)

[Quick start](#quick-start) · [How it works](#how-it-works) · [vs alternatives](#vs-alternatives) · [Examples](examples/)

</div>

---

## What this is

You give samocode a real engineering task — research a codebase, plan a refactor, implement a feature, run tests, clean up. It runs an AI CLI in a loop, walking your task through investigation → planning → implementation → testing → quality phases. It pauses to ask you questions when it needs to (`_qa.md`), waits for plan approval, and notifies you on Telegram when something needs your attention. You come back two hours later, your branch has the work done, with commits, tests, and a summary.

It's open-source, runs Claude or Codex as the orchestration provider (Gemini available as a second-opinion subagent), and runs locally — no SaaS, no proxy, your code never leaves your machine.

Not an engineer? See [docs/eli10.md](docs/eli10.md) for a friendly walkthrough.

## When this is useful

- "Add JWT auth to this Express app, write tests, make sure CI passes." (90 min unattended)
- "Investigate how rate-limiting currently works in this codebase, then design a new sliding-window approach." (45 min unattended)
- "Refactor this 800-line file into focused modules, keep the test suite green." (2 hours unattended)
- "Run the linter on the whole repo, fix every issue except the ones in `legacy/`." (30 min unattended)

If your task is "I need to think about this with the AI for 10 minutes" — use Claude / Cursor / Aider directly. samocode is for the cases where you'd rather walk away.

## Quick start (60 seconds)

```bash
pip install samocode
```

Create `.samocode` in your project root:
```ini
MAIN_REPO=~/your-project
WORKTREES=~/your-project/worktrees/
SESSIONS=~/your-project/_sessions/
```

Run a session:
```bash
samocode \
  --config ~/your-project/.samocode \
  --session add-jwt-auth \
  --task "Add JWT-based authentication to the Express API"
```

samocode creates a worktree, spawns the AI CLI, walks the task through phases, and signals when it's done or needs you. Watch progress in `~/your-project/_sessions/26-XX-XX-add-jwt-auth/_overview.md`.

To hack on samocode itself, clone the repo instead:
```bash
git clone https://github.com/Yuvasee/samocode ~/samocode
cd ~/samocode && ./install.sh && pip install -r requirements.txt
```

→ See [`examples/`](examples/) for runnable scenarios.

## How it works

Three layers, each with a single responsibility:

```
Parent session       Worker (Python)         Child AI CLI
─────────────       ───────────────         ────────────
You + your CLI  →   spawns provider CLI  →  reads _overview.md
monitors progress   reads _signal.json      executes one action
relays Q&A          decides loop/stop       writes _signal.json
```

Each iteration is **stateless**: the child CLI starts fresh, reads `_overview.md`, executes one action, writes a signal, exits. The Python worker is intentionally dumb — it just spawns the CLI and reads signals. All decisions happen in the child agent.

Phases:
```
init → investigation → requirements → planning → implementation → testing → quality → done
                            ↑              ↑
                       human gate     human gate
                       (answer Q&A)   (approve plan)
```

→ See [ARCHITECTURE.md](ARCHITECTURE.md) for deeper dive.

## vs alternatives

| Tool | Style | Session length | Human gates | Provider |
|------|-------|----------------|-------------|----------|
| **samocode** | External orchestrator over AI CLI | Hours–days, multi-phase | Built-in (Q&A + plan approval) | Claude / Codex |
| Aider | Interactive pair-programming | Minutes–hours | Per-message | Any LLM via API |
| Cursor Background Agents | SaaS unattended runs | Hours | Limited | Cursor's own |
| Devin | Closed SaaS | Hours | Limited | Cognition's own |
| LangGraph | Embeddable graph framework | App-defined | Code-defined | Any |
| CrewAI | Embeddable role-based multi-agent | App-defined | Code-defined | Any |
| AutoGen | Embeddable conversational multi-agent | App-defined | Code-defined | Any |
| Claude Agent SDK | SDK for embedding Claude agents | App-defined | Code-defined | Claude |

**TL;DR positioning:** samocode is the open-source, local-first version of "set the AI on this task and walk away" tooling, with explicit phase separation and human gates.

## Configuration

### `.samocode` File (per project, all required)

| Key | Description |
|-----|-------------|
| `MAIN_REPO` | Main git repository path |
| `WORKTREES` | Where git worktrees are created |
| `SESSIONS` | Where session folders are stored |

### Environment Variables (`.env`, optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `SAMOCODE_PROVIDER` | `claude` | Provider: `claude` or `codex` |
| `CLAUDE_PATH` | `claude` | Path to Claude CLI |
| `CLAUDE_MODEL` | `opus` | Claude model |
| `CLAUDE_TIMEOUT` | `1800` | Claude timeout per iteration (seconds) |
| `CODEX_PATH` | `codex` | Path to Codex CLI |
| `CODEX_MODEL` | empty | Codex model (empty = use `~/.codex/config.toml`) |
| `CODEX_TIMEOUT` | `1800` | Codex timeout per iteration (seconds) |
| `TELEGRAM_BOT_TOKEN` | - | Telegram notifications |
| `TELEGRAM_CHAT_ID` | - | Telegram notifications |

## Phase reference

| Phase | What happens |
|-------|--------------|
| init | Create worktree + session infrastructure |
| investigation | Explore the codebase via `dive` skill |
| requirements | Q&A with you via `_qa.md` (human gate) |
| planning | Create phased plan, wait for approval (human gate) |
| implementation | Execute plan phases iteratively |
| testing | Verify by fresh agent (not ad-hoc tests) |
| quality | Review + fix blocking issues (max 3 iterations) |
| done | Generate summary, signal complete |

## Signal protocol

The child agent writes `_signal.json` to control the loop:

| Signal | Effect | Example |
|--------|--------|---------|
| `continue` | Next iteration | `{"status": "continue", "phase": "implementation"}` |
| `done` | Stop, success | `{"status": "done", "summary": "..."}` |
| `blocked` | Stop, notify human | `{"status": "blocked", "reason": "...", "needs": "human_decision"}` |
| `waiting` | Pause for input | `{"status": "waiting", "for": "qa_answers"}` |

## Session Structure

```
_sessions/26-01-08-my-task/
├── _overview.md              # Session state
├── _qa.md                    # Q&A (when waiting for human)
├── _signal.json              # Flow control
├── _logs/                    # Iteration logs (JSONL)
├── 01-08-10:00-dive-*.md     # Investigation docs
├── 01-08-11:00-plan-*.md     # Plans
└── ...                       # Other artifacts
```

## Commands

Standalone utilities, work without the orchestrator:

| Command | Description |
|---------|-------------|
| `/dive` | Investigate a topic |
| `/task` | Define task with Q&A |
| `/create-plan` | Create implementation plan |
| `/do`, `/do2` | Execute task (single / dual-agent) |
| `/dop`, `/dop2` | Execute plan phase (single / dual-agent) |
| `/cleanup` | Code cleanup analysis |
| `/multi-review` | Multi-perspective code review |
| `/session-start`, `/session-continue`, `/session-archive` | Session management |

## Examples

→ [`examples/`](examples/)

- [`hello-agent/`](examples/hello-agent/) — minimal session (creates a single file)
- [`add-feature/`](examples/add-feature/) — full pipeline on a small Express app
- [`refactor/`](examples/refactor/) — multi-file refactor with tests
- [`research-only/`](examples/research-only/) — investigation-only, no code changes
- [`provider-codex/`](examples/provider-codex/) — same task, Codex provider

Examples are scaffolds for the next polish phase — not all are present yet.

## Providers

Today: **Claude** is the primary orchestration provider. **Codex** (`--provider codex`) works for full sessions but with reduced feature parity (no native subagents — phase agents are injected as prompts). **Gemini** is available as a second-opinion subagent in the `/multi-review` skill, not as an orchestration provider.

On the roadmap: full Codex/Gemini orchestration parity (native subagent equivalents, provider-specific phase agents).

## Roadmap

- [ ] Monitor process for crash recovery (see [IDEAS.md](IDEAS.md) §1)
- [ ] Stall detection
- [ ] Handoff pattern for context refresh
- [ ] Parallel worker support
- [ ] Full Codex/Gemini orchestration parity

## Contributing

Issues and PRs welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

### Recommended Claude Code plugins

This repo's `.claude/settings.json` recommends [revdiff](https://github.com/umputun/revdiff). When you open the repo in Claude Code, you'll be prompted to install it (skippable). It's used for inline diff review.

## License

MIT — see [LICENSE](LICENSE).
