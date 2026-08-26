---
name: samocode-run
description: Run and monitor samocode autonomous sessions on projects. Use when user says "run samocode" or wants to continue a samocode session.
---

# Samocode Run

Runs the samocode autonomous orchestrator on a project session and monitors its progress.

## CRITICAL: DO NOT MANUALLY ORCHESTRATE

**When user asks to "run samocode" or "continue samocode", you MUST use this skill.**

DO NOT:
- Launch phase agents or Task subagents yourself for investigation/planning/implementation phases
- Infer or change the workflow phase yourself; read `_overview.md` only for status/monitoring
- Edit `_overview.md`, `_signal.json`, or `_signal_history.jsonl` to advance, unblock, rewind, or complete a session
- Pretend to be the orchestrator

The Python worker (`main.py`) handles ALL of this. Your job is to START the worker and MONITOR its output.

## Trigger Phrases

Use this skill when user says:
- "run samocode"
- "start samocode"
- "continue samocode"
- "let samocode work on it"
- "implement the plan"
- Claude command equivalent: `/samocode-implement`

## What is Samocode?

Samocode is an autonomous session orchestrator that runs the configured AI CLI provider in a loop to complete complex tasks. It:
- Reads session state from `_overview.md`
- Selects one provider for the process and resolves every iteration through a semantic model profile
- Runs phase-specific agents automatically based on current phase
- Sends Telegram notifications on state changes
- Continues until task is complete or blocked

For workflow details and phase definitions, see the installed `workflow.md`. For model
routing, defaults, and migration behavior, see `docs/model-routing.md` in the Samocode
source/package.

## Sessions: Manual vs Autonomous

There's no strict "samocode session" - just sessions. Any session can be worked on:
- **Manually** by you (the parent agent session) - e.g., answering Q&A or reviewing a plan
- **Autonomously** by samocode - e.g., implementation, testing, quality fixes
- **Mixed** - start manually, hand off to samocode, take back control when blocked

Human judgment can supply inputs and approve supported gates. Once the worker owns a
session lifecycle, only its CLI services may mutate lifecycle state; manual engineering
work never authorizes hand-editing workflow control files.

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
   - **If `Phase` is not one of `init|investigation|requirements|planning|implementation|testing|quality|pr-readiness|done`:**
     report the exact value and stop. Do not edit `_overview.md`; the worker refuses
     unknown phases at preflight, and only the human repairs the field.
   - **If user asked to "implement the plan" or invoked the `/samocode-implement` equivalent:**
     - This is an implementation handoff, not a generic continue.
     - If `Phase: planning` and the session is waiting for plan approval (`Blocked: waiting_human`, `Next: Await plan approval`, or `_signal.json` has `"for": "plan_approval"`), approve the gate before starting samocode:
       ```bash
       samocode approve --config [PATH_TO_.SAMOCODE] --session [SESSION_NAME]
       ```
       The approval service atomically advances the overview state (Phase → implementation, Blocked → no) and consumes the pending signal. Do NOT manually edit `_overview.md` or `_signal.json`.
     - Do NOT start from task-definition, requirements, or planning steps unless no approved/waiting plan exists.
     - If no plan file exists or requirements are incomplete, report that implementation cannot start yet and ask the user how to proceed.
   - **If `Phase: done`:**
     - Ask user: "Session is complete. What new work do you want to do?"
     - Do not reopen the completed lifecycle by editing `_overview.md`; create a new
       session for new work unless a dedicated worker command supports reopening.
   - **If `Blocked: workflow_error`:**
     - Report Last Action and Next, then stop. Do not run a phase agent and do not
       edit any control file.
     - **If it is a `worktree_mutated` guard rejection** (testing mutated tracked files;
       Last Action names the phase and a HEAD/tracked-path delta): report the delta, then
       have the human inspect and restore the working dir themselves —
       `git -C [WORKING_DIR] status` to see the change, `git -C [WORKING_DIR] checkout --
       <path>` (or reset the stray commit) to undo it — and restart the worker. Do NOT run
       `recover final-polish` for this class and never edit `_overview.md`/`_signal.json`.
     - **If it is a `worktree_unverifiable` guard rejection** (the post-run git snapshot
       could not run, so the guard could not confirm the working dir is unchanged — git
       broke or the repo went away, nothing was necessarily mutated): have the human
       confirm the working dir is still a healthy git repo (`git -C [WORKING_DIR] status`)
       and fix whatever broke git before restarting the worker. Same handling class as
       `worktree_mutated`: never run `recover final-polish` and never edit control files.
     - For a final-polish provenance error, run the read-only eligibility check:
       ```bash
       samocode recover final-polish --config [PATH_TO_.SAMOCODE] --session [SESSION_NAME] --check
       ```
     - Show the exact result to the user. Only after explicit user approval, apply:
       ```bash
       samocode recover final-polish --config [PATH_TO_.SAMOCODE] --session [SESSION_NAME] --apply
       ```
     - A successful recovery creates an immutable `_recovery/` snapshot, preserves
       `_signal_history.jsonl`, and returns to completed implementation so the worker
       can honestly replay testing → quality → testing → pr-readiness. Restart the
       worker normally after the command succeeds.
     - If the check refuses, investigate/report the mismatch; never broaden or bypass
       the recovery preconditions.
   - **If `Blocked: yes`, `blocked`, or `waiting_human`:**
     - Show user the current status (Last Action, Next, reason if available)
     - Ask how to proceed
     - Use a documented CLI gate when one exists; otherwise gather the requested
       human input and let the worker consume it. Do not edit lifecycle fields.

4. **Understand routing before startup:**
   - Global config path: `$XDG_CONFIG_HOME/samocode/config.toml` when
     `XDG_CONFIG_HOME` is absolute; otherwise `~/.config/samocode/config.toml`.
   - `samocode install` creates defaults only when the file is absent and never
     overwrites an existing config.
   - Provider precedence is `--provider` → `SAMOCODE_PROVIDER` → config
     `default_provider` → legacy `claude`.
   - The provider is fixed for the process. Workflow and implementation-plan phases
     select semantic profiles only; they never switch providers.
   - The worker loads and validates the global config once at startup. Do not edit it
     expecting a running process to change; restart Samocode after an edit.
   - Do not hand-resolve or pass per-phase model/effort arguments. The worker logs the
     authoritative provider/profile/model/effort/source once per iteration.
   - A missing global config enables legacy model env behavior with a warning. Prefer
     running `samocode install` before autonomous work.

5. **Start samocode:**
   ```bash
   samocode run \
     --config [PATH_TO_.SAMOCODE] \
     --session [SESSION_NAME] 2>&1
   ```

   **Optional:** Add `--provider claude|codex` to override provider selection for this
   process, or `--timeout SECONDS` for the per-iteration time limit (default: 1800s = 30 min).
   Each child agent iteration is killed if it exceeds this. Increase for complex phases:
   ```bash
   samocode run --config ... --session ... --provider codex --timeout 3600
   ```

   **Do NOT wrap with bash `timeout`** - The orchestrator manages its own timeouts via `--timeout`.
   External timeouts can kill iterations mid-work and corrupt session state.

   Run this in background using `run_in_background: true`

   **Avoid reading background task output directly.** The samocode worker output includes full
   Agent CLI logs which are large (100KB+ per iteration). Monitor progress via `_overview.md`
   and other session files instead.

   If debugging requires checking task output (e.g., investigating a crash):
   - Use `grep` to search for specific errors or patterns first
   - Use `tail -n 2` or `tail -n 5` max - each line can be huge (full JSON)
   - Use `Read` with `offset` and `limit` to read small portions
   - Never read the entire file

6. **Monitor loop (prefer session files over task output):**

   6.1. Start background check (sleep duration by phase: investigation/planning 60s, implementation 120-180s, quality 120s, testing 60s):
   ```bash
   Bash(command="sleep 60 && cat [SESSION]/_overview.md", run_in_background=true)
   ```
   Returns task_id (e.g., "b155903")

   6.2. Wait for result - **DO NOT SKIP, do immediately after 6.1:**
   ```bash
   TaskOutput(task_id="b155903", block=true, timeout=600000)
   ```
   Note: 600000ms (10 min) is the max allowed timeout.

   6.3. Extract from result: Phase, Iteration, Total Iterations, Blocked, Last Action, Next, last 3 Flow Log entries

   6.4. Get recent commits:
   ```bash
   git -C [WORKING_DIR] log --oneline -3
   ```

   6.5. Report to user:
   ```
   Samocode Progress [HH:MM elapsed]
   --------------------------------
   Phase: [phase] (Iteration N/Total)
   Last: [Last Action]
   Next: [Next]

   Recent commits:
   - [hash] [message]

   Flow:
   - [last 2-3 Flow Log entries]
   ```

   6.6. Check stop condition:
   - `Phase: done` → report final summary, STOP
   - `Blocked:` contains `workflow_error`, `yes`, or `waiting` → handle accordingly, STOP
   - Otherwise → goto step 6.1

   **IMPORTANT: On STOP, clean up monitoring.** When samocode finishes (done/blocked/waiting), do NOT leave pending background sleep tasks running. Stop any active monitoring task via `TaskStop` before reporting the final status. This prevents stale notification floods.

## Handling Waiting States

When samocode signals `waiting`:

**Auto-approve/answer ONLY if user explicitly requested it** (e.g., "run samocode and approve", "accept suggestions", "auto-approve"). Otherwise, report the waiting state and wait for user decision.

**For `waiting_for: plan_approval`:**
1. Report: "Plan ready for review: [full path to plan file]"
2. If user requested auto-approve: proceed to approval
3. Otherwise: Ask "Approve this plan?" and wait
4. On approval, run the approval CLI (do NOT manually edit `_overview.md` or `_signal.json`):
   ```bash
   samocode approve --config [PATH_TO_.SAMOCODE] --session [SESSION_NAME]
   ```
   The service validates the pending gate, atomically advances the overview (Phase → implementation, Blocked → no), and consumes the pending signal. Non-zero exit means approval was rejected — show the error and ask the user how to proceed.
5. Then restart samocode

**For `waiting_for: qa_answers`:**
1. Report: "Q&A ready: [full path to _qa.md]" (includes suggestions)
2. If user requested to accept suggestions: fill in suggested answers
3. Otherwise: Wait for user to provide/confirm answers
4. Update `_qa.md` with answers
5. Then restart samocode

## Read-Only Gate Check

To verify the final-polish gate state without running the orchestrator:
```bash
samocode check final-polish --config [PATH_TO_.SAMOCODE] --session [SESSION_NAME]
```
- Exit 0: clean — the `pr-readiness -> done` gate would pass
- Exit 1: errors printed to stderr, one per line, each naming the expected value
- No lock, no writes, no `Phase`/`Blocked` precondition — safe mid-session

Quality and pr-readiness agents run this command as a self-check after writing
reports or the ledger and fix vocabulary drift before routing. If you see a
quality or pr-readiness agent loop unexpectedly, use this command to inspect
the current gate state without waiting for the next iteration.

## Monitoring Testing Escalation

Testing can auto-escalate one profile rung on an `environment` block. This is a normal
`continue`, not a stop condition — the next iteration reruns testing on the stronger
profile. Recognize it in the Flow Log so you report it as progress, not a failure:

```
- [NNN @ MM-DD HH:MM] Escalation: testing strong (model/effort) -> max (model/effort); blocker: <reason>
```

- `Blocked` stays `no`, `Last Action` reads "Testing blocked on environment; escalating
  strong -> max", `Next` reads "Escalated testing attempt N/M".
- The per-iteration routing log line for the rerun carries `source=escalation` and
  `escalated_from=<profile>`; `_signal_history.jsonl` gains a `status=escalation` row.
- One attempt per phase entry. If the escalated attempt blocks again on the environment,
  the session stops as a normal block — handle it like any `blocked` state, do not
  restart hoping for another escalation.

## Required `.samocode` File

Every project using samocode MUST have a `.samocode` file in its root:

```
MAIN_REPO=~/path/to/main/repo
WORKTREES=~/path/to/worktrees/
SESSIONS=~/path/to/_sessions/
```

**All three keys are REQUIRED:**
- `MAIN_REPO`: The main working directory (where the child agent runs)
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
Phase: [init|investigation|requirements|planning|implementation|testing|quality|pr-readiness|done]
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
3. **Timeout**: Default is 30 min. Increase provider timeout env var (`CLAUDE_TIMEOUT` or `CODEX_TIMEOUT`) if iterations need more
4. **Missing global config**: Run `samocode install`; legacy mode still runs but cannot use semantic profile routing
5. **Invalid config/profile**: Fix the reported TOML path/profile. Validation fails before any provider call; do not bypass it with model env vars
6. **Provider unavailable**: Install the selected CLI or choose a registered provider with `--provider`; a process never falls back to another provider mid-run
7. **Lifecycle preflight/workflow_error**: Stop before provider execution. Use the
   read-only `samocode recover final-polish ... --check` only for the reported
   final-polish provenance class; never repair history or phase fields manually

## Debugging Samocode Bugs

If samocode exhibits bugs or weird behavior (loops, wrong decisions, missing steps, etc.):

1. **Analyze the issue:**
   - Check worker output logs for errors
   - Read `_overview.md` to see what went wrong
   - Check if workflow.md instructions are unclear
   - Check whether the parent session loaded a stale installed skill before the
     current worker version
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
→ Run: samocode run --config ~/code/hvac-voice-agent/.samocode --session voice-agent
→ Monitor iterations, report progress

User: "Continue the samocode session"
→ Find session name from context or ask user
→ Find .samocode file path
→ Run: samocode run --config [CONFIG_PATH] --session [SESSION_NAME]
→ Monitor iterations, report progress
```

**Remember:** You run `samocode run`; the Python worker resolves the execution target and runs the selected provider (Claude or Codex). You do NOT run phase agents or select their concrete models yourself.
