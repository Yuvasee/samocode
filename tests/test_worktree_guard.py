"""Tests for worktree_guard: snapshot and mutation detection."""

import subprocess
from pathlib import Path

import pytest

from worker.worktree_guard import (
    WorktreeSnapshot,
    describe_worktree_mutation,
    snapshot_worktree,
)


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    """Minimal git repo with one tracked file and an initial commit."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@test.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "Test"],
        check=True,
        capture_output=True,
    )
    file = tmp_path / "tracked.txt"
    file.write_text("initial")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "tracked.txt"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )
    return tmp_path


def test_snapshot_clean_repo(git_repo: Path) -> None:
    snap = snapshot_worktree(git_repo)
    assert snap is not None
    assert len(snap.head) == 40
    assert snap.tracked_status == ""


def test_snapshot_modified_tracked_file(git_repo: Path) -> None:
    (git_repo / "tracked.txt").write_text("changed")
    snap = snapshot_worktree(git_repo)
    assert snap is not None
    assert "tracked.txt" in snap.tracked_status


def test_snapshot_untracked_file_not_in_status(git_repo: Path) -> None:
    (git_repo / "untracked.txt").write_text("new")
    snap = snapshot_worktree(git_repo)
    assert snap is not None
    assert "untracked.txt" not in snap.tracked_status
    assert snap.tracked_status == ""


def test_snapshot_new_commit_changes_head(git_repo: Path) -> None:
    snap_before = snapshot_worktree(git_repo)
    assert snap_before is not None

    (git_repo / "tracked.txt").write_text("v2")
    subprocess.run(
        ["git", "-C", str(git_repo), "add", "tracked.txt"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(git_repo), "commit", "-m", "v2"],
        check=True,
        capture_output=True,
    )

    snap_after = snapshot_worktree(git_repo)
    assert snap_after is not None
    assert snap_after.head != snap_before.head


def test_snapshot_non_git_directory(tmp_path: Path) -> None:
    assert snapshot_worktree(tmp_path) is None


def test_describe_no_mutation(git_repo: Path) -> None:
    snap = snapshot_worktree(git_repo)
    assert snap is not None
    assert describe_worktree_mutation(snap, snap) is None


def test_describe_mutation_tracked_file(git_repo: Path) -> None:
    before = snapshot_worktree(git_repo)
    assert before is not None

    (git_repo / "tracked.txt").write_text("changed")
    after = snapshot_worktree(git_repo)
    assert after is not None

    desc = describe_worktree_mutation(before, after)
    assert desc is not None
    assert "tracked.txt" in desc
    assert before.head[:12] not in desc  # HEAD unchanged


def test_describe_mutation_new_commit(git_repo: Path) -> None:
    before = snapshot_worktree(git_repo)
    assert before is not None

    (git_repo / "tracked.txt").write_text("v2")
    subprocess.run(
        ["git", "-C", str(git_repo), "add", "tracked.txt"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(git_repo), "commit", "-m", "v2"],
        check=True,
        capture_output=True,
    )

    after = snapshot_worktree(git_repo)
    assert after is not None

    desc = describe_worktree_mutation(before, after)
    assert desc is not None
    assert "HEAD" in desc
    assert before.head[:12] in desc
    assert after.head[:12] in desc


def test_describe_mutation_untracked_only_is_no_mutation(git_repo: Path) -> None:
    before = snapshot_worktree(git_repo)
    assert before is not None

    (git_repo / "new_untracked.txt").write_text("new")
    after = snapshot_worktree(git_repo)
    assert after is not None

    assert describe_worktree_mutation(before, after) is None


def test_describe_mutation_dataclass_equality() -> None:
    a = WorktreeSnapshot(head="abc", tracked_status="")
    b = WorktreeSnapshot(head="abc", tracked_status="")
    assert describe_worktree_mutation(a, b) is None

    c = WorktreeSnapshot(head="xyz", tracked_status="")
    assert describe_worktree_mutation(a, c) is not None
