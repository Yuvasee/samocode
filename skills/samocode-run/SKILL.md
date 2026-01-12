---
name: samocode-run
description: Run and monitor samocode autonomous sessions on projects. Use when continuing work on a project with an existing _samocode session.
---

# Samocode Run

Runs the samocode autonomous orchestrator on a project session and monitors its progress.

## What is Samocode?

Samocode is an autonomous session orchestrator that runs Claude CLI in a loop to complete complex tasks. It:
- Reads session state from `_overview.md`
- Follows a structured workflow (investigation → Q&A → planning → implementation → testing → quality)
- Sends Telegram notifications on state changes
- Continues until task is complete or blocked

## When to Use

- User wants to continue work on a project with an existing `_samocode/` folder
- User says "run samocode", "continue the session", "let samocode work on it"
- Project has a `_samocode/_overview.md` file with session state

## Execution

**Arguments:** $ARGUMENTS (session path or project path)

### Steps

1. **Find session:**
   - If `$ARGUMENTS` is a full path to `_samocode/` or `_overview.md`, use it
   - If `$ARGUMENTS` is a project path, look for `_samocode/_overview.md` inside it
   - If no argument, check if current working dir has `_samocode/`
   - Derive PROJECT_PATH from session path (parent of `_samocode/`)

2. **Read project paths from `.samocode` file:**

   Look for `.samocode` in PROJECT_PATH (or its parent if needed). Parse key=value format:

   ```
   MAIN_REPO=~/project/repo
   WORKTREES=~/project/worktrees/
   SESSIONS=~/project/_sessions/
   ```

   Extract values:
   - `SESSIONS_DIR` from SESSIONS line
   - `WORKTREES_DIR` from WORKTREES line
   - `MAIN_REPO` from MAIN_REPO line (optional, for --repo flag)

   **If `.samocode` file is missing:**
   - ERROR: Tell user they need to create `.samocode` file in project root
   - Show them the required format (see below)
   - Do NOT proceed without this file

3. **Verify session exists:**
   ```bash
   cat [SESSION_PATH]/_overview.md
   ```
   - Show user the current Status section (Phase, Blocked, Last Action, Next)
   - If blocked, ask user how to proceed

4. **Start samocode:**
   ```bash
   cd ~/samocode && \
     SESSIONS_DIR=[extracted value] \
     WORKTREES_DIR=[extracted value] \
     CLAUDE_TIMEOUT=900 \
     python worker.py --session [SESSION_PATH] 2>&1
   ```

   If MAIN_REPO was found and session is repo-based, add `--repo [MAIN_REPO]`

   Run this in background using `run_in_background: true`

5. **Monitor loop using background sleep triggers:**

   Since you can't poll automatically, use background bash with sleep:

   ```bash
   sleep 60 && tail -20 /tmp/claude/.../tasks/[TASK_ID].output
   ```
   Run with `run_in_background: true`. When complete, you get a notification with output.

   **Sleep duration by phase:**
   - Investigation/planning: 60s (fast iterations)
   - Implementation: 120-180s (longer iterations)
   - Quality review: 120s (multi-review takes time)
   - Testing: 60s (usually quick)

   On each notification:
   - Parse the output to see current iteration and signal
   - Check `_overview.md` for phase: `grep -E "^(Phase|Last Action):" [SESSION]/_overview.md`
   - Report progress to user
   - Set next timer (adjust sleep based on phase)
   - Stop when `done`, alert on `blocked`

6. **On completion or block:**
   - Read final `_overview.md` status
   - Summarize what was accomplished
   - If blocked, explain what's needed

## Required `.samocode` File

Every project using samocode MUST have a `.samocode` file in its root:

```
MAIN_REPO=~/path/to/main/repo
WORKTREES=~/path/to/worktrees/
SESSIONS=~/path/to/_sessions/
```

**Keys:**
- `SESSIONS`: Where samocode session folders are stored
- `WORKTREES`: Where git worktrees are created for repo-based sessions
- `MAIN_REPO`: The main git repository (optional, used with --repo flag)

## Session Structure

```
project/
└── _samocode/
    ├── _overview.md      # Main session state
    ├── _qa.md            # Q&A when waiting for human input
    ├── [date]-plan-*.md  # Implementation plans
    ├── [date]-dive-*.md  # Investigation reports
    └── [date]-*.md       # Other artifacts
```

## Key Files in _overview.md

```markdown
## Status
Phase: [investigation|planning|implementation|testing|quality|done]
Iteration: N
Blocked: [yes/no]
Last Action: [what happened]
Next: [what to do next]

## Flow Log
- [MM-DD HH:MM] Event description → optional-file.md
```

## Common Issues

1. **Missing .samocode file**: Create `.samocode` file in project root with SESSIONS, WORKTREES, MAIN_REPO
2. **Telegram errors**: Check `~/samocode/.env` has TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
3. **Timeout**: Increase CLAUDE_TIMEOUT if iterations take >15 min
4. **ngrok needed**: For webhooks (Vapi, Stripe), run `ngrok http [PORT]` first
5. **Session not found**: Ensure `_samocode/_overview.md` exists in project

## Debugging Samocode Bugs

If samocode exhibits bugs or weird behavior (loops, wrong decisions, missing steps, etc.):

1. **Analyze the issue:**
   - Check worker output logs for errors
   - Read `_overview.md` to see what went wrong
   - Check if workflow.md instructions are unclear
   - Check if skills have ambiguous or missing guidance

2. **Suggest fixes - DO NOT auto-implement:**
   - Identify the root cause (workflow.md, skill, or worker code)
   - Propose specific fix to user with explanation
   - Show exact file and changes needed
   - **WAIT FOR USER CONFIRMATION before making any changes**

3. **Samocode source locations:**
   - Worker/orchestrator: `~/samocode/worker.py`, `claude_runner.py`, `config.py`
   - Workflow prompt: `~/samocode/workflow.md`
   - Skills: `~/samocode/skills/*/SKILL.md`
   - Commands: `~/samocode/commands/*.md`

4. **Common fix patterns:**
   - Infinite loops → Add explicit stop conditions in workflow.md
   - Wrong phase transitions → Clarify phase criteria in workflow.md
   - Missing context → Add more explicit instructions in skill
   - Format errors → Add examples in skill or workflow

**IMPORTANT:** Always propose fixes and wait for user approval. Samocode is critical infrastructure - no cowboy coding.

## Example Usage

```
User: "Run samocode on the hvac project"
→ Read ~/code/hvac-voice-agent/.samocode for paths
→ Session: ~/code/hvac-voice-agent/_samocode
→ Start worker with SESSIONS_DIR and WORKTREES_DIR from .samocode
→ Monitor iterations, report progress

User: "Continue the samocode session"
→ Find session from context or ask user
→ Read project's .samocode for paths
→ Start worker, monitor iterations, report progress
```
