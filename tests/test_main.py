import json
import logging
import subprocess
from pathlib import Path

import pytest

from worker import cli as main
from worker import workflow_state
from worker.approval import ApprovalOutcome, approve_session
from worker.config import ProjectConfig
from worker.phases import Phase
from worker.signals import Signal, SignalStatus
from worker.workflow_event import RejectionReason
from worker.workflow_state import (
    OverviewParseError,
    OverviewWriteResult,
    read_overview_state,
)


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


def _git_project(tmp_path: Path) -> tuple[Path, str]:
    project = tmp_path / "working"
    project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    subprocess.run(
        ["git", "-C", str(project), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(project), "config", "user.name", "Test"], check=True
    )
    (project / "file.txt").write_text("tested\n")
    subprocess.run(["git", "-C", str(project), "add", "file.txt"], check=True)
    subprocess.run(["git", "-C", str(project), "commit", "-qm", "initial"], check=True)
    head = subprocess.run(
        ["git", "-C", str(project), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return project, head


def _write_final_polish_evidence(session: Path, head: str) -> None:
    (session / "01-code-clarity.md").write_text(
        f"Reviewed HEAD: {head}\nResult: clean\nDisposition: settled\n"
    )
    (session / "02-comment-hygiene.md").write_text(
        f"Input HEAD: {head}\nOutput HEAD: {head}\nSafety check: PASS\n"
    )
    (session / "03-test-report.md").write_text(
        f"Run: 2nd (post-quality)\nResult: PASS\nTested HEAD: {head}\n"
    )
    transitions = (
        ("implementation", "testing"),
        ("testing", "quality"),
        ("quality", "testing"),
        ("testing", "pr-readiness"),
    )
    rows = []
    for iteration, (source, target) in enumerate(transitions, 1):
        rows.append(
            json.dumps(
                {
                    "v": 2,
                    "timestamp": "2026-08-24 08:00:00",
                    "iteration": iteration,
                    "source_phase": source,
                    "target_phase": target,
                    "status": "continue",
                    "accepted": True,
                    "validation_error": None,
                    "outcome_kind": "accepted_transition",
                    "mutated": True,
                }
            )
        )
    (session / "_signal_history.jsonl").write_text("\n".join(rows) + "\n")


class TestProcessSignalBootstrap:
    def test_no_overview_validates_and_records_against_init(
        self, tmp_path: Path
    ) -> None:
        session = tmp_path / "_sessions" / "task"
        session.mkdir(parents=True)
        sig = _sig(SignalStatus.CONTINUE)

        result = main.apply_signal(sig, None, session, 1, _logger())

        assert result.status is SignalStatus.CONTINUE
        rows = _history_rows(session)
        assert len(rows) == 1
        assert rows[0]["source_phase"] == "init"
        assert rows[0]["accepted"] is True

    def test_no_overview_enforces_init_iteration_limit(self, tmp_path: Path) -> None:
        session = tmp_path / "_sessions" / "task"
        session.mkdir(parents=True)
        for i in range(1, 6):
            main.apply_signal(_sig(SignalStatus.CONTINUE), None, session, i, _logger())

        result = main.apply_signal(
            _sig(SignalStatus.CONTINUE), None, session, 6, _logger()
        )

        assert result.status is SignalStatus.BLOCKED
        assert "iteration limit" in (result.reason or "")

    def test_parseable_successor_overview_is_recorded_against_init(
        self, tmp_path: Path
    ) -> None:
        session = _session(tmp_path, "investigation")
        sig = _sig(SignalStatus.CONTINUE)

        result = main.apply_signal(sig, None, session, 1, _logger())

        assert result.status is SignalStatus.CONTINUE
        assert _history_rows(session)[0]["source_phase"] == "init"

    def test_bootstrap_signal_cannot_skip_past_successor(self, tmp_path: Path) -> None:
        session = _session(tmp_path, "investigation")
        sig = _sig(SignalStatus.CONTINUE, phase="requirements")

        result = main.apply_signal(sig, None, session, 1, _logger())

        assert result.status is SignalStatus.BLOCKED
        assert _history_rows(session)[0]["source_phase"] == "init"

    def test_unparseable_overview_blocks(self, tmp_path: Path) -> None:
        session = tmp_path / "_sessions" / "task"
        session.mkdir(parents=True)
        (session / "_overview.md").write_text("# no status fields here\n")
        sig = _sig(SignalStatus.CONTINUE)

        result = main.apply_signal(sig, None, session, 1, _logger())

        assert result.status is SignalStatus.BLOCKED
        assert result.needs == "investigation"
        assert "unparseable" in (result.reason or "")
        assert not (session / "_signal_history.jsonl").exists()

    def test_unreadable_overview_blocks_with_read_reason(self, tmp_path: Path) -> None:
        session = tmp_path / "_sessions" / "task"
        session.mkdir(parents=True)
        (session / "_overview.md").mkdir()  # read_text raises OSError -> READ_FAILED
        sig = _sig(SignalStatus.CONTINUE)

        result = main.apply_signal(sig, None, session, 1, _logger())

        assert result.status is SignalStatus.BLOCKED
        assert "cannot be read" in (result.reason or "")
        assert not (session / "_signal_history.jsonl").exists()

    def test_smuggled_bootstrap_phase_blocks(self, tmp_path: Path) -> None:
        session = _session(tmp_path, "quality")
        sig = _sig(SignalStatus.CONTINUE)

        result = main.apply_signal(sig, None, session, 1, _logger())

        assert result.status is SignalStatus.BLOCKED
        assert "smuggled" in (result.reason or "")
        assert not (session / "_signal_history.jsonl").exists()

    def test_real_init_signal_advances_to_investigation(self, tmp_path: Path) -> None:
        session = _session(tmp_path, "init")
        sig = _sig(SignalStatus.CONTINUE, phase="investigation")

        result = main.apply_signal(sig, None, session, 1, _logger())

        assert result.status is SignalStatus.CONTINUE
        assert _overview_phase(session) is Phase.INVESTIGATION
        rows = _history_rows(session)
        assert rows[0]["source_phase"] == "init"
        assert rows[0]["mutated"] is True

    def test_smuggled_bootstrap_phase_resets_overview_to_init(
        self, tmp_path: Path
    ) -> None:
        session = _session(tmp_path, "quality")
        sig = _sig(SignalStatus.CONTINUE)

        result = main.apply_signal(sig, None, session, 1, _logger())

        assert result.status is SignalStatus.BLOCKED
        assert "reset to init" in (result.reason or "")
        assert _overview_phase(session) is Phase.INIT

    def test_failed_reset_quarantines_overview_so_restart_rebootstraps(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = _session(tmp_path, "quality")

        def fail(*_a: object, **_k: object) -> OverviewWriteResult:
            return OverviewWriteResult.write_failed("boom")

        monkeypatch.setattr(main, "apply_overview_transition", fail)
        inspection = main._inspect_bootstrap_overview(session)
        assert inspection.declared_phase is Phase.QUALITY
        recovery = main._reset_or_quarantine_bootstrap_overview(
            session, inspection.declared_phase
        )
        reason = main._format_bootstrap_block_reason(inspection, recovery)

        assert recovery.status is main.BootstrapRecoveryStatus.QUARANTINED
        assert "quarantined" in reason
        assert not (session / "_overview.md").exists()
        assert (session / "_overview.rejected.md").exists()

        assert read_overview_state(session).error is OverviewParseError.FILE_NOT_FOUND
        restart = main._inspect_bootstrap_overview(session)
        assert restart.status is main.BootstrapInspectionStatus.USE_INIT

    def test_failed_reset_and_failed_quarantine_reports_both(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = _session(tmp_path, "quality")

        def fail(*_a: object, **_k: object) -> OverviewWriteResult:
            return OverviewWriteResult.write_failed("boom")

        def bad_replace(_src: object, _dst: object) -> None:
            raise OSError("rename boom")

        monkeypatch.setattr(main, "apply_overview_transition", fail)
        monkeypatch.setattr(main.os, "replace", bad_replace)
        inspection = main._inspect_bootstrap_overview(session)
        assert inspection.declared_phase is Phase.QUALITY
        recovery = main._reset_or_quarantine_bootstrap_overview(
            session, inspection.declared_phase
        )
        reason = main._format_bootstrap_block_reason(inspection, recovery)

        assert recovery.status is main.BootstrapRecoveryStatus.FAILED
        assert "reset FAILED" in reason
        assert "quarantine rename also FAILED" in reason
        assert "manual recovery required before restart" in reason

    def test_already_init_reset_is_achieved_no_quarantine(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = _session(tmp_path, "quality")
        replace_calls: list[tuple[object, object]] = []

        def spy_replace(src: object, dst: object) -> None:
            replace_calls.append((src, dst))

        def moved(*_a: object, **_k: object) -> OverviewWriteResult:
            return OverviewWriteResult.phase_moved(Phase.INIT, "moved")

        monkeypatch.setattr(main.os, "replace", spy_replace)
        monkeypatch.setattr(main, "apply_overview_transition", moved)
        inspection = main._inspect_bootstrap_overview(session)
        assert inspection.declared_phase is Phase.QUALITY
        recovery = main._reset_or_quarantine_bootstrap_overview(
            session, inspection.declared_phase
        )
        reason = main._format_bootstrap_block_reason(inspection, recovery)

        assert recovery.status is main.BootstrapRecoveryStatus.ALREADY_SAFE
        assert "already at init" in reason
        assert replace_calls == []  # no quarantine rename attempted

    def test_reset_normalizes_blocked_to_no(self, tmp_path: Path) -> None:
        session = tmp_path / "_sessions" / "task"
        session.mkdir(parents=True)
        (session / "_overview.md").write_text(
            _overview_text("quality", blocked="waiting_human")
        )

        inspection = main._inspect_bootstrap_overview(session)
        assert inspection.declared_phase is Phase.QUALITY
        recovery = main._reset_or_quarantine_bootstrap_overview(
            session, inspection.declared_phase
        )
        reason = main._format_bootstrap_block_reason(inspection, recovery)

        assert recovery.status is main.BootstrapRecoveryStatus.RESET
        assert "reset to init" in reason
        parsed = read_overview_state(session)
        assert parsed.state is not None
        assert parsed.state.phase is Phase.INIT
        assert parsed.state.blocked == "no"


class TestProcessSignalAccepted:
    def test_continue_no_target_accepted_no_change(self, tmp_path: Path) -> None:
        session = _session(tmp_path, "init")
        sig = _sig(SignalStatus.CONTINUE)

        result = main.apply_signal(sig, "init", session, 1, _logger())

        assert result.status is SignalStatus.CONTINUE
        assert _overview_phase(session) is Phase.INIT
        rows = _history_rows(session)
        assert len(rows) == 1
        assert rows[0]["accepted"] is True
        assert rows[0]["outcome_kind"] == "accepted_no_change"
        assert rows[0]["source_phase"] == "init"

    def test_accepted_transition_mutates_overview(self, tmp_path: Path) -> None:
        session = _session(tmp_path, "init")
        overview = session / "_overview.md"
        overview.write_text(
            overview.read_text().replace("Blocked: no", "Blocked: workflow_error")
        )
        sig = _sig(SignalStatus.CONTINUE, phase="investigation")

        result = main.apply_signal(sig, "init", session, 1, _logger())

        assert result is sig  # original signal returned unchanged
        assert _overview_phase(session) is Phase.INVESTIGATION
        rows = _history_rows(session)
        assert rows[0]["accepted"] is True
        assert rows[0]["mutated"] is True
        assert rows[0]["outcome_kind"] == "accepted_transition"
        parsed = read_overview_state(session)
        assert parsed.state is not None and parsed.state.blocked == "no"

    def test_waiting_accepted_no_change(self, tmp_path: Path) -> None:
        session = _session(tmp_path, "requirements")
        sig = _sig(SignalStatus.WAITING, phase="requirements", for_="qa_answers")

        result = main.apply_signal(sig, "requirements", session, 1, _logger())

        assert result.status is SignalStatus.WAITING
        assert _overview_phase(session) is Phase.REQUIREMENTS
        assert _history_rows(session)[0]["accepted"] is True


class TestProcessSignalRejected:
    def test_invalid_transition_blocked_investigation(self, tmp_path: Path) -> None:
        session = _session(tmp_path, "init")
        sig = _sig(SignalStatus.CONTINUE, phase="quality")

        result = main.apply_signal(sig, "init", session, 1, _logger())

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

        result = main.apply_signal(sig, "planning", session, 1, _logger())

        assert result.status is SignalStatus.BLOCKED
        assert result.needs == "human_decision"
        assert _overview_phase(session) is Phase.PLANNING
        assert _history_rows(session)[0]["rejection_reason"] == (
            "transition_requires_approval"
        )

    def test_done_outside_terminal_blocked(self, tmp_path: Path) -> None:
        session = _session(tmp_path, "implementation")
        sig = _sig(SignalStatus.DONE, phase="implementation")

        result = main.apply_signal(sig, "implementation", session, 1, _logger())

        assert result.status is SignalStatus.BLOCKED
        assert result.needs == "investigation"
        assert _history_rows(session)[0]["rejection_reason"] == "status_not_allowed"

    def test_waiting_cannot_smuggle_done_and_persists_truthful_block(
        self, tmp_path: Path
    ) -> None:
        session = _session(tmp_path, "implementation")
        sig = _sig(SignalStatus.WAITING, phase="done", for_="human_action")

        result = main.apply_signal(sig, "implementation", session, 1, _logger())

        assert result.status is SignalStatus.BLOCKED
        assert _overview_phase(session) is Phase.IMPLEMENTATION
        parsed = read_overview_state(session)
        assert parsed.state is not None
        assert parsed.state.blocked == "workflow_error"
        assert "waiting' cannot change phase" in parsed.state.last_action
        assert _history_rows(session)[0]["rejection_reason"] == (
            "waiting_cannot_change_phase"
        )


class TestIterationLimitBoundary:
    def test_last_allowed_run_accepted(self, tmp_path: Path) -> None:
        session = _session(tmp_path, "pr-readiness")
        for i in range(1, 5):
            main.apply_signal(
                _sig(SignalStatus.CONTINUE), "pr-readiness", session, i, _logger()
            )

        result = main.apply_signal(
            _sig(SignalStatus.CONTINUE), "pr-readiness", session, 5, _logger()
        )

        assert result.status is SignalStatus.CONTINUE  # 5 == max, not exceeded

    def test_boundary_run_blocked_and_counted(self, tmp_path: Path) -> None:
        session = _session(tmp_path, "pr-readiness")
        for i in range(1, 6):
            main.apply_signal(
                _sig(SignalStatus.CONTINUE), "pr-readiness", session, i, _logger()
            )

        result = main.apply_signal(
            _sig(SignalStatus.CONTINUE), "pr-readiness", session, 6, _logger()
        )

        assert result.status is SignalStatus.BLOCKED
        assert result.needs == "human_decision"
        assert "iteration limit" in (result.reason or "")
        rows = [
            r for r in _history_rows(session) if r["source_phase"] == "pr-readiness"
        ]
        assert len(rows) == 6


class TestRejectedMutation:
    def test_write_failure_blocked_investigation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = _session(tmp_path, "init")

        def fail(*_args: object, **_kwargs: object) -> OverviewWriteResult:
            return OverviewWriteResult.write_failed("boom")

        monkeypatch.setattr(workflow_state, "apply_overview_transition", fail)
        sig = _sig(SignalStatus.CONTINUE, phase="investigation")

        result = main.apply_signal(sig, "init", session, 1, _logger())

        assert result.status is SignalStatus.BLOCKED
        assert result.needs == "investigation"
        assert "boom" in (result.reason or "")
        assert _overview_phase(session) is Phase.INIT  # unchanged
        assert _history_rows(session)[0]["outcome_kind"] == "rejected_mutation"


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


class TestPlanningApprovalRestart:
    def test_waiting_then_approve_advances(self, tmp_path: Path) -> None:
        session = _session(tmp_path, "planning")
        project = _project(tmp_path)
        (session / "plan.md").write_text(
            "## Implementation Phases\n\n"
            "### Phase 1: Build feature\n"
            "**Profile:** `standard`\n"
            "- [ ] Implement behavior\n"
        )
        overview = session / "_overview.md"
        overview.write_text(
            overview.read_text() + "\n## Plans\n- plan.md - implementation plan\n"
        )

        waiting = _sig(SignalStatus.WAITING, phase="planning", for_="plan_approval")
        (session / "_signal.json").write_text(
            json.dumps(
                {"status": "waiting", "for": "plan_approval", "phase": "planning"}
            )
        )
        result = main.apply_signal(waiting, "planning", session, 1, _logger())
        assert result.status is SignalStatus.WAITING
        assert _overview_phase(session) is Phase.PLANNING

        approval = approve_session(project, "task")
        assert approval.outcome is ApprovalOutcome.APPROVED
        assert _overview_phase(session) is Phase.IMPLEMENTATION

        cont = main.apply_signal(
            _sig(SignalStatus.CONTINUE), "implementation", session, 2, _logger()
        )
        assert cont.status is SignalStatus.CONTINUE


class TestPrReadinessAutoDone:
    def test_pr_readiness_continue_advances_to_done(self, tmp_path: Path) -> None:
        session = _session(tmp_path, "pr-readiness")
        project, head = _git_project(tmp_path)
        _write_final_polish_evidence(session, head)
        sig = _sig(SignalStatus.CONTINUE, phase="done")

        result = main.apply_signal(
            sig,
            "pr-readiness",
            session,
            5,
            _logger(),
            working_dir=project,
        )

        assert result.status is SignalStatus.CONTINUE
        assert _overview_phase(session) is Phase.DONE
        assert _history_rows(session)[-1]["outcome_kind"] == "accepted_transition"

    def test_missing_final_polish_blocks_and_keeps_truthful_phase(
        self, tmp_path: Path
    ) -> None:
        session = _session(tmp_path, "pr-readiness")
        project, _ = _git_project(tmp_path)

        result = main.apply_signal(
            _sig(SignalStatus.CONTINUE, phase="done"),
            "pr-readiness",
            session,
            1,
            _logger(),
            working_dir=project,
        )

        assert result.status is SignalStatus.BLOCKED
        assert _overview_phase(session) is Phase.PR_READINESS
        parsed = read_overview_state(session)
        assert parsed.state is not None
        assert parsed.state.blocked == "workflow_error"
        assert "Final-polish provenance invalid" in parsed.state.last_action
        assert _history_rows(session)[-1]["rejection_reason"] == (
            "final_polish_invalid"
        )

    def test_done_phase_done_signal_completes(self, tmp_path: Path) -> None:
        session = _session(tmp_path, "done")
        sig = _sig(SignalStatus.DONE, phase="done", summary="all set")

        result = main.apply_signal(sig, "done", session, 1, _logger())

        assert result.status is SignalStatus.DONE
        assert _history_rows(session)[0]["accepted"] is True


class TestCliParsing:
    def test_parse_args_bare_flags_run(self) -> None:
        args = main.parse_args(["--config", "x", "--session", "y"])
        assert args.command == "run"

    def test_parse_args_run_once_flag(self) -> None:
        args = main.parse_args(["run", "--config", "x", "--session", "y", "--once"])
        assert args.once is True
