"""Authoritative overview workflow-state parsing, atomic mutation, and processing.

This is the side-effecting layer around the pure `validate_workflow_event`:

1. Strict parse: `_overview.md` text -> typed `OverviewState` or typed parse error.
2. Atomic write: `atomic_write_text` (tempfile + `os.replace`, same directory).
3. Pure render + apply: `render_overview` / `apply_overview_transition` rewrite the
   Phase (and optionally Blocked/Last Action/Next/Flow Log) in place without
   clobbering agent-owned content.
4. Processor: `process_workflow_event` validates completely before mutating, mutates
   only on an accepted phase change, and converts mutation failure into an explicit
   rejection.

Phase 4's approval service reuses the atomic primitive and `apply_overview_transition`;
Phase 3's history reuses `ProcessedOutcome` source/target/accepted/error fields.
"""

import contextlib
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

from .phases import Phase
from .signals import OVERVIEW_FILENAME, Signal, SignalStatus
from .timestamps import iteration_timestamp
from .workflow_event import (
    RejectionReason,
    WorkflowEvent,
    validate_workflow_event,
)

# =============================================================================
# Strict overview parsing
# =============================================================================


class OverviewParseError(Enum):
    """Typed cause of a strict overview-state parse failure."""

    FILE_NOT_FOUND = "file_not_found"
    MISSING_PHASE = "missing_phase"
    DUPLICATE_PHASE = "duplicate_phase"
    MALFORMED_PHASE = "malformed_phase"  # value not a Phase enum member
    MISSING_BLOCKED = "missing_blocked"
    DUPLICATE_BLOCKED = "duplicate_blocked"
    MALFORMED_BLOCKED = "malformed_blocked"  # empty value
    MISSING_LAST_ACTION = "missing_last_action"
    DUPLICATE_LAST_ACTION = "duplicate_last_action"
    MISSING_NEXT = "missing_next"
    DUPLICATE_NEXT = "duplicate_next"
    MISSING_FLOW_LOG = "missing_flow_log"  # no "## Flow Log" heading
    DUPLICATE_FLOW_LOG = "duplicate_flow_log"


@dataclass(frozen=True)
class OverviewState:
    """Strictly-parsed authoritative workflow state of one `_overview.md`.

    raw_text is retained so transitions rewrite in place (single-match subn) rather
    than reconstructing the file and clobbering agent-owned content. blocked is kept
    as its raw value (e.g. "no", "waiting_human") - structure-strict, value-lenient.
    """

    phase: Phase
    blocked: str
    last_action: str
    next_action: str
    raw_text: str


@dataclass(frozen=True)
class OverviewParseResult:
    """Tagged parse result: exactly one of state / error is non-None."""

    state: OverviewState | None
    error: OverviewParseError | None = None
    message: str | None = None


_FLOW_LOG_HEADING = re.compile(r"^##\s+Flow Log\s*$", re.MULTILINE)


def _extract_single(
    content: str,
    field: str,
    missing: OverviewParseError,
    duplicate: OverviewParseError,
) -> tuple[str | None, OverviewParseError | None]:
    """Return the single stripped value of `field:` or a missing/duplicate error."""
    matches = re.findall(rf"^{re.escape(field)}:[ \t]*(.*)$", content, re.MULTILINE)
    if len(matches) == 0:
        return None, missing
    if len(matches) > 1:
        return None, duplicate
    return matches[0].strip(), None


def _parse_failure(error: OverviewParseError, message: str) -> OverviewParseResult:
    return OverviewParseResult(state=None, error=error, message=message)


def parse_overview_state(content: str) -> OverviewParseResult:
    """Strictly parse the Status block. First violation wins; no mutation.

    Requires exactly one each of Phase, Blocked, Last Action, Next, and a single
    `## Flow Log` heading. Unrelated lines are ignored and preserved by callers.
    """
    phase_val, err = _extract_single(
        content,
        "Phase",
        OverviewParseError.MISSING_PHASE,
        OverviewParseError.DUPLICATE_PHASE,
    )
    if err is not None:
        return _parse_failure(err, f"Phase field {err.value}")
    assert phase_val is not None
    try:
        phase = Phase(phase_val.lower())
    except ValueError:
        return _parse_failure(
            OverviewParseError.MALFORMED_PHASE, f"Unknown phase value: {phase_val}"
        )

    blocked_val, err = _extract_single(
        content,
        "Blocked",
        OverviewParseError.MISSING_BLOCKED,
        OverviewParseError.DUPLICATE_BLOCKED,
    )
    if err is not None:
        return _parse_failure(err, f"Blocked field {err.value}")
    assert blocked_val is not None
    if not blocked_val:
        return _parse_failure(
            OverviewParseError.MALFORMED_BLOCKED, "Blocked value is empty"
        )

    last_val, err = _extract_single(
        content,
        "Last Action",
        OverviewParseError.MISSING_LAST_ACTION,
        OverviewParseError.DUPLICATE_LAST_ACTION,
    )
    if err is not None:
        return _parse_failure(err, f"Last Action field {err.value}")
    assert last_val is not None

    next_val, err = _extract_single(
        content,
        "Next",
        OverviewParseError.MISSING_NEXT,
        OverviewParseError.DUPLICATE_NEXT,
    )
    if err is not None:
        return _parse_failure(err, f"Next field {err.value}")
    assert next_val is not None

    headings = _FLOW_LOG_HEADING.findall(content)
    if len(headings) == 0:
        return _parse_failure(
            OverviewParseError.MISSING_FLOW_LOG, "No '## Flow Log' heading"
        )
    if len(headings) > 1:
        return _parse_failure(
            OverviewParseError.DUPLICATE_FLOW_LOG, "Multiple '## Flow Log' headings"
        )

    return OverviewParseResult(
        state=OverviewState(
            phase=phase,
            blocked=blocked_val,
            last_action=last_val,
            next_action=next_val,
            raw_text=content,
        )
    )


def read_overview_state(session_path: Path) -> OverviewParseResult:
    """Read and strictly parse `_overview.md`; FILE_NOT_FOUND when absent."""
    overview_path = session_path / OVERVIEW_FILENAME
    if not overview_path.exists():
        return _parse_failure(
            OverviewParseError.FILE_NOT_FOUND, f"No _overview.md at {overview_path}"
        )
    return parse_overview_state(overview_path.read_text())


# =============================================================================
# Atomic same-file replacement (reused by Phase 4 approval)
# =============================================================================


def atomic_write_text(target: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Atomically replace `target` with `content`.

    Writes to a temp file in `target.parent` (same filesystem, so `os.replace` is
    atomic), fsyncs, then replaces. On any failure the temp file is removed and the
    original target is left byte-for-byte intact. Raises OSError on write failure.
    """
    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=target.name + ".", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


# =============================================================================
# Pure render + atomic apply
# =============================================================================


@dataclass(frozen=True)
class OverviewTransition:
    """A requested authoritative overview change. None fields are preserved.

    Phase 2 orchestrator/processor transitions set target_phase (+ optional Flow Log
    audit line). Phase 4 approval additionally sets blocked/last_action/next_action.
    """

    target_phase: Phase
    flow_log_entry: str | None = None  # full preformatted "- [NNN @ ...] ..." line
    blocked: str | None = None
    last_action: str | None = None
    next_action: str | None = None


class OverviewWriteError(Enum):
    """Typed cause of a failed overview transition apply."""

    PARSE_FAILED = "parse_failed"  # re-read/parse failed before writing
    WRITE_FAILED = "write_failed"  # atomic_write_text raised OSError
    PHASE_MOVED = "phase_moved"  # expected_source != freshly parsed phase (CAS miss)


@dataclass(frozen=True)
class OverviewWriteResult:
    """Outcome of `apply_overview_transition`. ok=True means an atomic write landed."""

    ok: bool
    new_phase: Phase | None = None
    error: OverviewWriteError | None = None
    parse_error: OverviewParseError | None = None
    message: str | None = None


def _replace_field(content: str, field: str, value: str) -> str:
    """Single-match replace of a `field:` line. Raises if not exactly one match."""
    new_content, count = re.subn(
        rf"^{re.escape(field)}:.*$",
        f"{field}: {value}",
        content,
        count=1,
        flags=re.MULTILINE,
    )
    if count != 1:
        raise ValueError(f"Expected exactly one '{field}:' line, found {count}")
    return new_content


def _append_flow_log(content: str, entry: str) -> str:
    """Insert `entry` as the last line of the '## Flow Log' section."""
    trailing_newline = content.endswith("\n")
    lines = content.splitlines()

    heading_idx: int | None = None
    for i, line in enumerate(lines):
        if _FLOW_LOG_HEADING.match(line):
            heading_idx = i
            break
    if heading_idx is None:
        raise ValueError("No '## Flow Log' heading to append to")

    end_idx = len(lines)
    for j in range(heading_idx + 1, len(lines)):
        if lines[j].startswith("## "):
            end_idx = j
            break

    insert_at = end_idx
    while insert_at - 1 > heading_idx and lines[insert_at - 1].strip() == "":
        insert_at -= 1

    lines.insert(insert_at, entry)
    result = "\n".join(lines)
    if trailing_newline:
        result += "\n"
    return result


def render_overview(state: OverviewState, transition: OverviewTransition) -> str:
    """Pure: render new overview text applying a transition in place.

    Parse guarantees each targeted field occurs exactly once, so single-match
    replacement is safe. Unrelated lines are preserved verbatim.
    """
    content = _replace_field(state.raw_text, "Phase", transition.target_phase.value)
    if transition.blocked is not None:
        content = _replace_field(content, "Blocked", transition.blocked)
    if transition.last_action is not None:
        content = _replace_field(content, "Last Action", transition.last_action)
    if transition.next_action is not None:
        content = _replace_field(content, "Next", transition.next_action)
    if transition.flow_log_entry is not None:
        content = _append_flow_log(content, transition.flow_log_entry)
    return content


def apply_overview_transition(
    session_path: Path,
    transition: OverviewTransition,
    expected_source: Phase | None = None,
) -> OverviewWriteResult:
    """Re-read, render, and atomically write the overview transition.

    Re-parses immediately before writing (never trusts a stale state). When
    `expected_source` is given, it is a compare-and-swap guard: if the freshly parsed
    Phase differs, no write happens and PHASE_MOVED is returned (another actor advanced
    the phase between the caller's read and this write). Parse failure or an atomic-write
    OSError yields ok=False with no partial mutation.
    """
    parsed = read_overview_state(session_path)
    if parsed.state is None:
        return OverviewWriteResult(
            ok=False,
            error=OverviewWriteError.PARSE_FAILED,
            parse_error=parsed.error,
            message=parsed.message,
        )
    if expected_source is not None and parsed.state.phase != expected_source:
        return OverviewWriteResult(
            ok=False,
            error=OverviewWriteError.PHASE_MOVED,
            message=(
                f"Phase moved to '{parsed.state.phase.value}', "
                f"expected '{expected_source.value}'; not writing"
            ),
        )
    new_content = render_overview(parsed.state, transition)
    try:
        atomic_write_text(session_path / OVERVIEW_FILENAME, new_content)
    except OSError as exc:
        return OverviewWriteResult(
            ok=False, error=OverviewWriteError.WRITE_FAILED, message=str(exc)
        )
    return OverviewWriteResult(ok=True, new_phase=transition.target_phase)


# =============================================================================
# Event processor
# =============================================================================


class OutcomeKind(Enum):
    """Discriminated outcome of processing one workflow event."""

    ACCEPTED_NO_CHANGE = "accepted_no_change"  # validated, same phase, no write
    ACCEPTED_TRANSITION = "accepted_transition"  # validated + Phase atomically mutated
    REJECTED_VALIDATION = (
        "rejected_validation"  # validator rejected; no mutation attempted
    )
    REJECTED_MUTATION = (
        "rejected_mutation"  # validated but atomic overview write failed
    )


@dataclass(frozen=True)
class ProcessedOutcome:
    """Truthful outcome of processing one workflow event.

    Phase 3 history consumes source_phase/target_phase/accepted/validation_error to
    write source-counted, accept-flagged audit rows. `mutated` reports whether
    `_overview.md` changed on disk.
    """

    kind: OutcomeKind
    accepted: bool
    source_phase: Phase | None
    target_phase: Phase | None
    mutated: bool
    rejection_reason: RejectionReason | None = None
    write_error: OverviewWriteError | None = None
    parse_error: OverviewParseError | None = None
    validation_error: str | None = None


def process_workflow_event(
    session_path: Path,
    signal: Signal,
    source_phase: str | None,
    source_iterations: int,
    iteration: int,
    now: datetime | None = None,
) -> ProcessedOutcome:
    """Validate an event fully, then mutate only on an accepted phase change.

    Control flow: build event -> `validate_workflow_event` (pure) -> reject if invalid
    (no file touched) -> if accepted and Phase actually changes via continue/done,
    atomically apply the transition -> convert any write failure into an explicit
    REJECTED_MUTATION. waiting/blocked can never reach the mutation branch.
    """
    event = WorkflowEvent(
        source_phase=source_phase,
        status=signal.status,
        requested_target=signal.phase,
        waiting_for=signal.waiting_for,
        source_iterations=source_iterations,
    )
    result = validate_workflow_event(event)
    if not result.accepted:
        return ProcessedOutcome(
            kind=OutcomeKind.REJECTED_VALIDATION,
            accepted=False,
            source_phase=result.source_phase,
            target_phase=result.target_phase,
            mutated=False,
            rejection_reason=result.rejection_reason,
            validation_error=result.validation_error,
        )

    # Defense-in-depth against phase smuggling: only continue/done may mutate Phase.
    is_change = (
        signal.status in (SignalStatus.CONTINUE, SignalStatus.DONE)
        and result.source_phase is not None
        and result.target_phase is not None
        and result.target_phase != result.source_phase
    )
    if not is_change:
        return ProcessedOutcome(
            kind=OutcomeKind.ACCEPTED_NO_CHANGE,
            accepted=True,
            source_phase=result.source_phase,
            target_phase=result.target_phase,
            mutated=False,
        )

    assert result.source_phase is not None and result.target_phase is not None
    entry = (
        f"- {iteration_timestamp(iteration, now)} Phase transition: "
        f"{result.source_phase.value} -> {result.target_phase.value}"
    )
    write = apply_overview_transition(
        session_path,
        OverviewTransition(target_phase=result.target_phase, flow_log_entry=entry),
        expected_source=result.source_phase,
    )
    if not write.ok:
        return ProcessedOutcome(
            kind=OutcomeKind.REJECTED_MUTATION,
            accepted=False,
            source_phase=result.source_phase,
            target_phase=result.target_phase,
            mutated=False,
            write_error=write.error,
            parse_error=write.parse_error,
            validation_error=write.message,
        )
    return ProcessedOutcome(
        kind=OutcomeKind.ACCEPTED_TRANSITION,
        accepted=True,
        source_phase=result.source_phase,
        target_phase=result.target_phase,
        mutated=True,
    )
