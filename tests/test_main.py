"""Tests for main.py Phase 5 orchestrator seam - process_signal + outcome mapping.

Exercises the authoritative path (process_workflow_event + record_processed_outcome)
through main.process_signal with real overview I/O and real history rows (no mocks
except one injected overview-write failure). Also covers the outcome->signal mapping,
iteration-limit boundary counting, the planning approval restart lifecycle, automatic
pr-readiness->done, and implicit `run` CLI parsing.
"""

import json
import logging
from pathlib import Path

import pytest

import main
from worker import workflow_state
from worker.approval import ApprovalOutcome, approve_session
from worker.config import ProjectConfig
from worker.phases import Phase
from worker.signals import Signal, SignalStatus
from worker.workflow_event import RejectionReason
from worker.workflow_state import (
    OverviewWriteError,
    OverviewWriteResult,
    read_overview_state,
)

# =============================================================================
# Helpers
# =============================================================================


def _overview_text(
    phase: str,
    blocked: str = "no",
    last_action: str = "Did work",
    next_action: str = "Do next",
) -> str:
    return (
        "# Session: Test\n\n"
        "## Status\n"
        f"Phase: {phase}\n"
        "Iteration: 1\n"
        "Total Iterations: 1\n"
        f"Blocked: {blocked}\n"
        f"Last Action: {last_action}\n"
        f"Next: {next_action}\n\n"
        "## Flow Log\n"
        "- [001 @ 08-22 16:40] Session initialized\n\n"
        "## Files\n"
        "- _overview.md\n"
    )


def _session(tmp_path: Path, phase: str, name: str = "task") -> Path:
    session = tmp_path / "_sessions" / name
    session.mkdir(parents=True)
    (session / "_overview.md").write_text(_overview_text(phase))
    return session


def _sig(
    status: SignalStatus,
    phase: str | None = None,
    for_: str | None = None,
    summary: str | None = None,
) -> Signal:
    return Signal(status=status, phase=phase, waiting_for=for_, summary=summary)


def _logger() -> logging.Logger:
    return logging.getLogger("test_main")


def _overview_phase(session: Path) -> Phase:
    parsed = read_overview_state(session)
    assert parsed.state is not None
    return parsed.state.phase


def _history_rows(session: Path) -> list[dict[str, object]]:
    path = session / "_signal_history.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _project(tmp_path: Path) -> ProjectConfig:
    repo = tmp_path / "repo"
    worktrees = tmp_path / "worktrees"
    repo.mkdir(exist_ok=True)
    worktrees.mkdir(exist_ok=True)
    return ProjectConfig(
        main_repo=repo, worktrees=worktrees, sessions=tmp_path / "_sessions"
    )


# =============================================================================
# Bootstrap: unknown source phase
# =============================================================================


class TestProcessSignalBootstrap:
    def test_no_overview_validates_and_records_against_init(
        self, tmp_path: Path
    ) -> None:
        # No overview yet: bootstrap validates against init, records an audit row.
        session = tmp_path / "_sessions" / "task"
        session.mkdir(parents=True)
        sig = _sig(SignalStatus.CONTINUE)

        result = main.process_signal(sig, None, session, 1, _logger())

        assert result.status is SignalStatus.CONTINUE
        rows = _history_rows(session)
        assert len(rows) == 1
        assert rows[0]["source_phase"] == "init"
        assert rows[0]["accepted"] is True

    def test_no_overview_enforces_init_iteration_limit(self, tmp_path: Path) -> None:
        # init max_iterations = 5; the 6th run in init trips the limit.
        session = tmp_path / "_sessions" / "task"
        session.mkdir(parents=True)
        for i in range(1, 6):
            main.process_signal(
                _sig(SignalStatus.CONTINUE), None, session, i, _logger()
            )

        result = main.process_signal(
            _sig(SignalStatus.CONTINUE), None, session, 6, _logger()
        )

        assert result.status is SignalStatus.BLOCKED
        assert "iteration limit" in (result.reason or "")

    def test_parseable_successor_overview_is_recorded_against_init(
        self, tmp_path: Path
    ) -> None:
        # Init is the authoritative bootstrap source: even when the child wrote a legal
        # init successor into the overview, this iteration is counted/recorded as init,
        # never trusting the child's self-declared later phase.
        session = _session(tmp_path, "investigation")
        sig = _sig(SignalStatus.CONTINUE)

        result = main.process_signal(sig, None, session, 1, _logger())

        assert result.status is SignalStatus.CONTINUE
        assert _history_rows(session)[0]["source_phase"] == "init"

    def test_bootstrap_signal_cannot_skip_past_successor(self, tmp_path: Path) -> None:
        # A child that wrote 'investigation' and then signals a jump to 'requirements'
        # must not advance two phases in one iteration: validated as an init event,
        # init->requirements is rejected (init reaches only investigation).
        session = _session(tmp_path, "investigation")
        sig = _sig(SignalStatus.CONTINUE, phase="requirements")

        result = main.process_signal(sig, None, session, 1, _logger())

        assert result.status is SignalStatus.BLOCKED
        assert _history_rows(session)[0]["source_phase"] == "init"

    def test_unparseable_overview_blocks(self, tmp_path: Path) -> None:
        session = tmp_path / "_sessions" / "task"
        session.mkdir(parents=True)
        (session / "_overview.md").write_text("# no status fields here\n")
        sig = _sig(SignalStatus.CONTINUE)

        result = main.process_signal(sig, None, session, 1, _logger())

        assert result.status is SignalStatus.BLOCKED
        assert result.needs == "investigation"
        assert "unparseable" in (result.reason or "")
        assert not (session / "_signal_history.jsonl").exists()

    def test_unreadable_overview_blocks_with_read_reason(self, tmp_path: Path) -> None:
        # An overview that exists but cannot be read (here: a directory) is reported as
        # a read failure, distinct from an unparseable file.
        session = tmp_path / "_sessions" / "task"
        session.mkdir(parents=True)
        (session / "_overview.md").mkdir()  # read_text raises OSError -> READ_FAILED
        sig = _sig(SignalStatus.CONTINUE)

        result = main.process_signal(sig, None, session, 1, _logger())

        assert result.status is SignalStatus.BLOCKED
        assert "cannot be read" in (result.reason or "")
        assert not (session / "_signal_history.jsonl").exists()

    def test_smuggled_bootstrap_phase_blocks(self, tmp_path: Path) -> None:
        # A child-written overview phase that init cannot reach (e.g. 'quality') is
        # phase smuggling: block instead of trusting it. init->investigation stays valid.
        session = _session(tmp_path, "quality")
        sig = _sig(SignalStatus.CONTINUE)

        result = main.process_signal(sig, None, session, 1, _logger())

        assert result.status is SignalStatus.BLOCKED
        assert "smuggled" in (result.reason or "")
        assert not (session / "_signal_history.jsonl").exists()


# =============================================================================
# Accepted outcomes
# =============================================================================


class TestProcessSignalAccepted:
    def test_continue_no_target_accepted_no_change(self, tmp_path: Path) -> None:
        session = _session(tmp_path, "init")
        sig = _sig(SignalStatus.CONTINUE)

        result = main.process_signal(sig, "init", session, 1, _logger())

        assert result.status is SignalStatus.CONTINUE
        assert _overview_phase(session) is Phase.INIT
        rows = _history_rows(session)
        assert len(rows) == 1
        assert rows[0]["accepted"] is True
        assert rows[0]["outcome_kind"] == "accepted_no_change"
        assert rows[0]["source_phase"] == "init"

    def test_accepted_transition_mutates_overview(self, tmp_path: Path) -> None:
        session = _session(tmp_path, "init")
        sig = _sig(SignalStatus.CONTINUE, phase="investigation")

        result = main.process_signal(sig, "init", session, 1, _logger())

        assert result is sig  # original signal returned unchanged
        assert _overview_phase(session) is Phase.INVESTIGATION
        rows = _history_rows(session)
        assert rows[0]["accepted"] is True
        assert rows[0]["mutated"] is True
        assert rows[0]["outcome_kind"] == "accepted_transition"

    def test_waiting_accepted_no_change(self, tmp_path: Path) -> None:
        session = _session(tmp_path, "requirements")
        sig = _sig(SignalStatus.WAITING, phase="requirements", for_="qa_answers")

        result = main.process_signal(sig, "requirements", session, 1, _logger())

        assert result.status is SignalStatus.WAITING
        assert _overview_phase(session) is Phase.REQUIREMENTS
        assert _history_rows(session)[0]["accepted"] is True


# =============================================================================
# Rejected outcomes
# =============================================================================


class TestProcessSignalRejected:
    def test_invalid_transition_blocked_investigation(self, tmp_path: Path) -> None:
        session = _session(tmp_path, "init")
        sig = _sig(SignalStatus.CONTINUE, phase="quality")

        result = main.process_signal(sig, "init", session, 1, _logger())

        assert result.status is SignalStatus.BLOCKED
        assert result.phase == "init"
        assert result.needs == "investigation"
        assert "Invalid transition" in (result.reason or "")
        assert _overview_phase(session) is Phase.INIT  # no mutation
        rows = _history_rows(session)
        assert rows[0]["accepted"] is False
        assert rows[0]["rejection_reason"] == "transition_not_allowed"

    def test_gate_transition_blocked_human_decision(self, tmp_path: Path) -> None:
        session = _session(tmp_path, "planning")
        sig = _sig(SignalStatus.CONTINUE, phase="implementation")

        result = main.process_signal(sig, "planning", session, 1, _logger())

        assert result.status is SignalStatus.BLOCKED
        assert result.needs == "human_decision"
        assert _overview_phase(session) is Phase.PLANNING
        assert _history_rows(session)[0]["rejection_reason"] == (
            "transition_requires_approval"
        )

    def test_done_outside_terminal_blocked(self, tmp_path: Path) -> None:
        session = _session(tmp_path, "implementation")
        sig = _sig(SignalStatus.DONE, phase="implementation")

        result = main.process_signal(sig, "implementation", session, 1, _logger())

        assert result.status is SignalStatus.BLOCKED
        assert result.needs == "investigation"
        assert _history_rows(session)[0]["rejection_reason"] == "status_not_allowed"


# =============================================================================
# Iteration-limit boundary (off-by-one guard)
# =============================================================================


class TestIterationLimitBoundary:
    def test_last_allowed_run_accepted(self, tmp_path: Path) -> None:
        # pr-readiness max_iterations = 5. Seed 4 prior rows -> this is the 5th run.
        session = _session(tmp_path, "pr-readiness")
        for i in range(1, 5):
            main.process_signal(
                _sig(SignalStatus.CONTINUE), "pr-readiness", session, i, _logger()
            )

        result = main.process_signal(
            _sig(SignalStatus.CONTINUE), "pr-readiness", session, 5, _logger()
        )

        assert result.status is SignalStatus.CONTINUE  # 5 == max, not exceeded

    def test_boundary_run_blocked_and_counted(self, tmp_path: Path) -> None:
        session = _session(tmp_path, "pr-readiness")
        for i in range(1, 6):
            main.process_signal(
                _sig(SignalStatus.CONTINUE), "pr-readiness", session, i, _logger()
            )

        result = main.process_signal(
            _sig(SignalStatus.CONTINUE), "pr-readiness", session, 6, _logger()
        )

        assert result.status is SignalStatus.BLOCKED
        assert result.needs == "human_decision"
        assert "iteration limit" in (result.reason or "")
        # The blocked boundary run is still recorded: 6 rows for pr-readiness.
        rows = [
            r for r in _history_rows(session) if r["source_phase"] == "pr-readiness"
        ]
        assert len(rows) == 6


# =============================================================================
# Rejected mutation (injected overview-write failure)
# =============================================================================


class TestRejectedMutation:
    def test_write_failure_blocked_investigation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = _session(tmp_path, "init")

        def fail(*_args: object, **_kwargs: object) -> OverviewWriteResult:
            return OverviewWriteResult(
                ok=False, error=OverviewWriteError.WRITE_FAILED, message="boom"
            )

        monkeypatch.setattr(workflow_state, "apply_overview_transition", fail)
        sig = _sig(SignalStatus.CONTINUE, phase="investigation")

        result = main.process_signal(sig, "init", session, 1, _logger())

        assert result.status is SignalStatus.BLOCKED
        assert result.needs == "investigation"
        assert "boom" in (result.reason or "")
        assert _overview_phase(session) is Phase.INIT  # unchanged
        assert _history_rows(session)[0]["outcome_kind"] == "rejected_mutation"


# =============================================================================
# needs mapping
# =============================================================================


class TestNeedsMapping:
    def test_human_decision_set(self) -> None:
        assert main._needs_for_rejection(
            RejectionReason.TRANSITION_REQUIRES_APPROVAL
        ) == ("human_decision")
        assert main._needs_for_rejection(RejectionReason.ITERATION_LIMIT_EXCEEDED) == (
            "human_decision"
        )

    def test_investigation_default(self) -> None:
        for reason in (
            RejectionReason.TRANSITION_NOT_ALLOWED,
            RejectionReason.STATUS_NOT_ALLOWED,
            RejectionReason.DONE_ONLY_IN_TERMINAL,
            None,
        ):
            assert main._needs_for_rejection(reason) == "investigation"


# =============================================================================
# Planning approval restart lifecycle
# =============================================================================


class TestPlanningApprovalRestart:
    def test_waiting_then_approve_advances(self, tmp_path: Path) -> None:
        session = _session(tmp_path, "planning")
        project = _project(tmp_path)

        # 1. Agent waits for plan approval; phase stays planning.
        waiting = _sig(SignalStatus.WAITING, phase="planning", for_="plan_approval")
        (session / "_signal.json").write_text(
            json.dumps(
                {"status": "waiting", "for": "plan_approval", "phase": "planning"}
            )
        )
        result = main.process_signal(waiting, "planning", session, 1, _logger())
        assert result.status is SignalStatus.WAITING
        assert _overview_phase(session) is Phase.PLANNING

        # 2. Human approves out-of-band -> overview advances to implementation.
        approval = approve_session(project, "task")
        assert approval.outcome is ApprovalOutcome.APPROVED
        assert _overview_phase(session) is Phase.IMPLEMENTATION

        # 3. Restart: an implementation continue is now accepted.
        cont = main.process_signal(
            _sig(SignalStatus.CONTINUE), "implementation", session, 2, _logger()
        )
        assert cont.status is SignalStatus.CONTINUE


# =============================================================================
# Automatic pr-readiness -> done
# =============================================================================


class TestPrReadinessAutoDone:
    def test_pr_readiness_continue_advances_to_done(self, tmp_path: Path) -> None:
        session = _session(tmp_path, "pr-readiness")
        sig = _sig(SignalStatus.CONTINUE, phase="done")

        result = main.process_signal(sig, "pr-readiness", session, 1, _logger())

        assert result.status is SignalStatus.CONTINUE
        assert _overview_phase(session) is Phase.DONE
        assert _history_rows(session)[0]["outcome_kind"] == "accepted_transition"

    def test_done_phase_done_signal_completes(self, tmp_path: Path) -> None:
        session = _session(tmp_path, "done")
        sig = _sig(SignalStatus.DONE, phase="done", summary="all set")

        result = main.process_signal(sig, "done", session, 1, _logger())

        assert result.status is SignalStatus.DONE
        assert _history_rows(session)[0]["accepted"] is True


# =============================================================================
# CLI parsing (implicit legacy run)
# =============================================================================


class TestCliParsing:
    def test_parse_args_bare_flags_run(self) -> None:
        args = main.parse_args(["--config", "x", "--session", "y"])
        assert args.command == "run"

    def test_parse_args_run_once_flag(self) -> None:
        args = main.parse_args(["run", "--config", "x", "--session", "y", "--once"])
        assert args.once is True
