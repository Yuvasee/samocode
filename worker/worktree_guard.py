"""Snapshot and mutation detection for readonly-phase worktree guard."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorktreeSnapshot:
    head: str
    tracked_status: str


def _git_run(working_dir: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(working_dir), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def snapshot_worktree(working_dir: Path) -> WorktreeSnapshot | None:
    head_raw = _git_run(working_dir, "rev-parse", "HEAD")
    if head_raw is None:
        return None
    # rstrip only — leading spaces in status lines are the X (index) status char.
    status_raw = _git_run(working_dir, "status", "--porcelain", "--untracked-files=no")
    if status_raw is None:
        return None
    return WorktreeSnapshot(
        head=head_raw.strip(), tracked_status=status_raw.rstrip("\n")
    )


def describe_worktree_mutation(
    before: WorktreeSnapshot, after: WorktreeSnapshot
) -> str | None:
    if before == after:
        return None
    parts: list[str] = []
    if before.head != after.head:
        parts.append(f"HEAD {before.head[:12]}→{after.head[:12]}")
    if before.tracked_status != after.tracked_status:
        before_paths = {
            line[3:] for line in before.tracked_status.splitlines() if len(line) > 3
        }
        after_paths = {
            line[3:] for line in after.tracked_status.splitlines() if len(line) > 3
        }
        changed = sorted((before_paths | after_paths) - (before_paths & after_paths))
        parts.append(
            f"tracked: {', '.join(changed)}" if changed else "tracked files changed"
        )
    return "; ".join(parts)
