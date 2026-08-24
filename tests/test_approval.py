import json
import threading
from pathlib import Path

import pytest

import main
from worker import approval
from worker.approval import (
    ApprovalOutcome,
    ApprovalRejection,
    ApprovalResult,
    approve,
    approve_session,
    check_approval,
    exit_code_for,
)
from worker.config import ProjectConfig
from worker.phases import Phase
from worker.signals import Signal, SignalStatus
from worker.workflow_state import (
    LockOutcome,
    LockState,
    OverviewTransition,
    OverviewWriteError,
    OverviewWriteResult,
    parse_overview_state,
)


def _overview_text(
    phase: str = "planning",
    blocked: str = "no",
    last_action: str = "Plan drafted",
    next_action: str = "Await approval",
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
        "- _overview.md\n\n"
        "## Plans\n"
        "- plan.md - test plan\n"
    )


def _state(phase: str = "planning"):
    result = parse_overview_state(_overview_text(phase=phase))
    assert result.state is not None
    return result.state


def _waiting_signal(
    reason: str = "plan_approval", phase: str | None = "planning"
) -> Signal:
    return Signal(status=SignalStatus.WAITING, waiting_for=reason, phase=phase)


def _write_signal(
    session: Path,
    status: str = "waiting",
    for_: str | None = "plan_approval",
    phase: str | None = "planning",
) -> None:
    payload: dict[str, object] = {"status": status}
    if for_ is not None:
        payload["for"] = for_
    if phase is not None:
        payload["phase"] = phase
    (session / "_signal.json").write_text(json.dumps(payload))


def _make_session(
    sessions_dir: Path, name: str = "task", phase: str = "planning"
) -> Path:
    session = sessions_dir / name
    session.mkdir(parents=True)
    (session / "_overview.md").write_text(_overview_text(phase=phase))
    (session / "plan.md").write_text(
        "## Implementation Phases\n\n"
        "### Phase 1: Build feature\n"
        "**Profile:** `standard`\n"
        "- [ ] Implement behavior\n"
    )
    _write_signal(session)
    return session


def _project(sessions_dir: Path, tmp_path: Path) -> ProjectConfig:
    repo = tmp_path / "repo"
    worktrees = tmp_path / "worktrees"
    repo.mkdir(exist_ok=True)
    worktrees.mkdir(exist_ok=True)
    return ProjectConfig(main_repo=repo, worktrees=worktrees, sessions=sessions_dir)


@pytest.fixture
def sessions_dir(tmp_path: Path) -> Path:
    d = tmp_path / "_sessions"
    d.mkdir()
    return d


class TestCheckApproval:
    def test_accepts_planning_plan_approval_waiting(self) -> None:
        check = check_approval(_state("planning"), _waiting_signal())
        assert check.plan is not None
        assert check.plan.source_phase is Phase.PLANNING
        assert check.plan.target_phase is Phase.IMPLEMENTATION

    def test_rejects_phase_without_gate_implementation(self) -> None:
        check = check_approval(_state("implementation"), _waiting_signal())
        assert check.rejection is ApprovalRejection.PHASE_HAS_NO_GATE

    def test_rejects_phase_without_gate_testing(self) -> None:
        check = check_approval(_state("testing"), _waiting_signal())
        assert check.rejection is ApprovalRejection.PHASE_HAS_NO_GATE

    def test_rejects_non_waiting_continue(self) -> None:
        sig = Signal(status=SignalStatus.CONTINUE, phase="planning")
        assert (
            check_approval(_state(), sig).rejection
            is ApprovalRejection.SIGNAL_NOT_WAITING
        )

    def test_rejects_non_waiting_blocked(self) -> None:
        sig = Signal(status=SignalStatus.BLOCKED, phase="planning")
        assert (
            check_approval(_state(), sig).rejection
            is ApprovalRejection.SIGNAL_NOT_WAITING
        )

    def test_rejects_reason_mismatch(self) -> None:
        sig = _waiting_signal(reason="qa_answers")
        assert (
            check_approval(_state(), sig).rejection
            is ApprovalRejection.SIGNAL_REASON_MISMATCH
        )

    def test_rejects_missing_reason(self) -> None:
        sig = _waiting_signal(reason="")
        assert (
            check_approval(_state(), sig).rejection
            is ApprovalRejection.SIGNAL_REASON_MISMATCH
        )

    def test_rejects_signal_phase_mismatch(self) -> None:
        sig = _waiting_signal(phase="requirements")
        assert (
            check_approval(_state(), sig).rejection
            is ApprovalRejection.SIGNAL_PHASE_MISMATCH
        )

    def test_accepts_when_signal_phase_absent(self) -> None:
        sig = _waiting_signal(phase=None)
        assert check_approval(_state(), sig).plan is not None

    def test_rejects_gate_target_not_in_graph(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from worker.phases import ApprovalGate, PhaseConfig

        broken = PhaseConfig(
            phase=Phase.PLANNING,
            agent_name="planning-agent",
            allowed_next=frozenset(),  # target not on graph
            allowed_signals=frozenset({"continue", "waiting", "blocked"}),
            max_iterations=10,
            default_profile="max",
            allowed_waits=frozenset({"plan_approval"}),
            approval_gate=ApprovalGate("plan_approval", Phase.IMPLEMENTATION),
        )
        monkeypatch.setattr(approval, "get_phase_config", lambda _p: broken)
        check = check_approval(_state(), _waiting_signal())
        assert check.rejection is ApprovalRejection.GATE_TARGET_INVALID


class TestApproveSessionSuccess:
    def test_advances_planning_to_implementation(
        self, sessions_dir: Path, tmp_path: Path
    ) -> None:
        session = _make_session(sessions_dir)
        result = approve_session(_project(sessions_dir, tmp_path), "task")
        assert result.outcome is ApprovalOutcome.APPROVED
        assert result.advanced is True
        state = parse_overview_state((session / "_overview.md").read_text()).state
        assert state is not None and state.phase is Phase.IMPLEMENTATION

    def test_sets_blocked_no_last_next_and_flow_log(
        self, sessions_dir: Path, tmp_path: Path
    ) -> None:
        session = _make_session(sessions_dir)
        approve_session(_project(sessions_dir, tmp_path), "task")
        text = (session / "_overview.md").read_text()
        state = parse_overview_state(text).state
        assert state is not None
        assert state.blocked == "no"
        assert "Human approved planning -> implementation" in state.last_action
        assert "Enter implementation phase" == state.next_action
        assert "Approved planning -> implementation (reason 'plan_approval')" in text

    def test_consumes_signal_to_empty(self, sessions_dir: Path, tmp_path: Path) -> None:
        session = _make_session(sessions_dir)
        result = approve_session(_project(sessions_dir, tmp_path), "task")
        assert result.signal_consumed is True
        assert json.loads((session / "_signal.json").read_text()) == {}

    def test_result_fields_truthful(self, sessions_dir: Path, tmp_path: Path) -> None:
        _make_session(sessions_dir)
        result = approve_session(_project(sessions_dir, tmp_path), "task")
        assert result.source_phase is Phase.PLANNING
        assert result.target_phase is Phase.IMPLEMENTATION
        assert result.advanced is True
        assert result.rejection is None

    def test_resolver_dated_match_used(
        self, sessions_dir: Path, tmp_path: Path
    ) -> None:
        _make_session(sessions_dir, name="26-08-22-task")
        result = approve_session(_project(sessions_dir, tmp_path), "task")
        assert result.outcome is ApprovalOutcome.APPROVED


class TestApproveSessionRejections:
    def test_outer_lifecycle_phase_cannot_be_approved(
        self, sessions_dir: Path, tmp_path: Path
    ) -> None:
        session = _make_session(sessions_dir)
        (session / "plan.md").write_text(
            "## Implementation Phases\n\n"
            "### Phase 14: PR readiness and approval stop\n"
            "**Profile:** `standard`\n"
            "- [ ] Run the `pr-readiness` gate\n"
        )
        before = (session / "_overview.md").read_text()

        result = approve_session(_project(sessions_dir, tmp_path), "task")

        assert result.rejection is ApprovalRejection.PLAN_INVALID
        assert "outer workflow stage" in (result.message or "")
        assert (session / "_overview.md").read_text() == before
        assert json.loads((session / "_signal.json").read_text())["status"] == "waiting"

    def test_missing_session_dir(self, sessions_dir: Path, tmp_path: Path) -> None:
        result = approve_session(_project(sessions_dir, tmp_path), "ghost")
        assert result.rejection is ApprovalRejection.SESSION_NOT_FOUND
        assert result.advanced is False

    def test_missing_overview(self, sessions_dir: Path, tmp_path: Path) -> None:
        (sessions_dir / "task").mkdir()
        result = approve_session(_project(sessions_dir, tmp_path), "task")
        assert result.rejection is ApprovalRejection.OVERVIEW_INVALID

    def test_malformed_overview(self, sessions_dir: Path, tmp_path: Path) -> None:
        session = sessions_dir / "task"
        session.mkdir()
        (session / "_overview.md").write_text("# no status block\n")
        result = approve_session(_project(sessions_dir, tmp_path), "task")
        assert result.rejection is ApprovalRejection.OVERVIEW_INVALID
        assert result.parse_error is not None

    def test_phase_without_gate(self, sessions_dir: Path, tmp_path: Path) -> None:
        _make_session(sessions_dir, phase="implementation")
        result = approve_session(_project(sessions_dir, tmp_path), "task")
        assert result.rejection is ApprovalRejection.PHASE_HAS_NO_GATE

    def test_missing_signal_file(self, sessions_dir: Path, tmp_path: Path) -> None:
        session = _make_session(sessions_dir)
        (session / "_signal.json").unlink()
        result = approve_session(_project(sessions_dir, tmp_path), "task")
        assert result.rejection is ApprovalRejection.SIGNAL_NOT_WAITING

    def test_wrong_reason(self, sessions_dir: Path, tmp_path: Path) -> None:
        session = _make_session(sessions_dir)
        _write_signal(session, for_="qa_answers")
        before = (session / "_overview.md").read_text()
        result = approve_session(_project(sessions_dir, tmp_path), "task")
        assert result.rejection is ApprovalRejection.SIGNAL_REASON_MISMATCH
        assert (session / "_overview.md").read_text() == before

    def test_wrong_status(self, sessions_dir: Path, tmp_path: Path) -> None:
        session = _make_session(sessions_dir)
        _write_signal(session, status="continue")
        result = approve_session(_project(sessions_dir, tmp_path), "task")
        assert result.rejection is ApprovalRejection.SIGNAL_NOT_WAITING

    def test_wrong_signal_phase(self, sessions_dir: Path, tmp_path: Path) -> None:
        session = _make_session(sessions_dir)
        _write_signal(session, phase="requirements")
        result = approve_session(_project(sessions_dir, tmp_path), "task")
        assert result.rejection is ApprovalRejection.SIGNAL_PHASE_MISMATCH


class TestIdempotencyConcurrency:
    def test_repeated_approve_rejected_after_success(
        self, sessions_dir: Path, tmp_path: Path
    ) -> None:
        session = _make_session(sessions_dir)
        project = _project(sessions_dir, tmp_path)
        first = approve_session(project, "task")
        assert first.outcome is ApprovalOutcome.APPROVED
        second = approve_session(project, "task")
        assert second.rejection is ApprovalRejection.PHASE_HAS_NO_GATE
        text = (session / "_overview.md").read_text()
        assert text.count("Approved planning -> implementation") == 1

    def test_stale_signal_after_advance_does_not_readvance(
        self, sessions_dir: Path, tmp_path: Path
    ) -> None:
        session = _make_session(sessions_dir)
        project = _project(sessions_dir, tmp_path)
        approve_session(project, "task")
        _write_signal(session, phase="planning")  # stale waiting signal reappears
        result = approve_session(project, "task")
        assert result.rejection is ApprovalRejection.PHASE_HAS_NO_GATE

    def test_lock_contended_returns_rejection(
        self, sessions_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_session(sessions_dir)
        import contextlib

        @contextlib.contextmanager
        def busy(_session: Path):
            yield LockOutcome(LockState.CONTENDED)

        monkeypatch.setattr(approval, "session_lock", busy)
        result = approve_session(_project(sessions_dir, tmp_path), "task")
        assert result.rejection is ApprovalRejection.LOCK_CONTENDED
        assert exit_code_for(result) == 3

    def test_lock_io_failure_returns_rejection(
        self, sessions_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_session(sessions_dir)
        import contextlib

        @contextlib.contextmanager
        def failed(_session: Path):
            yield LockOutcome(LockState.FAILED, "Cannot acquire approval lock: ENOLCK")

        monkeypatch.setattr(approval, "session_lock", failed)
        result = approve_session(_project(sessions_dir, tmp_path), "task")
        assert result.rejection is ApprovalRejection.LOCK_IO_FAILED
        assert result.advanced is False
        assert exit_code_for(result) == 4

    def test_already_advanced_when_phase_changes_between_precheck_and_lock(
        self, sessions_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_session(sessions_dir)
        import contextlib

        @contextlib.contextmanager
        def advancing_lock(sp: Path):
            (sp / "_overview.md").write_text(_overview_text(phase="implementation"))
            yield LockOutcome(LockState.ACQUIRED)

        monkeypatch.setattr(approval, "session_lock", advancing_lock)
        result = approve_session(_project(sessions_dir, tmp_path), "task")
        assert result.outcome is ApprovalOutcome.ALREADY_ADVANCED
        assert result.advanced is False
        assert exit_code_for(result) == 6

    def test_under_lock_phase_off_target_is_state_conflict(
        self, sessions_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_session(sessions_dir)
        import contextlib

        @contextlib.contextmanager
        def off_target_lock(sp: Path):
            (sp / "_overview.md").write_text(_overview_text(phase="pr-readiness"))
            yield LockOutcome(LockState.ACQUIRED)

        monkeypatch.setattr(approval, "session_lock", off_target_lock)
        result = approve_session(_project(sessions_dir, tmp_path), "task")
        assert result.outcome is ApprovalOutcome.REJECTED
        assert result.rejection is ApprovalRejection.OVERVIEW_STATE_CONFLICT
        assert result.advanced is False
        assert exit_code_for(result) == 4

    def test_write_phase_moved_to_target_maps_to_already_advanced(
        self, sessions_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_session(sessions_dir)

        def moved(*_a: object, **_k: object) -> OverviewWriteResult:
            return OverviewWriteResult.phase_moved(
                Phase.IMPLEMENTATION,
                "moved",
            )

        monkeypatch.setattr(approval, "apply_overview_transition_locked", moved)
        result = approve_session(_project(sessions_dir, tmp_path), "task")
        assert result.outcome is ApprovalOutcome.ALREADY_ADVANCED
        assert result.advanced is False
        assert result.write_error is OverviewWriteError.PHASE_MOVED
        assert exit_code_for(result) == 6

    def test_write_phase_moved_off_target_is_state_conflict(
        self, sessions_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_session(sessions_dir)

        def moved(*_a: object, **_k: object) -> OverviewWriteResult:
            return OverviewWriteResult.phase_moved(
                Phase.PR_READINESS,
                "moved",
            )

        monkeypatch.setattr(approval, "apply_overview_transition_locked", moved)
        result = approve_session(_project(sessions_dir, tmp_path), "task")
        assert result.outcome is ApprovalOutcome.REJECTED
        assert result.rejection is ApprovalRejection.OVERVIEW_STATE_CONFLICT
        assert result.advanced is False
        assert exit_code_for(result) == 4

    def test_under_lock_rejection_with_unchanged_phase_is_honest(
        self, sessions_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_session(sessions_dir)
        import contextlib

        @contextlib.contextmanager
        def clearing_lock(sp: Path):
            (sp / "_signal.json").write_text("{}")
            yield LockOutcome(LockState.ACQUIRED)

        monkeypatch.setattr(approval, "session_lock", clearing_lock)
        result = approve_session(_project(sessions_dir, tmp_path), "task")
        assert result.outcome is ApprovalOutcome.REJECTED
        assert result.rejection is ApprovalRejection.SIGNAL_NOT_WAITING
        assert result.source_phase is Phase.PLANNING

    def test_concurrent_threads_single_advance(
        self, sessions_dir: Path, tmp_path: Path
    ) -> None:
        _make_session(sessions_dir)
        project = _project(sessions_dir, tmp_path)
        barrier = threading.Barrier(5)
        outcomes: list[ApprovalOutcome] = []
        lock = threading.Lock()

        def worker() -> None:
            barrier.wait()
            res = approve_session(project, "task")
            with lock:
                outcomes.append(res.outcome)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert outcomes.count(ApprovalOutcome.APPROVED) == 1
        for o in outcomes:
            assert o in (
                ApprovalOutcome.APPROVED,
                ApprovalOutcome.ALREADY_ADVANCED,
                ApprovalOutcome.REJECTED,  # LOCK_CONTENDED
            )


class TestFailureInjection:
    def test_overview_write_failure(
        self, sessions_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = _make_session(sessions_dir)

        def fail(
            _sp: Path, _t: OverviewTransition, **_kw: object
        ) -> OverviewWriteResult:
            return OverviewWriteResult.write_failed("boom")

        monkeypatch.setattr(approval, "apply_overview_transition_locked", fail)
        result = approve_session(_project(sessions_dir, tmp_path), "task")
        assert result.rejection is ApprovalRejection.OVERVIEW_WRITE_FAILED
        assert result.advanced is False
        assert json.loads((session / "_signal.json").read_text())["status"] == "waiting"

    def test_signal_consume_failure_keeps_advance_recoverable(
        self, sessions_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = _make_session(sessions_dir)
        project = _project(sessions_dir, tmp_path)

        def fail(target: Path, content: str, **_kw: object) -> None:
            if target.name == "_signal.json":
                raise OSError("consume failed")
            raise AssertionError("unexpected atomic_write_text target")

        monkeypatch.setattr(approval, "atomic_write_text", fail)
        result = approve_session(project, "task")
        assert result.outcome is ApprovalOutcome.APPROVED_SIGNAL_RETAINED
        assert result.advanced is True
        assert result.signal_consumed is False
        assert result.rejection is None
        state = parse_overview_state((session / "_overview.md").read_text()).state
        assert state is not None and state.phase is Phase.IMPLEMENTATION
        monkeypatch.undo()
        again = approve_session(project, "task")
        assert again.rejection is ApprovalRejection.PHASE_HAS_NO_GATE


class TestApprovalResultContracts:
    def test_outcome_controls_derived_state(self) -> None:
        approved = ApprovalResult.approved(
            Phase.PLANNING, Phase.IMPLEMENTATION, "approved"
        )
        retained = ApprovalResult.approved_signal_retained(
            Phase.PLANNING, Phase.IMPLEMENTATION, "retained"
        )

        assert approved.advanced and approved.signal_consumed is True
        assert retained.advanced and retained.signal_consumed is False

    def test_non_rejected_result_requires_phase_change(self) -> None:
        with pytest.raises(ValueError, match="phase change"):
            ApprovalResult.approved(Phase.PLANNING, Phase.PLANNING, "approved")

    def test_invalid_overview_rejection_requires_parse_error(self) -> None:
        with pytest.raises(ValueError, match="parse error"):
            ApprovalResult.rejected(
                ApprovalRejection.OVERVIEW_INVALID, "invalid overview"
            )

    def test_lock_rejection_cannot_carry_write_failure(self) -> None:
        failure = OverviewWriteResult.write_failed("write failed").failure
        assert failure is not None
        with pytest.raises(ValueError, match="cannot carry"):
            ApprovalResult.rejected(
                ApprovalRejection.LOCK_CONTENDED,
                "lock contended",
                write_failure=failure,
            )


class TestProviderIndependence:
    def test_ignores_missing_global_toml(
        self, sessions_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        empty = tmp_path / "xdg"
        empty.mkdir()
        monkeypatch.setenv("XDG_CONFIG_HOME", str(empty))
        _make_session(sessions_dir)
        result = approve_session(_project(sessions_dir, tmp_path), "task")
        assert result.outcome is ApprovalOutcome.APPROVED

    def test_ignores_invalid_global_toml(
        self, sessions_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        xdg = tmp_path / "xdg"
        (xdg / "samocode").mkdir(parents=True)
        (xdg / "samocode" / "config.toml").write_text("this is not valid = = toml [[[")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
        _make_session(sessions_dir)
        result = approve_session(_project(sessions_dir, tmp_path), "task")
        assert result.outcome is ApprovalOutcome.APPROVED

    def test_ignores_absent_provider_clis(
        self, sessions_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CLAUDE_PATH", "/nonexistent/claude")
        monkeypatch.setenv("CODEX_PATH", "/nonexistent/codex")
        _make_session(sessions_dir)
        result = approve_session(_project(sessions_dir, tmp_path), "task")
        assert result.outcome is ApprovalOutcome.APPROVED

    def test_module_has_no_routing_imports(self) -> None:
        import ast

        tree = ast.parse(Path(approval.__file__).read_text())
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
        forbidden = {
            ".routing",
            ".adapters",
            ".runner",
            ".startup",
            "worker.routing",
            "worker.adapters",
            "worker.runner",
            "worker.startup",
        }
        assert not (imported & forbidden)
        assert not any("global_config" in m for m in imported)


class TestApproveConfigAndCli:
    def test_approve_invalid_config_path(self, tmp_path: Path) -> None:
        result = approve(tmp_path / "missing.samocode", "task")
        assert result.rejection is ApprovalRejection.CONFIG_INVALID

    def test_approve_loads_config_and_advances(
        self, sessions_dir: Path, tmp_path: Path
    ) -> None:
        _make_session(sessions_dir)
        project = _project(sessions_dir, tmp_path)
        config_file = tmp_path / ".samocode"
        config_file.write_text(
            f"MAIN_REPO={project.main_repo}\n"
            f"WORKTREES={project.worktrees}\n"
            f"SESSIONS={project.sessions}\n"
        )
        result = approve(config_file, "task")
        assert result.outcome is ApprovalOutcome.APPROVED

    def test_exit_code_approved_zero(self) -> None:
        r = ApprovalResult.approved(Phase.PLANNING, Phase.IMPLEMENTATION, "approved")
        assert exit_code_for(r) == 0

    def test_exit_code_already_advanced_fails_fast(self) -> None:
        r = ApprovalResult.already_advanced(
            Phase.PLANNING, Phase.IMPLEMENTATION, "already advanced"
        )
        assert exit_code_for(r) == 6

    def test_exit_code_signal_retained_five(self) -> None:
        r = ApprovalResult.approved_signal_retained(
            Phase.PLANNING, Phase.IMPLEMENTATION, "signal retained"
        )
        assert exit_code_for(r) == 5

    def test_exit_code_lock_contended_three(self) -> None:
        r = ApprovalResult.rejected(ApprovalRejection.LOCK_CONTENDED, "lock contended")
        assert exit_code_for(r) == 3

    def test_exit_code_write_failed_four(self) -> None:
        failure = OverviewWriteResult.write_failed("write failed").failure
        assert failure is not None
        r = ApprovalResult.rejected(
            ApprovalRejection.OVERVIEW_WRITE_FAILED,
            "write failed",
            write_failure=failure,
        )
        assert exit_code_for(r) == 4

    def test_exit_code_generic_rejection_one(self) -> None:
        r = ApprovalResult.rejected(
            ApprovalRejection.SESSION_NOT_FOUND, "session not found"
        )
        assert exit_code_for(r) == 1

    def test_parse_args_approve_subcommand(self) -> None:
        args = main.parse_args(["approve", "--config", "x", "--session", "y"])
        assert args.command == "approve"
        assert args.session == "y"

    def test_parse_args_bare_flags_still_run(self) -> None:
        args = main.parse_args(["--config", "x", "--session", "y"])
        assert args.command == "run"

    def test_cmd_approve_success_exits_zero(
        self, sessions_dir: Path, tmp_path: Path
    ) -> None:
        _make_session(sessions_dir)
        project = _project(sessions_dir, tmp_path)
        config_file = tmp_path / ".samocode"
        config_file.write_text(
            f"MAIN_REPO={project.main_repo}\n"
            f"WORKTREES={project.worktrees}\n"
            f"SESSIONS={project.sessions}\n"
        )
        args = main.parse_args(
            ["approve", "--config", str(config_file), "--session", "task"]
        )
        with pytest.raises(SystemExit) as exc:
            main.cmd_approve(args)
        assert exc.value.code == 0

    def test_cmd_approve_failure_exits_nonzero(
        self, sessions_dir: Path, tmp_path: Path
    ) -> None:
        project = _project(sessions_dir, tmp_path)
        config_file = tmp_path / ".samocode"
        config_file.write_text(
            f"MAIN_REPO={project.main_repo}\n"
            f"WORKTREES={project.worktrees}\n"
            f"SESSIONS={project.sessions}\n"
        )
        args = main.parse_args(
            ["approve", "--config", str(config_file), "--session", "ghost"]
        )
        with pytest.raises(SystemExit) as exc:
            main.cmd_approve(args)
        assert exc.value.code == 1
