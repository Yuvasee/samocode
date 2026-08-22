"""Pure, provider-neutral workflow-event validation.

`validate_workflow_event` is the single authority on whether a
(source_phase, status, target, wait-reason, iterations) tuple is a legal workflow
event. It performs no I/O and no mutation. Phase 2's processor and Phase 4's approval
service consume this same contract and add side effects around it.
"""

from dataclasses import dataclass
from enum import Enum

from .phases import Phase, get_phase_config, is_iteration_limit_exceeded
from .signals import SignalStatus


class RejectionReason(Enum):
    """Typed cause of a rejected workflow event (for tests and audit rows)."""

    UNKNOWN_SOURCE_PHASE = "unknown_source_phase"
    UNKNOWN_TARGET_PHASE = "unknown_target_phase"
    STATUS_NOT_ALLOWED = "status_not_allowed"
    ITERATION_LIMIT_EXCEEDED = "iteration_limit_exceeded"
    WAITING_MISSING_REASON = "waiting_missing_reason"
    WAITING_REASON_NOT_ALLOWED = "waiting_reason_not_allowed"
    WAITING_CANNOT_CHANGE_PHASE = "waiting_cannot_change_phase"
    BLOCKED_CANNOT_CHANGE_PHASE = "blocked_cannot_change_phase"
    TRANSITION_NOT_ALLOWED = "transition_not_allowed"
    TRANSITION_REQUIRES_APPROVAL = "transition_requires_approval"
    DONE_ONLY_IN_TERMINAL = "done_only_in_terminal"


@dataclass(frozen=True)
class WorkflowEvent:
    """A provider-neutral request to advance/hold workflow state.

    requested_target None means "stay in source_phase". source_iterations counts runs
    already spent in source_phase, including this one.
    """

    source_phase: str | None
    status: SignalStatus
    requested_target: str | None = None
    waiting_for: str | None = None
    source_iterations: int = 0


@dataclass(frozen=True)
class WorkflowEventResult:
    """Outcome of validating a WorkflowEvent. Never mutates state."""

    accepted: bool
    source_phase: Phase | None
    target_phase: Phase | None
    validation_error: str | None = None
    rejection_reason: RejectionReason | None = None


def _reject(
    source: Phase | None,
    target: Phase | None,
    reason: RejectionReason,
    message: str,
) -> WorkflowEventResult:
    return WorkflowEventResult(
        accepted=False,
        source_phase=source,
        target_phase=target,
        validation_error=message,
        rejection_reason=reason,
    )


def _accept(source: Phase, target: Phase) -> WorkflowEventResult:
    return WorkflowEventResult(accepted=True, source_phase=source, target_phase=target)


def validate_workflow_event(event: WorkflowEvent) -> WorkflowEventResult:
    """Validate one workflow event. Pure: no I/O, no mutation, no logging.

    Fail-fast rule order: source known, status allowed, iteration limit, target
    parseable, then status-specific rules (waiting / blocked / done / continue).
    """
    source_config = get_phase_config(event.source_phase)
    if source_config is None:
        return _reject(
            None,
            None,
            RejectionReason.UNKNOWN_SOURCE_PHASE,
            f"Unknown source phase: {event.source_phase}",
        )
    source = source_config.phase

    if not source_config.is_signal_allowed(event.status.value):
        return _reject(
            source,
            None,
            RejectionReason.STATUS_NOT_ALLOWED,
            f"Signal '{event.status.value}' not allowed in phase '{source.value}'. "
            f"Allowed: {sorted(source_config.allowed_signals)}",
        )

    exceeded, max_allowed = is_iteration_limit_exceeded(source.value, event.source_iterations)
    if exceeded:
        return _reject(
            source,
            None,
            RejectionReason.ITERATION_LIMIT_EXCEEDED,
            f"Phase '{source.value}' exceeded {max_allowed} iteration limit",
        )

    target = source
    is_change = False
    raw_target = event.requested_target
    if raw_target is not None and raw_target.lower() != source.value:
        try:
            target = Phase(raw_target.lower())
            is_change = True
        except ValueError:
            return _reject(
                source,
                None,
                RejectionReason.UNKNOWN_TARGET_PHASE,
                f"Unknown target phase: {raw_target}",
            )

    if event.status is SignalStatus.WAITING:
        reason = (event.waiting_for or "").strip()
        if not reason:
            return _reject(
                source,
                source,
                RejectionReason.WAITING_MISSING_REASON,
                f"Phase '{source.value}' 'waiting' requires a non-empty reason",
            )
        if not source_config.is_wait_allowed(reason):
            return _reject(
                source,
                source,
                RejectionReason.WAITING_REASON_NOT_ALLOWED,
                f"Wait reason '{reason}' not allowed in '{source.value}'. "
                f"Allowed: {sorted(source_config.allowed_waits)}",
            )
        if is_change:
            return _reject(
                source,
                target,
                RejectionReason.WAITING_CANNOT_CHANGE_PHASE,
                f"'waiting' cannot change phase ({source.value} -> {target.value})",
            )
        return _accept(source, source)

    if event.status is SignalStatus.BLOCKED:
        if is_change:
            return _reject(
                source,
                target,
                RejectionReason.BLOCKED_CANNOT_CHANGE_PHASE,
                f"'blocked' cannot change phase ({source.value} -> {target.value})",
            )
        return _accept(source, source)

    if event.status is SignalStatus.DONE:
        if source is not Phase.DONE or (is_change and target is not Phase.DONE):
            return _reject(
                source,
                target,
                RejectionReason.DONE_ONLY_IN_TERMINAL,
                "'done' is only valid in the terminal 'done' phase",
            )
        return _accept(source, Phase.DONE)

    # CONTINUE
    if not is_change:
        return _accept(source, source)
    if source_config.gate_owns_transition(target):
        gate = source_config.approval_gate
        assert gate is not None  # gate_owns_transition guarantees this
        return _reject(
            source,
            target,
            RejectionReason.TRANSITION_REQUIRES_APPROVAL,
            f"Transition {source.value} -> {target.value} requires approval "
            f"(reason '{gate.waiting_for}'); 'continue' cannot cross an approval gate",
        )
    if not source_config.can_transition_to(target):
        return _reject(
            source,
            target,
            RejectionReason.TRANSITION_NOT_ALLOWED,
            f"Invalid transition: {source.value} -> {target.value}. "
            f"Valid targets: {[p.value for p in source_config.allowed_next]}",
        )
    return _accept(source, target)
