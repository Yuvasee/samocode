---
name: code-clarity
description: Review changed code for human comprehension cost — names, local reasoning, hidden state, working-memory load — and report findings with an impact/cost/risk assessment table. Review-only; never modifies code. Use when asked for a clarity review, readability review, comprehension-cost analysis of a diff/PR, or when running /code-clarity. Works in Claude Code and Codex.
---

# Code Clarity Review

Review changed code for **human comprehension cost**.

This is review-only. Do not modify code, produce patches, or refactor.
Planning or implementing fixes is OUT of scope for this skill — it ends at the
report. If the user wants fixes, that is a separate task with its own plan.

## Scope of the review

Unless the user names a different scope, review the current change set: the
branch diff against the default branch (`git diff origin/<default>...HEAD`), or
the PR diff if a PR is named. Read enough surrounding code to judge each finding
in context — but only changed code produces findings.

## Reader model

Assume the reader is a competent but busy engineer with little context and limited working memory.

The reader should not have to:

* remember unnecessary temporary facts;
* mentally execute code;
* repeatedly jump through files or functions;
* reconstruct hidden state or dependencies;
* reverse-engineer what generic names mean.

**The human reader's cognitive budget is the scarce resource.**

Prefer storing information in the code over storing it in the reader's head.

---

## Review for

### 1. Names that carry meaning

A name should reduce how much context the reader must remember.

Bad:

```ts
const data = await load();
const result = process(data);
return handle(result);
```

Clearer concepts:

```ts
const activePolicies = await loadActivePolicies();
const evaluation = evaluatePolicies(activePolicies);
return buildDecision(evaluation);
```

Report generic or misleading names when their meaning must be remembered from elsewhere.

Do not report short names whose meaning is obvious locally.

---

### 2. Local reasoning

Prefer code that can be understood from what is visible nearby.

Bad:

```ts
await prepare(user);
await execute(user);
```

if `execute()` only works because `prepare()` invisibly mutated fields on `user`.

Report hidden dependencies where understanding one operation requires discovering state or assumptions elsewhere.

Ask:

> What must the reader know that is not visible here?

---

### 3. Visible execution narrative

A high-level workflow should reveal the important steps.

Easy:

```text
approveWithdrawal
  → validateWithdrawal
  → reserveFunds
  → createTransaction
  → publishApproval
```

Hard:

```text
approve
  → handle
  → processor.process
  → manager.execute
  → helper.apply
```

Report navigation that exists only to discover what actually happens.

Every level of indirection should introduce useful meaning.

---

### 4. Working-memory stack

Look for code that requires the reader to keep multiple unresolved conditions or states in mind.

Example:

```ts
if (account) {
  if (account.isActive) {
    if (!account.isLocked) {
      if (request.amount < account.limit) {
        ...
      }
    }
  }
}
```

At the inner operation, four conditions must remain active in the reader's mental stack.

Report deep or complicated control flow when understanding the current line requires retaining too much previous context.

Do not complain about branching merely because branching exists.

---

### 5. Responsibility boundaries

A function should have an obvious job.

```ts
processRequest()
```

is a clarity problem if it secretly:

```text
validates input
loads an account
updates the database
publishes an event
```

Important responsibilities and side effects should not disappear behind vague operations.

Also detect the opposite:

```text
processOrder
  → prepareOrder
    → buildOrder
      → createOrderData
        → makeOrder
```

**Good extraction introduces a concept. Bad extraction only moves code somewhere else.**

---

### 6. Predictability over clever abstraction

Do not treat duplication as a clarity problem by itself.

Predictable repeated code may be cheaper to understand than generic machinery.

Prefer:

```ts
validateEmail(email);
validatePhone(phone);
validateAddress(address);
```

over an abstraction only if understanding the abstraction requires following configuration, callbacks, factories, or generic dispatch.

Report abstractions that reduce lines of code while increasing the amount of context required to understand them.

---

### 7. Mental execution

Flag code that is compact but requires mentally simulating language semantics.

Examples include complicated expressions, surprising mutation, evaluation-order tricks, or control flow hidden inside expressions.

Ask:

> Can the reader understand what this means immediately, or must they execute it in their head?

Compact is not the same as clear.

---

### 8. State and side effects

Make sure the reader can see:

* what important values represent;
* when their meaning changes;
* what gets mutated;
* what gets persisted;
* where external effects occur.

Bad:

```ts
let result = parse(input);
result = normalize(result);
result = await save(result);
```

The reader must continuously update the meaning of `result`.

Also report important side effects hidden behind innocent-looking operations.

---

### 9. Comments consume attention

Do not request comments for obvious code.

Report comments when they are misleading, redundant enough to distract, or necessary because an important non-obvious constraint or reason is otherwise invisible.

Comments should add context, not duplicate syntax.

---

## The main review test

For every piece of changed code ask:

> **What does the human reader have to remember, infer, navigate to, or mentally execute in order to understand this?**

If the answer is "nothing unreasonable", do not report a finding.

If the code is clear, return **no findings**.

Do not invent stylistic issues to produce output.

---

## Finding format

Report each finding as:

**Issue** — concrete clarity problem.

**Cognitive cost** — exactly what the reader must remember, infer, navigate to, or mentally execute.

**Evidence** — relevant symbol, function, expression, or call chain (with `file:line`).

**Direction** — what information should become more explicit or local.

Do not propose a patch unless a separate task asks for one.

---

## Assessment table

After the detailed findings, close the report with a summary table assessing
every finding:

| # | Finding | Clarity impact | Refactoring size | Risk |
|---|---------|----------------|------------------|------|

- **Clarity impact** — how much reader attention the fix would recover (high / medium / low).
- **Refactoring size** — the size of the change the Direction implies (comment-only / rename / local refactor / cross-file refactor).
- **Risk** — chance the fix changes behavior or breaks consumers (none / low / medium / high), noting contract or API renames explicitly.

The table is an assessment of the findings, not a plan. Do not order the work,
group it into phases, or start fixing. The skill's deliverable is the findings
plus this table — nothing more.

---

## Do not

Do not review based on line count, personal style, DRY, function size, or abstract "clean code" rules.

Do not produce readability scores.

Do not prefer abstraction simply because it removes duplication.

Do not use your own ability to eventually understand the code as evidence that the code is clear.

**The standard is not "Can the agent understand this?"**

**The standard is "How much of a human's limited attention and working memory does this code consume?"**
