"""Signal history tracking for session debugging.

Records all signals to _signal_history.jsonl for post-mortem analysis.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .signals import Signal


@dataclass(frozen=True)
class SignalHistoryEntry:
    """A recorded signal with metadata."""

    timestamp: str
    iteration: int
    phase: str | None
    status: str
    summary: str | None
    reason: str | None
    needs: str | None
    waiting_for: str | None

    def to_dict(self) -> dict[str, str | int | None]:
        """Convert to JSON-serializable dict."""
        return {
            "timestamp": self.timestamp,
            "iteration": self.iteration,
            "phase": self.phase,
            "status": self.status,
            "summary": self.summary,
            "reason": self.reason,
            "needs": self.needs,
            "for": self.waiting_for,
        }


def record_signal(
    session_path: Path,
    signal: Signal,
    iteration: int,
    phase_from_overview: str | None = None,
) -> None:
    """Append signal to history file.

    Uses phase from signal if available, falls back to phase_from_overview.
    """
    history_file = session_path / "_signal_history.jsonl"

    entry = SignalHistoryEntry(
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        iteration=iteration,
        phase=signal.phase or phase_from_overview,
        status=signal.status.value,
        summary=signal.summary,
        reason=signal.reason,
        needs=signal.needs,
        waiting_for=signal.waiting_for,
    )

    with open(history_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry.to_dict()) + "\n")


def get_phase_iteration_count(session_path: Path, phase: str) -> int:
    """Count iterations spent in a specific phase from history.

    Useful for enforcing per-phase iteration limits.
    """
    history_file = session_path / "_signal_history.jsonl"
    if not history_file.exists():
        return 0

    count = 0
    phase_lower = phase.lower()
    for line in history_file.read_text().splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            entry_phase = entry.get("phase")
            if entry_phase and entry_phase.lower() == phase_lower:
                count += 1
        except json.JSONDecodeError:
            continue

    return count


def read_signal_history(session_path: Path) -> list[SignalHistoryEntry]:
    """Read all signal history entries for debugging."""
    history_file = session_path / "_signal_history.jsonl"
    if not history_file.exists():
        return []

    entries: list[SignalHistoryEntry] = []
    for line in history_file.read_text().splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            entries.append(
                SignalHistoryEntry(
                    timestamp=data.get("timestamp", ""),
                    iteration=data.get("iteration", 0),
                    phase=data.get("phase"),
                    status=data.get("status", ""),
                    summary=data.get("summary"),
                    reason=data.get("reason"),
                    needs=data.get("needs"),
                    waiting_for=data.get("for"),
                )
            )
        except json.JSONDecodeError:
            continue

    return entries
