---
name: codex
description: Run OpenAI Codex CLI as a subagent for second opinions, code reviews, and questions. Use when you want a different AI model's perspective.
---

# Codex Subagent

Run OpenAI Codex CLI with its own configured model for second opinions and external
reviews. This is deliberately independent of the orchestration process's provider and
semantic profile.

## Availability Check

Before using Codex, verify it's installed:

```bash
which codex >/dev/null 2>&1 || echo "CODEX_NOT_INSTALLED"
```

If not installed, inform the user: "Codex CLI is not installed. Skipping Codex review."

## Execution Pattern

```bash
OUTPUT_FILE=$(mktemp)
codex exec \
  --skip-git-repo-check \
  --dangerously-bypass-approvals-and-sandbox \
  -o "$OUTPUT_FILE" \
  "$PROMPT" </dev/null 2>/tmp/codex_stderr.log
cat "$OUTPUT_FILE"
rm "$OUTPUT_FILE"
```

### Flags

| Flag | Purpose |
|------|---------|
| `exec` | Non-interactive mode |
| `--skip-git-repo-check` | Run outside git repositories |
| `--dangerously-bypass-approvals-and-sandbox` | No prompts, no sandbox restrictions |
| `-o <file>` | Capture clean output to file |
| `</dev/null` | **Required.** Close stdin — without it codex blocks on "Reading additional input from stdin..." and the `-o` file comes back empty (exit 0, zero bytes). Bites hardest in non-interactive / background Bash where there's no TTY. |
| `2>/tmp/codex_stderr.log` | Send stderr to a file, not `2>/dev/null` — if codex hangs or errors you need to *see* the stdin/auth message. Inspect the log when the output file is empty. |

### Timeout

Codex can take several minutes for complex prompts. Use 15 minute timeout:

```bash
timeout 900 codex exec --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox -o "$OUTPUT_FILE" "$PROMPT" </dev/null 2>/tmp/codex_stderr.log
```

## Usage Examples

### Simple Question

```bash
OUTPUT_FILE=$(mktemp)
codex exec --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox -o "$OUTPUT_FILE" \
  "What are the tradeoffs between Redis and Memcached for session storage?" </dev/null 2>/tmp/codex_stderr.log
cat "$OUTPUT_FILE"
rm "$OUTPUT_FILE"
```

### Code Review

```bash
DIFF=$(git diff main...HEAD)
OUTPUT_FILE=$(mktemp)
codex exec --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox -o "$OUTPUT_FILE" \
  "Review this diff for issues:

$DIFF

List concerns with severity (blocking/important/nice-to-have)." </dev/null 2>/tmp/codex_stderr.log
cat "$OUTPUT_FILE"
rm "$OUTPUT_FILE"
```

### With Working Directory

```bash
OUTPUT_FILE=$(mktemp)
codex exec --dangerously-bypass-approvals-and-sandbox -C /path/to/repo -o "$OUTPUT_FILE" \
  "Analyze the architecture of this codebase" </dev/null 2>/tmp/codex_stderr.log
cat "$OUTPUT_FILE"
rm "$OUTPUT_FILE"
```

### GitHub PR Review

Codex has access to `gh` CLI. Pass the PR URL and let it fetch the diff:

```bash
OUTPUT_FILE=$(mktemp)
timeout 900 codex exec --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox -o "$OUTPUT_FILE" \
  "Review this PR: https://github.com/owner/repo/pull/123

   Use gh CLI to get the diff and review for issues." </dev/null 2>/tmp/codex_stderr.log
cat "$OUTPUT_FILE"
rm "$OUTPUT_FILE"
```

## Notes

- The direct Codex call uses the Codex CLI's own configured/default model; Samocode's
  orchestration profile does not silently retarget an explicit second opinion
- Each call is stateless - no conversation continuity
- API costs apply per call
- Output may contain duplicates - parse accordingly
- **Always redirect stdin from `/dev/null`.** Even with a prompt passed as an argument, `codex exec` reads stdin; in a non-interactive/background Bash call with no TTY it blocks ("Reading additional input from stdin...") and the `-o` output file ends up empty with exit code 0.
