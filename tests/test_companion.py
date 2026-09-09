import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "companion" / "scripts" / "status.py"


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def make_target(tmp_path: Path) -> tuple[Path, Path]:
    session = tmp_path / "session"
    repo = tmp_path / "repo"
    session.mkdir()
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    (repo / "code.txt").write_text("base\n")
    git(repo, "add", "code.txt")
    git(repo, "commit", "-qm", "base")
    (session / "_overview.md").write_text(
        "## Status\nPhase: implementation\nBlocked: no\nIteration: 2\n"
        "## Plans\n- plan.md - active\n## Other\nPhase: done\n"
    )
    (session / "plan.md").write_text(
        "## Implementation Phases\n### Phase 1: Build\n- [x] first\n- [ ] second\n"
    )
    return session, repo


def run_status(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        text=True,
        capture_output=True,
        timeout=30,
    )


def test_snapshot_uses_committed_diff_and_does_not_write(tmp_path: Path) -> None:
    session, repo = make_target(tmp_path)
    base = git(repo, "rev-parse", "HEAD")
    (repo / "code.txt").write_text("changed\n")
    git(repo, "commit", "-qam", "change")
    (repo / "untracked.txt").write_text("exclude from committed diff")
    before = {p.name: p.read_bytes() for p in session.iterdir()}
    output = run_status("--target", str(session), str(repo), "--base", base)
    assert output.returncode == 0, output.stderr
    row = json.loads(output.stdout)
    assert row["status"]["Phase"] == "implementation"
    assert row["plan"]["tasks_completed"] == 1
    assert row["plan"]["tasks_total"] == 2
    assert row["plan"]["phases_completed"] == 0
    assert row["changed_files"] == ["M\tcode.txt"]
    assert row["dirty"] is True
    assert row["head_changed_during_read"] is False
    assert before == {p.name: p.read_bytes() for p in session.iterdir()}
    assert (repo / "untracked.txt").read_text() == "exclude from committed diff"


def test_missing_target_does_not_hide_other_sessions(tmp_path: Path) -> None:
    session, repo = make_target(tmp_path)
    output = run_status(
        "--target",
        str(tmp_path / "absent"),
        str(repo),
        "--target",
        str(session),
        str(repo),
    )
    assert output.returncode == 1
    rows = [json.loads(line) for line in output.stdout.splitlines()]
    assert "error" in rows[0]
    assert rows[1]["status"]["Blocked"] == "no"


def test_missing_plan_is_not_reported_as_completed(tmp_path: Path) -> None:
    session, repo = make_target(tmp_path)
    (session / "_overview.md").write_text("## Status\nPhase: planning\n")
    output = run_status("--target", str(session), str(repo))
    assert output.returncode == 0
    row = json.loads(output.stdout)
    assert "plan_error" in row
    assert "plan" not in row


def test_invalid_base_is_visible(tmp_path: Path) -> None:
    session, repo = make_target(tmp_path)
    output = run_status("--target", str(session), str(repo), "--base", "absent-ref")
    assert output.returncode == 1
    assert "error" in json.loads(output.stdout)
