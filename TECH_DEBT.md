# Technical Debt

Known architectural issues and improvements queued for future work.

---

## Phase Sequence Not Centralized

**Status:** Open
**Priority:** Medium
**Added:** 2026-01-13

### Problem

Phase sequence and human gates are spread across multiple files with no single source of truth:

1. **`workflow.md`** - Documents the flow (human-readable, can drift from reality)
2. **`worker/runner.py`** - `PHASE_AGENTS` dict maps phases to agents (defines valid phases)
3. **Individual agent files** - Each agent decides:
   - What phase to signal next
   - Whether to wait for human or continue

### Consequences

- Phase sequence is implicit in agent behavior
- If an agent signals wrong phase, flow breaks silently
- Human gates only defined in individual agent files
- No validation that phase transitions are valid
- Adding/removing phases requires changes in multiple places

### Current Flow

```
init -> investigation -> requirements -> planning -> implementation -> testing -> quality -> done
                              |              |
                         Q&A gate       Plan gate
```

### Proposed Solution

Create a central phase configuration that defines:
- Valid phases and their order
- Which agent handles each phase
- Human gates (which phases require approval)
- Valid phase transitions

Example structure:
```python
PHASES = [
    {"name": "init", "agent": "init-agent", "next": "investigation"},
    {"name": "investigation", "agent": "investigation-agent", "next": "requirements"},
    {"name": "requirements", "agent": "requirements-agent", "next": "planning", "gate": "qa_answers"},
    {"name": "planning", "agent": "planning-agent", "next": "implementation", "gate": "plan_approval"},
    {"name": "implementation", "agent": "implementation-agent", "next": "testing"},
    {"name": "testing", "agent": "testing-agent", "next": "quality"},
    {"name": "quality", "agent": "quality-agent", "next": "done"},
    {"name": "done", "agent": "done-agent", "next": None},
]
```

Benefits:
- Single source of truth
- Agents don't need to know about other phases
- Orchestrator can validate transitions
- Easy to add/modify phases
- Human gates clearly visible

### Files to Change

- `worker/runner.py` - Add central phase config, use it for agent selection
- `workflow.md` - Generate from config or reference it
- Agent files - Remove phase transition logic, just signal "continue" or "waiting"
