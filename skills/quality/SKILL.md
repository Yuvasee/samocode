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

1. **Determine scope:**
   - If $ARGUMENTS specifies files/scope, use that
   - Otherwise: `git diff main...HEAD` from current directory

2. **Analyze for issues:**
   - Dead code - exported but never used functions/classes/constants
   - Duplicate code - similar logic in multiple places
   - Unclear patterns - confusing imports, magic numbers, missing comments
   - Inconsistencies - same thing done differently in different files
   - Type safety - missing or incorrect type hints
   - Complexity - overly complex patterns that could be simplified
   - Documentation - missing docstrings, unclear parameter purposes
   - TODOs/FIXMEs - should they be tracked issues instead?

3. **Create cleanup report:**
   - File: `[TIMESTAMP_FILE]-cleanup.md` in current directory

   ```markdown
   # Cleanup Analysis
   Date: [TIMESTAMP_LOG]
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

4. **Report back:** Summary of issues found by priority

---

### multi-review - Multi-Perspective Review

Review changes using five different perspectives: Future Maintainer, System Architect, Product/User Advocate, and two external reviewers (Codex and Gemini).

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

Spawn three Claude sub-agents **in parallel** (via Task tool), plus Codex and Gemini reviews (via Bash tool). All five run concurrently. Give each Claude agent:

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

**Agent 4: External Reviewer (Codex)**

Role: Independent second opinion from a different AI model (GPT-5.2), combining maintainability, architecture, and UX perspectives.

This agent runs via Bash tool (not Task tool) in parallel with the other three.

Instructions (for root agent):

1. Check if Codex is available:
   ```bash
   which codex >/dev/null 2>&1 || echo "CODEX_NOT_INSTALLED"
   ```

2. If not installed, skip and note in synthesis: "Codex review skipped - not installed"

3. If available, run (adjust based on input type):

   **IMPORTANT:** Always use `2>&1` (not `2>/dev/null`) to capture both stdout and stderr. Codex prints the review to stdout - the `-o` flag only saves the final message. Always `cd` into a git repo directory before running codex so `gh` commands work.

   **If $ARGUMENTS is a GitHub PR URL:**
   ```bash
   cd <REVIEW_DIRECTORY> && \
   timeout 900 codex exec --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox \
     "You are a senior engineer reviewing a pull request.

   PR: <PR_URL>

   Use gh CLI to fetch the PR diff, then review from three angles:

   1. MAINTAINABILITY: Will a new developer understand this in 6 months? Look for:
      - Unclear naming or confusing logic
      - Missing context or documentation
      - Magic numbers, implicit assumptions
      - Cognitive load issues

   2. ARCHITECTURE: Does this fit the system well? Look for:
      - Coupling and dependency issues
      - Pattern consistency
      - Scalability concerns
      - Abstraction quality (too much/little)
      - Technical debt being introduced

   3. USER EXPERIENCE: Does this serve users well? Look for:
      - Edge cases in user flows
      - Error messages - helpful or developer-speak?
      - Failure modes - what happens when things break?
      - Accessibility concerns

   Format: List each concern with severity (blocking/important/nice-to-have), file location, and recommendation." 2>&1
   ```

   **If reviewing local branch (git diff):**
   ```bash
   cd <REVIEW_DIRECTORY> && \
   timeout 900 codex exec --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox \
     "You are a senior engineer reviewing code changes.

   Run 'git diff origin/main...HEAD' to get the diff, then review from three angles:

   1. MAINTAINABILITY: Will a new developer understand this in 6 months? Look for:
      - Unclear naming or confusing logic
      - Missing context or documentation
      - Magic numbers, implicit assumptions
      - Cognitive load issues

   2. ARCHITECTURE: Does this fit the system well? Look for:
      - Coupling and dependency issues
      - Pattern consistency
      - Scalability concerns
      - Abstraction quality (too much/little)
      - Technical debt being introduced

   3. USER EXPERIENCE: Does this serve users well? Look for:
      - Edge cases in user flows
      - Error messages - helpful or developer-speak?
      - Failure modes - what happens when things break?
      - Accessibility concerns

   Format: List each concern with severity (blocking/important/nice-to-have), file location, and recommendation." 2>&1
   ```

4. Include Codex output in synthesis alongside other reviews

---

**Agent 5: External Reviewer (Gemini)**

Role: Independent second opinion from Google's Gemini model, combining maintainability, architecture, and UX perspectives.

This agent runs via Bash tool (not Task tool) in parallel with all other agents.

Instructions (for root agent):

1. Check if Gemini CLI is available:
   ```bash
   which gemini >/dev/null 2>&1 || echo "GEMINI_NOT_INSTALLED"
   ```

2. If not installed, skip and note in synthesis: "Gemini review skipped - not installed"

3. If available, run (adjust based on input type):

   **IMPORTANT:** Always use `2>&1` to capture both stdout and stderr. Always `cd` into a git repo directory before running gemini so git commands work.

   **If $ARGUMENTS is a GitHub PR URL:**
   ```bash
   cd <REVIEW_DIRECTORY> && \
   timeout 900 gemini -p "You are a senior engineer reviewing a pull request.

   PR: <PR_URL>

   Use gh CLI to fetch the PR diff, then review from three angles:

   1. MAINTAINABILITY: Will a new developer understand this in 6 months? Look for:
      - Unclear naming or confusing logic
      - Missing context or documentation
      - Magic numbers, implicit assumptions
      - Cognitive load issues

   2. ARCHITECTURE: Does this fit the system well? Look for:
      - Coupling and dependency issues
      - Pattern consistency
      - Scalability concerns
      - Abstraction quality (too much/little)
      - Technical debt being introduced

   3. USER EXPERIENCE: Does this serve users well? Look for:
      - Edge cases in user flows
      - Error messages - helpful or developer-speak?
      - Failure modes - what happens when things break?
      - Accessibility concerns

   Format: List each concern with severity (blocking/important/nice-to-have), file location, and recommendation." --yolo 2>&1
   ```

   **If reviewing local branch (git diff):**
   ```bash
   cd <REVIEW_DIRECTORY> && \
   timeout 900 gemini -p "You are a senior engineer reviewing code changes.

   Run 'git diff origin/main...HEAD' to get the diff, then review from three angles:

   1. MAINTAINABILITY: Will a new developer understand this in 6 months? Look for:
      - Unclear naming or confusing logic
      - Missing context or documentation
      - Magic numbers, implicit assumptions
      - Cognitive load issues

   2. ARCHITECTURE: Does this fit the system well? Look for:
      - Coupling and dependency issues
      - Pattern consistency
      - Scalability concerns
      - Abstraction quality (too much/little)
      - Technical debt being introduced

   3. USER EXPERIENCE: Does this serve users well? Look for:
      - Edge cases in user flows
      - Error messages - helpful or developer-speak?
      - Failure modes - what happens when things break?
      - Accessibility concerns

   Format: List each concern with severity (blocking/important/nice-to-have), file location, and recommendation." --yolo 2>&1
   ```

4. Include Gemini output in synthesis alongside other reviews

---

#### Synthesis

After receiving all five reviews (three Claude sub-agents + Codex + Gemini):

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
