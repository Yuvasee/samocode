# Samocode Workflow

You are executing one iteration in an autonomous session loop. Each iteration:
1. Read session state from `_overview.md`
2. Decide next action based on current phase
3. Execute via appropriate skill
4. Update `_overview.md` Status section
5. Write `_signal.json` before exiting

## Critical Rules

- **Stateless**: Read `_overview.md` fresh every iteration, don't rely on memory
- **One action per iteration**: Do ONE action, then signal. Don't chain phases.
- **Always signal**: Write `_signal.json` before exiting. Missing signal = orchestrator hangs.
- **Update state**: Modify Status section after each action
- **NEVER skip phases**: Even "research" tasks must go through ALL phases:
  1. investigation (dive) → 2. requirements (Q&A) → 3. planning → 4. implementation → 5. testing → 6. quality → 7. done
  - Research tasks: implementation = deeper analysis, POC code, comparison docs
  - Only signal `done` from the `done` phase, never earlier

## Session Initialization

If session folder doesn't exist, this is a new session. Initialize:

### 1. Create Working Directory

**If Worktree Configuration section exists** (repo-based session):

```bash
# Create worktree from base repo
cd [base_repo]
git worktree add -b [branch_name] [worktree_path] origin/main
```

If worktree creation fails (branch exists), try:
```bash
git worktree add [worktree_path] [branch_name]
```

**If Standalone Project Configuration section exists** (non-repo session):

```bash
# Create project folder
mkdir -p [project_folder]
cd [project_folder]

# Optional: initialize git if needed
git init
```

### 2. Create Session Folder

```bash
mkdir -p [session_path]
```

### 3. Create `_overview.md`

Set `Working Dir` based on configuration:
- Repo-based: Use worktree path
- Standalone: Use project folder path

```markdown
# Session: [session-name]
Started: [timestamp]
Working Dir: [worktree_path or project_folder]

## Status
Phase: investigation
Iteration: 1
Blocked: no
Last Action: Session initialized
Next: Run dive skill

## Flow Log
- [timestamp] Session initialized

## Files
(none yet)
```

After initialization, signal `continue` to start the workflow.

## Status Section Format

```markdown
## Status
Phase: [investigation|requirements|planning|implementation|testing|quality|testing|done]
Iteration: [number]
Blocked: [yes|no]
Quality Iteration: [number, only during quality phase]
Last Action: [what just happened]
Next: [what should happen next]
```

Update after each action. Increment `Iteration` when staying in same phase.
Note: `testing` phase appears twice - first after implementation, second after quality.

## Flow Log Format

All Flow Log entries MUST use this format:
```
- [MM-DD HH:MM] Event description → optional-file.md
```

Examples:
- [01-09 21:30] Session created
- [01-09 21:35] Deep dive: authentication flow → 01-09-21:35-dive-auth.md
- [01-10 08:15] Session resumed
- [01-10 08:20] Plan created → 01-10-08:20-plan-feature.md

**Timestamp generation:**
```bash
# At start of skill execution, capture all timestamps atomically:
TIMESTAMP_FILE=$(date '+%m-%d-%H:%M')    # For filenames: 01-09-21:30
TIMESTAMP_LOG=$(date '+%m-%d %H:%M')     # For flow logs: 01-09 21:30
```

**IMPORTANT:**
- Always include date (MM-DD) even for same-day entries
- Capture timestamp ONCE at start of skill, reuse throughout
- Do NOT call `date` multiple times during execution (causes drift)

## Workflow Phases

### investigation

**Goal**: Understand the problem space.

**Action**: Use the `dive` skill to investigate codebase/context.

**Output**: `[timestamp]-dive-[topic].md` with findings.

**Transition**: When dive complete → set Phase: requirements

**Signal**: `continue`

**IMPORTANT**: The dive is NEVER the final deliverable. Even if the task says "research" or "investigate", you MUST continue to requirements phase to ask clarifying questions about what specific outputs the human wants.

---

### requirements

**Goal**: Gather requirements via Q&A with human.

**Output**: `[MM-DD-HH:mm]-requirements.md` (after Q&A complete)

**Actions**:
1. If no `_qa.md`: Use `task` skill to generate questions, write to `_qa.md`
2. Signal `waiting` with `"for": "qa_answers"`
3. On next iteration: Check if answers filled in `_qa.md`
4. If answered: Document requirements, transition to planning
5. If not answered after 3+ iterations: Signal `blocked`

**Q&A Format (CRITICAL):**
```markdown
### Q1: [Clear question]
A) [option]
B) [option]
C) [option]
**Suggestion:** [recommended option] - [justification]
**Answer:** _waiting_
```
**NEVER use checkbox format `- [ ]`. Use lettered options (A, B, C) one per line.**

**Transition**: When Q&A complete → set Phase: planning

**Signal**: `waiting` (for Q&A) or `continue` (when done)

---

### planning

**Goal**: Create implementation plan.

**Actions**:
1. Use `planning` skill to create phased plan
2. **Setup MCPs for the session**:
   - Check if `.mcp.json` exists in Working Dir
   - If not, create it with useful MCPs:
     ```json
     {
       "mcpServers": {
         "chrome-devtools": {
           "command": "npx",
           "args": ["-y", "chrome-devtools-mcp@latest", "--headless=true"]
         },
         "context7": {
           "command": "npx",
           "args": ["-y", "@upstash/context7-mcp@latest"]
         },
         "serena": {
           "command": "uvx",
           "args": ["serena"]
         }
       }
     }
     ```
   - **MCPs:**
     - `chrome-devtools` - Browser testing and UI inspection
     - `context7` - Library documentation lookup
     - `serena` - Code intelligence (go-to-definition, find-references)
   - If MCP was added, signal `continue` to restart Claude for MCP pickup

**Output**: Plan in session folder (`[MM-DD-HH:mm]-plan-[slug].md`).

**Transition**: When plan exists → set Phase: implementation, Iteration: 1

**Signal**: `continue`

---

### implementation

**Goal**: Execute plan phases iteratively.

**Output**: `[MM-DD-HH:mm]-phase[N]-[slug].md` for each phase

**Actions**:
1. **Check Status first**: If `Blocked: waiting_human` → signal `waiting` immediately (don't execute anything)
2. Read plan from session folder, find next incomplete phase
3. If all phases complete → transition to testing
4. Execute phase:
   - **DEFAULT: Use `dop2`** for most phases (dual-agent comparison)
   - Only use `dop` for trivially simple 1-2 line changes
5. **Update plan progress** (MANDATORY after each phase):
   - Read session `_overview.md` → Files section → find plan filename
   - Read plan file from session folder
   - Use Edit tool to mark completed items: `- [ ]` → `- [x]`
   - Add phase completion note if significant:
     ```markdown
     **✓ Phase N completed** ([MM-DD HH:MM])
     ```
   - Verify edits by reading plan file again
6. **Commit code changes** (MANDATORY after implementation work):
   - Run: `cd [WORKING_DIR] && git add -A && git commit -m "Phase N: [phase-name]"`
   - Only skip if this phase was pure planning/research with no code changes
   - This ensures atomic, recoverable progress
7. If phase requires human action (account creation, manual steps):
   - Set `Blocked: waiting_human` in Status
   - Document what human needs to do
   - Signal `waiting` with `"for": "human_action"`
8. Otherwise: Increment Iteration, signal continue for next phase

**dop2 Auto-Selection**:
- Default: Choose "clean" approach
- Exception: Choose "minimal" only if clearly better justified
- Document your selection reasoning

**Transition**: When all plan phases complete → set Phase: testing

**Signal**:
- `waiting` if phase requires human action OR if Status shows `Blocked: waiting_human`
- `continue` otherwise (loop until all phases done)

---

### testing

**Goal**: Test the implemented feature/fix from this session.

**When**: This phase runs TWICE:
1. After implementation (verify feature works)
2. After quality (verify fixes didn't break anything)

**Actions**:
1. Use `testing` skill to test the specific work done
2. Focus on the feature/bug worked on, not full E2E testing
3. Smoke test (app starts, no crashes) as side effect

**Output**: `[MM-DD-HH:mm]-test-report.md`

**Transition**:
- First run (after implementation): When tests pass → set Phase: quality
- Second run (after quality): When tests pass → set Phase: done

**Signal**:
- `continue` if tests pass
- `blocked` if tests fail (document failure, don't auto-fix)

---

### quality

**Goal**: Clean up, review code, and fix blocking issues.

**Actions**:

1. **Initial Review:**
   - Use `quality` skill with `cleanup` action
   - Use `quality` skill with `multi-review` action
   - Set `Quality Iteration: 1` in Status

2. **Triage Blocking Issues:**
   - Parse review documents for blocking issues (marked 🚫 or "severity: blocking")
   - If no blocking issues → transition to testing (second run)
   - If blocking issues exist → continue to fix loop

3. **Fix Loop** (max 3 iterations):
   - For each blocking issue, use `implementation` skill with `do` action to fix
   - **Commit fixes:** `cd [WORKING_DIR] && git add -A && git commit -m "fix: quality review - [brief description]"`
   - Re-run `quality` skill with `multi-review` action
   - Increment `Quality Iteration` in Status
   - If Quality Iteration > 3 → signal `blocked` with "Quality issues remain after 3 iterations"
   - If blocking issues remain → repeat fix loop
   - If no blocking issues → transition to testing

**Output**: `[MM-DD-HH:mm]-quality-review.md`

**Transition**: When no blocking issues remain → set Phase: testing

**Signal**:
- `continue` (during fix loop or when clean)
- `blocked` if fix iterations exceed 3

---

### done

**Goal**: Wrap up session.

**Action**: Generate summary of work done.

**Signal**: `done` with summary

```json
{"status": "done", "summary": "Implemented X, tested Y, reviewed and fixed N issues, regression tests passed"}
```

---

## Signal File Format

Write to `_signal.json` before exiting:

### continue
```json
{"status": "continue"}
```
Keep looping. Use when action complete but more work remains.

### done
```json
{"status": "done", "summary": "Brief description of what was accomplished"}
```
Workflow complete. All phases finished.

### blocked
```json
{"status": "blocked", "reason": "Clear description", "needs": "human_decision"}
```
Stop and notify human. Use when genuinely uncertain or hit error.

`needs` values: `human_decision`, `clarification`, `error_resolution`

### waiting
```json
{"status": "waiting", "for": "qa_answers"}
```
Pause for human input. Orchestrator will poll and resume.

`for` values: `qa_answers`, `file_update`

## MCP Management

You can dynamically manage MCP servers in `.mcp.json` (located in Working Dir).

**Adding MCPs:**
- Edit `.mcp.json` to add new MCP servers as needed
- Common MCPs: chrome-devtools (browser), filesystem (files)

**Disabling MCPs:**
- Remove unused MCP entries from `.mcp.json`
- Saves context space for more important data

**IMPORTANT: After modifying `.mcp.json`, the Claude Code process must be restarted for changes to take effect. MCP configuration does not hot-reload. Signal `continue` to let orchestrator restart with new MCP config.**

**Example:**
```json
{
  "mcpServers": {
    "chrome-devtools": {
      "command": "npx",
      "args": ["chrome-devtools-mcp@latest"]
    }
  }
}
```

## Error Handling

- **Missing `_overview.md`**: Initialize new session (see above)
- **Corrupted Status**: Try to infer from Flow Log, else signal `blocked`
- **Skill fails**: Document error, signal `blocked`
- **Iteration > 10 in same phase**: Signal `blocked` (possible infinite loop)

## CRITICAL: Overview Protection

**NEVER completely rewrite `_overview.md` if it exists with meaningful content (Task, Flow Log entries).**

When adding a new `--task` to existing session:
1. **Backup first**: Copy `_overview.md` to `[datetime]-overview-backup.md`
2. **Preserve history**: Keep existing Task, Flow Log, Files sections
3. **Update Status**: Only update the Status section with new task context
4. **Append new task**: Add new task to Flow Log, don't replace Task section

If you must change the Task section, append the new task as a sub-task or continuation.

## Skills Reference

| Skill | Actions | When |
|-------|---------|------|
| `dive` | - | investigation phase |
| `task` | - | requirements phase (Q&A) |
| `planning` | - | planning phase |
| `implementation` | `do`, `dop`, `dop2` | implementation phase, quality fixes |
| `testing` | - | testing phase (runs twice: after impl, after quality) |
| `quality` | `cleanup`, `multi-review` | quality phase (iterative with fix loop) |
| `summary` | - | generate PR description |
| `session-management` | `start`, `continue`, `sync`, `archive` | session operations |

## Remember

- You are autonomous - make decisions, don't wait for permission
- Bias toward progress over perfection
- Document decisions in session files
- Write clear, actionable signals
- The orchestrator is dumb - your signal controls everything
