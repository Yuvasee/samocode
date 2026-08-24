"""Approve gates from authoritative overview state, independent of providers."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

from .config import ProjectConfig, resolve_session_path
from .phases import ApprovalGate, Phase, get_phase_config
from .plan_resolver import (
    PlanResolutionError,
    select_active_plan,
    validate_plan_contract,
)
from .signals import SIGNAL_FILENAME, Signal, SignalStatus, read_signal_file
from .timestamps import log_timestamp
from .workflow_state import (
    LockState,
    OverviewParseError,
    OverviewState,
    OverviewTransition,
    OverviewWriteError,
    OverviewWriteFailure,
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
    PLAN_INVALID = "plan_invalid"


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

    def __post_init__(self) -> None:
        if (self.plan is None) == (self.rejection is None):
            raise ValueError("Approval check requires exactly one of plan or rejection")
        if self.plan is not None and self.message is not None:
            raise ValueError("Allowed approval check cannot carry a rejection message")
        if self.rejection is not None and not self.message:
            raise ValueError("Denied approval check requires a message")

    @classmethod
    def allowed(cls, plan: ApprovalPlan) -> "ApprovalCheck":
        return cls(plan=plan)

    @classmethod
    def denied(cls, rejection: ApprovalRejection, message: str) -> "ApprovalCheck":
        return cls(plan=None, rejection=rejection, message=message)


@dataclass(frozen=True)
class ApprovalResult:
    """Truthful outcome of one approval attempt. `advanced` == overview mutated now."""

    outcome: ApprovalOutcome
    source_phase: Phase | None
    target_phase: Phase | None
    rejection: ApprovalRejection | None = None
    overview_parse_error: OverviewParseError | None = None
    write_failure: OverviewWriteFailure | None = None
    message: str | None = None

    def __post_init__(self) -> None:
        # One branch per outcome, each stating exactly what it may carry.
        if self.outcome is ApprovalOutcome.REJECTED:
            if self.rejection is None or not self.message:
                raise ValueError("Rejected approval requires a rejection and message")
            if self.rejection is ApprovalRejection.OVERVIEW_INVALID:
                if self.overview_parse_error is None:
                    raise ValueError(
                        "Invalid-overview rejection requires a parse error"
                    )
            elif self.overview_parse_error is not None:
                raise ValueError(
                    "Only invalid-overview rejection carries a parse error"
                )
            if self.rejection is ApprovalRejection.OVERVIEW_WRITE_FAILED:
                if self.write_failure is None:
                    raise ValueError(
                        "Overview-write rejection requires a write failure"
                    )
            elif (
                self.rejection is not ApprovalRejection.OVERVIEW_STATE_CONFLICT
                and self.write_failure is not None
            ):
                raise ValueError(
                    "Rejection type cannot carry an overview write failure"
                )
            return

        # APPROVED, APPROVED_SIGNAL_RETAINED, and ALREADY_ADVANCED share one shape.
        if self.source_phase is None or self.target_phase is None:
            raise ValueError("Non-rejected approval requires source and target phases")
        if self.source_phase is self.target_phase:
            raise ValueError("Non-rejected approval requires a phase change")
        if self.rejection is not None or self.overview_parse_error is not None:
            raise ValueError("Non-rejected approval cannot carry rejection details")
        if not self.message:
            raise ValueError("Non-rejected approval requires a message")
        if self.outcome is ApprovalOutcome.ALREADY_ADVANCED:
            if (
                self.write_failure is not None
                and self.write_failure.error is not OverviewWriteError.PHASE_MOVED
            ):
                raise ValueError("Already-advanced diagnostics must be PHASE_MOVED")
        elif self.write_failure is not None:
            raise ValueError(
                "Only rejected or already-advanced results carry write failures"
            )

    @property
    def advanced(self) -> bool:
        return self.outcome in (
            ApprovalOutcome.APPROVED,
            ApprovalOutcome.APPROVED_SIGNAL_RETAINED,
        )

    @property
    def signal_consumed(self) -> bool | None:
        if self.outcome is ApprovalOutcome.APPROVED:
            return True
        if self.outcome is ApprovalOutcome.APPROVED_SIGNAL_RETAINED:
            return False
        return None

    @property
    def write_error(self) -> OverviewWriteError | None:
        return self.write_failure.error if self.write_failure else None

    @property
    def parse_error(self) -> OverviewParseError | None:
        if self.write_failure is not None:
            return self.write_failure.parse_error
        return self.overview_parse_error

    @classmethod
    def approved(cls, source: Phase, target: Phase, message: str) -> "ApprovalResult":
        return cls(ApprovalOutcome.APPROVED, source, target, message=message)

    @classmethod
    def approved_signal_retained(
        cls, source: Phase, target: Phase, message: str
    ) -> "ApprovalResult":
        return cls(
            ApprovalOutcome.APPROVED_SIGNAL_RETAINED,
            source,
            target,
            message=message,
        )

    @classmethod
    def already_advanced(
        cls,
        source: Phase,
        target: Phase,
        message: str,
        *,
        write_failure: OverviewWriteFailure | None = None,
    ) -> "ApprovalResult":
        return cls(
            ApprovalOutcome.ALREADY_ADVANCED,
            source,
            target,
            write_failure=write_failure,
            message=message,
        )

    @classmethod
    def rejected(
        cls,
        rejection: ApprovalRejection,
        message: str,
        *,
        source: Phase | None = None,
        target: Phase | None = None,
        parse_error: OverviewParseError | None = None,
        write_failure: OverviewWriteFailure | None = None,
    ) -> "ApprovalResult":
        return cls(
            ApprovalOutcome.REJECTED,
            source,
            target,
            rejection=rejection,
            overview_parse_error=parse_error,
            write_failure=write_failure,
            message=message,
        )


def _denied_check(reason: ApprovalRejection, message: str) -> ApprovalCheck:
    return ApprovalCheck.denied(reason, message)


def check_approval(state: OverviewState, signal: Signal) -> ApprovalCheck:
    """Check the overview-owned gate without I/O or mutation."""
    source = state.phase
    config = get_phase_config(source.value)
    assert config is not None  # source came from the Phase enum parse
    gate = config.approval_gate
    if gate is None:
        return _denied_check(
            ApprovalRejection.PHASE_HAS_NO_GATE,
            f"Phase '{source.value}' owns no approval gate; nothing to approve",
        )

    if not config.can_transition_to(gate.approved_next):
        return _denied_check(
            ApprovalRejection.GATE_TARGET_INVALID,
            f"Gate target '{gate.approved_next.value}' is not an allowed transition "
            f"from '{source.value}'",
        )

    if signal.status is not SignalStatus.WAITING:
        # Preserve parse detail instead of reporting a misleading generic status.
        detail = f" ({signal.reason})" if signal.reason else ""
        return _denied_check(
            ApprovalRejection.SIGNAL_NOT_WAITING,
            f"No pending approval: signal status is '{signal.status.value}'{detail}, "
            f"expected 'waiting' for reason '{gate.waiting_for}'",
        )

    if signal.phase is not None and signal.phase.lower() != source.value:
        return _denied_check(
            ApprovalRejection.SIGNAL_PHASE_MISMATCH,
            f"Pending signal names phase '{signal.phase}', "
            f"but session is in '{source.value}'",
        )

    reason = (signal.waiting_for or "").strip()
    if reason != gate.waiting_for:
        return _denied_check(
            ApprovalRejection.SIGNAL_REASON_MISMATCH,
            f"Pending signal waits for '{reason or '(none)'}', "
            f"gate requires '{gate.waiting_for}'",
        )

    return ApprovalCheck.allowed(
        ApprovalPlan(source_phase=source, gate=gate, target_phase=gate.approved_next)
    )


def _rejected_result(
    reason: ApprovalRejection,
    message: str,
    *,
    source: Phase | None = None,
    parse_error: OverviewParseError | None = None,
) -> ApprovalResult:
    return ApprovalResult.rejected(
        reason,
        message,
        source=source,
        parse_error=parse_error,
    )


def approve(
    config_path: Path, session_name: str, now: datetime | None = None
) -> ApprovalResult:
    """Approve using project/session state only, independent of model routing."""
    try:
        project = ProjectConfig.from_file(config_path)
    except ValueError as exc:
        return _rejected_result(
            ApprovalRejection.CONFIG_INVALID, f"Config error: {exc}"
        )
    errors = project.validate()
    if errors:
        return _rejected_result(
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
        return _rejected_result(
            ApprovalRejection.SESSION_NOT_FOUND,
            f"Session directory does not exist: {session_path}",
        )

    # The pre-lock pass is a pure fail-fast: it rejects no-gate / wrong-signal
    # calls without contending the lock. Its plan survives only as the yardstick
    # for the under-lock re-check to tell "another approver already won" from
    # "an external writer moved the phase".
    prelock_overview = read_overview_state(session_path)
    if prelock_overview.state is None:
        return _rejected_result(
            ApprovalRejection.OVERVIEW_INVALID,
            prelock_overview.message or "Invalid _overview.md",
            parse_error=prelock_overview.error,
        )

    prelock_check = check_approval(
        prelock_overview.state, read_signal_file(session_path)
    )
    if prelock_check.plan is None:
        assert prelock_check.rejection is not None
        return _rejected_result(
            prelock_check.rejection,
            prelock_check.message or prelock_check.rejection.value,
            source=prelock_overview.state.phase,
        )
    requested_plan = prelock_check.plan
    plan_error = _active_plan_error(session_path, prelock_overview.state)
    if plan_error is not None:
        return _rejected_result(
            ApprovalRejection.PLAN_INVALID,
            plan_error,
            source=prelock_overview.state.phase,
        )

    with session_lock(session_path) as lock:
        if lock.state is LockState.CONTENDED:
            return _rejected_result(
                ApprovalRejection.LOCK_CONTENDED,
                "Another approval is in progress for this session; retry",
                source=prelock_overview.state.phase,
            )
        if lock.state is LockState.FAILED:
            return _rejected_result(
                ApprovalRejection.LOCK_IO_FAILED,
                lock.message or "Approval lock could not be acquired",
                source=prelock_overview.state.phase,
            )

        # Only state read under this lock is authoritative.
        locked_overview = read_overview_state(session_path)
        if locked_overview.state is None:
            return _rejected_result(
                ApprovalRejection.OVERVIEW_INVALID,
                locked_overview.message or "Invalid _overview.md",
                parse_error=locked_overview.error,
            )
        locked_check = check_approval(
            locked_overview.state, read_signal_file(session_path)
        )
        if locked_check.plan is None:
            observed_phase = locked_overview.state.phase
            if observed_phase == requested_plan.source_phase:
                assert locked_check.rejection is not None
                return _rejected_result(
                    locked_check.rejection,
                    locked_check.message or locked_check.rejection.value,
                    source=observed_phase,
                )
            # The gate target means another approver won; any other move is a conflict.
            return _classify_phase_moved(observed_phase, requested_plan)

        authoritative_plan = locked_check.plan
        plan_error = _active_plan_error(session_path, locked_overview.state)
        if plan_error is not None:
            return _rejected_result(
                ApprovalRejection.PLAN_INVALID,
                plan_error,
                source=locked_overview.state.phase,
            )
        return _apply_approval(session_path, authoritative_plan, now)


def _active_plan_error(session_path: Path, state: OverviewState) -> str | None:
    if state.phase is not Phase.PLANNING:
        return None
    try:
        plan_path = select_active_plan(session_path, state.raw_text)
        phases = validate_plan_contract(plan_path.read_text())
        if not phases:
            raise PlanResolutionError(
                f"{plan_path} has no implementation phases to approve"
            )
    except (OSError, UnicodeDecodeError, PlanResolutionError) as exc:
        return f"Implementation plan contract invalid: {exc}"
    return None


def _already_advanced(
    plan: ApprovalPlan, *, write_failure: OverviewWriteFailure | None = None
) -> ApprovalResult:
    src = plan.source_phase.value
    tgt = plan.target_phase.value
    return ApprovalResult.already_advanced(
        plan.source_phase,
        plan.target_phase,
        (
            f"Gate already crossed: another approval advanced '{src}' -> '{tgt}'; "
            f"this call made no change"
        ),
        write_failure=write_failure,
    )


def _classify_phase_moved(
    observed: Phase | None,
    plan: ApprovalPlan,
    *,
    write_failure: OverviewWriteFailure | None = None,
) -> ApprovalResult:
    """Classify every re-check and write-stage CAS miss by the same rule."""
    if observed is not None and observed == plan.target_phase:
        return _already_advanced(plan, write_failure=write_failure)
    observed_label = observed.value if observed is not None else "?"
    return ApprovalResult.rejected(
        ApprovalRejection.OVERVIEW_STATE_CONFLICT,
        (
            f"Overview phase moved to '{observed_label}', not the gate target "
            f"'{plan.target_phase.value}'; refusing to treat as an approval"
        ),
        source=plan.source_phase,
        target=plan.target_phase,
        write_failure=write_failure,
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
            assert write.failure is not None
            return _classify_phase_moved(
                write.observed_phase, plan, write_failure=write.failure
            )
        assert write.failure is not None
        return ApprovalResult.rejected(
            ApprovalRejection.OVERVIEW_WRITE_FAILED,
            write.message or "Overview write failed",
            source=plan.source_phase,
            target=plan.target_phase,
            write_failure=write.failure,
        )

    try:
        atomic_write_text(session_path / SIGNAL_FILENAME, "{}")
    except OSError as exc:
        return ApprovalResult.approved_signal_retained(
            plan.source_phase,
            plan.target_phase,
            (
                f"Phase advanced to '{tgt}' but the pending signal could not be "
                f"cleared: {exc}. The retained _signal.json is inert (it cannot "
                f"re-advance the session); clearing it is optional."
            ),
        )

    return ApprovalResult.approved(
        plan.source_phase,
        plan.target_phase,
        f"Approved {src} -> {tgt}",
    )


_REJECTION_EXIT_CODES: dict[ApprovalRejection, int] = {
    ApprovalRejection.LOCK_CONTENDED: 3,  # transient contention; retry
    ApprovalRejection.LOCK_IO_FAILED: 4,  # non-contention lock fault; not retryable
    ApprovalRejection.OVERVIEW_WRITE_FAILED: 4,  # write fault; may be transient
    ApprovalRejection.OVERVIEW_STATE_CONFLICT: 4,  # external writer moved the phase
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
