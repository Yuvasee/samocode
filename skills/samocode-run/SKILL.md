---
name: samocode-run
description: Run and monitor samocode autonomous sessions on projects. Use when user says "run samocode" or wants to continue a samocode session.
---

# Samocode Run

Runs the samocode autonomous orchestrator on a project session and monitors its progress.

## CRITICAL: DO NOT MANUALLY ORCHESTRATE

**When user asks to "run samocode" or "continue samocode", you MUST use this skill.**

DO NOT:
- Launch Task subagents yourself for investigation/planning/implementation phases
- Manually read `_overview.md` and decide what phase to run
- Update `_signal.json` yourself
- Pretend to be the orchestrator

The Python worker (`main.py`) handles ALL of this. Your job is to START the worker and MONITOR its output.

## Trigger Phrases

Use this skill when user says:
- "run samocode"
- "start samocode"
- "continue samocode"
- "let samocode work on it"
- "continue the session"
- "run the orchestrator"

## What is Samocode?

Samocode is an autonomous session orchestrator that runs Claude CLI in a loop to complete complex tasks. It:
- Reads session state from `_overview.md`
- Follows a structured workflow (investigation → Q&A → planning → implementation → testing → quality)
- Sends Telegram notifications on state changes
- Continues until task is complete or blocked

## When to Use

- User wants to run/continue a samocode session
- Session folder exists with `_overview.md` file

## Execution

**Arguments:** $ARGUMENTS (session path or project path)

### Steps

1. **Find session:**
   - If `$ARGUMENTS` is a full path to a session folder or `_overview.md`, use it
   - If `$ARGUMENTS` is a project path, look for `.samocode` file to find SESSIONS dir
   - If no argument, check current working dir for `.samocode` config
   - Session folders are in SESSIONS dir (from `.samocode` file), NOT nested in project

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
     python main.py --session [SESSION_PATH] 2>&1
   ```

   Default timeout is 30 min, max turns 300. Override with CLAUDE_TIMEOUT and CLAUDE_MAX_TURNS env vars if needed.

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

Sessions are stored in SESSIONS dir (from `.samocode` file), NOT nested inside projects:

```
[SESSIONS_DIR]/
└── [YY-MM-DD]-[session-name]/    # Session folder (e.g., 26-01-15-pyright-ci)
    ├── _overview.md              # Main session state
    ├── _signal.json              # Control signal
    ├── _qa.md                    # Q&A when waiting for human input
    ├── [MM-DD-HH:mm]-plan-*.md   # Implementation plans
    ├── [MM-DD-HH:mm]-dive-*.md   # Investigation reports
    └── [MM-DD-HH:mm]-*.md        # Other artifacts
```

**IMPORTANT:** Do NOT create `_samocode/` subfolders inside sessions. All files go directly in the session folder.

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
3. **Timeout**: Default is 30 min. Increase CLAUDE_TIMEOUT env var if iterations need more
4. **ngrok needed**: For webhooks (Vapi, Stripe), run `ngrok http [PORT]` first
5. **Session not found**: Ensure session folder with `_overview.md` exists in SESSIONS dir
6. **Nested _samocode/ error**: Sessions must NOT have `_samocode/` subfolder - migrate files to session root

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
   - Worker/orchestrator: `~/samocode/main.py`, `claude_runner.py`, `config.py`
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
→ Read ~/code/hvac-voice-agent/.samocode for paths (SESSIONS=~/code/hvac/_sessions/)
→ Find session in SESSIONS dir (e.g., ~/code/hvac/_sessions/26-01-15-voice-agent/)
→ Run: python main.py --session [SESSION_PATH]
→ Monitor iterations, report progress

User: "Continue the samocode session"
→ Find session from context or ask user
→ Read project's .samocode for paths
→ Run: python main.py --session [SESSION_PATH]
→ Monitor iterations, report progress
```

**Remember:** You run `python main.py`, the Python worker runs Claude. You do NOT run phase agents yourself.
