"""Pure validation of provider-neutral workflow events."""

from dataclasses import dataclass
from enum import Enum

from .phases import Phase, get_phase_config, is_iteration_limit_exceeded
from .signals import SignalStatus


class RejectionReason(Enum):
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
    FINAL_POLISH_INVALID = "final_polish_invalid"


@dataclass(frozen=True)
class WorkflowEvent:
    """`source_iterations` includes this run; a missing target means stay put."""

    source_phase: str | None
    status: SignalStatus
    requested_target: str | None = None
    waiting_for: str | None = None
    source_iterations: int = 0


@dataclass(frozen=True)
class WorkflowEventResult:
    """Accepted events carry both phases; target == source means stay put.

    On rejection, `target_phase` is the requested target phase — None when the
    event requested no change or the target was not (yet) parsed.
    """

    source_phase: Phase | None
    target_phase: Phase | None
    validation_error: str | None = None
    rejection_reason: RejectionReason | None = None

    def __post_init__(self) -> None:
        if self.rejection_reason is None:
            if self.source_phase is None or self.target_phase is None:
                raise ValueError(
                    "Accepted workflow events require source and target phases"
                )
            if self.validation_error is not None:
                raise ValueError(
                    "Accepted workflow events cannot carry a validation error"
                )
        elif not self.validation_error:
            raise ValueError("Rejected workflow events require a validation error")

    @property
    def accepted(self) -> bool:
        return self.rejection_reason is None

    @classmethod
    def accepted_event(cls, source: Phase, target: Phase) -> "WorkflowEventResult":
        return cls(source_phase=source, target_phase=target)

    @classmethod
    def rejected_event(
        cls,
        source: Phase | None,
        target: Phase | None,
        reason: RejectionReason,
        message: str,
    ) -> "WorkflowEventResult":
        return cls(
            source_phase=source,
            target_phase=target,
            validation_error=message,
            rejection_reason=reason,
        )


def _reject(
    source: Phase | None,
    target: Phase | None,
    reason: RejectionReason,
    message: str,
) -> WorkflowEventResult:
    return WorkflowEventResult.rejected_event(source, target, reason, message)


def _accept(source: Phase, target: Phase) -> WorkflowEventResult:
    return WorkflowEventResult.accepted_event(source, target)


def validate_workflow_event(event: WorkflowEvent) -> WorkflowEventResult:
    """Validate without I/O, mutation, or logging; return the first violation."""
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

    exceeded, max_allowed = is_iteration_limit_exceeded(
        source.value, event.source_iterations
    )
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
    requested = target if is_change else None

    if event.status is SignalStatus.WAITING:
        reason = (event.waiting_for or "").strip()
        if not reason:
            return _reject(
                source,
                requested,
                RejectionReason.WAITING_MISSING_REASON,
                f"Phase '{source.value}' 'waiting' requires a non-empty reason",
            )
        if not source_config.is_wait_allowed(reason):
            return _reject(
                source,
                requested,
                RejectionReason.WAITING_REASON_NOT_ALLOWED,
                f"Wait reason '{reason}' not allowed in '{source.value}'. "
                f"Allowed: {sorted(source_config.allowed_waits)}",
            )
        if is_change:
            return _reject(
                source,
                requested,
                RejectionReason.WAITING_CANNOT_CHANGE_PHASE,
                f"'waiting' cannot change phase ({source.value} -> {target.value})",
            )
        return _accept(source, source)

    if event.status is SignalStatus.BLOCKED:
        if is_change:
            return _reject(
                source,
                requested,
                RejectionReason.BLOCKED_CANNOT_CHANGE_PHASE,
                f"'blocked' cannot change phase ({source.value} -> {target.value})",
            )
        return _accept(source, source)

    if event.status is SignalStatus.DONE:
        if source is not Phase.DONE or (is_change and target is not Phase.DONE):
            return _reject(
                source,
                requested,
                RejectionReason.DONE_ONLY_IN_TERMINAL,
                "'done' is only valid in the terminal 'done' phase",
            )
        return _accept(source, Phase.DONE)

    if not is_change:
        return _accept(source, source)
    if source_config.gate_owns_transition(target):
        gate = source_config.approval_gate
        assert gate is not None
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
