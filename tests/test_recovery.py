import hashlib
import json
import logging
import subprocess
from argparse import Namespace
from pathlib import Path

import pytest

from worker import cli as main
from worker.config import ProjectConfig, RuntimeConfig, SamocodeConfig
from worker.final_polish import validate_final_polish
from worker.lifecycle import (
    count_epoch_source_phase_runs_including_current,
    validate_phase_provenance,
)
from worker.phases import Phase, is_iteration_limit_exceeded
from worker.process_lease import ProcessLeaseState, acquire_process_lease
from worker.recovery import (
    RecoveryOutcome,
    RecoveryRejection,
    inspect_final_polish_recovery,
    recover_final_polish,
)
from worker.signal_history import count_source_phase_iterations_including_current
from worker.signals import Signal, SignalStatus
from worker.startup import StartupComposition
from worker.workflow_state import read_overview_state


def _overview() -> str:
    return (
        "# Session: Test\n\n"
        "Working Dir: /tmp/example\n\n"
        "## Status\n"
        "Phase: pr-readiness\n"
        "Iteration: 4\n"
        "Total Iterations: 10\n"
        "Blocked: workflow_error\n"
        "Last Action: Workflow event rejected: final polish invalid\n"
        "Next: Repair the workflow state and rerun the current phase\n\n"
        "## Flow Log\n"
        "- [001 @ 08-24 10:00] Work completed\n\n"
        "## Plans\n"
        "- plan.md - active plan\n"
    )


def _history_row(
    source: str,
    target: str,
    iteration: int,
    *,
    accepted: bool = True,
    rejection_reason: str | None = None,
) -> str:
    return json.dumps(
        {
            "v": 2,
            "timestamp": "2026-08-24 10:00:00",
            "iteration": iteration,
            "source_phase": source,
            "target_phase": target,
            "status": "continue",
            "accepted": accepted,
            "validation_error": (
                None if accepted else "Final-polish provenance invalid"
            ),
            "rejection_reason": rejection_reason,
            "outcome_kind": (
                "accepted_transition" if accepted else "rejected_validation"
            ),
            "mutated": accepted,
        }
    )


def _no_change_history_row(source: str, iteration: int) -> str:
    return json.dumps(
        {
            "v": 2,
            "timestamp": "2026-08-24 10:00:00",
            "iteration": iteration,
            "source_phase": source,
            "target_phase": source,
            "status": "continue",
            "accepted": True,
            "validation_error": None,
            "rejection_reason": None,
            "outcome_kind": "accepted_no_change",
            "mutated": False,
        }
    )


def _insert_before_latest_history_row(session: Path, rows: list[str]) -> None:
    history_path = session / "_signal_history.jsonl"
    existing = history_path.read_text().splitlines()
    history_path.write_text("\n".join([*existing[:-1], *rows, existing[-1]]) + "\n")


def _git_init(path: Path) -> str:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Test"], check=True
    )
    (path / "file.txt").write_text("clean\n")
    subprocess.run(["git", "-C", str(path), "add", "file.txt"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "initial"], check=True)
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _recoverable_project(tmp_path: Path) -> tuple[Path, Path, Path, str]:
    main_repo = tmp_path / "repo"
    worktrees = tmp_path / "worktrees"
    sessions = tmp_path / "sessions"
    main_repo.mkdir()
    worktrees.mkdir()
    sessions.mkdir()
    config = tmp_path / ".samocode"
    config.write_text(
        f"MAIN_REPO={main_repo}\nWORKTREES={worktrees}\nSESSIONS={sessions}\n"
    )

    session = sessions / "26-08-24-task"
    session.mkdir()
    (session / "_overview.md").write_text(_overview())
    (session / "_signal.json").write_text(
        json.dumps({"status": "continue", "phase": "done"})
    )
    (session / "plan.md").write_text(
        "## Implementation Phases\n\n"
        "### Phase 1: Finished\n"
        "**Profile:** `standard`\n"
        "- [x] Complete feature\n"
    )
    history = [
        _history_row("quality", "testing", 7),
        _history_row("testing", "pr-readiness", 8),
        _history_row(
            "pr-readiness",
            "done",
            9,
            accepted=False,
            rejection_reason="final_polish_invalid",
        ),
    ]
    (session / "_signal_history.jsonl").write_text("\n".join(history) + "\n")

    working_dir = worktrees / session.name
    head = _git_init(working_dir)
    (session / "01-code-clarity.md").write_text(
        f"Reviewed HEAD: {head}\nResult: clean\nDisposition: settled\n"
    )
    (session / "02-comment-hygiene.md").write_text(
        f"Input HEAD: {head}\nOutput HEAD: {head}\nSafety check: PASS\n"
    )
    (session / "03-test-report.md").write_text(
        f"Run: 2nd (post-quality)\nResult: PASS\nTested HEAD: {head}\n"
    )
    return config, session, working_dir, head


def test_check_is_read_only_and_reports_exact_recoverable_state(tmp_path: Path) -> None:
    config, session, _working_dir, _head = _recoverable_project(tmp_path)
    before = {
        path.name: path.read_bytes()
        for path in session.iterdir()
        if path.is_file()
    }

    result = inspect_final_polish_recovery(config, "task")

    assert result.outcome is RecoveryOutcome.RECOVERABLE
    assert result.inspection is not None
    assert not (session / "_recovery").exists()
    assert before == {
        path.name: path.read_bytes()
        for path in session.iterdir()
        if path.is_file()
    }


def test_apply_snapshots_state_preserves_history_and_sets_anchor(tmp_path: Path) -> None:
    config, session, working_dir, _head = _recoverable_project(tmp_path)
    history_before = (session / "_signal_history.jsonl").read_bytes()
    overview_before = (session / "_overview.md").read_bytes()

    result = recover_final_polish(config, "task")

    assert result.outcome is RecoveryOutcome.RECOVERED
    assert result.receipt_path is not None
    parsed = read_overview_state(session)
    assert parsed.state is not None
    assert parsed.state.phase is Phase.IMPLEMENTATION
    assert parsed.state.blocked == "no"
    assert (session / "_signal.json").read_text() == "{}"
    assert (session / "_signal_history.jsonl").read_bytes() == history_before
    receipt = json.loads(result.receipt_path.read_text())
    assert receipt["history_sha256_before"] == hashlib.sha256(history_before).hexdigest()
    assert (result.receipt_path.parent / "_overview.before.md").read_bytes() == overview_before

    still_missing = validate_final_polish(session, working_dir)
    assert not still_missing.ok

    with (session / "_signal_history.jsonl").open("a") as handle:
        for iteration, (source, target) in enumerate(
            (
                ("implementation", "testing"),
                ("testing", "quality"),
                ("quality", "testing"),
                ("testing", "pr-readiness"),
            ),
            10,
        ):
            handle.write(_history_row(source, target, iteration) + "\n")
    assert validate_final_polish(session, working_dir).ok


def test_epoch_phase_budget_excludes_pre_recovery_quality_runs(
    tmp_path: Path,
) -> None:
    config, session, _working_dir, _head = _recoverable_project(tmp_path)
    _insert_before_latest_history_row(
        session,
        [_no_change_history_row("quality", iteration) for iteration in range(10, 35)],
    )
    lifetime_count = count_source_phase_iterations_including_current(session, "quality")
    assert lifetime_count > 20
    assert recover_final_polish(config, "task").ok

    first_epoch_run = count_epoch_source_phase_runs_including_current(
        session, "quality"
    )

    assert first_epoch_run.count == 1


def test_epoch_phase_budget_counts_post_anchor_rows_and_enforces_boundary(
    tmp_path: Path,
) -> None:
    config, session, _working_dir, _head = _recoverable_project(tmp_path)
    assert recover_final_polish(config, "task").ok
    history_path = session / "_signal_history.jsonl"
    with history_path.open("a") as handle:
        for iteration in range(1, 20):
            handle.write(_no_change_history_row("quality", iteration) + "\n")

    at_limit = count_epoch_source_phase_runs_including_current(session, "quality")
    assert at_limit.count == 20
    assert is_iteration_limit_exceeded("quality", at_limit.count)[0] is False

    with history_path.open("a") as handle:
        handle.write(_no_change_history_row("quality", 20) + "\n")
    over_limit = count_epoch_source_phase_runs_including_current(session, "quality")
    assert over_limit.count == 21
    assert is_iteration_limit_exceeded("quality", over_limit.count)[0] is True


def test_apply_signal_uses_recovery_epoch_budget_not_lifetime_history(
    tmp_path: Path,
) -> None:
    config, session, working_dir, _head = _recoverable_project(tmp_path)
    _insert_before_latest_history_row(
        session,
        [_no_change_history_row("quality", iteration) for iteration in range(10, 35)],
    )
    assert recover_final_polish(config, "task").ok
    history_path = session / "_signal_history.jsonl"
    with history_path.open("a") as handle:
        handle.write(_history_row("implementation", "testing", 1) + "\n")
        handle.write(_history_row("testing", "quality", 2) + "\n")
        for iteration in range(3, 6):
            handle.write(_no_change_history_row("quality", iteration) + "\n")
    overview_path = session / "_overview.md"
    overview_path.write_text(
        overview_path.read_text().replace("Phase: implementation", "Phase: quality")
    )

    result = main.apply_signal(
        Signal(status=SignalStatus.CONTINUE),
        "quality",
        session,
        6,
        logging.getLogger("test_epoch_budget"),
        working_dir=working_dir,
    )

    assert result.status is SignalStatus.CONTINUE
    assert count_source_phase_iterations_including_current(session, "quality") > 20
    parsed = read_overview_state(session)
    assert parsed.state is not None
    assert parsed.state.phase is Phase.QUALITY


@pytest.mark.parametrize("mutation", ["dirty", "plan", "signal", "phase"])
def test_recovery_refuses_non_allowlisted_state(
    tmp_path: Path, mutation: str
) -> None:
    config, session, working_dir, _head = _recoverable_project(tmp_path)
    if mutation == "dirty":
        (working_dir / "untracked.txt").write_text("dirty")
    elif mutation == "plan":
        path = session / "plan.md"
        path.write_text(path.read_text().replace("- [x]", "- [ ]"))
    elif mutation == "signal":
        (session / "_signal.json").write_text("{}")
    else:
        path = session / "_overview.md"
        path.write_text(path.read_text().replace("Phase: pr-readiness", "Phase: quality"))

    result = recover_final_polish(config, "task")

    assert result.outcome is RecoveryOutcome.REJECTED
    assert not (session / "_recovery").exists()


def test_second_recovery_is_refused(tmp_path: Path) -> None:
    config, _session, _working_dir, _head = _recoverable_project(tmp_path)
    assert recover_final_polish(config, "task").ok

    result = recover_final_polish(config, "task")

    assert result.rejection in {
        RecoveryRejection.STATE_NOT_RECOVERABLE,
        RecoveryRejection.ALREADY_RECOVERED,
    }


def test_phase_preflight_detects_manual_jump_and_accepts_real_transition(
    tmp_path: Path,
) -> None:
    _config, session, _working_dir, _head = _recoverable_project(tmp_path)
    assert not validate_phase_provenance(session, Phase.QUALITY).ok

    (session / "_signal_history.jsonl").write_text(
        _history_row("implementation", "testing", 1)
        + "\n"
        + _history_row("testing", "quality", 2)
        + "\n"
    )
    assert validate_phase_provenance(session, Phase.QUALITY).ok


def test_process_lease_prevents_concurrent_owner(tmp_path: Path) -> None:
    session = tmp_path / "session"
    first = acquire_process_lease(session)
    second = acquire_process_lease(session)
    try:
        assert first.state is ProcessLeaseState.ACQUIRED
        assert second.state is ProcessLeaseState.CONTENDED
    finally:
        first.release()
        second.release()


def test_recovery_refuses_while_worker_holds_process_lease(tmp_path: Path) -> None:
    config, session, _working_dir, _head = _recoverable_project(tmp_path)
    lease = acquire_process_lease(session)
    try:
        result = recover_final_polish(config, "task")
    finally:
        lease.release()

    assert result.rejection is RecoveryRejection.PROCESS_CONTENDED
    assert not (session / "_recovery").exists()


def test_run_preflight_blocks_before_signal_clear_or_provider_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path, session, _working_dir, _head = _recoverable_project(tmp_path)
    project = ProjectConfig.from_file(config_path)
    runtime = RuntimeConfig(ai_provider="codex")
    config = SamocodeConfig(
        project=project,
        runtime=runtime,
        session_path=session,
        provider="codex",
    )
    monkeypatch.setattr(
        main,
        "compose_startup",
        lambda **_kwargs: StartupComposition(config, (), ()),
    )
    provider_calls: list[object] = []
    monkeypatch.setattr(
        main, "run_ai_with_retry", lambda *_a, **_k: provider_calls.append(object())
    )
    monkeypatch.setattr(main, "notify_blocked", lambda *_a, **_k: None)
    signal_before = (session / "_signal.json").read_bytes()
    overview_before = (session / "_overview.md").read_text()

    main.run_orchestrator(
        Namespace(
            config=str(config_path),
            session="task",
            provider=None,
            timeout=None,
            dive=None,
            task=None,
            once=False,
        )
    )

    assert provider_calls == []
    assert (session / "_signal.json").read_bytes() == signal_before
    parsed = read_overview_state(session)
    assert parsed.state is not None
    assert parsed.state.blocked == "workflow_error"
    assert "Lifecycle preflight rejected state" in parsed.state.last_action
    assert "Total Iterations: 10" in overview_before
    assert "Total Iterations: 10" in parsed.state.raw_text


def test_modified_pre_recovery_history_invalidates_anchor(tmp_path: Path) -> None:
    config, session, working_dir, _head = _recoverable_project(tmp_path)
    assert recover_final_polish(config, "task").ok
    history_path = session / "_signal_history.jsonl"
    history_path.write_bytes(b" " + history_path.read_bytes()[1:])

    result = validate_final_polish(session, working_dir)
    budget = count_epoch_source_phase_runs_including_current(session, "implementation")

    assert "modified" in "; ".join(result.errors)
    assert budget.count is None
    assert "modified" in "; ".join(budget.errors)


def test_missing_applied_recovery_receipt_fails_closed(tmp_path: Path) -> None:
    config, session, working_dir, _head = _recoverable_project(tmp_path)
    result = recover_final_polish(config, "task")
    assert result.receipt_path is not None
    result.receipt_path.unlink()

    check = validate_final_polish(session, working_dir)
    budget = count_epoch_source_phase_runs_including_current(session, "implementation")
    preflight = validate_phase_provenance(session, Phase.IMPLEMENTATION)

    assert "valid receipts; expected 1" in "; ".join(check.errors)
    assert budget.count is None
    assert not preflight.ok


def test_apply_signal_rejects_invalid_epoch_without_phase_mutation(
    tmp_path: Path,
) -> None:
    config, session, working_dir, _head = _recoverable_project(tmp_path)
    recovery = recover_final_polish(config, "task")
    assert recovery.receipt_path is not None
    recovery.receipt_path.unlink()

    result = main.apply_signal(
        Signal(status=SignalStatus.CONTINUE, phase="testing"),
        "implementation",
        session,
        1,
        logging.getLogger("test_invalid_epoch"),
        working_dir=working_dir,
    )

    assert result.status is SignalStatus.BLOCKED
    assert "Recovery epoch invalid" in (result.reason or "")
    parsed = read_overview_state(session)
    assert parsed.state is not None
    assert parsed.state.phase is Phase.IMPLEMENTATION
    assert parsed.state.blocked == "workflow_error"
    latest = json.loads((session / "_signal_history.jsonl").read_text().splitlines()[-1])
    assert latest["rejection_reason"] == "recovery_anchor_invalid"


def test_recovery_cli_parser_requires_explicit_mode() -> None:
    args = main.parse_args(
        [
            "recover",
            "final-polish",
            "--config",
            "x",
            "--session",
            "y",
            "--check",
        ]
    )

    assert args.command == "recover"
    assert args.recovery_kind == "final-polish"
    assert args.check is True
