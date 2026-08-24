<div align="center">

# samocode

**Walk-away AI coding sessions, locally orchestrated.**
Drives Claude or Codex through full SDLC phases with human gates,
so you can hand off multi-hour engineering work and walk away.

[![PyPI](https://img.shields.io/pypi/v/samocode.svg)](https://pypi.org/project/samocode/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![CI](https://github.com/Yuvasee/samocode/actions/workflows/ci.yml/badge.svg)](https://github.com/Yuvasee/samocode/actions/workflows/ci.yml)

[Quick start](#quick-start) · [How it works](#how-it-works) · [vs alternatives](#vs-alternatives) · [Examples](examples/)

</div>

---

## What this is

You give samocode a real engineering task — research a codebase, plan a refactor, implement a feature, run tests, clean up. It runs an AI CLI in a loop, walking your task through investigation → requirements → planning → implementation → testing → quality phases. It pauses to ask you questions when it needs to (`_qa.md`), waits for plan approval, and notifies you on Telegram when something needs your attention. You come back two hours later, your branch has the work done, with commits, tests, and a summary.

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
samocode install
```

The install command makes the bundled skills/agents available and creates the default
global model-profile config without overwriting an existing one.

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
cd ~/samocode && pip install -r requirements.txt && samocode install
```

`samocode install` creates the user-global model config when absent, prints its path and
profile table, and installs skills into both `~/.claude/skills` and
`~/.codex/skills`. Claude-only slash commands and phase-agent files go into
`~/.claude`; Codex runs those same packaged phase-agent instructions through prompt
injection. From a repo checkout the installer uses symlinks (so edits go live); from a
`pip install` it auto-detects and copies — use `--copy` only to force copying from a
checkout.

Re-running a **symlink** install refreshes Samocode-owned links and preserves foreign
links/files. In **copy** mode, existing skills and commands are still treated as
user-owned and skipped. Packaged Claude phase-agent files are the narrow exception:
they are Samocode-managed, so a stale real file is moved to `<name>.bak` and refreshed.
This is required to keep their `model: inherit` routing contract current.

`samocode uninstall` removes only samocode-owned **symlinks**. Copy-mode installs are real files samocode cannot prove it created, so they are left in place and reported as skipped — delete them manually if needed.

After a `pip install`, run `samocode install` once to make the skills, agents, and commands available to your provider (it auto-copies from a packaged install).

> **Upgrading from the old `install.sh`?** Skills that were removed or renamed (e.g. `adhd`, `gemini`) leave dangling symlinks in `~/.claude/skills` and `~/.codex/skills`. Remove them manually: `rm ~/.claude/skills/adhd ~/.codex/skills/gemini` (ignore "No such file" errors).

→ See [`examples/`](examples/) for runnable scenarios.

## Use samocode skills standalone

samocode's skills (`investigation`, `planning`, `implementation`, `quality`, `testing`, and more) are plain [Agent Skills](https://github.com/vercel-labs/skills) — a `SKILL.md` per directory — so any compatible agent can use them without the orchestrator. Install them cross-agent in one line:

```bash
npx skills add Yuvasee/samocode
```

This discovers every skill in the repo and links them into your agent skill directories (`~/.claude/skills`, `~/.codex/skills`, and others — the `skills` CLI is multi-agent aware). Add `--copy` to copy instead of symlink, or `-a '*' --skill '*' -y` to install all skills to all detected agents non-interactively. Preview without installing:

```bash
npx skills add Yuvasee/samocode --list
```

This installs **skills only** — not the orchestrator, agents, or slash commands. For the full samocode loop, use `pip install samocode` (above); for self-install of all assets from a checkout, use `samocode install`.

## How it works

Three layers, each with a single responsibility:

```
Parent session       Worker (Python)         Child AI CLI
─────────────       ───────────────         ────────────
You + your CLI  →   spawns provider CLI  →  reads _overview.md
monitors progress   resolves phase/profile  executes one action
relays Q&A          validates signal/retry  writes _signal.json
```

Each iteration is **stateless**: the child CLI starts fresh, reads `_overview.md`,
executes one action, writes a signal, and exits. The worker deterministically owns
state, routing, retries, and lifecycle; the child owns the engineering decisions for
that action.

Phases:
```
init → investigation → requirements → planning → implementation → testing → quality → testing → pr-readiness → done
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
| `CLAUDE_MODEL` | `opus` | Claude model (**legacy only** — ignored when a global config exists) |
| `CLAUDE_TIMEOUT` | `1800` | Claude timeout per iteration (seconds) |
| `CODEX_PATH` | `codex` | Path to Codex CLI |
| `CODEX_MODEL` | empty | Codex model, **legacy only** (empty = use `~/.codex/config.toml`) |
| `CODEX_TIMEOUT` | `1800` | Codex timeout per iteration (seconds) |
| `TELEGRAM_BOT_TOKEN` | - | Telegram notifications |
| `TELEGRAM_CHAT_ID` | - | Telegram notifications |

### Global model routing (`config.toml`)

Samocode routes every iteration through a **semantic profile** (`light`, `standard`,
`strong`, `max`, or custom) that resolves to a concrete model and optional reasoning
effort for the one selected provider. The user-global config lives at
`$XDG_CONFIG_HOME/samocode/config.toml` (fallback `~/.config/samocode/config.toml`)
and is created from defaults by `samocode install` — it is never overwritten.

Install reports whether the file was created or preserved and shows the active
defaults, for example:

```text
Global config:
  Path: /Users/you/.config/samocode/config.toml
  Status: created
  Default: provider=claude profile=standard
  PROVIDER   PROFILE    MODEL                        EFFORT
  claude     standard   claude-sonnet-4-6            high
  claude     strong     claude-opus-4-8              high
  codex      standard   gpt-5.6-terra                medium
  codex      strong     gpt-5.6-sol                  medium
```

Without it, samocode runs in **legacy mode** using `CLAUDE_MODEL`/`CODEX_MODEL`. With a
valid config, profile model/effort is authoritative and those env vars are ignored.
The config is loaded once at process startup; restart Samocode after editing it.
Provider precedence is `--provider` → `SAMOCODE_PROVIDER` → config default → legacy
Claude. Requires **Python 3.11+**.

→ Full concepts, default table, profile syntax, overrides, and migration: [docs/model-routing.md](docs/model-routing.md)

## Phase reference

| Phase | What happens |
|-------|--------------|
| init | Create worktree + session infrastructure |
| investigation | Explore the codebase via `dive` skill |
| requirements | Q&A with you via `_qa.md` (human gate) |
| planning | Create phased plan, wait for approval (human gate) |
| implementation | Execute plan phases iteratively |
| testing | Verify by fresh agent (not ad-hoc tests) |
| quality | Ordinary review/fixes, then a Code Clarity fix loop and final Comment Hygiene cleanup (max 3 fix batches per loop) |
| pr-readiness | Final-head gate after regression tests; validates review debt and the clarity → hygiene provenance chain |
| done | Generate summary, signal complete |

## Signal protocol

The child agent writes `_signal.json` to control the loop:

| Signal | Effect | Example |
|--------|--------|---------|
| `continue` | Next iteration | `{"status": "continue", "phase": "implementation"}` |
| `done` | Stop, success | `{"status": "done", "summary": "..."}` |
| `blocked` | Stop, notify human | `{"status": "blocked", "reason": "...", "needs": "human_decision"}` |
| `waiting` | Pause for input | `{"status": "waiting", "for": "qa_answers"}` |

**Plan approval gate:** when the planning phase signals `{"status": "waiting", "for": "plan_approval"}`, review the plan and then run:
```bash
samocode approve --config ~/project/.samocode --session my-task
```
This atomically advances the session to the implementation phase and consumes the pending signal. Do **not** manually edit `_overview.md`.

`samocode approve` exits with a code describing the outcome (also in `--help`):

| Code | Meaning |
|------|---------|
| 0 | Approved: this call advanced the phase and consumed the signal |
| 1 | Rejected: precondition failed (no gate, wrong/absent signal, etc.) |
| 3 | Lock contended: another approval is in progress; retry |
| 4 | State/IO fault: overview-write fault (may be transient), lock I/O (not retryable), or the phase moved off the gate target (an external writer may have moved it); not advanced by this call — read stderr to disambiguate |
| 5 | Advanced but signal retained: phase advanced; retained `_signal.json` is inert, cleanup optional |
| 6 | Already advanced: another approval reached the gate target; this call made no change |

(argparse reserves exit code 2 for CLI usage errors.)

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
| `/merge` | Guarded merge from `origin/main` or another source branch |
| `/prcomments` | Investigate and triage PR review comments |
| `/pr-readiness` | Final-head PR readiness gate before summary/merge |
| `/session-start`, `/session-continue`, `/session-archive` | Session management |

## Examples

→ [`examples/`](examples/)

*These are scaffolds for the next polish phase — none exist yet.*

- [`hello-agent/`](examples/hello-agent/) — minimal session (creates a single file)
- [`add-feature/`](examples/add-feature/) — full pipeline on a small Express app
- [`refactor/`](examples/refactor/) — multi-file refactor with tests
- [`research-only/`](examples/research-only/) — investigation-only, no code changes
- [`provider-codex/`](examples/provider-codex/) — same task, Codex provider

Examples are scaffolds for the next polish phase — not all are present yet.

## Providers

Today: **Claude** and **Codex** are supported orchestration providers. Claude uses native agent selection; Codex uses provider-specific prompt injection for the same phase agents and falls back to inline multi-pass workflows where Claude would use Task subagents. **Gemini** is available as a second-opinion reviewer in the `/multi-review` skill, not as an orchestration provider.

On the roadmap: Gemini orchestration support and deeper provider-specific agent optimizations.

## Roadmap

- [ ] Monitor process for crash recovery (see [IDEAS.md](IDEAS.md) §1)
- [ ] Stall detection
- [ ] Handoff pattern for context refresh
- [ ] Parallel worker support
- [ ] Gemini orchestration support

## Contributing

Issues and PRs are welcome.

### Recommended Claude Code plugins

This repo's `.claude/settings.json` recommends [revdiff](https://github.com/umputun/revdiff). When you open the repo in Claude Code, you'll be prompted to install it (skippable). It's used for inline diff review.

## License

MIT — see [LICENSE](LICENSE).
