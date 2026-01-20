# Technical Debt

Known architectural issues and improvements queued for future work.

---

## runner.py Needs Separation of Concerns

**Status:** Open
**Priority:** Low
**Added:** 2026-01-13

### Problem

`worker/runner.py` is ~480 lines with 12+ functions handling multiple responsibilities:
- CLI execution and retry logic
- Overview file parsing (phase, iteration, working dir extraction)
- Context/prompt building
- Log streaming
- Process management

### Current Structure

Organized with section comments but all in one file:
```
# Public API - Main execution functions
# Overview extraction utilities
# Context and prompt building
# Log streaming
# Private helpers
```

### Proposed Solution

Split into focused modules:

```
worker/
├── runner.py          # Public API: run_claude_with_retry, run_claude_once
├── extraction.py      # extract_phase, extract_iteration, extract_working_dir
├── context.py         # build_session_context, _build_config_section, _build_initial_instructions
├── streaming.py       # stream_logs, _drain_remaining
└── execution.py       # _execute_process, _build_cli_args
```

### Tests

Tests exist in `tests/test_runner.py` (87 tests covering all functions). Refactoring should maintain test coverage.

### Notes

- Deferred because current structure works and is readable
- Section comments provide adequate organization for now
- Refactor when adding significant new functionality
