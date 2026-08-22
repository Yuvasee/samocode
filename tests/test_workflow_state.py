
import threading
import time
from pathlib import Path

import pytest

import worker.workflow_state as ws
from worker.phases import Phase
from worker.signals import Signal, SignalStatus
from worker.workflow_event import RejectionReason
from worker.workflow_state import (
    LockState,
    OutcomeKind,
    OverviewParseError,
    OverviewTransition,
    OverviewWriteError,
    apply_overview_transition,
    apply_overview_transition_locked,
    atomic_write_text,
    parse_overview_state,
    process_workflow_event,
    read_overview_state,
    render_overview,
    session_lock,
)


def _overview_text(
    phase: str = "investigation",
    blocked: str = "no",
    last_action: str = "Session initialized",
    next_action: str = "Run dive",
    extra_flow: str = "",
) -> str:
    flow = "- [001 @ 08-22 16:40] Session initialized"
    if extra_flow:
        flow += f"\n{extra_flow}"
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
        f"{flow}\n\n"
        "## Files\n"
        "- _overview.md\n"
    )


def _write_overview(session: Path, **kwargs: str) -> Path:
    overview = session / "_overview.md"
    overview.write_text(_overview_text(**kwargs))
    return overview


def _raise_oserror(*_args: object, **_kwargs: object) -> None:
    raise OSError("injected replace failure")




class TestParseOverviewState:
    def test_valid_parses_fields(self) -> None:
        result = parse_overview_state(
            _overview_text(phase="implementation", blocked="no")
        )
        state = result.state
        assert state is not None
        assert state.phase is Phase.IMPLEMENTATION
        assert state.blocked == "no"
        assert state.last_action == "Session initialized"
        assert state.next_action == "Run dive"

    def test_blocked_value_lenient_allows_waiting_human(self) -> None:
        result = parse_overview_state(_overview_text(blocked="waiting_human"))
        assert result.state is not None
        assert result.state.blocked == "waiting_human"

    def test_missing_phase(self) -> None:
        text = _overview_text().replace("Phase: investigation\n", "")
        assert parse_overview_state(text).error is OverviewParseError.MISSING_PHASE

    def test_duplicate_phase(self) -> None:
        text = _overview_text().replace(
            "Phase: investigation\n", "Phase: investigation\nPhase: testing\n"
        )
        assert parse_overview_state(text).error is OverviewParseError.DUPLICATE_PHASE

    def test_malformed_phase(self) -> None:
        assert parse_overview_state(_overview_text(phase="bogus")).error is (
            OverviewParseError.MALFORMED_PHASE
        )

    def test_missing_blocked(self) -> None:
        text = _overview_text().replace("Blocked: no\n", "")
        assert parse_overview_state(text).error is OverviewParseError.MISSING_BLOCKED

    def test_malformed_blocked_empty(self) -> None:
        text = _overview_text().replace("Blocked: no\n", "Blocked:\n")
        assert parse_overview_state(text).error is OverviewParseError.MALFORMED_BLOCKED

    def test_missing_last_action(self) -> None:
        text = _overview_text().replace("Last Action: Session initialized\n", "")
        assert (
            parse_overview_state(text).error is OverviewParseError.MISSING_LAST_ACTION
        )

    def test_duplicate_next(self) -> None:
        text = _overview_text().replace(
            "Next: Run dive\n", "Next: Run dive\nNext: Again\n"
        )
        assert parse_overview_state(text).error is OverviewParseError.DUPLICATE_NEXT

    def test_missing_flow_log(self) -> None:
        text = _overview_text().replace("## Flow Log\n", "")
        assert parse_overview_state(text).error is OverviewParseError.MISSING_FLOW_LOG

    def test_duplicate_flow_log(self) -> None:
        text = _overview_text() + "\n## Flow Log\n- extra\n"
        assert parse_overview_state(text).error is OverviewParseError.DUPLICATE_FLOW_LOG

    def test_read_missing_file(self, temp_session: Path) -> None:
        assert (
            read_overview_state(temp_session).error is OverviewParseError.FILE_NOT_FOUND
        )

    def test_unrelated_lines_ignored(self, temp_session: Path) -> None:
        _write_overview(temp_session)
        result = read_overview_state(temp_session)
        assert result.state is not None
        assert result.state.phase is Phase.INVESTIGATION




class TestAtomicWriteText:
    def test_writes_new_file(self, tmp_path: Path) -> None:
        target = tmp_path / "f.md"
        atomic_write_text(target, "hello")
        assert target.read_text() == "hello"

    def test_overwrites_existing(self, tmp_path: Path) -> None:
        target = tmp_path / "f.md"
        target.write_text("old")
        atomic_write_text(target, "new")
        assert target.read_text() == "new"

    def test_no_temp_residue_on_success(self, tmp_path: Path) -> None:
        target = tmp_path / "f.md"
        atomic_write_text(target, "data")
        assert [p.name for p in tmp_path.iterdir()] == ["f.md"]

    def test_replace_failure_leaves_original_and_no_residue(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "f.md"
        target.write_text("original")
        monkeypatch.setattr(ws.os, "replace", _raise_oserror)
        with pytest.raises(OSError):
            atomic_write_text(target, "new")
        assert target.read_text() == "original"
        assert [p.name for p in tmp_path.iterdir()] == ["f.md"]




class TestRenderOverview:
    def test_replaces_phase_only(self) -> None:
        state = parse_overview_state(_overview_text(phase="investigation")).state
        assert state is not None
        out = render_overview(
            state, OverviewTransition(target_phase=Phase.REQUIREMENTS)
        )
        assert "Phase: requirements\n" in out
        assert "Blocked: no\n" in out
        assert "Last Action: Session initialized\n" in out

    def test_replaces_all_fields(self) -> None:
        state = parse_overview_state(_overview_text()).state
        assert state is not None
        out = render_overview(
            state,
            OverviewTransition(
                target_phase=Phase.IMPLEMENTATION,
                blocked="no",
                last_action="Plan approved",
                next_action="Execute phase 1",
            ),
        )
        assert "Phase: implementation\n" in out
        assert "Last Action: Plan approved\n" in out
        assert "Next: Execute phase 1\n" in out

    def test_appends_flow_log_entry(self) -> None:
        state = parse_overview_state(_overview_text()).state
        assert state is not None
        out = render_overview(
            state,
            OverviewTransition(
                target_phase=Phase.REQUIREMENTS, flow_log_entry="- new entry"
            ),
        )
        lines = out.splitlines()
        flow_idx = lines.index("## Flow Log")
        files_idx = lines.index("## Files")
        section = [ln for ln in lines[flow_idx + 1 : files_idx] if ln.strip()]
        assert "- new entry" in section
        assert section[-1] == "- new entry"

    def test_unrelated_lines_preserved(self) -> None:
        state = parse_overview_state(_overview_text()).state
        assert state is not None
        out = render_overview(
            state, OverviewTransition(target_phase=Phase.REQUIREMENTS)
        )
        assert "Iteration: 1\n" in out
        assert "## Files\n" in out




class TestApplyOverviewTransition:
    def test_success_changes_phase_and_appends_flow_log(
        self, temp_session: Path
    ) -> None:
        _write_overview(temp_session)
        result = apply_overview_transition(
            temp_session,
            OverviewTransition(
                target_phase=Phase.REQUIREMENTS, flow_log_entry="- audit"
            ),
        )
        assert result.ok
        assert result.new_phase is Phase.REQUIREMENTS
        text = (temp_session / "_overview.md").read_text()
        assert "Phase: requirements\n" in text
        assert "- audit" in text

    def test_parse_failure_leaves_file_unchanged(self, temp_session: Path) -> None:
        overview = temp_session / "_overview.md"
        overview.write_text(_overview_text().replace("## Flow Log\n", ""))
        before = overview.read_text()
        result = apply_overview_transition(
            temp_session, OverviewTransition(target_phase=Phase.REQUIREMENTS)
        )
        assert not result.ok
        assert result.error is OverviewWriteError.PARSE_FAILED
        assert result.parse_error is OverviewParseError.MISSING_FLOW_LOG
        assert overview.read_text() == before

    def test_write_failure_leaves_file_unchanged(
        self, temp_session: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        overview = _write_overview(temp_session)
        before = overview.read_text()
        monkeypatch.setattr(ws.os, "replace", _raise_oserror)
        result = apply_overview_transition(
            temp_session, OverviewTransition(target_phase=Phase.REQUIREMENTS)
        )
        assert not result.ok
        assert result.error is OverviewWriteError.WRITE_FAILED
        assert overview.read_text() == before




class TestSessionLockSerialization:
    """`flock` treats separate file descriptions as independent lock owners."""

    def test_apply_refused_while_session_lock_held(self, temp_session: Path) -> None:
        overview = _write_overview(temp_session, phase="investigation")
        before = overview.read_text()
        with session_lock(temp_session) as held:
            assert held.state is LockState.ACQUIRED
            result = apply_overview_transition(
                temp_session,
                OverviewTransition(target_phase=Phase.REQUIREMENTS),
                expected_source=Phase.INVESTIGATION,
            )
        assert not result.ok
        assert result.error is OverviewWriteError.LOCK_UNAVAILABLE
        assert overview.read_text() == before  # lost update prevented

    def test_apply_succeeds_after_lock_released(self, temp_session: Path) -> None:
        _write_overview(temp_session, phase="investigation")
        with session_lock(temp_session) as held:
            assert held.state is LockState.ACQUIRED
        result = apply_overview_transition(
            temp_session,
            OverviewTransition(target_phase=Phase.REQUIREMENTS),
            expected_source=Phase.INVESTIGATION,
        )
        assert result.ok
        assert "Phase: requirements\n" in (temp_session / "_overview.md").read_text()

    def test_locked_entry_point_writes_when_caller_owns_lock(
        self, temp_session: Path
    ) -> None:
        _write_overview(temp_session, phase="investigation")
        with session_lock(temp_session) as held:
            assert held.state is LockState.ACQUIRED
            result = apply_overview_transition_locked(
                temp_session,
                OverviewTransition(target_phase=Phase.REQUIREMENTS),
                expected_source=Phase.INVESTIGATION,
            )
        assert result.ok
        assert "Phase: requirements\n" in (temp_session / "_overview.md").read_text()

    def test_process_event_refused_while_session_lock_held(
        self, temp_session: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ws, "WORKER_LOCK_WAIT_SECONDS", 0.05)
        before = _write_overview(temp_session, phase="investigation").read_text()
        with session_lock(temp_session) as held:
            assert held.state is LockState.ACQUIRED
            outcome = process_workflow_event(
                temp_session,
                Signal(status=SignalStatus.CONTINUE, phase="requirements"),
                "investigation",
                1,
                2,
            )
        assert outcome.kind is OutcomeKind.REJECTED_MUTATION
        assert outcome.write_error is OverviewWriteError.LOCK_UNAVAILABLE
        assert not outcome.mutated
        assert (temp_session / "_overview.md").read_text() == before


class TestWorkerLockWait:
    """Exercise deterministic contention through independently opened lock fds."""

    def test_apply_waits_for_lock_release(self, temp_session: Path) -> None:
        _write_overview(temp_session, phase="investigation")
        acquired = threading.Event()
        release = threading.Event()

        def holder() -> None:
            with session_lock(temp_session) as held:
                assert held.state is LockState.ACQUIRED
                acquired.set()
                release.wait(1.0)

        thread = threading.Thread(target=holder)
        thread.start()
        try:
            assert acquired.wait(1.0)
            timer = threading.Timer(0.15, release.set)
            timer.start()
            result = apply_overview_transition(
                temp_session,
                OverviewTransition(target_phase=Phase.REQUIREMENTS),
                expected_source=Phase.INVESTIGATION,
                wait_timeout=5.0,
            )
            timer.cancel()
        finally:
            release.set()
            thread.join(1.0)

        assert result.ok
        assert "Phase: requirements\n" in (temp_session / "_overview.md").read_text()

    def test_apply_gives_up_when_wait_timeout_elapses(self, temp_session: Path) -> None:
        before = _write_overview(temp_session, phase="investigation").read_text()
        acquired = threading.Event()
        release = threading.Event()

        def holder() -> None:
            with session_lock(temp_session) as held:
                assert held.state is LockState.ACQUIRED
                acquired.set()
                release.wait(1.0)

        thread = threading.Thread(target=holder)
        thread.start()
        try:
            assert acquired.wait(1.0)
            start = time.monotonic()
            result = apply_overview_transition(
                temp_session,
                OverviewTransition(target_phase=Phase.REQUIREMENTS),
                expected_source=Phase.INVESTIGATION,
                wait_timeout=0.05,
            )
            elapsed = time.monotonic() - start
        finally:
            release.set()
            thread.join(1.0)

        assert not result.ok
        assert result.error is OverviewWriteError.LOCK_UNAVAILABLE
        assert elapsed < 1.0  # gave up near the timeout, not at the holder's release
        assert (temp_session / "_overview.md").read_text() == before




def _signal(
    status: SignalStatus, phase: str | None = None, waiting_for: str | None = None
) -> Signal:
    return Signal(status=status, phase=phase, waiting_for=waiting_for)


class TestProcessWorkflowEvent:
    def test_same_phase_continue_no_mutation(self, temp_session: Path) -> None:
        before = _write_overview(temp_session).read_text()
        outcome = process_workflow_event(
            temp_session, _signal(SignalStatus.CONTINUE), "investigation", 1, 2
        )
        assert outcome.kind is OutcomeKind.ACCEPTED_NO_CHANGE
        assert outcome.accepted and not outcome.mutated
        assert (temp_session / "_overview.md").read_text() == before

    def test_accepted_transition_mutates(self, temp_session: Path) -> None:
        _write_overview(temp_session, phase="investigation")
        outcome = process_workflow_event(
            temp_session,
            _signal(SignalStatus.CONTINUE, "requirements"),
            "investigation",
            1,
            2,
        )
        assert outcome.kind is OutcomeKind.ACCEPTED_TRANSITION
        assert outcome.accepted and outcome.mutated
        text = (temp_session / "_overview.md").read_text()
        assert "Phase: requirements\n" in text
        assert "Phase transition: investigation -> requirements" in text

    def test_status_not_allowed_rejected(self, temp_session: Path) -> None:
        _write_overview(temp_session, phase="init")
        outcome = process_workflow_event(
            temp_session, _signal(SignalStatus.DONE), "init", 1, 1
        )
        assert outcome.kind is OutcomeKind.REJECTED_VALIDATION
        assert outcome.rejection_reason is RejectionReason.STATUS_NOT_ALLOWED
        assert not outcome.mutated

    def test_invalid_transition_rejected(self, temp_session: Path) -> None:
        before = _write_overview(temp_session, phase="implementation").read_text()
        outcome = process_workflow_event(
            temp_session, _signal(SignalStatus.CONTINUE, "done"), "implementation", 1, 2
        )
        assert outcome.rejection_reason is RejectionReason.TRANSITION_NOT_ALLOWED
        assert (temp_session / "_overview.md").read_text() == before

    def test_iteration_limit_rejected(self, temp_session: Path) -> None:
        _write_overview(temp_session, phase="init")
        outcome = process_workflow_event(
            temp_session, _signal(SignalStatus.CONTINUE, "investigation"), "init", 6, 6
        )
        assert outcome.rejection_reason is RejectionReason.ITERATION_LIMIT_EXCEEDED
        assert not outcome.mutated

    def test_gated_continue_requires_approval(self, temp_session: Path) -> None:
        before = _write_overview(temp_session, phase="planning").read_text()
        outcome = process_workflow_event(
            temp_session,
            _signal(SignalStatus.CONTINUE, "implementation"),
            "planning",
            1,
            2,
        )
        assert outcome.rejection_reason is RejectionReason.TRANSITION_REQUIRES_APPROVAL
        assert (temp_session / "_overview.md").read_text() == before

    def test_waiting_same_phase_no_mutation(self, temp_session: Path) -> None:
        before = _write_overview(temp_session, phase="planning").read_text()
        outcome = process_workflow_event(
            temp_session,
            _signal(SignalStatus.WAITING, "planning", "plan_approval"),
            "planning",
            1,
            2,
        )
        assert outcome.kind is OutcomeKind.ACCEPTED_NO_CHANGE
        assert not outcome.mutated
        assert (temp_session / "_overview.md").read_text() == before

    def test_waiting_cannot_change_phase(self, temp_session: Path) -> None:
        before = _write_overview(temp_session, phase="planning").read_text()
        outcome = process_workflow_event(
            temp_session,
            _signal(SignalStatus.WAITING, "implementation", "plan_approval"),
            "planning",
            1,
            2,
        )
        assert outcome.rejection_reason is RejectionReason.WAITING_CANNOT_CHANGE_PHASE
        assert (temp_session / "_overview.md").read_text() == before

    def test_blocked_cannot_change_phase(self, temp_session: Path) -> None:
        before = _write_overview(temp_session, phase="implementation").read_text()
        outcome = process_workflow_event(
            temp_session, _signal(SignalStatus.BLOCKED, "done"), "implementation", 1, 2
        )
        assert outcome.rejection_reason is RejectionReason.BLOCKED_CANNOT_CHANGE_PHASE
        assert (temp_session / "_overview.md").read_text() == before

    def test_mutation_failure_becomes_rejection(
        self, temp_session: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        before = _write_overview(temp_session, phase="investigation").read_text()
        monkeypatch.setattr(ws.os, "replace", _raise_oserror)
        outcome = process_workflow_event(
            temp_session,
            _signal(SignalStatus.CONTINUE, "requirements"),
            "investigation",
            1,
            2,
        )
        assert outcome.kind is OutcomeKind.REJECTED_MUTATION
        assert not outcome.accepted and not outcome.mutated
        assert outcome.write_error is OverviewWriteError.WRITE_FAILED
        assert (temp_session / "_overview.md").read_text() == before

    def test_recovery_after_mutation_failure(
        self, temp_session: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_overview(temp_session, phase="investigation")
        monkeypatch.setattr(ws.os, "replace", _raise_oserror)
        first = process_workflow_event(
            temp_session,
            _signal(SignalStatus.CONTINUE, "requirements"),
            "investigation",
            1,
            2,
        )
        assert not first.accepted
        monkeypatch.undo()
        second = process_workflow_event(
            temp_session,
            _signal(SignalStatus.CONTINUE, "requirements"),
            "investigation",
            1,
            3,
        )
        assert second.kind is OutcomeKind.ACCEPTED_TRANSITION
        assert "Phase: requirements\n" in (temp_session / "_overview.md").read_text()

    def test_malformed_overview_rejects_transition(self, temp_session: Path) -> None:
        overview = temp_session / "_overview.md"
        overview.write_text(
            _overview_text(phase="investigation").replace("## Flow Log\n", "")
        )
        outcome = process_workflow_event(
            temp_session,
            _signal(SignalStatus.CONTINUE, "requirements"),
            "investigation",
            1,
            2,
        )
        assert outcome.kind is OutcomeKind.REJECTED_MUTATION
        assert outcome.parse_error is OverviewParseError.MISSING_FLOW_LOG
