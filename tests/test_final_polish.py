import json
import subprocess
from pathlib import Path

import pytest

from worker.final_polish import validate_final_polish


def _git(project: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(project), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _project(tmp_path: Path) -> tuple[Path, str]:
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    _git(project, "config", "user.email", "test@example.com")
    _git(project, "config", "user.name", "Test")
    (project / "source.py").write_text("VALUE = 1\n")
    _git(project, "add", "source.py")
    _git(project, "commit", "-qm", "initial")
    return project, _git(project, "rev-parse", "HEAD")


def _history(session: Path, *, complete: bool = True) -> None:
    transitions = [
        ("implementation", "testing"),
        ("testing", "quality"),
        ("quality", "testing"),
        ("testing", "pr-readiness"),
    ]
    if not complete:
        transitions.pop(2)
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


def _evidence(
    session: Path,
    *,
    reviewed: str,
    output: str,
    disposition: str = "settled",
    safety: str = "PASS",
    regression_result: str = "PASS",
) -> None:
    session.mkdir()
    (session / "01-code-clarity.md").write_text(
        f"Reviewed HEAD: {reviewed}\nResult: clean\nDisposition: {disposition}\n"
    )
    (session / "02-comment-hygiene.md").write_text(
        f"Input HEAD: {reviewed}\nOutput HEAD: {output}\nSafety check: {safety}\n"
    )
    (session / "03-test-report.md").write_text(
        f"Run: 2nd (post-quality)\nResult: {regression_result}\nTested HEAD: {output}\n"
    )
    _history(session)


def test_accepts_noop_hygiene_chain(tmp_path: Path) -> None:
    project, head = _project(tmp_path)
    session = tmp_path / "session"
    _evidence(session, reviewed=head, output=head)

    check = validate_final_polish(session, project)

    assert check.ok
    assert check.errors == ()


def test_accepts_hygiene_commit_between_review_and_regression(tmp_path: Path) -> None:
    project, reviewed = _project(tmp_path)
    (project / "source.py").write_text("VALUE = 1  # reason\n")
    _git(project, "add", "source.py")
    _git(project, "commit", "-qm", "comment hygiene")
    output = _git(project, "rev-parse", "HEAD")
    session = tmp_path / "session"
    _evidence(session, reviewed=reviewed, output=output)

    assert validate_final_polish(session, project).ok


@pytest.mark.parametrize(
    "artifact",
    ["01-code-clarity.md", "02-comment-hygiene.md", "03-test-report.md"],
)
def test_rejects_missing_artifact(tmp_path: Path, artifact: str) -> None:
    project, head = _project(tmp_path)
    session = tmp_path / "session"
    _evidence(session, reviewed=head, output=head)
    (session / artifact).unlink()

    check = validate_final_polish(session, project)

    assert not check.ok
    assert any("Missing" in error for error in check.errors)


def test_rejects_mismatched_or_short_sha(tmp_path: Path) -> None:
    project, head = _project(tmp_path)
    session = tmp_path / "session"
    _evidence(session, reviewed="abc123", output=head)

    check = validate_final_polish(session, project)

    assert not check.ok
    assert any("full 40-character git SHA" in error for error in check.errors)


def test_rejects_dirty_project_worktree(tmp_path: Path) -> None:
    project, head = _project(tmp_path)
    session = tmp_path / "session"
    _evidence(session, reviewed=head, output=head)
    (project / "untracked.txt").write_text("dirty\n")

    check = validate_final_polish(session, project)

    assert "Project working tree is not clean after Comment Hygiene" in check.errors


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("disposition", "pending", "not settled"),
        ("safety", "FAIL", "safety check did not pass"),
        ("regression_result", "FAIL", "regression did not pass"),
    ],
)
def test_rejects_failed_polish_result(
    tmp_path: Path, field: str, value: str, expected: str
) -> None:
    project, head = _project(tmp_path)
    session = tmp_path / "session"
    kwargs = {field: value}
    _evidence(session, reviewed=head, output=head, **kwargs)  # type: ignore[arg-type]

    check = validate_final_polish(session, project)

    assert any(expected in error for error in check.errors)


def test_rejects_incomplete_lifecycle_history(tmp_path: Path) -> None:
    project, head = _project(tmp_path)
    session = tmp_path / "session"
    _evidence(session, reviewed=head, output=head)
    _history(session, complete=False)

    check = validate_final_polish(session, project)

    assert any("ordered final-polish lifecycle" in error for error in check.errors)


def test_rejects_open_clarity_or_quality_debt(tmp_path: Path) -> None:
    project, head = _project(tmp_path)
    session = tmp_path / "session"
    _evidence(session, reviewed=head, output=head)
    (session / "_review_debt.md").write_text(
        "| ID | Severity | Decision | Status |\n"
        "|---|---|---|---|\n"
        "| CL-001 | important | undecided | open |\n"
    )

    check = validate_final_polish(session, project)

    assert "Review debt row CL-001 has no explicit decision" in check.errors


def test_accepts_evidenced_deferred_debt(tmp_path: Path) -> None:
    project, head = _project(tmp_path)
    session = tmp_path / "session"
    _evidence(session, reviewed=head, output=head)
    (session / "_review_debt.md").write_text(
        "| ID | Severity | Decision | Evidence / Ticket | Status |\n"
        "|---|---|---|---|---|\n"
        "| Q-001 | important | defer | TEAM-123 owned by platform | open |\n"
    )

    assert validate_final_polish(session, project).ok


def test_latest_artifact_must_be_well_formed(tmp_path: Path) -> None:
    project, head = _project(tmp_path)
    session = tmp_path / "session"
    _evidence(session, reviewed=head, output=head)
    (session / "99-code-clarity-verify.md").write_text(
        f"Reviewed HEAD: {head}\nResult: clean\nDisposition: settled\n"
        "Disposition: pending\n"
    )

    check = validate_final_polish(session, project)

    assert any("exactly one" in error for error in check.errors)
