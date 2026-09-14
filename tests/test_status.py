import json
from pathlib import Path

from worker.process_lease import acquire_process_lease
from worker.status import (
    SessionStatus,
    collect_status,
    render_status_json,
    render_status_text,
)

OVERVIEW = """# Session: task
Started: 09-14 10:00
Working Dir: /repo/worktrees/26-09-14-task

## Status
Phase: implementation
Iteration: 4
Total Iterations: 12
Blocked: no
Last Action: Implemented phase 2
Next: Run tests

## Flow Log
- [001 @ 09-14 10:00] Session created
- [002 @ 09-14 10:10] Investigation done -> dive.md
- [003 @ 09-14 10:30] Plan approved
- [004 @ 09-14 11:00] Implemented phase 2

## Files
- dive.md - investigation
"""


def _project(tmp_path: Path, overview: str | None = OVERVIEW) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    sessions = tmp_path / "_sessions"
    worktrees = tmp_path / "worktrees"
    worktrees.mkdir()
    session = sessions / "26-09-14-task"
    session.mkdir(parents=True)
    if overview is not None:
        (session / "_overview.md").write_text(overview)
    config = tmp_path / ".samocode"
    config.write_text(f"MAIN_REPO={repo}\nWORKTREES={worktrees}\nSESSIONS={sessions}\n")
    return config, session


def test_collects_overview_fields_and_flow_log_tail(tmp_path: Path) -> None:
    config, session = _project(tmp_path)

    status = collect_status(config, "task", flow_entries=2)

    assert status.ok
    assert status.session == "26-09-14-task"
    assert status.session_path == str(session)
    assert status.working_dir == "/repo/worktrees/26-09-14-task"
    assert status.phase == "implementation"
    assert status.iteration == 4
    assert status.total_iterations == 12
    assert status.blocked == "no"
    assert status.last_action == "Implemented phase 2"
    assert status.next_action == "Run tests"
    assert status.flow_log == (
        "- [003 @ 09-14 10:30] Plan approved",
        "- [004 @ 09-14 11:00] Implemented phase 2",
    )
    assert status.signal is None
    assert status.worker_running is False
    assert status.qa_pending is False


def test_working_dir_falls_back_to_project_resolution(tmp_path: Path) -> None:
    overview = OVERVIEW.replace("Working Dir: /repo/worktrees/26-09-14-task\n", "")
    config, _session = _project(tmp_path, overview)

    status = collect_status(config, "task")

    assert status.working_dir == str(tmp_path / "repo")


def test_reads_waiting_signal_and_pending_qa(tmp_path: Path) -> None:
    config, session = _project(tmp_path)
    (session / "_signal.json").write_text(
        json.dumps({"status": "waiting", "for": "qa_answers", "phase": "requirements"})
    )
    (session / "_qa.md").write_text("# Q&A\n")

    status = collect_status(config, "task")

    assert status.signal == "waiting"
    assert status.waiting_for == "qa_answers"
    assert status.qa_pending is True


def test_cleared_signal_reports_none(tmp_path: Path) -> None:
    config, session = _project(tmp_path)
    (session / "_signal.json").write_text("{}")

    assert collect_status(config, "task").signal is None


def test_blocked_signal_exposes_reason_and_needs(tmp_path: Path) -> None:
    config, session = _project(tmp_path)
    (session / "_signal.json").write_text(
        json.dumps(
            {"status": "blocked", "reason": "No browser", "needs": "environment"}
        )
    )

    status = collect_status(config, "task")

    assert status.signal == "blocked"
    assert status.reason == "No browser"
    assert status.needs == "environment"


def test_worker_running_reflects_held_lease(tmp_path: Path) -> None:
    config, session = _project(tmp_path)
    lease = acquire_process_lease(session)
    try:
        assert collect_status(config, "task").worker_running is True
    finally:
        lease.release()
    assert collect_status(config, "task").worker_running is False
    assert (session / "_orchestrator.lock").exists()


def test_missing_session_fails(tmp_path: Path) -> None:
    config, _session = _project(tmp_path)

    status = collect_status(config, "nope")

    assert status.exit_code == 1
    assert status.errors[0].startswith("Session directory does not exist:")


def test_missing_overview_fails(tmp_path: Path) -> None:
    config, _session = _project(tmp_path, overview=None)

    status = collect_status(config, "task")

    assert status.exit_code == 1
    assert "_overview.md" in status.errors[0]


def test_bad_config_fails(tmp_path: Path) -> None:
    status = collect_status(tmp_path / "missing.samocode", "task")

    assert status.exit_code == 1
    assert status.errors[0].startswith("Config error:")


def test_render_text_and_json(tmp_path: Path) -> None:
    config, session = _project(tmp_path)
    (session / "_signal.json").write_text(
        json.dumps({"status": "waiting", "for": "plan_approval"})
    )

    status = collect_status(config, "task", flow_entries=1)
    text = render_status_text(status)
    payload = json.loads(render_status_json(status))

    assert "Phase: implementation (iteration 4, total 12)" in text
    assert "Worker: stopped" in text
    assert "Signal: waiting (for: plan_approval)" in text
    assert text.endswith("- [004 @ 09-14 11:00] Implemented phase 2")
    assert payload["phase"] == "implementation"
    assert payload["flow_log"] == ["- [004 @ 09-14 11:00] Implemented phase 2"]
    assert payload["errors"] == []
    assert isinstance(
        SessionStatus(**{**payload, "flow_log": (), "errors": ()}), SessionStatus
    )
