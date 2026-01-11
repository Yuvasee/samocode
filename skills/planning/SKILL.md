---
name: planning
description: Create implementation plans with phase management.
---

# Planning

Creates detailed implementation plans with phases, stored within the session folder.

## Requirements

- Active session must exist (session path in working memory)
- If no active session: **STOP and ask user** for session path

## Execution

**Session path:** [SESSION_PATH from working memory]
**Context:** $ARGUMENTS

### Steps

1. **Get current time:**
   - Run `date '+%m-%d-%H:%M'` for filename timestamp
   - Run `date '+%H:%M'` for flow log entries

2. **Gather context:**
   - Read recent dive/task documents from session
   - Review project documentation if available
   - Understand current codebase state

3. **Create plan file:**
   - Location: `[SESSION_PATH]/[MM-DD-HH:mm]-plan-[plan-slug].md`

   Structure:
   ```markdown
   # Plan: [Title]
   Created: [timestamp]

   ## Task Definition
   [Concise summary]

   ## Requirements
   - [ ] [Requirement 1]
   - [ ] [Requirement 2]

   ## Context
   [Key files, current state, constraints]

   ## Implementation Phases

   ### Phase 1: [Name]
   - [ ] [Step]
   - [ ] [Step]
   - [ ] Run pyright/ruff or tsc - fix errors

   ### Phase 2: [Name]
   - [ ] [Step]
   - [ ] [Step]
   - [ ] Run pyright/ruff or tsc - fix errors

   ### Phase 3: Testing
   - [ ] [Test case]
   - [ ] Final checks

   ## Notes
   [Important context from task definition]
   ```

4. **Update session:**
   - Edit `[SESSION_PATH]/_overview.md`:
     - Add to Flow Log: `- [TIMESTAMP_LOG] Plan created -> [filename].md`
     - Add to Plans: `- [filename].md - [brief description]`
     - Add to Files: `- [filename].md - Plan: [brief description]`
   - Commit (if git repo): `cd [SESSION_DIR] && git add . && git commit -m "Plan: [title]"`

5. **Report back:** Plan summary and file location
