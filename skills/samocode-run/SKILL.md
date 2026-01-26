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

3. **Check session state (if exists):**
   - Read `_overview.md` Status section
   - **If `Phase: done`:**
     - Ask user: "Session is complete. What new work do you want to do?"
     - Update `_overview.md`:
       - `Phase: investigation`
       - `Last Action: Resuming session for: [user's goal]`
       - `Next: Investigate approach for [user's goal]`
     - Add to Flow Log: `- [MM-DD HH:MM] Resuming: [user's goal]`
   - **If `Blocked: yes` or `blocked`:**
     - Show user the current status (Last Action, Next, reason if available)
     - Ask how to proceed
     - Update status based on user's direction

4. **Start samocode:**
   ```bash
   cd ~/samocode && python main.py \
     --config [PATH_TO_.SAMOCODE] \
     --session [SESSION_NAME] 2>&1
   ```

   **Optional:** Add `--timeout SECONDS` for per-iteration time limit (default: 1800s = 30 min).
   Each child Claude iteration is killed if it exceeds this. Increase for complex phases:
   ```bash
   python main.py --config ... --session ... --timeout 3600  # 1 hour per iteration
   ```

   Run this in background using `run_in_background: true`

5. **Monitor loop using Bash + TaskOutput (CRITICAL):**

   **IMPORTANT:** Always use `TaskOutput(block=true)` immediately after starting a background monitor.
   Do NOT rely on system notifications - they can be missed if you're mid-response or user sends a message.

   **Reliable monitoring pattern:**
   ```python
   # Step 1: Start background sleep + check
   Bash(
     command="sleep 60 && grep -E '^(Phase|Iteration|Blocked):' [SESSION]/_overview.md",
     run_in_background=true
   )
   # Returns task_id (e.g., "b155903")

   # Step 2: IMMEDIATELY block on result - DO NOT SKIP THIS
   TaskOutput(task_id="b155903", block=true, timeout=120000)
   # This guarantees you see the result

   # Step 3: Report progress to user

   # Step 4: If not done/blocked, repeat from Step 1
   ```

   **Sleep duration by phase:**
   - Investigation/planning: 60s (fast iterations)
   - Implementation: 120-180s (longer iterations)
   - Quality review: 120s (multi-review takes time)
   - Testing: 60s (usually quick)

   **On each TaskOutput result:**
   - Parse the status (Phase, Iteration, Blocked)
   - Report progress to user
   - If `done` or `blocked` → stop monitoring
   - Otherwise → start next monitor iteration (goto Step 1)

   **Why this matters:** Without `block=true`, you may lose track of monitors when:
   - User sends a message while you're waiting
   - Multiple monitors pile up
   - System notifications arrive during your response

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

## Key Files in _overview.md

```markdown
## Status
Phase: [investigation|planning|implementation|testing|quality|done]
Iteration: N
Blocked: [yes/no]
Last Action: [what happened]
Next: [what to do next]

## Flow Log
- [NNN @ MM-DD HH:MM] Event description -> optional-file.md
```

## Common Issues

1. **Missing .samocode file**: Create `.samocode` file in project root with SESSIONS, WORKTREES, MAIN_REPO
2. **Telegram errors**: Check `~/samocode/.env` has TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
3. **Timeout**: Default is 30 min. Increase CLAUDE_TIMEOUT env var if iterations need more

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
   - Worker/orchestrator: `~/samocode/main.py`, `~/samocode/worker/`
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
