"""Strict parsing and atomic mutation of authoritative workflow state."""

import contextlib
import fcntl
import os
import re
import tempfile
import time
from collections.abc import Iterator
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


class OverviewParseError(Enum):
    FILE_NOT_FOUND = "file_not_found"
    READ_FAILED = "read_failed"
    MISSING_PHASE = "missing_phase"
    DUPLICATE_PHASE = "duplicate_phase"
    MALFORMED_PHASE = "malformed_phase"
    MISSING_BLOCKED = "missing_blocked"
    DUPLICATE_BLOCKED = "duplicate_blocked"
    MALFORMED_BLOCKED = "malformed_blocked"
    MISSING_LAST_ACTION = "missing_last_action"
    DUPLICATE_LAST_ACTION = "duplicate_last_action"
    MISSING_NEXT = "missing_next"
    DUPLICATE_NEXT = "duplicate_next"
    MISSING_FLOW_LOG = "missing_flow_log"
    DUPLICATE_FLOW_LOG = "duplicate_flow_log"


@dataclass(frozen=True)
class OverviewState:
    """Retain raw text so transitions preserve agent-owned content byte-for-byte."""

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

    def __post_init__(self) -> None:
        if (self.state is None) == (self.error is None):
            raise ValueError(
                "Overview parse result requires exactly one of state or error"
            )
        if self.error is None and self.message is not None:
            raise ValueError("Successful overview parse cannot carry an error message")
        if self.error is not None and not self.message:
            raise ValueError("Failed overview parse requires an error message")

    @classmethod
    def parsed(cls, state: OverviewState) -> "OverviewParseResult":
        return cls(state=state)

    @classmethod
    def failed(cls, error: OverviewParseError, message: str) -> "OverviewParseResult":
        return cls(state=None, error=error, message=message)


_FLOW_LOG_HEADING = re.compile(r"^##\s+Flow Log\s*$", re.MULTILINE)


def _extract_single(
    content: str,
    field: str,
    missing: OverviewParseError,
    duplicate: OverviewParseError,
) -> tuple[str | None, OverviewParseError | None]:
    matches = re.findall(rf"^{re.escape(field)}:[ \t]*(.*)$", content, re.MULTILINE)
    if len(matches) == 0:
        return None, missing
    if len(matches) > 1:
        return None, duplicate
    return matches[0].strip(), None


def _parse_failure(error: OverviewParseError, message: str) -> OverviewParseResult:
    return OverviewParseResult.failed(error, message)


def parse_overview_state(content: str) -> OverviewParseResult:
    """Require one authoritative copy of each field while preserving unrelated text."""
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

    return OverviewParseResult.parsed(
        OverviewState(
            phase=phase,
            blocked=blocked_val,
            last_action=last_val,
            next_action=next_val,
            raw_text=content,
        )
    )


def read_overview_state(session_path: Path) -> OverviewParseResult:
    overview_path = session_path / OVERVIEW_FILENAME
    if not overview_path.exists():
        return _parse_failure(
            OverviewParseError.FILE_NOT_FOUND, f"No _overview.md at {overview_path}"
        )
    try:
        content = overview_path.read_text()
    except (OSError, UnicodeDecodeError) as exc:
        return _parse_failure(
            OverviewParseError.READ_FAILED, f"Cannot read {overview_path}: {exc}"
        )
    return parse_overview_state(content)


def atomic_write_text(target: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Use same-directory replace so failures leave the original byte-identical."""
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


SESSION_LOCK_FILENAME = "_session.lock"

# Workers absorb brief approval contention; approval itself remains fail-fast.
WORKER_LOCK_WAIT_SECONDS = 5.0

_LOCK_RETRY_INTERVAL_SECONDS = 0.05


class LockState(Enum):
    ACQUIRED = "acquired"
    CONTENDED = "contended"
    FAILED = "failed"


@dataclass(frozen=True)
class LockOutcome:
    """Tagged lock result. `message` is populated only for FAILED."""

    state: LockState
    message: str | None = None

    def __post_init__(self) -> None:
        if self.state is LockState.FAILED and not self.message:
            raise ValueError("Failed lock outcome requires a message")
        if self.state is not LockState.FAILED and self.message is not None:
            raise ValueError("Non-failed lock outcome cannot carry a message")

    @classmethod
    def acquired(cls) -> "LockOutcome":
        return cls(LockState.ACQUIRED)

    @classmethod
    def contended(cls) -> "LockOutcome":
        return cls(LockState.CONTENDED)

    @classmethod
    def failed(cls, message: str) -> "LockOutcome":
        return cls(LockState.FAILED, message)


@contextlib.contextmanager
def session_lock(
    session_path: Path, *, wait_timeout: float = 0.0
) -> Iterator[LockOutcome]:
    """Serialize session-state writers via flock on a dedicated lock file.

    The lock file is never unlinked: flock serializes on the inode, so removing
    and recreating it would let two processes hold the lock at once.
    `wait_timeout` is the maximum time to retry on contention; the default 0.0
    gives up immediately.
    """
    try:
        fd = os.open(
            str(session_path / SESSION_LOCK_FILENAME), os.O_CREAT | os.O_RDWR, 0o644
        )
    except OSError as exc:
        yield LockOutcome.failed(f"Cannot open session lock file: {exc}")
        return
    try:
        deadline = time.monotonic() + wait_timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    yield LockOutcome.contended()
                    return
                time.sleep(_LOCK_RETRY_INTERVAL_SECONDS)
            except OSError as exc:
                yield LockOutcome.failed(f"Cannot acquire session lock: {exc}")
                return
        try:
            yield LockOutcome.acquired()
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


@dataclass(frozen=True)
class OverviewTransition:
    """A `None` field preserves its existing authoritative value."""

    target_phase: Phase
    flow_log_entry: str | None = None  # full preformatted "- [NNN @ ...] ..." line
    blocked: str | None = None
    last_action: str | None = None
    next_action: str | None = None


class OverviewWriteError(Enum):
    PARSE_FAILED = "parse_failed"  # re-read/parse failed before writing
    WRITE_FAILED = "write_failed"  # atomic_write_text raised OSError
    PHASE_MOVED = "phase_moved"  # expected_source != freshly parsed phase (CAS miss)
    LOCK_CONTENDED = "lock_contended"
    LOCK_IO_FAILED = "lock_io_failed"
    LOCK_UNAVAILABLE = "lock_unavailable"


@dataclass(frozen=True)
class OverviewWriteFailure:
    error: OverviewWriteError
    message: str
    observed_phase: Phase | None = None
    parse_error: OverviewParseError | None = None

    def __post_init__(self) -> None:
        if not self.message:
            raise ValueError("Overview write failure requires a message")
        if self.error is OverviewWriteError.PHASE_MOVED:
            if self.observed_phase is None or self.parse_error is not None:
                raise ValueError("PHASE_MOVED requires only an observed phase")
        elif self.error is OverviewWriteError.PARSE_FAILED:
            if self.parse_error is None or self.observed_phase is not None:
                raise ValueError("PARSE_FAILED requires only a parse error")
        elif self.observed_phase is not None or self.parse_error is not None:
            raise ValueError(f"{self.error.value} cannot carry phase or parse details")


@dataclass(frozen=True)
class OverviewWriteResult:
    new_phase: Phase | None = None
    failure: OverviewWriteFailure | None = None

    def __post_init__(self) -> None:
        if (self.new_phase is None) == (self.failure is None):
            raise ValueError(
                "Overview write result requires exactly one of phase or failure"
            )

    @property
    def ok(self) -> bool:
        return self.failure is None

    @property
    def error(self) -> OverviewWriteError | None:
        return self.failure.error if self.failure else None

    @property
    def observed_phase(self) -> Phase | None:
        return self.failure.observed_phase if self.failure else None

    @property
    def parse_error(self) -> OverviewParseError | None:
        return self.failure.parse_error if self.failure else None

    @property
    def message(self) -> str | None:
        return self.failure.message if self.failure else None

    @classmethod
    def applied(cls, phase: Phase) -> "OverviewWriteResult":
        return cls(new_phase=phase)

    @classmethod
    def failed(cls, failure: OverviewWriteFailure) -> "OverviewWriteResult":
        return cls(failure=failure)

    @classmethod
    def parse_failed(
        cls, error: OverviewParseError, message: str
    ) -> "OverviewWriteResult":
        return cls.failed(
            OverviewWriteFailure(
                OverviewWriteError.PARSE_FAILED,
                message,
                parse_error=error,
            )
        )

    @classmethod
    def phase_moved(cls, observed: Phase, message: str) -> "OverviewWriteResult":
        return cls.failed(
            OverviewWriteFailure(
                OverviewWriteError.PHASE_MOVED,
                message,
                observed_phase=observed,
            )
        )

    @classmethod
    def write_failed(cls, message: str) -> "OverviewWriteResult":
        return cls.failed(
            OverviewWriteFailure(OverviewWriteError.WRITE_FAILED, message)
        )

    @classmethod
    def lock_contended(cls, message: str) -> "OverviewWriteResult":
        return cls.failed(
            OverviewWriteFailure(OverviewWriteError.LOCK_CONTENDED, message)
        )

    @classmethod
    def lock_io_failed(cls, message: str) -> "OverviewWriteResult":
        return cls.failed(
            OverviewWriteFailure(OverviewWriteError.LOCK_IO_FAILED, message)
        )


def _replace_field(content: str, field: str, value: str) -> str:
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
    """Rewrite only strictly parsed fields, preserving every unrelated line."""
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
    *,
    wait_timeout: float = 0.0,
) -> OverviewWriteResult:
    """Serialize CAS; callers already holding the lock must use the locked variant."""
    with session_lock(session_path, wait_timeout=wait_timeout) as lock:
        if lock.state is LockState.CONTENDED:
            return OverviewWriteResult.lock_contended(
                "Session lock is held by a concurrent writer; not writing"
            )
        if lock.state is LockState.FAILED:
            return OverviewWriteResult.lock_io_failed(
                lock.message or "Session lock could not be acquired"
            )
        return apply_overview_transition_locked(
            session_path, transition, expected_source
        )


def apply_overview_transition_locked(
    session_path: Path,
    transition: OverviewTransition,
    expected_source: Phase | None = None,
) -> OverviewWriteResult:
    """Re-read under the caller's lock and reject a stale expected source without writing."""
    parsed = read_overview_state(session_path)
    if parsed.state is None:
        assert parsed.error is not None and parsed.message is not None
        return OverviewWriteResult.parse_failed(
            parsed.error,
            parsed.message,
        )
    if expected_source is not None and parsed.state.phase != expected_source:
        return OverviewWriteResult.phase_moved(
            parsed.state.phase,
            (
                f"Phase moved to '{parsed.state.phase.value}', "
                f"expected '{expected_source.value}'; not writing"
            ),
        )
    new_content = render_overview(parsed.state, transition)
    try:
        atomic_write_text(session_path / OVERVIEW_FILENAME, new_content)
    except OSError as exc:
        return OverviewWriteResult.write_failed(str(exc))
    return OverviewWriteResult.applied(transition.target_phase)


class OutcomeKind(Enum):
    ACCEPTED_NO_CHANGE = "accepted_no_change"
    ACCEPTED_TRANSITION = "accepted_transition"
    REJECTED_VALIDATION = "rejected_validation"
    REJECTED_MUTATION = "rejected_mutation"


@dataclass(frozen=True)
class ProcessedOutcome:
    """Carry authoritative source and acceptance data into the audit history."""

    kind: OutcomeKind
    source_phase: Phase | None
    target_phase: Phase | None
    rejection_reason: RejectionReason | None = None
    validation_message: str | None = None
    write_failure: OverviewWriteFailure | None = None

    def __post_init__(self) -> None:
        if self.kind in (
            OutcomeKind.ACCEPTED_NO_CHANGE,
            OutcomeKind.ACCEPTED_TRANSITION,
        ):
            if self.source_phase is None or self.target_phase is None:
                raise ValueError("Accepted outcomes require source and target phases")
            if self.rejection_reason or self.validation_message or self.write_failure:
                raise ValueError("Accepted outcomes cannot carry failure details")
            if (
                self.kind is OutcomeKind.ACCEPTED_TRANSITION
                and self.source_phase is self.target_phase
            ):
                raise ValueError("Accepted transition requires a phase change")
            if (
                self.kind is OutcomeKind.ACCEPTED_NO_CHANGE
                and self.source_phase is not self.target_phase
            ):
                raise ValueError("Accepted no-change outcome requires one phase")
        elif self.kind is OutcomeKind.REJECTED_VALIDATION:
            if self.rejection_reason is None or not self.validation_message:
                raise ValueError("Validation rejection requires a reason and message")
            if self.write_failure is not None:
                raise ValueError("Validation rejection cannot carry a write failure")
        elif self.kind is OutcomeKind.REJECTED_MUTATION:
            if self.source_phase is None or self.target_phase is None:
                raise ValueError("Mutation rejection requires source and target phases")
            if self.source_phase is self.target_phase:
                raise ValueError("Mutation rejection requires a phase change")
            if self.write_failure is None:
                raise ValueError("Mutation rejection requires a write failure")
            if self.rejection_reason or self.validation_message:
                raise ValueError("Mutation rejection cannot carry validation details")

    @property
    def accepted(self) -> bool:
        return self.kind in (
            OutcomeKind.ACCEPTED_NO_CHANGE,
            OutcomeKind.ACCEPTED_TRANSITION,
        )

    @property
    def mutated(self) -> bool:
        return self.kind is OutcomeKind.ACCEPTED_TRANSITION

    @property
    def write_error(self) -> OverviewWriteError | None:
        return self.write_failure.error if self.write_failure else None

    @property
    def parse_error(self) -> OverviewParseError | None:
        return self.write_failure.parse_error if self.write_failure else None

    @property
    def rejection_message(self) -> str | None:
        """Message from whichever stage rejected: validation or overview mutation."""
        if self.validation_message is not None:
            return self.validation_message
        return self.write_failure.message if self.write_failure else None

    @classmethod
    def accepted_no_change(cls, source: Phase, target: Phase) -> "ProcessedOutcome":
        return cls(OutcomeKind.ACCEPTED_NO_CHANGE, source, target)

    @classmethod
    def accepted_transition(cls, source: Phase, target: Phase) -> "ProcessedOutcome":
        return cls(OutcomeKind.ACCEPTED_TRANSITION, source, target)

    @classmethod
    def rejected_validation(
        cls,
        source: Phase | None,
        target: Phase | None,
        reason: RejectionReason,
        message: str,
    ) -> "ProcessedOutcome":
        return cls(
            OutcomeKind.REJECTED_VALIDATION,
            source,
            target,
            rejection_reason=reason,
            validation_message=message,
        )

    @classmethod
    def rejected_mutation(
        cls,
        source: Phase,
        target: Phase,
        failure: OverviewWriteFailure,
    ) -> "ProcessedOutcome":
        return cls(
            OutcomeKind.REJECTED_MUTATION,
            source,
            target,
            write_failure=failure,
        )


def apply_workflow_event(
    session_path: Path,
    signal: Signal,
    source_phase: str | None,
    source_iterations: int,
    iteration: int,
    now: datetime | None = None,
) -> ProcessedOutcome:
    """Validate fully, then apply an accepted transition to _overview.md on disk.

    Write failures become explicit rejection; no mutation happens before
    validation passes.
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
        assert (
            result.rejection_reason is not None and result.validation_error is not None
        )
        return ProcessedOutcome.rejected_validation(
            result.source_phase,
            result.target_phase,
            result.rejection_reason,
            result.validation_error,
        )

    # Defense-in-depth against phase smuggling: only continue/done may mutate Phase.
    is_change = (
        signal.status in (SignalStatus.CONTINUE, SignalStatus.DONE)
        and result.source_phase is not None
        and result.target_phase is not None
        and result.target_phase != result.source_phase
    )
    if not is_change:
        assert result.source_phase is not None and result.target_phase is not None
        return ProcessedOutcome.accepted_no_change(
            result.source_phase, result.target_phase
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
        wait_timeout=WORKER_LOCK_WAIT_SECONDS,
    )
    if not write.ok:
        assert write.failure is not None
        return ProcessedOutcome.rejected_mutation(
            result.source_phase,
            result.target_phase,
            write.failure,
        )
    return ProcessedOutcome.accepted_transition(
        result.source_phase,
        result.target_phase,
    )
