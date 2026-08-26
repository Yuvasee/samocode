import pytest

from worker.phases import Phase
from worker.signals import SignalStatus
from worker.workflow_event import (
    RejectionReason,
    WorkflowEvent,
    WorkflowEventResult,
    validate_workflow_event,
)


def _event(
    source: str | None,
    status: SignalStatus,
    target: str | None = None,
    waiting_for: str | None = None,
    source_iterations: int = 0,
) -> WorkflowEvent:
    return WorkflowEvent(
        source_phase=source,
        status=status,
        requested_target=target,
        waiting_for=waiting_for,
        source_iterations=source_iterations,
    )


class TestSourceAndStatus:
    def test_unknown_source_phase(self) -> None:
        result = validate_workflow_event(_event("bogus", SignalStatus.CONTINUE))
        assert not result.accepted
        assert result.rejection_reason is RejectionReason.UNKNOWN_SOURCE_PHASE
        assert result.source_phase is None

    def test_status_not_allowed_done_in_init(self) -> None:
        result = validate_workflow_event(_event("init", SignalStatus.DONE))
        assert not result.accepted
        assert result.rejection_reason is RejectionReason.STATUS_NOT_ALLOWED

    def test_status_not_allowed_waiting_in_testing(self) -> None:
        result = validate_workflow_event(_event("testing", SignalStatus.WAITING))
        assert not result.accepted
        assert result.rejection_reason is RejectionReason.STATUS_NOT_ALLOWED

    def test_unknown_target_on_continue(self) -> None:
        result = validate_workflow_event(
            _event("init", SignalStatus.CONTINUE, target="bogus")
        )
        assert not result.accepted
        assert result.rejection_reason is RejectionReason.UNKNOWN_TARGET_PHASE


class TestIterationLimit:
    def test_exceeded_rejects_even_valid_continue(self) -> None:
        result = validate_workflow_event(
            _event("init", SignalStatus.CONTINUE, source_iterations=6)
        )
        assert not result.accepted
        assert result.rejection_reason is RejectionReason.ITERATION_LIMIT_EXCEEDED

    def test_exactly_at_limit_accepts(self) -> None:
        result = validate_workflow_event(
            _event("init", SignalStatus.CONTINUE, source_iterations=5)
        )
        assert result.accepted


class TestWaiting:
    def test_requirements_qa_answers_accepts(self) -> None:
        result = validate_workflow_event(
            _event("requirements", SignalStatus.WAITING, waiting_for="qa_answers")
        )
        assert result.accepted
        assert result.target_phase is Phase.REQUIREMENTS

    def test_planning_plan_approval_accepts_and_stays(self) -> None:
        result = validate_workflow_event(
            _event("planning", SignalStatus.WAITING, waiting_for="plan_approval")
        )
        assert result.accepted
        assert result.source_phase is Phase.PLANNING
        assert result.target_phase is Phase.PLANNING

    def test_implementation_human_action_accepts(self) -> None:
        result = validate_workflow_event(
            _event("implementation", SignalStatus.WAITING, waiting_for="human_action")
        )
        assert result.accepted

    def test_missing_reason_rejects(self) -> None:
        result = validate_workflow_event(
            _event("requirements", SignalStatus.WAITING, waiting_for="")
        )
        assert not result.accepted
        assert result.rejection_reason is RejectionReason.WAITING_MISSING_REASON
        assert result.target_phase is None  # no phase change was requested

    def test_unlisted_reason_rejects(self) -> None:
        result = validate_workflow_event(
            _event("requirements", SignalStatus.WAITING, waiting_for="plan_approval")
        )
        assert not result.accepted
        assert result.rejection_reason is RejectionReason.WAITING_REASON_NOT_ALLOWED

    def test_cannot_change_phase(self) -> None:
        result = validate_workflow_event(
            _event(
                "planning",
                SignalStatus.WAITING,
                target="implementation",
                waiting_for="plan_approval",
            )
        )
        assert not result.accepted
        assert result.rejection_reason is RejectionReason.WAITING_CANNOT_CHANGE_PHASE
        assert result.target_phase is Phase.IMPLEMENTATION  # the requested target


class TestBlocked:
    def test_staying_accepts(self) -> None:
        for phase in Phase:
            result = validate_workflow_event(_event(phase.value, SignalStatus.BLOCKED))
            assert result.accepted, phase.value
            assert result.target_phase is phase

    def test_changing_phase_rejects(self) -> None:
        result = validate_workflow_event(
            _event("planning", SignalStatus.BLOCKED, target="implementation")
        )
        assert not result.accepted
        assert result.rejection_reason is RejectionReason.BLOCKED_CANNOT_CHANGE_PHASE


class TestContinue:
    def test_staying_accepts(self) -> None:
        result = validate_workflow_event(
            _event("implementation", SignalStatus.CONTINUE)
        )
        assert result.accepted
        assert result.target_phase is Phase.IMPLEMENTATION

    def test_requirements_to_planning_accepts(self) -> None:
        result = validate_workflow_event(
            _event("requirements", SignalStatus.CONTINUE, target="planning")
        )
        assert result.accepted
        assert result.target_phase is Phase.PLANNING

    def test_planning_to_implementation_requires_approval(self) -> None:
        result = validate_workflow_event(
            _event("planning", SignalStatus.CONTINUE, target="implementation")
        )
        assert not result.accepted
        assert result.rejection_reason is RejectionReason.TRANSITION_REQUIRES_APPROVAL

    def test_pr_readiness_to_done_accepts(self) -> None:
        result = validate_workflow_event(
            _event("pr-readiness", SignalStatus.CONTINUE, target="done")
        )
        assert result.accepted
        assert result.target_phase is Phase.DONE

    def test_disallowed_transition_rejects(self) -> None:
        result = validate_workflow_event(
            _event("init", SignalStatus.CONTINUE, target="done")
        )
        assert not result.accepted
        assert result.rejection_reason is RejectionReason.TRANSITION_NOT_ALLOWED

    def test_implementation_to_quality_rejects(self) -> None:
        result = validate_workflow_event(
            _event("implementation", SignalStatus.CONTINUE, target="quality")
        )
        assert not result.accepted
        assert result.rejection_reason is RejectionReason.TRANSITION_NOT_ALLOWED

    def test_pr_readiness_to_quality_accepts_for_recovery(self) -> None:
        result = validate_workflow_event(
            _event("pr-readiness", SignalStatus.CONTINUE, target="quality")
        )
        assert result.accepted
        assert result.target_phase is Phase.QUALITY

    def test_case_insensitive_target(self) -> None:
        result = validate_workflow_event(
            _event("requirements", SignalStatus.CONTINUE, target="PLANNING")
        )
        assert result.accepted
        assert result.target_phase is Phase.PLANNING

    def test_testing_to_pr_readiness_has_no_pure_layer_gate(self) -> None:
        """R4's final-polish prerequisite gate is I/O-layer only (apply_workflow_event);
        the pure layer must keep allowing this transition unconditionally so the two
        layers never enforce conflicting rules."""
        result = validate_workflow_event(
            _event("testing", SignalStatus.CONTINUE, target="pr-readiness")
        )
        assert result.accepted
        assert result.target_phase is Phase.PR_READINESS


class TestDone:
    def test_done_in_done_accepts(self) -> None:
        result = validate_workflow_event(_event("done", SignalStatus.DONE))
        assert result.accepted
        assert result.target_phase is Phase.DONE

    def test_done_in_non_done_rejected_by_status(self) -> None:
        result = validate_workflow_event(_event("quality", SignalStatus.DONE))
        assert not result.accepted
        assert result.rejection_reason is RejectionReason.STATUS_NOT_ALLOWED


class TestResultShape:
    @pytest.mark.parametrize(
        "event",
        [
            _event("requirements", SignalStatus.CONTINUE, target="planning"),
            _event("planning", SignalStatus.CONTINUE, target="implementation"),
            _event("init", SignalStatus.CONTINUE, target="done"),
        ],
    )
    def test_error_none_iff_accepted(self, event: WorkflowEvent) -> None:
        result = validate_workflow_event(event)
        assert (result.validation_error is None) == result.accepted
        assert (result.rejection_reason is None) == result.accepted

    def test_accepted_result_requires_both_phases(self) -> None:
        with pytest.raises(ValueError, match="source and target"):
            WorkflowEventResult(source_phase=Phase.INIT, target_phase=None)

    def test_rejected_result_requires_message(self) -> None:
        with pytest.raises(ValueError, match="validation error"):
            WorkflowEventResult(
                source_phase=Phase.INIT,
                target_phase=None,
                rejection_reason=RejectionReason.UNKNOWN_TARGET_PHASE,
            )
