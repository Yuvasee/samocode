"""Signal file operations for orchestrator flow control."""

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


# Session-relative filenames shared across modules; signals.py is the lowest-level
# module (no intra-package imports) so both live here without an import cycle.
SIGNAL_FILENAME = "_signal.json"
OVERVIEW_FILENAME = "_overview.md"


# `needs` vocabularies for escalation policies. A trigger need must be in
# BLOCKED_NEEDS and absent from WORKER_NEEDS; the intentional overlap on
# "human_decision" makes it non-escalating (the worker can synthesize it itself).
BLOCKED_NEEDS = ("human_decision", "clarification", "error_resolution", "environment")
WORKER_NEEDS = ("investigation", "human_decision")


_MAX_OVERVIEW_REASON = 200


def sanitize_overview_reason(
    reason: str | None, *, max_length: int = _MAX_OVERVIEW_REASON
) -> str:
    """Flatten a provider-supplied reason into one bounded line safe for _overview.md.

    A reason lands verbatim in the persisted flow log; without this a newline could
    forge a second `Phase:` field or a `## Flow Log` heading and corrupt the control
    file. Collapse whitespace/control chars to single spaces, defang a leading
    Markdown-structural token, and cap the length.
    """
    if not reason:
        return ""
    collapsed = " ".join(
        "".join(ch if ch.isprintable() else " " for ch in reason).split()
    )
    collapsed = collapsed.lstrip("#>").strip()
    if len(collapsed) > max_length:
        collapsed = collapsed[: max_length - 1].rstrip() + "…"
    return collapsed


class SignalStatus(Enum):
    """Valid signal statuses for orchestrator control."""

    CONTINUE = "continue"
    DONE = "done"
    BLOCKED = "blocked"
    WAITING = "waiting"


@dataclass
class Signal:
    """Signal from child provider to orchestrator."""

    status: SignalStatus
    summary: str | None = None
    reason: str | None = None
    needs: str | None = None
    waiting_for: str | None = None
    phase: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        """Convert to JSON-serializable dict."""
        return {
            "status": self.status.value,
            "summary": self.summary,
            "reason": self.reason,
            "needs": self.needs,
            "for": self.waiting_for,
            "phase": self.phase,
        }


def clear_signal_file(session_path: Path) -> str | None:
    """Clear signal file at start of each iteration.

    Returns previous contents if file existed and had content, None otherwise.
    """
    session_path.mkdir(parents=True, exist_ok=True)
    signal_file = session_path / SIGNAL_FILENAME
    previous: str | None = None
    if signal_file.exists():
        content = signal_file.read_text().strip()
        if content and content != "{}":
            previous = content
    signal_file.write_text("{}")
    return previous


def read_signal_file(session_path: Path) -> Signal:
    """Read and parse signal file. Returns BLOCKED signal on parse errors."""
    signal_file = session_path / SIGNAL_FILENAME

    if not signal_file.exists():
        return Signal(
            status=SignalStatus.BLOCKED,
            reason="Signal file not created",
            needs="investigation",
        )

    try:
        data = json.loads(signal_file.read_text())

        if not data:
            return Signal(status=SignalStatus.CONTINUE)

        status_str = data.get("status", "").lower()

        try:
            status = SignalStatus(status_str)
        except ValueError:
            return Signal(
                status=SignalStatus.BLOCKED,
                reason=f"Invalid signal status: {status_str}",
                needs="investigation",
            )

        return Signal(
            status=status,
            summary=data.get("summary"),
            reason=data.get("reason"),
            needs=data.get("needs"),
            waiting_for=data.get("for"),
            phase=data.get("phase"),
        )

    except json.JSONDecodeError as e:
        return Signal(
            status=SignalStatus.BLOCKED,
            reason=f"Invalid signal JSON: {e}",
            needs="investigation",
        )
    except Exception as e:
        return Signal(
            status=SignalStatus.BLOCKED,
            reason=f"Failed to read signal: {e}",
            needs="investigation",
        )
