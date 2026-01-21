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

## What is Samocode?

Samocode is an autonomous session orchestrator that runs Claude CLI in a loop to complete complex tasks. It:
- Reads session state from `_overview.md`
- Runs phase-specific agents automatically based on current phase
- Sends Telegram notifications on state changes
- Continues until task is complete or blocked

For workflow details and phase definitions, see `~/samocode/CLAUDE.md`.

## Sessions: Manual vs Autonomous

There's no strict "samocode session" - just sessions. Any session can be worked on:
- **Manually** by you (the parent Claude) - e.g., investigation, Q&A, planning
- **Autonomously** by samocode - e.g., implementation, testing, quality fixes
- **Mixed** - start manually, hand off to samocode, take back control when blocked

This flexibility is intentional. Use samocode for repetitive/long-running phases, work manually when human judgment is needed.

## When to Use

**Only when user explicitly asks for samocode** (see Trigger Phrases above).

Do NOT assume samocode should run just because a session exists.

## Execution

**Arguments:** $ARGUMENTS (session name or project path with session name)

### Steps

1. **Find .samocode config:**
   - Look for `.samocode` file in current working dir or project path
   - Extract the full path to the `.samocode` file (e.g., `~/project/.samocode`)
   - **If `.samocode` file is missing:** ERROR and ask user to create it

2. **Determine session name:**
   - If `$ARGUMENTS` is a session name (e.g., "my-task"), use it directly
   - If `$ARGUMENTS` includes a path, extract session name from it
   - Session will be resolved: exact match → dated match → new session

3. **Verify session exists (if continuing):**
   - Parse SESSIONS path from `.samocode` file
   - Check for existing session: `{SESSIONS}/*-{session_name}/_overview.md`
   - If found, show user the current Status section (Phase, Blocked, Last Action, Next)
   - If blocked, ask user how to proceed

4. **Start samocode:**
   ```bash
   cd ~/samocode && python main.py \
     --config [PATH_TO_.SAMOCODE] \
     --session [SESSION_NAME] 2>&1
   ```

   Default timeout is 30 min, max turns 300. Override with CLAUDE_TIMEOUT and CLAUDE_MAX_TURNS env vars if needed.

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

**All three keys are REQUIRED:**
- `MAIN_REPO`: The main working directory (where Claude runs)
- `SESSIONS`: Where samocode session folders are stored
- `WORKTREES`: Where git worktrees are created

## Session Structure

Sessions are stored in SESSIONS dir (from `.samocode` file), NOT nested inside projects:

```
[SESSIONS_DIR]/
└── [YY-MM-DD]-[session-name]/    # Session folder (e.g., 26-01-15-pyright-ci)
    ├── _overview.md              # Main session state
    ├── _signal.json              # Control signal
    ├── _qa.md                    # Q&A when waiting for human input
    ├── _logs/                    # Agent iteration logs (JSONL)
    │   └── [MM-DD-HHMM]-[NNN]-[phase].jsonl
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
→ Find ~/code/hvac-voice-agent/.samocode file
→ Determine session name from context (e.g., "voice-agent")
→ Run: python main.py --config ~/code/hvac-voice-agent/.samocode --session voice-agent
→ Monitor iterations, report progress

User: "Continue the samocode session"
→ Find session name from context or ask user
→ Find .samocode file path
→ Run: python main.py --config [CONFIG_PATH] --session [SESSION_NAME]
→ Monitor iterations, report progress
```

**Remember:** You run `python main.py`, the Python worker runs Claude. You do NOT run phase agents yourself.
