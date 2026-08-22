"""Approve gates from authoritative overview state, independent of providers."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

from .config import ProjectConfig, resolve_session_path
from .phases import ApprovalGate, Phase, get_phase_config
from .signals import SIGNAL_FILENAME, Signal, SignalStatus, read_signal_file
from .timestamps import log_timestamp
from .workflow_state import (
    LockState,
    OverviewParseError,
    OverviewState,
    OverviewTransition,
    OverviewWriteError,
    apply_overview_transition_locked,
    atomic_write_text,
    read_overview_state,
    session_lock,
)

class ApprovalRejection(Enum):
    CONFIG_INVALID = "config_invalid"
    SESSION_NOT_FOUND = "session_not_found"
    OVERVIEW_INVALID = "overview_invalid"
    PHASE_HAS_NO_GATE = "phase_has_no_gate"
    GATE_TARGET_INVALID = "gate_target_invalid"
    SIGNAL_NOT_WAITING = "signal_not_waiting"
    SIGNAL_PHASE_MISMATCH = "signal_phase_mismatch"
    SIGNAL_REASON_MISMATCH = "signal_reason_mismatch"
    LOCK_CONTENDED = "lock_contended"
    LOCK_IO_FAILED = "lock_io_failed"
    OVERVIEW_WRITE_FAILED = "overview_write_failed"
    OVERVIEW_STATE_CONFLICT = "overview_state_conflict"
    SIGNAL_CONSUME_FAILED = "signal_consume_failed"


class ApprovalOutcome(Enum):
    APPROVED = "approved"
    APPROVED_SIGNAL_RETAINED = "approved_signal_retained"
    ALREADY_ADVANCED = "already_advanced"
    REJECTED = "rejected"


@dataclass(frozen=True)
class ApprovalPlan:
    source_phase: Phase
    gate: ApprovalGate
    target_phase: Phase


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


def _reject(reason: ApprovalRejection, message: str) -> ApprovalCheck:
    return ApprovalCheck(plan=None, rejection=reason, message=message)


def check_approval(state: OverviewState, signal: Signal) -> ApprovalCheck:
    """Check the overview-owned gate without I/O or mutation."""
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
        # Preserve parse detail instead of reporting a misleading generic status.
        detail = f" ({signal.reason})" if signal.reason else ""
        return _reject(
            ApprovalRejection.SIGNAL_NOT_WAITING,
            f"No pending approval: signal status is '{signal.status.value}'{detail}, "
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
    """Approve using project/session state only, independent of model routing."""
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
    """Trust only the under-lock check; target moves are wins, other moves conflicts."""
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

    with session_lock(session_path) as lock:
        if lock.state is LockState.CONTENDED:
            return _rejected(
                ApprovalRejection.LOCK_CONTENDED,
                "Another approval is in progress for this session; retry",
                source=parsed.state.phase,
            )
        if lock.state is LockState.FAILED:
            return _rejected(
                ApprovalRejection.LOCK_IO_FAILED,
                lock.message or "Approval lock could not be acquired",
                source=parsed.state.phase,
            )

        # Only state read under this lock is authoritative.
        locked = read_overview_state(session_path)
        if locked.state is None:
            return _rejected(
                ApprovalRejection.OVERVIEW_INVALID,
                locked.message or "Invalid _overview.md",
                parse_error=locked.error,
            )
        check = check_approval(locked.state, read_signal_file(session_path))
        if check.plan is None:
            observed = locked.state.phase
            if observed == pre.plan.source_phase:
                assert check.rejection is not None
                return _rejected(
                    check.rejection,
                    check.message or check.rejection.value,
                    source=observed,
                )
            # The gate target means another approver won; any other move is a conflict.
            return _classify_phase_moved(observed, pre.plan)

        plan = check.plan
        return _apply_approval(session_path, plan, now)


def _already_advanced(
    plan: ApprovalPlan, *, write_error: OverviewWriteError | None = None
) -> ApprovalResult:
    src = plan.source_phase.value
    tgt = plan.target_phase.value
    return ApprovalResult(
        outcome=ApprovalOutcome.ALREADY_ADVANCED,
        advanced=False,
        source_phase=plan.source_phase,
        target_phase=plan.target_phase,
        write_error=write_error,
        message=(
            f"Gate already crossed: another approval advanced '{src}' -> '{tgt}'; "
            f"this call made no change"
        ),
    )


def _classify_phase_moved(
    observed: Phase | None,
    plan: ApprovalPlan,
    *,
    write_error: OverviewWriteError | None = None,
) -> ApprovalResult:
    """Classify every re-check and write-stage CAS miss by the same rule."""
    if observed is not None and observed == plan.target_phase:
        return _already_advanced(plan, write_error=write_error)
    observed_label = observed.value if observed is not None else "?"
    return ApprovalResult(
        outcome=ApprovalOutcome.REJECTED,
        advanced=False,
        source_phase=plan.source_phase,
        target_phase=plan.target_phase,
        rejection=ApprovalRejection.OVERVIEW_STATE_CONFLICT,
        write_error=write_error,
        message=(
            f"Overview phase moved to '{observed_label}', not the gate target "
            f"'{plan.target_phase.value}'; refusing to treat as an approval"
        ),
    )


def _apply_approval(
    session_path: Path, plan: ApprovalPlan, now: datetime | None
) -> ApprovalResult:
    """Advance before consuming; a retained stale signal cannot cross the new gate."""
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
    # Re-acquiring flock through another fd in this process can deadlock.
    write = apply_overview_transition_locked(
        session_path, transition, expected_source=plan.source_phase
    )
    if not write.ok:
        # Keep CAS-miss classification identical to the earlier under-lock re-check.
        if write.error is OverviewWriteError.PHASE_MOVED:
            return _classify_phase_moved(
                write.observed_phase, plan, write_error=write.error
            )
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
                f"cleared: {exc}. The retained _signal.json is inert (it cannot "
                f"re-advance the session); clearing it is optional."
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


_REJECTION_EXIT_CODES: dict[ApprovalRejection, int] = {
    ApprovalRejection.LOCK_CONTENDED: 3,  # transient contention; retry
    ApprovalRejection.LOCK_IO_FAILED: 4,  # non-contention lock fault; not retryable
    ApprovalRejection.OVERVIEW_WRITE_FAILED: 4,  # write fault; may be transient
    ApprovalRejection.OVERVIEW_STATE_CONFLICT: 4,  # external writer moved the phase
    ApprovalRejection.SIGNAL_CONSUME_FAILED: 5,  # advanced; retained signal inert
}


def exit_code_for(result: ApprovalResult) -> int:
    """Keep retryable contention distinct from faults and concurrent winners."""
    if result.outcome is ApprovalOutcome.APPROVED:
        return 0
    if result.outcome is ApprovalOutcome.ALREADY_ADVANCED:
        return 6
    if result.outcome is ApprovalOutcome.APPROVED_SIGNAL_RETAINED:
        return 5
    if result.rejection is not None:
        return _REJECTION_EXIT_CODES.get(result.rejection, 1)
    return 1
