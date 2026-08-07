---
name: task-definition
description: Interactive task definition with Q&A and documentation.
---

# Task Definition

Turn a raw task into a documented spec: grill the user until you reach a shared
understanding, then record the decisions.

## Requirements

- Active session must exist (session path in working memory)
- If no active session: **STOP and ask user** for session path

## Execution

**Session path:** [SESSION_PATH from working memory]
**Task:** $ARGUMENTS

### 1. Analyze

- Review recent dive/task documents in the session for context
- Check project docs for related documentation
- Understand scope and implications
- **Facts are YOUR job, never the user's.** Anything you can look up (filesystem,
  code, tools) you find yourself — dispatch a sub-agent for it. Never ask the user
  for something you could look up. Don't block on a lookup: a running exploration
  is an unsettled prerequisite, so only the questions downstream of it wait for the
  sub-agent — ask the rest of the frontier now.

### 2. Grill the user (interactive Q&A)

Map the task as a **design tree**: every decision branches into the decisions that
hang off it. Work the tree in **rounds**.

The **frontier** is every decision whose prerequisites are already settled — the
questions you can ask _now_ without guessing at answers you haven't heard yet. Ask
the whole frontier in one round. A question whose answer depends on another question
still open in this round belongs to a _later_ round, not this one.

Format each question:

```
❓ **Q1** — **<question title>**: <question body, may span paragraphs and include options>

➡️ <your recommended answer + short justification>
```

- Every question MUST carry a recommended answer with a justification.
- Only ask about **judgments that are the user's to make.** Facts you look up
  yourself (step 1) — never put them to the user as a question.
- Each round the user's answers reshape the tree — settled decisions push the
  frontier outward and unblock questions that depended on them. Recompute the
  frontier and ask the next round.
- DO NOT start any code edits during grilling — wait for explicit instruction.

The Q&A is **done when the frontier is empty**: every branch of the design tree
visited, nothing left silently assumed. Say: "No more questions, we can move on!"
and wait for the user to confirm shared understanding before documenting.

### 3. Document the task (after Q&A complete)

Create file: `[SESSION_PATH]/[TIMESTAMP_FILE]-task-[task-slug].md`

```markdown
# Task: [title]
Date: [TIMESTAMP_LOG]

## Description
[What needs to be accomplished]

## Requirements
[Bullet list]

## Clarifications

### [Topic]
**Q:** [question]
**A:** [answer/decision]
**Rationale:** [why]

## Edge Cases
[Things to watch for]

## Success Criteria
[How we know it's done]
```

### 4. Update session

- Edit `[SESSION_PATH]/_overview.md`:
  - Add to Flow Log: `- [TIMESTAMP_ITERATION] Task defined: [title] -> [filename].md`
  - Add to Files: `- [filename].md - Task: [title]`
- Commit (if git repo): `cd [SESSION_DIR] && git add . && git commit -m "Task: [title]"`

### 5. Suggest next steps

/create-plan, /do, /dop2
