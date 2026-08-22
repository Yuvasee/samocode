"""Provider-independent workflow approval service and its typed contract.

An approval crosses exactly one configured `ApprovalGate`: it advances the current
phase to `gate.approved_next` and nothing else. It loads only `ProjectConfig` and
existing session-state primitives - never model routing, adapters, provider
executables, the global TOML, or the orchestration runner.

Structure mirrors the workflow-event split:

- `check_approval` is the pure authority (no I/O, no mutation) - the approval analogue
  of `validate_workflow_event`.
- `approve_session` / `approve` are the side-effecting orchestration: resolve -> read ->
  pre-check -> lock -> re-check -> atomic overview mutation -> signal consumption.
- `exit_code_for` maps a result to an actionable process exit code for the CLI.

Idempotency and concurrency derive authority from the overview phase (via the phase
registry), never from mutable signal contents, so a stale signal can never re-advance a
session and two racing approvals produce exactly one transition.
"""

import contextlib
import fcntl
import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

from .config import ProjectConfig, resolve_session_path
from .phases import ApprovalGate, Phase, get_phase_config
from .signals import SIGNAL_FILENAME, Signal, SignalStatus, read_signal_file
from .timestamps import log_timestamp
from .workflow_state import (
    OverviewParseError,
    OverviewState,
    OverviewTransition,
    OverviewWriteError,
    apply_overview_transition,
    atomic_write_text,
    read_overview_state,
)

LOCK_FILENAME = "_approval.lock"


# =============================================================================
# Typed outcome / rejection enums
# =============================================================================


class ApprovalRejection(Enum):
    """Typed cause of a refused approval. No overview mutation happened."""

    CONFIG_INVALID = "config_invalid"  # .samocode load/validate failed
    SESSION_NOT_FOUND = "session_not_found"  # resolved session dir absent
    OVERVIEW_INVALID = "overview_invalid"  # read_overview_state failed
    PHASE_HAS_NO_GATE = "phase_has_no_gate"  # current phase owns no approval_gate
    GATE_TARGET_INVALID = "gate_target_invalid"  # approved_next not in phase graph
    SIGNAL_NOT_WAITING = "signal_not_waiting"  # no pending waiting signal
    SIGNAL_PHASE_MISMATCH = "signal_phase_mismatch"  # signal phase != current phase
    SIGNAL_REASON_MISMATCH = "signal_reason_mismatch"  # for != gate.waiting_for
    LOCK_CONTENDED = "lock_contended"  # a concurrent approval holds the lock
    OVERVIEW_WRITE_FAILED = "overview_write_failed"  # atomic overview write failed
    SIGNAL_CONSUME_FAILED = "signal_consume_failed"  # advance ok, signal clear raised


class ApprovalOutcome(Enum):
    """Discriminated result of one approval attempt."""

    APPROVED = "approved"  # overview advanced + signal consumed
    APPROVED_SIGNAL_RETAINED = "approved_signal_retained"  # advanced; consume failed
    ALREADY_ADVANCED = "already_advanced"  # idempotent no-op: gate already crossed
    REJECTED = "rejected"  # precondition failed; no mutation


# =============================================================================
# Data structures
# =============================================================================


@dataclass(frozen=True)
class ApprovalPlan:
    """Accepted precondition check: the single transition authority grants."""

    source_phase: Phase
    gate: ApprovalGate
    target_phase: Phase  # == gate.approved_next


@dataclass(frozen=True)
class ApprovalCheck:
    """Tagged pure-check result: exactly one of plan / rejection is non-None."""

    plan: ApprovalPlan | None
    rejection: ApprovalRejection | None = None
    message: str | None = None


@dataclass(frozen=True)
class ApprovalResult:
    """Truthful outcome of one approval attempt. `advanced` == overview mutated now."""

    outcome: ApprovalOutcome
    advanced: bool
    source_phase: Phase | None
    target_phase: Phase | None
    rejection: ApprovalRejection | None = None
    parse_error: OverviewParseError | None = None
    write_error: OverviewWriteError | None = None
    signal_consumed: bool | None = None
    message: str | None = None


# =============================================================================
# Pure precondition authority (no I/O, no mutation)
# =============================================================================


def _reject(reason: ApprovalRejection, message: str) -> ApprovalCheck:
    return ApprovalCheck(plan=None, rejection=reason, message=message)


def check_approval(state: OverviewState, signal: Signal) -> ApprovalCheck:
    """Decide whether `signal` approves the gate the overview phase owns. Pure.

    Fail-fast rule order: phase owns a gate, gate target on-graph, signal waiting,
    signal source-phase matches, signal reason matches the gate. First violation wins.
    """
    source = state.phase
    config = get_phase_config(source.value)
    assert config is not None  # source came from the Phase enum parse
    gate = config.approval_gate
    if gate is None:
        return _reject(
            ApprovalRejection.PHASE_HAS_NO_GATE,
            f"Phase '{source.value}' owns no approval gate; nothing to approve",
        )

    if not config.can_transition_to(gate.approved_next):
        return _reject(
            ApprovalRejection.GATE_TARGET_INVALID,
            f"Gate target '{gate.approved_next.value}' is not an allowed transition "
            f"from '{source.value}'",
        )

    if signal.status is not SignalStatus.WAITING:
        return _reject(
            ApprovalRejection.SIGNAL_NOT_WAITING,
            f"No pending approval: signal status is '{signal.status.value}', "
            f"expected 'waiting' for reason '{gate.waiting_for}'",
        )

    if signal.phase is not None and signal.phase.lower() != source.value:
        return _reject(
            ApprovalRejection.SIGNAL_PHASE_MISMATCH,
            f"Pending signal names phase '{signal.phase}', "
            f"but session is in '{source.value}'",
        )

    reason = (signal.waiting_for or "").strip()
    if reason != gate.waiting_for:
        return _reject(
            ApprovalRejection.SIGNAL_REASON_MISMATCH,
            f"Pending signal waits for '{reason or '(none)'}', "
            f"gate requires '{gate.waiting_for}'",
        )

    return ApprovalCheck(
        plan=ApprovalPlan(
            source_phase=source, gate=gate, target_phase=gate.approved_next
        )
    )


# =============================================================================
# Concurrency: advisory session lock
# =============================================================================


@contextlib.contextmanager
def _approval_lock(session_path: Path) -> Iterator[bool]:
    """Yield True while holding an exclusive advisory lock, False if contended.

    The lock file is created once and never unlinked (unlinking would let two
    processes lock different inodes). The lock is bound to the fd, released on exit,
    and by the kernel on process death, so no stale lock survives a crash.
    """
    fd = os.open(str(session_path / LOCK_FILENAME), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


# =============================================================================
# Side-effecting orchestration
# =============================================================================


def _rejected(
    reason: ApprovalRejection,
    message: str,
    *,
    source: Phase | None = None,
    parse_error: OverviewParseError | None = None,
    write_error: OverviewWriteError | None = None,
) -> ApprovalResult:
    return ApprovalResult(
        outcome=ApprovalOutcome.REJECTED,
        advanced=False,
        source_phase=source,
        target_phase=None,
        rejection=reason,
        parse_error=parse_error,
        write_error=write_error,
        message=message,
    )


def approve(
    config_path: Path, session_name: str, now: datetime | None = None
) -> ApprovalResult:
    """Load `ProjectConfig` from `.samocode`, then approve the session's pending gate.

    Loads only project/session configuration. A missing/invalid `.samocode` or a
    non-existent configured directory becomes CONFIG_INVALID with no session touched.
    """
    try:
        project = ProjectConfig.from_file(config_path)
    except ValueError as exc:
        return _rejected(ApprovalRejection.CONFIG_INVALID, f"Config error: {exc}")
    errors = project.validate()
    if errors:
        return _rejected(
            ApprovalRejection.CONFIG_INVALID,
            "Invalid project config: " + "; ".join(errors),
        )
    return approve_session(project, session_name, now)


def approve_session(
    project: ProjectConfig, session_name: str, now: datetime | None = None
) -> ApprovalResult:
    """Advance one session across its pending approval gate. Fail-fast, typed.

    Order: resolve -> read overview -> read signal -> pre-check (cheap feedback) ->
    lock -> re-read + re-check under lock -> atomic overview mutation -> consume signal.
    Only an accepted under-lock check mutates state. A pre-lock accept that turns into
    an under-lock reject means another actor advanced first -> ALREADY_ADVANCED.
    """
    session_path = resolve_session_path(project.sessions, session_name)
    if not (session_path.exists() and session_path.is_dir()):
        return _rejected(
            ApprovalRejection.SESSION_NOT_FOUND,
            f"Session directory does not exist: {session_path}",
        )

    parsed = read_overview_state(session_path)
    if parsed.state is None:
        return _rejected(
            ApprovalRejection.OVERVIEW_INVALID,
            parsed.message or "Invalid _overview.md",
            parse_error=parsed.error,
        )

    pre = check_approval(parsed.state, read_signal_file(session_path))
    if pre.plan is None:
        assert pre.rejection is not None
        return _rejected(
            pre.rejection, pre.message or pre.rejection.value, source=parsed.state.phase
        )

    with _approval_lock(session_path) as acquired:
        if not acquired:
            return _rejected(
                ApprovalRejection.LOCK_CONTENDED,
                "Another approval is in progress for this session; retry",
                source=parsed.state.phase,
            )

        # Authoritative re-read + re-check under the lock.
        locked = read_overview_state(session_path)
        if locked.state is None:
            return _rejected(
                ApprovalRejection.OVERVIEW_INVALID,
                locked.message or "Invalid _overview.md",
                parse_error=locked.error,
            )
        check = check_approval(locked.state, read_signal_file(session_path))
        if check.plan is None:
            # Pre-lock accepted but now rejected. Only a real phase move (a concurrent
            # winner crossing the gate) is ALREADY_ADVANCED; otherwise the phase is
            # unchanged and the honest cause is check_approval's rejection.
            if locked.state.phase != pre.plan.source_phase:
                return ApprovalResult(
                    outcome=ApprovalOutcome.ALREADY_ADVANCED,
                    advanced=False,
                    source_phase=pre.plan.source_phase,
                    target_phase=pre.plan.target_phase,
                    message="Gate already crossed by a concurrent approval; no-op",
                )
            assert check.rejection is not None
            return _rejected(
                check.rejection,
                check.message or check.rejection.value,
                source=locked.state.phase,
            )

        plan = check.plan
        return _apply_approval(session_path, plan, now)


def _apply_approval(
    session_path: Path, plan: ApprovalPlan, now: datetime | None
) -> ApprovalResult:
    """Atomically advance the overview, then consume the pending signal.

    Point of no return is between the overview write and signal consumption: once the
    write lands the phase has advanced regardless of consumption. A consume failure is
    reported as APPROVED_SIGNAL_RETAINED (recoverable: the stale signal is inert because
    the advanced phase owns no matching gate).
    """
    src = plan.source_phase.value
    tgt = plan.target_phase.value
    transition = OverviewTransition(
        target_phase=plan.target_phase,
        blocked="no",
        last_action=f"Human approved {src} -> {tgt} ({plan.gate.waiting_for})",
        next_action=f"Enter {tgt} phase",
        flow_log_entry=(
            f"- [approve @ {log_timestamp(now)}] "
            f"Approved {src} -> {tgt} (reason '{plan.gate.waiting_for}')"
        ),
    )
    write = apply_overview_transition(
        session_path, transition, expected_source=plan.source_phase
    )
    if not write.ok:
        return ApprovalResult(
            outcome=ApprovalOutcome.REJECTED,
            advanced=False,
            source_phase=plan.source_phase,
            target_phase=plan.target_phase,
            rejection=ApprovalRejection.OVERVIEW_WRITE_FAILED,
            write_error=write.error,
            parse_error=write.parse_error,
            message=write.message or "Overview write failed",
        )

    try:
        atomic_write_text(session_path / SIGNAL_FILENAME, "{}")
    except OSError as exc:
        return ApprovalResult(
            outcome=ApprovalOutcome.APPROVED_SIGNAL_RETAINED,
            advanced=True,
            source_phase=plan.source_phase,
            target_phase=plan.target_phase,
            rejection=ApprovalRejection.SIGNAL_CONSUME_FAILED,
            signal_consumed=False,
            message=(
                f"Phase advanced to '{tgt}' but the pending signal could not be "
                f"cleared: {exc}. Safe to ignore; the stale signal cannot re-advance."
            ),
        )

    return ApprovalResult(
        outcome=ApprovalOutcome.APPROVED,
        advanced=True,
        source_phase=plan.source_phase,
        target_phase=plan.target_phase,
        signal_consumed=True,
        message=f"Approved {src} -> {tgt}",
    )


# =============================================================================
# CLI helper
# =============================================================================

_REJECTION_EXIT_CODES: dict[ApprovalRejection, int] = {
    ApprovalRejection.LOCK_CONTENDED: 3,  # transient; retry
    ApprovalRejection.OVERVIEW_WRITE_FAILED: 4,  # state/IO fault
    ApprovalRejection.SIGNAL_CONSUME_FAILED: 5,  # advanced; clear stale _signal.json
}


def exit_code_for(result: ApprovalResult) -> int:
    """Map an approval result to a process exit code.

    0 only for APPROVED (this caller advanced the phase); 6 ALREADY_ADVANCED (a
    concurrent winner advanced it, this attempt did not -> fail-fast, distinct code);
    5 advanced-but-signal-retained; 3 lock contention (retryable); 4 overview write
    fault; 1 all other rejections. argparse independently reserves 2 for usage errors.
    """
    if result.outcome is ApprovalOutcome.APPROVED:
        return 0
    if result.outcome is ApprovalOutcome.ALREADY_ADVANCED:
        return 6
    if result.outcome is ApprovalOutcome.APPROVED_SIGNAL_RETAINED:
        return 5
    if result.rejection is not None:
        return _REJECTION_EXIT_CODES.get(result.rejection, 1)
    return 1
