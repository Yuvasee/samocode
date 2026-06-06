---
name: claude
description: Run Anthropic Claude CLI as a subagent for second opinions, code reviews, and questions. Use from a Codex (or other) session when you want Claude's perspective.
---

# Claude Subagent

Run Anthropic Claude CLI (Claude Opus) for second opinions and external reviews —
the symmetric counterpart to the `codex` skill, for use when the orchestration
provider is Codex (or any non-Claude agent) and you want Claude's perspective.

## Availability Check

Before using Claude, verify it's installed:

```bash
which claude >/dev/null 2>&1 || echo "CLAUDE_NOT_INSTALLED"
```

If not installed, inform the user: "Claude CLI is not installed. Skipping Claude review."

## Execution Pattern

```bash
claude -p "$PROMPT" \
  --dangerously-skip-permissions \
  --model opus \
  --output-format text 2>/dev/null
```

Output goes straight to stdout — no temp file needed.

### Flags

| Flag | Purpose |
|------|---------|
| `-p` / `--print` | Non-interactive print mode |
| `--dangerously-skip-permissions` | No permission prompts |
| `--model opus` | Pick the model (e.g. `opus`, `sonnet`) |
| `--output-format text` | Clean text output |
| `2>/dev/null` | Suppress session info and stderr noise |

### Timeout

Claude can take several minutes for complex prompts. Use a 15 minute timeout:

```bash
timeout 900 claude -p "$PROMPT" --dangerously-skip-permissions --model opus --output-format text 2>/dev/null
```

## Usage Examples

### Simple Question

```bash
claude -p "What are the tradeoffs between Redis and Memcached for session storage?" \
  --dangerously-skip-permissions --model opus --output-format text 2>/dev/null
```

### Code Review

```bash
DIFF=$(git diff main...HEAD)
claude -p "Review this diff for issues:

$DIFF

List concerns with severity (blocking/important/nice-to-have)." \
  --dangerously-skip-permissions --model opus --output-format text 2>/dev/null
```

### With Working Directory

Claude runs in the current directory; use `--add-dir` to grant access to others:

```bash
( cd /path/to/repo && claude -p "Analyze the architecture of this codebase" \
  --dangerously-skip-permissions --model opus --output-format text 2>/dev/null )
```

### GitHub PR Review

Claude has access to the `gh` CLI. Pass the PR URL and let it fetch the diff:

```bash
timeout 900 claude -p "Review this PR: https://github.com/owner/repo/pull/123

   Use gh CLI to get the diff and review for issues." \
  --dangerously-skip-permissions --model opus --output-format text 2>/dev/null
```

## Notes

- Use this from a Codex-provider session to get a genuinely different perspective
- Each call is stateless - no conversation continuity (no `--resume`)
- API / subscription costs apply per call
- Mirrors the `codex` skill so either provider can consult the other
