---
name: session-management
description: Manage work sessions with creation, loading, syncing, and archiving capabilities.
---

# Session Management

Manages work sessions. Session paths must be explicitly provided or known from context.

## Session Path Resolution

**IMPORTANT:** Sessions do NOT have a default location. The session path must be:
1. Provided explicitly in arguments (e.g., `start ~/projects/my-project/_sessions/session-name`)
2. Already known from conversation context (active session in working memory)
3. Defined in project's `CLAUDE.md` under `SESSIONS` path

If session path cannot be determined: **STOP and ask the user** for the session location.

## Actions

Use `$ARGUMENTS` to specify action and parameters:
- `start [session-path/name]` - Create new session
- `continue [session-name-pattern]` - Load existing session
- `sync` - Sync current conversation to active session
- `archive [session-name-pattern]` - Archive a session

## Action: start

Create a new work session.

### Steps

1. **Resolve session location:**
   - If full path provided in arguments: use it
   - If only name provided: check project `CLAUDE.md` for `SESSIONS` path
   - If neither: **STOP and ask user** for session directory path

2. **Capture timestamps atomically:**
   ```bash
   TIMESTAMP_FILE=$(date '+%m-%d-%H:%M')    # For filenames: 01-09-21:30
   TIMESTAMP_LOG=$(date '+%m-%d %H:%M')     # For flow logs: 01-09 21:30
   TIMESTAMP_FULL=$(date '+%Y-%m-%d %H:%M') # For headers: 2026-01-09 21:30
   TIMESTAMP_FOLDER=$(date '+%y-%m-%d')     # For folder name: 26-01-09
   ```
   **CRITICAL:** Run all at once, reuse throughout. Do NOT call `date` again.

3. **Parse session name:**
   - Take session name from arguments after "start"
   - If empty, ERROR: "Session name required. Usage: start [session-name]"
   - Sanitize name (lowercase, replace spaces with hyphens)

4. **Create session folder:**
   - Path: `[SESSIONS_DIR]/[YY-MM-DD]-[session-name]/`
   - Use current date
   - If folder exists, ERROR: "Session already exists"

5. **Detect Working Dir:**
   - Check project `CLAUDE.md` for `MAIN_REPO` path
   - Or use git root: `git rev-parse --show-toplevel`
   - Or use current working directory
   - If unclear: leave as "TBD" and note to set it

6. **Create _overview.md:**
   ```markdown
   # Session: [session-name]
   Started: [TIMESTAMP_FULL]
   Working Dir: [detected or TBD]

   ## Status
   Phase: investigation
   Iteration: 1
   Blocked: no
   Last Action: Session created
   Next: Ready to work

   ## Flow Log
   - [TIMESTAMP_LOG] Session created

   ## Files
   (none yet)

   ## Plans
   (none yet)

   ## Linear Tasks
   (none yet)
   ```

7. **Commit (if sessions dir is a git repo):**
   - `cd [SESSIONS_DIR] && git add . && git commit -m "Start session: [session-name]"`

8. **Confirm to user:**
   ```
   Session created: [YY-MM-DD]-[session-name]
   Path: [full-path]

   IMPORTANT: This is now your active session. Remember this path for subsequent commands.

   Ready to work. Use /dive, /task, or /create-plan to continue.
   ```

IMPORTANT: After creating the session, keep the session path in your working memory for all subsequent session-aware commands.

## Action: continue

Load and continue working in an existing session.

### Steps

1. **Resolve session location:**
   - If full path provided: use it
   - Check project `CLAUDE.md` for `SESSIONS` path
   - If not found: **STOP and ask user** for sessions directory

2. **Find matching sessions:**
   - Search sessions directory for folders matching `*$ARGUMENTS*` (exclude archive/)
   - Sort by modification time (most recent first)

3. **Handle results:**
   - **No matches:** ERROR: "No sessions found matching '$ARGUMENTS'. Use start action to create one."
   - **One match:** Proceed to load
   - **Multiple matches:** List them with dates and ask user to specify

4. **Load session:**
   - Capture timestamp: `TIMESTAMP_LOG=$(date '+%m-%d %H:%M')`
   - Read `_overview.md` from the session folder
   - Add Flow Log entry: `- [TIMESTAMP_LOG] Session resumed`
   - Commit if git repo: `git add . && git commit -m "Resume session: [session-name]"`

5. **Present summary:**
   ```
   Session: [session-name]
   Path: [full-path]
   Working Dir: [from _overview.md]
   Started: [date]

   Recent Activity:
   [Last 5-10 Flow Log entries]

   Files: [count]
   [List with brief descriptions]

   Plans: [list if any]
   Linear Tasks: [list if any]

   ---
   Session loaded. Ready to continue.
   ```

IMPORTANT: After loading, keep the session path in your working memory for all subsequent session-aware commands.

## Action: sync

Ensure all work from this conversation is recorded in the active session.

### Steps

1. **Check for active session:**
   - If no session in working memory: ERROR: "No active session. Use continue action to load one first."

2. **Read current session state:**
   - Read `[SESSION_PATH]/_overview.md`

3. **Review conversation for unrecorded work:**
   - Code changes (files created, modified, deleted)
   - Decisions made
   - Problems solved
   - Discoveries about the codebase
   - Commits made
   - Blockers/TODOs remaining

4. **Update _overview.md:**
   - Add missing Flow Log entries
   - Add missing Files entries
   - Update other sections as needed

5. **Create detail files if warranted:**
   - Only for complex topics that need more than a log entry

6. **Commit (if git repo):**
   - `cd [SESSION_DIR] && git add . && git commit -m "Sync session: [session-name]"`

7. **Report:**
   - "Session synced: [what was added]"
   - Or: "Session already up to date."

## Action: archive

Archive a session.

### Session Resolution

1. **If arguments after "archive" are empty:**
   - Check for active session in working memory
   - If no active session: ERROR: "No active session and no session name provided."
   - Use active session path

2. **If arguments provided:**
   - Search sessions directory for folders matching `*$ARGUMENTS*` (exclude archive/)
   - **No matches:** ERROR: "No sessions found matching '$ARGUMENTS'"
   - **One match:** Confirm with user: "Archive session [name]? (y/n)"
   - **Multiple matches:** List and ask user to specify

### Archive Process

1. **Get session info:**
   - Read `[SESSION_PATH]/_overview.md`
   - Extract Working Dir line

2. **Create archive folder if needed:**
   ```bash
   mkdir -p [SESSIONS_DIR]/archive
   ```

3. **Remove worktree (if applicable):**
   - If Working Dir contains `/worktrees/`:
     ```bash
     git worktree remove [working_dir_path]
     ```
   - If removal fails (uncommitted changes), warn user and ask to proceed or abort
   - Note: Branch is preserved, only worktree removed

4. **Move session folder:**
   ```bash
   mv [SESSION_PATH] [SESSIONS_DIR]/archive/
   ```

5. **Commit changes (if git repo):**
   - `cd [SESSIONS_DIR] && git add . && git commit -m "Archive session: [session-name]"`

6. **Clear active session** (if archiving active session):
   - Remove from working memory

7. **Confirm to user:**
   ```
   Session archived: [session-name]
   Moved to: [archive-path]
   Worktree removed: [path] (branch preserved)

   Session closed.
   ```
