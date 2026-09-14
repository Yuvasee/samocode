"""Read-only session status for local and remote monitoring.

Never mutates session files: the lease is probed, not taken, and the signal file
is read without being cleared.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import ProjectConfig, resolve_project_working_dir, resolve_session_path
from .process_lease import ORCHESTRATOR_LEASE_FILENAME
from .signals import OVERVIEW_FILENAME, SIGNAL_FILENAME, read_signal_file

DEFAULT_FLOW_LOG_ENTRIES = 3
QA_FILENAME = "_qa.md"
QA_WAITING_FOR = "qa_answers"

_FLOW_LOG_SECTION = re.compile(
    r"^##\s+Flow Log\s*$(.*?)(?=^##\s|\Z)", re.MULTILINE | re.DOTALL
)


@dataclass(frozen=True)
class SessionStatus:
    session: str
    session_path: str
    working_dir: str | None
    phase: str | None
    iteration: int | None
    total_iterations: int | None
    blocked: str | None
    last_action: str | None
    next_action: str | None
    signal: str | None
    waiting_for: str | None
    needs: str | None
    reason: str | None
    worker_running: bool
    qa_pending: bool
    flow_log: tuple[str, ...]
    errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else 1


def collect_status(
    config_path: Path,
    session_name: str,
    flow_entries: int = DEFAULT_FLOW_LOG_ENTRIES,
) -> SessionStatus:
    try:
        project = ProjectConfig.from_file(config_path)
    except ValueError as exc:
        return _failed(session_name, "", (f"Config error: {exc}",))

    config_errors = project.validate()
    if config_errors:
        return _failed(
            session_name,
            "",
            tuple(f"Invalid project config: {err}" for err in config_errors),
        )

    session_path = resolve_session_path(project.sessions, session_name)
    if not session_path.is_dir():
        return _failed(
            session_path.name,
            str(session_path),
            (f"Session directory does not exist: {session_path}",),
        )

    overview_path = session_path / OVERVIEW_FILENAME
    if not overview_path.exists():
        return _failed(
            session_path.name,
            str(session_path),
            (f"No {OVERVIEW_FILENAME} in {session_path}",),
        )
    content = overview_path.read_text()

    signal = _read_signal(session_path)
    waiting_for = signal.get("for")
    working_dir = _field(content, "Working Dir") or str(
        resolve_project_working_dir(project, session_path)
    )

    return SessionStatus(
        session=session_path.name,
        session_path=str(session_path),
        working_dir=working_dir,
        phase=_field(content, "Phase"),
        iteration=_int_field(content, "Iteration"),
        total_iterations=_int_field(content, "Total Iterations"),
        blocked=_field(content, "Blocked"),
        last_action=_field(content, "Last Action"),
        next_action=_field(content, "Next"),
        signal=signal.get("status"),
        waiting_for=waiting_for,
        needs=signal.get("needs"),
        reason=signal.get("reason"),
        worker_running=_lease_held(session_path),
        qa_pending=waiting_for == QA_WAITING_FOR
        and (session_path / QA_FILENAME).exists(),
        flow_log=_flow_log_tail(content, flow_entries),
        errors=(),
    )


def render_status_text(status: SessionStatus) -> str:
    counters = f"iteration {status.iteration or '?'}"
    if status.total_iterations is not None:
        counters += f", total {status.total_iterations}"
    signal = status.signal or "none"
    if status.waiting_for:
        signal += f" (for: {status.waiting_for})"
    if status.needs:
        signal += f" (needs: {status.needs})"
    lines = [
        f"Session: {status.session}",
        f"Path: {status.session_path}",
        f"Working Dir: {status.working_dir or '?'}",
        f"Phase: {status.phase or '?'} ({counters})",
        f"Blocked: {status.blocked or '?'}",
        f"Worker: {'running' if status.worker_running else 'stopped'}",
        f"Signal: {signal}",
    ]
    if status.reason:
        lines.append(f"Reason: {status.reason}")
    if status.qa_pending:
        lines.append(f"Q&A pending: {Path(status.session_path) / QA_FILENAME}")
    lines.append(f"Last: {status.last_action or '?'}")
    lines.append(f"Next: {status.next_action or '?'}")
    if status.flow_log:
        lines.append("Flow:")
        lines.extend(status.flow_log)
    return "\n".join(lines)


def render_status_json(status: SessionStatus) -> str:
    payload = asdict(status)
    payload["flow_log"] = list(status.flow_log)
    payload["errors"] = list(status.errors)
    return json.dumps(payload, indent=2)


# =============================================================================
# Private helpers
# =============================================================================


def _failed(session: str, session_path: str, errors: tuple[str, ...]) -> SessionStatus:
    return SessionStatus(
        session=session,
        session_path=session_path,
        working_dir=None,
        phase=None,
        iteration=None,
        total_iterations=None,
        blocked=None,
        last_action=None,
        next_action=None,
        signal=None,
        waiting_for=None,
        needs=None,
        reason=None,
        worker_running=False,
        qa_pending=False,
        flow_log=(),
        errors=errors,
    )


def _field(content: str, field: str) -> str | None:
    match = re.search(rf"^{re.escape(field)}:[ \t]*(.*)$", content, re.MULTILINE)
    if match is None:
        return None
    value = match.group(1).strip()
    return value or None


def _int_field(content: str, field: str) -> int | None:
    value = _field(content, field)
    if value is None or not value.isdigit():
        return None
    return int(value)


def _flow_log_tail(content: str, count: int) -> tuple[str, ...]:
    if count <= 0:
        return ()
    section = _FLOW_LOG_SECTION.search(content)
    if section is None:
        return ()
    entries = [
        line.strip()
        for line in section.group(1).splitlines()
        if line.strip().startswith("- ")
    ]
    return tuple(entries[-count:])


def _read_signal(session_path: Path) -> dict[str, str]:
    signal_file = session_path / SIGNAL_FILENAME
    if not signal_file.exists():
        return {}
    if signal_file.read_text().strip() in ("", "{}"):
        return {}
    return {
        key: value
        for key, value in read_signal_file(session_path).to_dict().items()
        if value is not None
    }


def _lease_held(session_path: Path) -> bool:
    """Probe the orchestrator lease without creating or taking it."""
    lease_path = session_path / ORCHESTRATOR_LEASE_FILENAME
    if not lease_path.exists():
        return False
    try:
        fd = os.open(str(lease_path), os.O_RDWR)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    except OSError:
        return False
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)
