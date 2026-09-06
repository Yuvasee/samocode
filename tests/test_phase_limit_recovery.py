import json
from pathlib import Path

import pytest

from tests.test_recovery import (
    _history_row,
    _no_change_history_row,
    _recoverable_project,
)
from worker import recovery
from worker.cli import build_parser
from worker.lifecycle import (
    TESTING_RUN_SECOND,
    count_epoch_source_phase_runs_including_current,
    derive_testing_run,
    scoped_history,
    validate_phase_provenance,
)
from worker.phases import Phase
from worker.process_lease import ProcessLease, acquire_process_lease
from worker.recovery import (
    RecoveryOutcome,
    RecoveryRejection,
    inspect_phase_limit_recovery,
    recover_phase_limit,
)
from worker.signal_history import record_processed_outcome
from worker.signals import Signal, SignalStatus
from worker.workflow_state import (
    apply_workflow_event,
    read_overview_state,
    session_lock,
)


def _project(tmp_path: Path) -> tuple[Path, Path, Path, str]:
    config, session, working_dir, head = _recoverable_project(tmp_path)
    overview = session / "_overview.md"
    overview.write_text(
        overview.read_text().replace("Phase: pr-readiness", "Phase: quality")
    )
    (session / "_signal.json").write_text('{"status":"continue","phase":"testing"}')
    rows = [
        _history_row("implementation", "testing", 1),
        _history_row("testing", "quality", 2),
    ]
    rows.extend(_no_change_history_row("quality", i) for i in range(3, 23))
    rejection = json.loads(
        _history_row(
            "quality",
            "testing",
            23,
            accepted=False,
            rejection_reason="iteration_limit_exceeded",
        )
    )
    rejection["target_phase"] = None
    rows.append(json.dumps(rejection))
    (session / "_signal_history.jsonl").write_text("\n".join(rows) + "\n")
    (session / "03-test-report.md").unlink()
    return config, session, working_dir, head


def test_check_is_read_only_without_regression_report(tmp_path: Path) -> None:
    config, session, _, _ = _project(tmp_path)
    before = {p.name: p.read_bytes() for p in session.iterdir()}
    assert (
        inspect_phase_limit_recovery(config, "task").outcome
        is RecoveryOutcome.RECOVERABLE
    )
    assert before == {p.name: p.read_bytes() for p in session.iterdir()}


def test_apply_preserves_history_counts_and_admits_regression(tmp_path: Path) -> None:
    config, session, working_dir, _ = _project(tmp_path)
    before = (session / "_signal_history.jsonl").read_bytes()
    count = count_epoch_source_phase_runs_including_current(session, "quality").count
    result = recover_phase_limit(config, "task")
    assert result.outcome is RecoveryOutcome.RECOVERED
    assert result.receipt_path is not None
    assert (session / "_signal_history.jsonl").read_bytes() == before
    assert (
        result.receipt_path.parent / "_signal_history.before.jsonl"
    ).read_bytes() == before
    assert (result.receipt_path.parent / "evidence/02-comment-hygiene.md").is_file()
    state = read_overview_state(session).state
    assert state and state.phase is Phase.TESTING and state.blocked == "no"
    assert validate_phase_provenance(session, Phase.TESTING).ok
    assert derive_testing_run(session) == TESTING_RUN_SECOND
    assert (
        count_epoch_source_phase_runs_including_current(session, "quality").count
        == count
    )
    assert recover_phase_limit(config, "task").outcome is RecoveryOutcome.REJECTED
    signal = Signal(SignalStatus.CONTINUE, phase="pr-readiness")
    outcome = apply_workflow_event(
        session, signal, "testing", 2, 24, working_dir=working_dir
    )
    assert outcome.accepted
    record_processed_outcome(session, signal, 24, outcome)
    assert validate_phase_provenance(session, Phase.PR_READINESS).ok


@pytest.mark.parametrize(
    "fault",
    [
        "dirty",
        "hygiene",
        "clarity",
        "debt",
        "plan",
        "signal",
        "history",
        "count",
        "provenance",
        "malformed",
    ],
)
def test_rejects_unproven_completion(tmp_path: Path, fault: str) -> None:
    config, session, working_dir, _ = _project(tmp_path)
    if fault == "dirty":
        (working_dir / "file.txt").write_text("changed")
    elif fault in {"hygiene", "clarity"}:
        name = "02-comment-hygiene.md" if fault == "hygiene" else "01-code-clarity.md"
        (session / name).write_text("invalid")
    elif fault == "debt":
        (session / "_review_debt.md").write_text(
            "| ID | Decision | Status |\n| CL-001 | undecided | open |\n"
        )
    elif fault == "plan":
        path = session / "plan.md"
        path.write_text(path.read_text().replace("[x]", "[ ]"))
    elif fault == "signal":
        (session / "_signal.json").write_text('{"status":"continue","phase":"done"}')
    else:
        path = session / "_signal_history.jsonl"
        rows = path.read_text().splitlines()
        if fault == "history":
            rows[-1] = rows[-1].replace("iteration_limit_exceeded", "worktree_mutated")
        elif fault == "count":
            rows = rows[:2] + rows[-1:]
        elif fault == "provenance":
            rows = rows[2:]
        else:
            rows.append("not json")
        path.write_text("\n".join(rows) + "\n")
    assert recover_phase_limit(config, "task").outcome is RecoveryOutcome.REJECTED
    assert not (session / "_recovery").exists()


def test_process_lease_blocks_apply(tmp_path: Path) -> None:
    config, session, _, _ = _project(tmp_path)
    lease = acquire_process_lease(session)
    try:
        assert (
            recover_phase_limit(config, "task").rejection
            is RecoveryRejection.PROCESS_CONTENDED
        )
    finally:
        lease.release()


def test_backup_failure_leaves_state_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, session, _, _ = _project(tmp_path)
    before = (session / "_overview.md").read_bytes()

    def fail(path: Path, content: bytes) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(recovery, "_write_sync", fail)
    assert (
        recover_phase_limit(config, "task").rejection is RecoveryRejection.BACKUP_FAILED
    )
    assert (session / "_overview.md").read_bytes() == before
    assert validate_phase_provenance(session, Phase.QUALITY).ok


def test_uncommitted_receipt_is_inert_and_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, session, _, _ = _project(tmp_path)
    original = recovery._commit_recovery_overview

    def crash(*args: object, **kwargs: object) -> None:
        raise OSError("interrupted before commit")

    monkeypatch.setattr(recovery, "_commit_recovery_overview", crash)
    with pytest.raises(OSError):
        recover_phase_limit(config, "task")
    assert validate_phase_provenance(session, Phase.QUALITY).ok
    monkeypatch.setattr(recovery, "_commit_recovery_overview", original)
    assert recover_phase_limit(config, "task").outcome is RecoveryOutcome.RECOVERED


def test_signal_clear_failure_does_not_undo_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, session, _, _ = _project(tmp_path)

    def fail(path: Path, content: str) -> None:
        raise OSError("signal unavailable")

    monkeypatch.setattr(recovery, "atomic_write_text", fail)
    assert (
        recover_phase_limit(config, "task").outcome
        is RecoveryOutcome.RECOVERED_SIGNAL_RETAINED
    )
    assert validate_phase_provenance(session, Phase.TESTING).ok


@pytest.mark.parametrize("fault", ["missing", "history", "receipt"])
def test_committed_receipt_corruption_fails_closed(tmp_path: Path, fault: str) -> None:
    config, session, _, _ = _project(tmp_path)
    result = recover_phase_limit(config, "task")
    assert result.receipt_path is not None
    if fault == "missing":
        result.receipt_path.unlink()
    elif fault == "history":
        path = session / "_signal_history.jsonl"
        path.write_text(path.read_text().replace("2026-08-24", "2026-08-25"))
    else:
        result.receipt_path.write_text("{}")
    assert scoped_history(session)[1]
    assert not validate_phase_provenance(session, Phase.TESTING).ok


def test_cli_requires_explicit_recovery_mode() -> None:
    args = build_parser().parse_args(
        ["recover", "phase-limit", "--config", "config", "--session", "task", "--check"]
    )
    assert args.recovery_kind == "phase-limit" and args.check
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["recover", "phase-limit", "--config", "config", "--session", "task"]
        )


def test_evidence_change_between_inspections_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, session, _, _ = _project(tmp_path)

    def acquire_with_change(path: Path) -> ProcessLease:
        evidence = path / "02-comment-hygiene.md"
        evidence.write_text(evidence.read_text() + "\nChanged evidence\n")
        return acquire_process_lease(path)

    monkeypatch.setattr(recovery, "acquire_process_lease", acquire_with_change)
    assert (
        recover_phase_limit(config, "task").rejection is RecoveryRejection.STATE_CHANGED
    )
    assert not (session / "_recovery").exists()


def test_session_lock_contention_is_rejected(tmp_path: Path) -> None:
    config, session, _, _ = _project(tmp_path)
    with session_lock(session):
        assert (
            recover_phase_limit(config, "task").rejection
            is RecoveryRejection.LOCK_CONTENDED
        )


def test_old_reports_at_different_head_are_rejected(tmp_path: Path) -> None:
    config, session, _, head = _project(tmp_path)
    path = session / "02-comment-hygiene.md"
    path.write_text(
        path.read_text().replace(f"Output HEAD: {head}", "Output HEAD: " + "f" * 40)
    )
    assert (
        recover_phase_limit(config, "task").rejection
        is RecoveryRejection.EVIDENCE_INVALID
    )
