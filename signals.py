"""Signal file operations for orchestrator flow control."""

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class SignalStatus(Enum):
    """Valid signal statuses for orchestrator control."""

    CONTINUE = "continue"
    DONE = "done"
    BLOCKED = "blocked"
    WAITING = "waiting"


@dataclass
class Signal:
    """Signal from Claude to orchestrator."""

    status: SignalStatus
    summary: str | None = None
    reason: str | None = None
    needs: str | None = None
    waiting_for: str | None = None
    next_state: str | None = None
    context: dict[str, str | int | bool] | None = None

    def to_dict(self) -> dict[str, str | dict[str, str | int | bool] | None]:
        """Convert to JSON-serializable dict."""
        return {
            "status": self.status.value,
            "summary": self.summary,
            "reason": self.reason,
            "needs": self.needs,
            "for": self.waiting_for,
            "next_state": self.next_state,
            "context": self.context,
        }


def clear_signal_file(session_path: Path) -> None:
    """Create empty signal file at start of each iteration."""
    session_path.mkdir(parents=True, exist_ok=True)
    signal_file = session_path / "_signal.json"
    signal_file.write_text("{}")


def read_signal_file(session_path: Path) -> Signal:
    """Read and parse signal file. Returns BLOCKED signal on parse errors."""
    signal_file = session_path / "_signal.json"

    if not signal_file.exists():
        return Signal(
            status=SignalStatus.BLOCKED,
            reason="Signal file not created",
            needs="investigation",
        )

    try:
        data = json.loads(signal_file.read_text())

        if not data:
            # Empty signal likely means Claude crashed without writing
            # Default to BLOCKED to prevent infinite loops
            return Signal(
                status=SignalStatus.BLOCKED,
                reason="Empty signal file - Claude may have crashed",
                needs="investigation",
            )

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
            next_state=data.get("next_state"),
            context=data.get("context"),
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
