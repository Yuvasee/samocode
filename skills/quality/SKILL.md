---
name: quality
description: Code quality analysis through cleanup analysis and multi-perspective reviews.
---

# Quality

Analyze code quality through different lenses: cleanup analysis for technical debt and multi-perspective reviews for holistic assessment.

## Requirements

- None (both actions work independently)

## Actions

### cleanup - Code Quality Analysis

Analyze changed code for quality issues and technical debt.

**Scope:** $ARGUMENTS (or current branch vs main if not specified)

#### Steps

1. **Get current time:**
   - Run `date '+%m-%d-%H:%M'` for filename timestamp
   - Run `date '+%H:%M'` for flow log entries

2. **Determine scope:**
   - If $ARGUMENTS specifies files/scope, use that
   - Otherwise: `git diff main...HEAD` from current directory

3. **Analyze for issues:**
   - Dead code - exported but never used functions/classes/constants
   - Duplicate code - similar logic in multiple places
   - Unclear patterns - confusing imports, magic numbers, missing comments
   - Inconsistencies - same thing done differently in different files
   - Type safety - missing or incorrect type hints
   - Complexity - overly complex patterns that could be simplified
   - Documentation - missing docstrings, unclear parameter purposes
   - TODOs/FIXMEs - should they be tracked issues instead?

4. **Create cleanup report:**
   - File: `[timestamp]-cleanup.md` in current directory

   ```markdown
   # Cleanup Analysis
   Date: [timestamp]
   Scope: [what was analyzed]

   ## Issues Found

   ### 1. [Issue title]
   **Type:** [Dead code/Duplicate/Unclear/etc.]
   **Location:** [file:line]
   **Priority:** [High/Medium/Low]
   **Breaking:** [Yes/No]

   **Problem:**
   [Description]

   **Options:**
   1. [Option with pros/cons]
   2. [Option with pros/cons]

   **Recommendation:** [Which option and why]

   [Repeat for each issue]

   ## Summary

   | Issue | Recommendation | Priority | Breaking? |
   |-------|---------------|----------|-----------|
   | ...   | ...           | ...      | ...       |

   ## Implementation Phases

   ### Phase 1: High Priority
   - [ ] [Action item]

   ### Phase 2: Medium Priority
   - [ ] [Action item]

   ### Phase 3: Low Priority
   - [ ] [Action item]
   ```

5. **Report back:** Summary of issues found by priority

---

### multi-review - Multi-Perspective Review

Review changes using three different perspectives: Future Maintainer, System Architect, and Product/User Advocate.

**Target branch:** $ARGUMENTS (defaults to current branch if not specified)

#### Setup

Before spawning sub-agents, set up the review environment:

1. **If `$ARGUMENTS` is empty or not provided**:
   - Use current directory, no setup needed
   - Working directory for sub-agents: current directory

2. **If `$ARGUMENTS` is specified**:

   ```bash
   # Fetch to ensure remote branches are available
   git fetch origin

   # Clean up any existing worktree from previous reviews
   WORKTREE_PATH="../review-$ARGUMENTS"
   git worktree remove --force "$WORKTREE_PATH" 2>/dev/null || true

   # Try local branch first, then remote
   if git show-ref --verify --quiet "refs/heads/$ARGUMENTS"; then
     git worktree add "$WORKTREE_PATH" "$ARGUMENTS"
   elif git show-ref --verify --quiet "refs/remotes/origin/$ARGUMENTS"; then
     git worktree add "$WORKTREE_PATH" "origin/$ARGUMENTS"
   else
     echo "Error: Branch '$ARGUMENTS' not found locally or on origin"
     # Stop here - do not spawn sub-agents
   fi
   ```

   - Working directory for sub-agents: the worktree path

#### Sub-Agent Instructions

Spawn three sub-agents **in parallel** with the following roles. Give each agent:

- The **review directory path** determined above (current directory or worktree path)
- Instructions to run the git diff command **from that directory** using `cd <path> && git diff main...HEAD`

**Critical**: When a worktree is used, agents MUST `cd` into the worktree directory before running git commands.

---

**Agent 1: Future Maintainer**

Role: A developer who inherits this code in 6 months with no context.

Instructions:

1. Run `cd <REVIEW_DIRECTORY> && git diff main...HEAD` to get the changes (the root agent will provide the exact path)
2. Review the diff as if you're seeing this codebase for the first time, looking for:
   - Unclear or misleading naming (variables, functions, files)
   - Missing or outdated comments/documentation
   - Complex logic that's hard to follow without explanation
   - Magic numbers or unexplained constants
   - Implicit assumptions that aren't documented
   - Functions doing too many things
   - Cognitive load — how much context do I need to hold in my head?
   - "Why was this done this way?" moments with no answer

Output format: List each concern with severity (blocking/important/nice-to-have), file location, and what would make it clearer.

---

**Agent 2: System Architect**

Role: Guardian of system-wide design and long-term technical health.

Instructions:

1. Run `cd <REVIEW_DIRECTORY> && git diff main...HEAD` to get the changes (the root agent will provide the exact path)
2. Review the diff from an architectural perspective, looking for:
   - Coupling issues — does this create unwanted dependencies?
   - Boundary violations — is code in the right layer/module?
   - Pattern consistency — does this follow or break established conventions?
   - Scalability concerns — will this approach hold up under growth?
   - Abstraction quality — too much, too little, or at wrong level?
   - Single responsibility — are concerns properly separated?
   - Extensibility — how hard will it be to modify this later?
   - Error handling strategy — consistent with the rest of the system?

Output format: List each concern with severity (blocking/important/nice-to-have), file location, and architectural recommendation.

---

**Agent 3: Product/User Advocate**

Role: Representative of the end user and product goals.

Instructions:

1. Run `cd <REVIEW_DIRECTORY> && git diff main...HEAD` to get the changes (the root agent will provide the exact path)
2. Review the diff from a product perspective, looking for:
   - Does this actually solve the intended user problem?
   - Edge cases in user flows that aren't handled
   - Error messages — are they helpful to users or developer-speak?
   - UX implications of technical decisions
   - Accessibility concerns
   - Data handling — does it respect user expectations?
   - Failure modes — what does the user experience when things go wrong?
   - Missing validation that could let users get into bad states

Output format: List each concern with severity (blocking/important/nice-to-have), file location, and user impact.

---

#### Synthesis

After receiving all three sub-agent reviews:

1. **Deduplicate**: Merge overlapping concerns raised by multiple agents (note when multiple perspectives flagged the same issue — this increases priority)
2. **Categorize**: Group findings into:
   - 🚫 **Blocking** — must fix before merge
   - ⚠️ **Important** — should fix, real risk or significant improvement
   - 💡 **Nice-to-have** — minor improvements, style, polish
3. **Preserve strengths**: Note anything explicitly good that should NOT be changed
4. **Create action plan**: Numbered list of specific fixes, ordered by priority
5. Create a short guide for human reviewer on what this PR is about, what sequence to review things in, and any context needed (keep it concise).

#### Final Output Format

Present the synthesized review as:

1. Summary (2-3 sentences on overall change quality)
2. Human review guide
3. Blocking issues (if any)
4. Important issues
5. Nice-to-have improvements
6. What's good / don't change

#### Cleanup

After all sub-agents complete and synthesis is done:

```bash
# Only if a worktree was created
git worktree remove --force "../review-$ARGUMENTS" 2>/dev/null || true
```

#### Important

⛔ **Do NOT start fixing anything.** After presenting the review, STOP and wait for explicit confirmation on which items to fix. Ask which issues to address before making any changes.
