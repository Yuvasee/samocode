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

2. **Verify session exists:**
   ```bash
   cat [SESSION_PATH]/_overview.md
   ```
   - Show user the current Status section (Phase, Blocked, Last Action, Next)
   - If blocked, ask user how to proceed

3. **Start samocode:**
   ```bash
   cd ~/samocode && CLAUDE_TIMEOUT=900 python worker.py --session [SESSION_PATH] 2>&1
   ```
   Run this in background using `run_in_background: true`

4. **Monitor loop using background sleep triggers:**

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

5. **On completion or block:**
   - Read final `_overview.md` status
   - Summarize what was accomplished
   - If blocked, explain what's needed

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

1. **Telegram errors**: Check `~/samocode/.env` has TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
2. **Timeout**: Increase CLAUDE_TIMEOUT if iterations take >15 min
3. **ngrok needed**: For webhooks (Vapi, Stripe), run `ngrok http [PORT]` first
4. **Session not found**: Ensure `_samocode/_overview.md` exists in project

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
   - Skills: `~/.claude/skills/*/SKILL.md`
   - Session management: `~/.claude/skills/session-management/SKILL.md`

4. **Common fix patterns:**
   - Infinite loops → Add explicit stop conditions in workflow.md
   - Wrong phase transitions → Clarify phase criteria in workflow.md
   - Missing context → Add more explicit instructions in skill
   - Format errors → Add examples in skill or workflow

**IMPORTANT:** Always propose fixes and wait for user approval. Samocode is critical infrastructure - no cowboy coding.

## Example Usage

```
User: "Run samocode on the hvac project"
→ Session: ~/code/hvac-voice-agent/_samocode
→ Start worker, monitor iterations, report progress

User: "Continue the samocode session"
→ Find session from context or ask user
→ Start worker, monitor iterations, report progress
```
