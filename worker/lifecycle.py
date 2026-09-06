"""Machine-owned final-polish lifecycle provenance."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .phases import Phase
from .signal_history import (
    ESCALATION_STATUS,
    HISTORY_FILENAME,
    HistoryRecord,
    read_history,
)
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

# Transition admission and phase provenance must share the same prerequisites.
FINAL_POLISH_PREREQUISITE_TRANSITIONS = REQUIRED_FINAL_POLISH_TRANSITIONS[:3]

# Agents copy these labels verbatim into reports checked by the evidence gate.
TESTING_RUN_FIRST = "1st (post-implementation)"
TESTING_RUN_SECOND = "2nd (post-quality)"

_PR_READINESS_TO_QUALITY = ("pr-readiness", "quality")

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
class EpochPhaseRunCount:
    count: int | None
    issues: tuple[LifecycleIssue, ...] = ()

    def __post_init__(self) -> None:
        if self.count is None and not self.issues:
            raise ValueError("Epoch phase count requires either a count or issues")
        if self.count is not None and self.issues:
            raise ValueError("Epoch phase count requires either a count or issues")

    @property
    def ok(self) -> bool:
        return self.count is not None

    @property
    def errors(self) -> tuple[str, ...]:
        return tuple(issue.message for issue in self.issues)


@dataclass(frozen=True)
class ProvenanceRule:
    required: tuple[tuple[str, str], ...]
    latest: tuple[str, str]


# Returning from readiness must preserve evidence of the initial testing pass.
PROVENANCE_RULES: dict[Phase, tuple[ProvenanceRule, ...]] = {
    Phase.TESTING: (
        ProvenanceRule(
            REQUIRED_FINAL_POLISH_TRANSITIONS[:1], REQUIRED_FINAL_POLISH_TRANSITIONS[0]
        ),
        ProvenanceRule(
            FINAL_POLISH_PREREQUISITE_TRANSITIONS, REQUIRED_FINAL_POLISH_TRANSITIONS[2]
        ),
    ),
    Phase.QUALITY: (
        ProvenanceRule(
            REQUIRED_FINAL_POLISH_TRANSITIONS[:2], REQUIRED_FINAL_POLISH_TRANSITIONS[1]
        ),
        ProvenanceRule(REQUIRED_FINAL_POLISH_TRANSITIONS[:2], _PR_READINESS_TO_QUALITY),
    ),
    Phase.PR_READINESS: (
        ProvenanceRule(
            FINAL_POLISH_PREREQUISITE_TRANSITIONS, REQUIRED_FINAL_POLISH_TRANSITIONS[3]
        ),
    ),
}


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
    if not transitions or transitions[-1] != REQUIRED_FINAL_POLISH_TRANSITIONS[-1]:
        issues.append(
            LifecycleIssue(
                LifecycleIssueCode.LATEST_TRANSITION_INVALID,
                LATEST_TRANSITION_ERROR,
            )
        )
    return LifecycleCheck(tuple(issues))


def derive_testing_run(session_path: Path) -> str:
    """Missing provenance cannot establish a post-quality run."""
    history, _ = scoped_history(session_path)
    transitions = accepted_transitions(history)
    latest_into_testing: tuple[str | None, str | None] | None = None
    for t in transitions:
        if t[1] == "testing":
            latest_into_testing = t
    if latest_into_testing is not None and latest_into_testing[0] == "quality":
        return TESTING_RUN_SECOND
    return TESTING_RUN_FIRST


def has_final_polish_prerequisites(
    transitions: list[tuple[str | None, str | None]],
) -> bool:
    return _contains_ordered(transitions, FINAL_POLISH_PREREQUISITE_TRANSITIONS)


def validate_phase_provenance(session_path: Path, phase: Phase) -> LifecycleCheck:
    """Validate the recovery epoch and late-phase transition provenance."""
    history, anchor_errors = scoped_history(session_path)
    if anchor_errors:
        return LifecycleCheck(
            tuple(
                LifecycleIssue(LifecycleIssueCode.RECOVERY_ANCHOR_INVALID, message)
                for message in anchor_errors
            )
        )
    rules = PROVENANCE_RULES.get(phase)
    if rules is None:
        return LifecycleCheck(())
    transitions = accepted_transitions(history)
    latest = transitions[-1] if transitions else None
    if any(
        latest == rule.latest and _contains_ordered(transitions, rule.required)
        for rule in rules
    ):
        return LifecycleCheck(())
    return LifecycleCheck(
        (
            LifecycleIssue(
                LifecycleIssueCode.PHASE_PROVENANCE_MISSING,
                _phase_provenance_error(phase, rules, latest),
            ),
        )
    )


def count_epoch_source_phase_runs_including_current(
    session_path: Path, phase: str
) -> EpochPhaseRunCount:
    history, anchor_errors = scoped_history(session_path)
    if anchor_errors:
        return EpochPhaseRunCount(
            count=None,
            issues=tuple(
                LifecycleIssue(LifecycleIssueCode.RECOVERY_ANCHOR_INVALID, message)
                for message in anchor_errors
            ),
        )
    wanted = phase.lower()
    completed_runs = sum(
        1
        for record in history
        if record.source_phase and record.source_phase.lower() == wanted
    )
    return EpochPhaseRunCount(count=completed_runs + 1)


def count_escalations_since_phase_entry(
    session_path: Path, phase: Phase
) -> EpochPhaseRunCount:
    """Escalations since the last accepted transition into `phase` (or the start of
    scoped history); anchor errors propagate as issues."""
    history, anchor_errors = scoped_history(session_path)
    if anchor_errors:
        return EpochPhaseRunCount(
            count=None,
            issues=tuple(
                LifecycleIssue(LifecycleIssueCode.RECOVERY_ANCHOR_INVALID, message)
                for message in anchor_errors
            ),
        )
    target_phase = phase.value
    entry_index = _last_phase_entry_index(history, target_phase)
    escalations = sum(
        1
        for record in history[entry_index + 1 :]
        if record.raw_status == ESCALATION_STATUS
        and record.target_phase == target_phase
    )
    return EpochPhaseRunCount(count=escalations)


def _last_phase_entry_index(history: list[HistoryRecord], target_phase: str) -> int:
    """-1 when none, so `history[index + 1:]` spans the whole scoped history."""
    for index in range(len(history) - 1, -1, -1):
        record = history[index]
        if _is_accepted_transition(record) and record.target_phase == target_phase:
            return index
    return -1


def accepted_transitions(
    history: list[HistoryRecord],
) -> list[tuple[str | None, str | None]]:
    return [
        (record.source_phase, record.target_phase)
        for record in history
        if _is_accepted_transition(record)
    ]


def _is_accepted_transition(record: HistoryRecord) -> bool:
    return record.accepted is True and record.mutated is True


def latest_applied_recovery_anchor(session_path: Path) -> RecoveryAnchor | None:
    """A receipt becomes authoritative only after its ID appears in the Flow Log."""
    anchor, _ = _resolve_applied_recovery_anchor(session_path)
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


def _phase_provenance_error(
    phase: Phase,
    rules: tuple[ProvenanceRule, ...],
    latest: tuple[str | None, str | None] | None,
) -> str:
    accepted = " or ".join(_format_transition(rule.latest) for rule in rules)
    return (
        f"Overview phase '{phase.value}' lacks matching accepted lifecycle provenance; "
        f"accepted latest transition is {accepted}, but latest accepted transition is "
        f"{_format_transition(latest)}. Inspect with `samocode check final-polish`"
    )


def _format_transition(transition: tuple[str | None, str | None] | None) -> str:
    if transition is None:
        return "none"
    return f"{transition[0]} -> {transition[1]}"
