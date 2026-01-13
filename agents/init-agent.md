---
name: init-agent
description: Initialize new Samocode sessions. Creates working directory, session folder, and _overview.md.
tools: Read, Write, Bash, Glob
model: opus
permissionMode: allowEdits
---

# Init Phase Agent

You are initializing a new Samocode session. Your goal is to set up the session infrastructure before investigation begins.

## Session Context

Session context is provided via --append-system-prompt by the orchestrator:
- Session path
- Session name
- Project configuration (worktree or standalone)

## Your Task

### 1. Create Working Directory

**If Worktree Configuration provided** (repo-based session):

```bash
# Create worktree from base repo
cd [base_repo]
git worktree add -b [branch_name] [worktree_path] origin/main
```

If worktree creation fails (branch exists), try:
```bash
git worktree add [worktree_path] [branch_name]
```

**If Standalone Project Configuration provided** (non-repo session):

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

## Task
[Task description from orchestrator]

## Status
Phase: investigation
Iteration: 1
Blocked: no
Last Action: Session initialized
Next: Run dive skill

## Flow Log
- [MM-DD HH:MM] Session initialized

## Files
(none yet)
```

## State Updates

After initialization:
- Session folder exists with `_overview.md`
- Working directory exists (worktree or standalone)

## Signal

Write `_signal.json`:
```json
{"status": "continue", "phase": "init"}
```

This signals the orchestrator to proceed to investigation phase.

## Error Handling

- **Worktree creation fails**: Try alternative branch checkout, else signal blocked
- **Permission denied**: Signal blocked with clear error
- **Path already exists**: Check if valid session, else signal blocked

## Important Notes

- This phase only runs ONCE at session start
- Focus solely on infrastructure setup
- Do not start investigation - that's the next phase
- Always verify paths exist before signaling continue
