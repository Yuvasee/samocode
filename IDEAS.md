# Samocode Improvement Ideas

Architectural patterns to make samocode more robust and scalable.

---

## 1. Monitor Process for Crash Recovery

**Current limitation:** If orchestrator crashes or hangs, session stops. Manual restart required.

**Solution:** A monitor process periodically checks if worker sessions are alive, restarts dead ones with their state intact.

**Implementation idea:**
```python
# monitor.py - runs separately
while True:
    for session in active_sessions():
        if not orchestrator_alive(session):
            # Session state survives in _overview.md
            restart_orchestrator(session)
    sleep(60)
```

**Why useful:** Long-running autonomous sessions need resilience. You walk away, come back, work continues even after crashes.

---

## 2. Parallel Worker Support

**Current limitation:** Single session, single task, sequential phases.

**Solution:** Spawn multiple workers in isolated git worktrees, each working on different tasks simultaneously.

**Implementation idea:**
```
_sessions/
├── task-auth/        # Worker 1 on auth feature
├── task-api/         # Worker 2 on API refactor
└── task-tests/       # Worker 3 on test coverage

# orchestrator manages all three in parallel
# each has own worktree, own _overview.md, own signal
```

**Why useful:** For larger projects, you could parallelize independent tasks (e.g., 3 features at once), then merge results.

---

## 3. Simpler "Hook" File for Current Task

**Current approach:** _overview.md contains everything - task, phase, history, status.

**Solution:** Simple hook file that just points to current task. Minimal, fast to read.

**Implementation idea:**
```
_hook.json
{
  "task_id": "implement-auth",
  "phase": "implementation",
  "iteration": 5
}
```

Separate from the larger _overview.md which holds history/context. Agent first reads hook (fast), then reads full overview only if needed.

**Why useful:** Faster iteration startup. Clear separation between "what to do now" vs "full history".

---

## 4. File-Based Message Queue Between Sessions

**Current limitation:** No communication between sessions or with parent process except signals.

**Solution:** Agents can send messages to each other via file-based mailboxes.

**Implementation idea:**
```
_sessions/
├── task-auth/
│   ├── _inbox/
│   │   └── msg-001.json   # "API team needs auth endpoint spec"
│   └── _overview.md
└── task-api/
    └── _inbox/
        └── msg-002.json   # "Auth team says use JWT"
```

**Why useful:** Parallel workers could coordinate. Parent Claude could send instructions to running sessions. Human could inject guidance without stopping orchestrator.

---

## 5. Handoff Pattern for Context Refresh

**Current approach:** Phases are somewhat like handoffs (different agents per phase).

**Current limitation:** No explicit "context is getting full, start fresh session" mechanism.

**Solution:** Agent can signal "handoff" - state saved, session killed, new session started, state restored.

**Implementation idea:**
```python
# New signal type
{"status": "handoff", "reason": "context_full", "state": {...}}

# Orchestrator handles:
if signal.status == "handoff":
    save_handoff_state(session_path, signal.state)
    restart_claude_session(session_path)  # fresh context
    # New session reads handoff state, continues
```

**Why useful:** Long implementation phases might fill context. Explicit handoff lets agent continue indefinitely.

---

## 6. Task Batching / Convoy Tracking

**Current limitation:** Each session is independent. No grouping of related tasks.

**Solution:** Group related tasks, track overall progress, notify when all complete.

**Implementation idea:**
```json
// _convoy.json at project level
{
  "id": "feature-auth",
  "name": "Auth System",
  "sessions": ["task-auth-core", "task-auth-db", "task-auth-ui"],
  "notify_on_complete": true
}
```

Orchestrator checks all sessions in convoy, sends notification when all are done.

**Why useful:** For features requiring multiple parallel tasks, you get unified tracking and completion notification.

---

## 7. Stall Detection

**Current limitation:** If Claude hangs (not crashes, just stalls), timeout eventually kills it, but no smart detection.

**Solution:** Monitor detects "no progress" (same state for N minutes) and can nudge or restart.

**Implementation idea:**
```python
# In orchestrator
last_activity = read_last_modified(session_path / "_overview.md")
if time.now() - last_activity > STALL_THRESHOLD:
    if phase == current_phase_for_too_long():
        notify_human("Session stalled in {phase}")
        # Or: inject nudge message, or restart
```

**Why useful:** Catches infinite loops or stuck agents without waiting for full timeout.

---

## 8. Role Detection from Directory

**Current approach:** Hardcoded phase → agent mapping.

**Solution:** Determine agent role from which directory it runs in.

**Implementation idea:**
```
sessions/
├── main-task/           # runs as "coordinator"
│   └── workers/
│       ├── impl-1/      # runs as "implementation-worker"
│       └── impl-2/      # runs as "implementation-worker"
```

Agent script detects role from `$PWD`, loads appropriate agent instructions.

**Why useful:** More flexible than phase-based. Same orchestrator code works for different agent types.

---

## Priority Ranking

| Idea | Effort | Impact | Recommendation |
|------|--------|--------|----------------|
| Monitor for crash recovery | Medium | High | **Do this first** - makes long sessions reliable |
| Stall detection | Low | Medium | Easy win - add to existing loop |
| Handoff pattern | Medium | High | Enables unlimited context |
| File-based messaging | Medium | Medium | Useful if you add parallel workers |
| Parallel workers | High | High | Big architectural change, but big payoff |
| Simpler hook file | Low | Low | Minor optimization |
| Task batching | Medium | Medium | Useful after parallel workers |
| Role from directory | Low | Low | Nice-to-have, not critical |

The **monitor process** is the highest-value addition - it transforms samocode from "hope it doesn't crash" to "self-healing autonomous system".
