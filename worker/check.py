"""Diagnostics bypass phase preconditions so failed sessions remain inspectable."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import ProjectConfig, resolve_project_working_dir, resolve_session_path
from .final_polish import validate_final_polish


@dataclass(frozen=True)
class CheckResult:
    errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else 1


def run_final_polish_check(config_path: Path, session_name: str) -> CheckResult:
    try:
        project = ProjectConfig.from_file(config_path)
    except ValueError as exc:
        return CheckResult((f"Config error: {exc}",))

    config_errors = project.validate()
    if config_errors:
        return CheckResult(
            tuple(f"Invalid project config: {err}" for err in config_errors)
        )

    session_path = resolve_session_path(project.sessions, session_name)
    if not session_path.is_dir():
        return CheckResult((f"Session directory does not exist: {session_path}",))

    working_dir = resolve_project_working_dir(project, session_path)
    return CheckResult(validate_final_polish(session_path, working_dir).errors)
