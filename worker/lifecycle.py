"""Machine-owned final-polish lifecycle provenance."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .phases import Phase
from .signal_history import HISTORY_FILENAME, HistoryRecord, read_history
from .signals import OVERVIEW_FILENAME

RECOVERY_DIRNAME = "_recovery"
RECOVERY_RECEIPT_FILENAME = "receipt.json"
_RECOVERY_COMMIT_MARKER = re.compile(r"\[samocode-recovery:([0-9a-f]{12})\]")

REQUIRED_FINAL_POLISH_TRANSITIONS = (
    ("implementation", "testing"),
    ("testing", "quality"),
    ("quality", "testing"),
    ("testing", "pr-readiness"),
)

LIFECYCLE_MISSING_ERROR = (
    "Signal history lacks the ordered final-polish lifecycle: "
    "testing -> quality -> testing -> pr-readiness"
)
LATEST_TRANSITION_ERROR = (
    "Latest accepted phase transition is not testing -> pr-readiness"
)


class LifecycleIssueCode(Enum):
    RECOVERY_ANCHOR_INVALID = "recovery_anchor_invalid"
    FINAL_POLISH_SEQUENCE_MISSING = "final_polish_sequence_missing"
    LATEST_TRANSITION_INVALID = "latest_transition_invalid"
    PHASE_PROVENANCE_MISSING = "phase_provenance_missing"


@dataclass(frozen=True)
class LifecycleIssue:
    code: LifecycleIssueCode
    message: str


@dataclass(frozen=True)
class LifecycleCheck:
    issues: tuple[LifecycleIssue, ...]

    @property
    def ok(self) -> bool:
        return not self.issues

    @property
    def errors(self) -> tuple[str, ...]:
        return tuple(issue.message for issue in self.issues)

    @property
    def codes(self) -> frozenset[LifecycleIssueCode]:
        return frozenset(issue.code for issue in self.issues)


@dataclass(frozen=True)
class RecoveryAnchor:
    recovery_id: str
    history_rows_before: int
    history_bytes_before: int
    history_sha256_before: str


def scoped_history(session_path: Path) -> tuple[list[HistoryRecord], tuple[str, ...]]:
    """Return history after the latest applied recovery receipt, if any."""
    history = read_history(session_path)
    anchor, resolution_errors = _resolve_applied_recovery_anchor(session_path)
    if resolution_errors:
        return [], resolution_errors
    if anchor is None:
        return history, ()

    history_path = session_path / HISTORY_FILENAME
    try:
        raw = history_path.read_bytes()
    except OSError as exc:
        return [], (f"Cannot read signal history for recovery anchor: {exc}",)
    prefix = raw[: anchor.history_bytes_before]
    if len(prefix) != anchor.history_bytes_before:
        return [], ("Signal history is shorter than the applied recovery anchor",)
    if hashlib.sha256(prefix).hexdigest() != anchor.history_sha256_before:
        return [], ("Signal history before the applied recovery anchor was modified",)
    if len(history) < anchor.history_rows_before:
        return [], ("Signal history has fewer rows than the applied recovery anchor",)
    return history[anchor.history_rows_before :], ()


def validate_final_polish_lifecycle(session_path: Path) -> LifecycleCheck:
    history, anchor_errors = scoped_history(session_path)
    issues = [
        LifecycleIssue(LifecycleIssueCode.RECOVERY_ANCHOR_INVALID, message)
        for message in anchor_errors
    ]
    transitions = accepted_transitions(history)
    if not _contains_ordered(transitions, REQUIRED_FINAL_POLISH_TRANSITIONS):
        issues.append(
            LifecycleIssue(
                LifecycleIssueCode.FINAL_POLISH_SEQUENCE_MISSING,
                LIFECYCLE_MISSING_ERROR,
            )
        )
    if (
        not transitions
        or transitions[-1] != REQUIRED_FINAL_POLISH_TRANSITIONS[-1]
    ):
        issues.append(
            LifecycleIssue(
                LifecycleIssueCode.LATEST_TRANSITION_INVALID,
                LATEST_TRANSITION_ERROR,
            )
        )
    return LifecycleCheck(tuple(issues))


def validate_phase_provenance(session_path: Path, phase: Phase) -> LifecycleCheck:
    """Fail when a late overview phase is not backed by accepted transitions."""
    if phase not in {Phase.TESTING, Phase.QUALITY, Phase.PR_READINESS}:
        return LifecycleCheck(())

    history, anchor_errors = scoped_history(session_path)
    if anchor_errors:
        return LifecycleCheck(
            tuple(
                LifecycleIssue(LifecycleIssueCode.RECOVERY_ANCHOR_INVALID, message)
                for message in anchor_errors
            )
        )
    transitions = accepted_transitions(history)
    latest = transitions[-1] if transitions else None

    if phase is Phase.QUALITY:
        required = REQUIRED_FINAL_POLISH_TRANSITIONS[:2]
        expected_latest = REQUIRED_FINAL_POLISH_TRANSITIONS[1]
    elif phase is Phase.PR_READINESS:
        required = REQUIRED_FINAL_POLISH_TRANSITIONS
        expected_latest = REQUIRED_FINAL_POLISH_TRANSITIONS[-1]
    elif latest == REQUIRED_FINAL_POLISH_TRANSITIONS[2]:
        required = REQUIRED_FINAL_POLISH_TRANSITIONS[:3]
        expected_latest = REQUIRED_FINAL_POLISH_TRANSITIONS[2]
    else:
        required = REQUIRED_FINAL_POLISH_TRANSITIONS[:1]
        expected_latest = REQUIRED_FINAL_POLISH_TRANSITIONS[0]

    if _contains_ordered(transitions, required) and latest == expected_latest:
        return LifecycleCheck(())
    expected = " -> ".join(target for _, target in required)
    return LifecycleCheck(
        (
            LifecycleIssue(
                LifecycleIssueCode.PHASE_PROVENANCE_MISSING,
                (
                    f"Overview phase '{phase.value}' lacks matching accepted "
                    f"lifecycle provenance ({expected}); latest accepted transition "
                    f"is {_format_transition(latest)}"
                ),
            ),
        )
    )


def accepted_transitions(
    history: list[HistoryRecord],
) -> list[tuple[str | None, str | None]]:
    return [
        (record.source_phase, record.target_phase)
        for record in history
        if record.accepted is True and record.mutated is True
    ]


def latest_applied_recovery_anchor(session_path: Path) -> RecoveryAnchor | None:
    """A receipt becomes authoritative only after its ID appears in the Flow Log."""
    anchor, _errors = _resolve_applied_recovery_anchor(session_path)
    return anchor


def recovery_commit_marker(recovery_id: str) -> str:
    """Render the machine-readable marker that makes a receipt authoritative."""
    return f"[samocode-recovery:{recovery_id}]"


def _resolve_applied_recovery_anchor(
    session_path: Path,
) -> tuple[RecoveryAnchor | None, tuple[str, ...]]:
    recovery_root = session_path / RECOVERY_DIRNAME
    overview_path = session_path / OVERVIEW_FILENAME
    if not overview_path.is_file():
        return None, ()
    try:
        overview = overview_path.read_text()
    except (OSError, UnicodeDecodeError) as exc:
        return None, (f"Cannot read overview for recovery anchor: {exc}",)
    recovery_ids = _RECOVERY_COMMIT_MARKER.findall(overview)
    if not recovery_ids:
        return None, ()
    recovery_id = recovery_ids[-1]
    if not recovery_root.is_dir():
        return None, ("Applied recovery Flow Log entry has no _recovery directory",)

    anchors: list[RecoveryAnchor] = []
    for receipt_path in recovery_root.glob(f"*/{RECOVERY_RECEIPT_FILENAME}"):
        try:
            payload = json.loads(receipt_path.read_text())
            if payload.get("recovery_id") != recovery_id:
                continue
            anchor = RecoveryAnchor(
                recovery_id=str(payload["recovery_id"]),
                history_rows_before=int(payload["history_rows_before"]),
                history_bytes_before=int(payload["history_bytes_before"]),
                history_sha256_before=str(payload["history_sha256_before"]),
            )
        except (
            OSError,
            UnicodeDecodeError,
            ValueError,
            KeyError,
            TypeError,
            json.JSONDecodeError,
        ):
            continue
        if (
            anchor.history_rows_before < 0
            or anchor.history_bytes_before < 0
            or not re.fullmatch(r"[0-9a-f]{64}", anchor.history_sha256_before)
        ):
            continue
        anchors.append(anchor)
    if len(anchors) != 1:
        return None, (
            f"Applied recovery {recovery_id} has {len(anchors)} valid receipts; expected 1",
        )
    return anchors[0], ()


def _contains_ordered(
    transitions: list[tuple[str | None, str | None]],
    required: tuple[tuple[str, str], ...],
) -> bool:
    required_index = 0
    for transition in transitions:
        if transition == required[required_index]:
            required_index += 1
            if required_index == len(required):
                return True
    return False


def _format_transition(transition: tuple[str | None, str | None] | None) -> str:
    if transition is None:
        return "none"
    return f"{transition[0]} -> {transition[1]}"
