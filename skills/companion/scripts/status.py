import argparse
import json
import subprocess
from datetime import datetime
from pathlib import Path

from worker.plan_resolver import parse_implementation_phases, select_active_plan


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Samocode session snapshots")
    parser.add_argument(
        "--target",
        nargs=2,
        action="append",
        required=True,
        metavar=("SESSION", "WORKTREE"),
    )
    parser.add_argument("--base", help="Explicit Git comparison base shared by targets")
    args = parser.parse_args()
    failed = False
    for session, worktree in args.target:
        try:
            result = collect_status(Path(session), Path(worktree), args.base)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            result = {"session": session, "error": str(exc)}
            failed = True
        print(json.dumps(result, ensure_ascii=False))
    return int(failed)


def collect_status(
    session: Path,
    worktree: Path,
    base: str | None = None,
) -> dict[str, object]:
    overview = (session / "_overview.md").read_text()
    fields = (
        "Phase",
        "Iteration",
        "Total Iterations",
        "Blocked",
        "Last Action",
        "Next",
    )
    status: dict[str, str] = {}
    in_status = False
    for line in overview.splitlines():
        if line.startswith("## "):
            in_status = line.strip() == "## Status"
        elif in_status:
            key, separator, value = line.partition(":")
            if separator and key in fields:
                status[key] = value.strip()
    result: dict[str, object] = {
        "session": str(session.resolve()),
        "observed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": status,
        "worktree": str(worktree.resolve()),
    }
    if not status:
        result["status_error"] = "No recognized fields in ## Status"
    try:
        plan = select_active_plan(session, overview)
        phases = parse_implementation_phases(plan.read_text())
        result["plan"] = {
            "path": str(plan),
            "phases_completed": sum(not phase.has_unchecked_task for phase in phases),
            "phases_total": len(phases),
            "tasks_completed": sum(phase.completed_tasks for phase in phases),
            "tasks_total": sum(phase.total_tasks for phase in phases),
        }
    except (OSError, ValueError) as exc:
        result["plan_error"] = str(exc)
    head = git(worktree, "rev-parse", "--verify", "HEAD")
    result["head"] = head
    result["recent_commits"] = git(
        worktree, "log", "-3", "--format=%h %s", head
    ).splitlines()
    result["dirty"] = bool(
        git(worktree, "status", "--porcelain", "--untracked-files=normal")
    )
    if base:
        base_head = git(
            worktree, "rev-parse", "--verify", "--end-of-options", base + "^{commit}"
        )
        result["base"] = base_head
        result["changed_files"] = git(
            worktree, "diff", "--name-status", base_head, head, "--"
        ).splitlines()
        result["diff_stat"] = git(
            worktree, "diff", "--shortstat", base_head, head, "--"
        )
    result["head_changed_during_read"] = head != git(
        worktree, "rev-parse", "--verify", "HEAD"
    )
    return result


def git(worktree: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "--no-optional-locks", "-C", str(worktree), *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())
