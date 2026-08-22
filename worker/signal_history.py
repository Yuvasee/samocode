"""Signal history tracking for session debugging.

Records provider iterations to `_signal_history.jsonl` for post-mortem analysis.

Two row schemas coexist in one file and are read through a single normalizer:
- Legacy (no `"v"` key): a single `phase` field, written by `record_signal`.
- v2 (`"v": 2`): source_phase / target_phase / status / accepted / validation_error,
  written by `record_processed_outcome` from a workflow_state.ProcessedOutcome.

Legacy public names (SignalHistoryEntry, record_signal, read_signal_history,
get_phase_iteration_count) remain thin compatibility shims over the same parse path.
Iteration counting is by SOURCE phase, so rejected events and the boundary iteration
that trips a limit are still counted for the phase they were spent in.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .phases import Phase
from .signals import Signal
from .workflow_state import ProcessedOutcome

HISTORY_FILENAME = "_signal_history.jsonl"
SCHEMA_VERSION = 2
LEGACY_SCHEMA_VERSION = 1


# =============================================================================
# Legacy compatibility row (unchanged public contract)
# =============================================================================


@dataclass(frozen=True)
class SignalHistoryEntry:
    """A recorded signal with metadata (legacy row shape)."""

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
    """Append a legacy signal row to history.

    Uses phase from signal if available, falls back to phase_from_overview.
    """
    entry = SignalHistoryEntry(
        timestamp=_now(),
        iteration=iteration,
        phase=signal.phase or phase_from_overview,
        status=signal.status.value,
        summary=signal.summary,
        reason=signal.reason,
        needs=signal.needs,
        waiting_for=signal.waiting_for,
    )
    _append(session_path, entry.to_dict())


# =============================================================================
# v2 typed history record
# =============================================================================


@dataclass(frozen=True)
class HistoryRecord:
    """Truthful, normalized record of one provider iteration.

    Written as v2 by `record_processed_outcome`; legacy rows normalize into this shape
    with accepted/target_phase None (unknown) and schema_version 1. `source_phase` is
    the phase the iteration was spent in and is the counting key.
    """

    timestamp: str
    iteration: int
    source_phase: str | None
    target_phase: str | None
    raw_status: str
    accepted: bool | None
    validation_error: str | None
    summary: str | None = None
    reason: str | None = None
    needs: str | None = None
    waiting_for: str | None = None
    rejection_reason: str | None = None
    outcome_kind: str | None = None
    mutated: bool | None = None
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        """Convert to JSON-serializable v2 dict."""
        return {
            "v": self.schema_version,
            "timestamp": self.timestamp,
            "iteration": self.iteration,
            "source_phase": self.source_phase,
            "target_phase": self.target_phase,
            "status": self.raw_status,
            "accepted": self.accepted,
            "validation_error": self.validation_error,
            "rejection_reason": self.rejection_reason,
            "outcome_kind": self.outcome_kind,
            "mutated": self.mutated,
            "summary": self.summary,
            "reason": self.reason,
            "needs": self.needs,
            "for": self.waiting_for,
        }


def record_processed_outcome(
    session_path: Path,
    signal: Signal,
    iteration: int,
    outcome: ProcessedOutcome,
) -> HistoryRecord:
    """Append a v2 audit row for a processed workflow event.

    Records the raw signal status alongside the authoritative outcome so rejected
    events and limit-boundary iterations are both counted by source phase. Returns the
    written record.
    """
    record = HistoryRecord(
        timestamp=_now(),
        iteration=iteration,
        source_phase=_phase_value(outcome.source_phase),
        target_phase=_phase_value(outcome.target_phase),
        raw_status=signal.status.value,
        accepted=outcome.accepted,
        validation_error=outcome.validation_error,
        summary=signal.summary,
        reason=signal.reason,
        needs=signal.needs,
        waiting_for=signal.waiting_for,
        rejection_reason=(
            outcome.rejection_reason.value if outcome.rejection_reason else None
        ),
        outcome_kind=outcome.kind.value,
        mutated=outcome.mutated,
    )
    _append(session_path, record.to_dict())
    return record


# =============================================================================
# Normalizing reader + source-phase counting
# =============================================================================


def read_history(session_path: Path) -> list[HistoryRecord]:
    """Read all rows, normalizing legacy and v2 rows into HistoryRecord.

    Corrupt (non-JSON) and non-object lines are skipped, matching the legacy reader.
    """
    return [_normalize(row) for row in _iter_rows(session_path)]


def count_source_phase_iterations(session_path: Path, phase: str) -> int:
    """Count iterations spent in `phase` (by source phase, case-insensitive).

    Includes rejected events and the boundary iteration that trips a limit, because
    each was recorded with its source phase.
    """
    wanted = phase.lower()
    return sum(
        1
        for record in read_history(session_path)
        if record.source_phase and record.source_phase.lower() == wanted
    )


# =============================================================================
# Legacy read shims (unchanged public contract)
# =============================================================================


def get_phase_iteration_count(session_path: Path, phase: str) -> int:
    """Count iterations in a phase for per-phase limit enforcement.

    Compatibility shim over source-phase counting; legacy rows count by their `phase`
    field (== normalized source_phase).
    """
    return count_source_phase_iterations(session_path, phase)


def read_signal_history(session_path: Path) -> list[SignalHistoryEntry]:
    """Read history as legacy SignalHistoryEntry rows (phase <- source_phase)."""
    return [
        SignalHistoryEntry(
            timestamp=record.timestamp,
            iteration=record.iteration,
            phase=record.source_phase,
            status=record.raw_status,
            summary=record.summary,
            reason=record.reason,
            needs=record.needs,
            waiting_for=record.waiting_for,
        )
        for record in read_history(session_path)
    ]


# =============================================================================
# Internals
# =============================================================================


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _phase_value(phase: Phase | None) -> str | None:
    """Return the enum value of a phase, or None."""
    return phase.value if phase is not None else None


def _append(session_path: Path, payload: Mapping[str, object]) -> None:
    history_file = session_path / HISTORY_FILENAME
    with open(history_file, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload) + "\n")


def _iter_rows(session_path: Path) -> list[dict[str, object]]:
    history_file = session_path / HISTORY_FILENAME
    if not history_file.exists():
        return []
    rows: list[dict[str, object]] = []
    for line in history_file.read_text().splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            rows.append(data)
    return rows


def _normalize(data: dict[str, object]) -> HistoryRecord:
    """Fold a legacy or v2 row dict into a HistoryRecord."""
    if data.get("v") is None:  # legacy row: only `phase` is known
        return HistoryRecord(
            timestamp=_as_str(data.get("timestamp")) or "",
            iteration=_as_int(data.get("iteration")),
            source_phase=_as_str(data.get("phase")),
            target_phase=None,
            raw_status=_as_str(data.get("status")) or "",
            accepted=None,
            validation_error=None,
            summary=_as_str(data.get("summary")),
            reason=_as_str(data.get("reason")),
            needs=_as_str(data.get("needs")),
            waiting_for=_as_str(data.get("for")),
            schema_version=LEGACY_SCHEMA_VERSION,
        )
    return HistoryRecord(
        timestamp=_as_str(data.get("timestamp")) or "",
        iteration=_as_int(data.get("iteration")),
        source_phase=_as_str(data.get("source_phase")),
        target_phase=_as_str(data.get("target_phase")),
        raw_status=_as_str(data.get("status")) or "",
        accepted=_as_bool(data.get("accepted")),
        validation_error=_as_str(data.get("validation_error")),
        summary=_as_str(data.get("summary")),
        reason=_as_str(data.get("reason")),
        needs=_as_str(data.get("needs")),
        waiting_for=_as_str(data.get("for")),
        rejection_reason=_as_str(data.get("rejection_reason")),
        outcome_kind=_as_str(data.get("outcome_kind")),
        mutated=_as_bool(data.get("mutated")),
        schema_version=_as_int(data.get("v")) or SCHEMA_VERSION,
    )


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _as_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _as_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None
