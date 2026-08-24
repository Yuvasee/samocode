"""Narrow, auditable recovery for legacy final-polish provenance gaps."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

from .config import ProjectConfig, resolve_session_path
from .final_polish import validate_final_polish_evidence
from .lifecycle import (
    RECOVERY_DIRNAME,
    RECOVERY_RECEIPT_FILENAME,
    LifecycleIssueCode,
    latest_applied_recovery_anchor,
    recovery_commit_marker,
    validate_final_polish_lifecycle,
)
from .phases import Phase
from .plan_resolver import PlanResolutionError, resolve_plan_phase
from .process_lease import ProcessLeaseState, acquire_process_lease
from .signal_history import HISTORY_FILENAME, read_history
from .signals import OVERVIEW_FILENAME, SIGNAL_FILENAME, SignalStatus, read_signal_file
from .timestamps import log_timestamp
from .workflow_state import (
    LockState,
    OverviewTransition,
    apply_overview_transition_locked,
    atomic_write_text,
    read_overview_state,
    session_lock,
)


class RecoveryOutcome(Enum):
    RECOVERABLE = "recoverable"
    RECOVERED = "recovered"
    RECOVERED_SIGNAL_RETAINED = "recovered_signal_retained"
    REJECTED = "rejected"


class RecoveryRejection(Enum):
    CONFIG_INVALID = "config_invalid"
    SESSION_NOT_FOUND = "session_not_found"
    OVERVIEW_INVALID = "overview_invalid"
    STATE_NOT_RECOVERABLE = "state_not_recoverable"
    SIGNAL_MISMATCH = "signal_mismatch"
    HISTORY_MISMATCH = "history_mismatch"
    EVIDENCE_INVALID = "evidence_invalid"
    PLAN_INCOMPLETE = "plan_incomplete"
    ALREADY_RECOVERED = "already_recovered"
    PROCESS_CONTENDED = "process_contended"
    LOCK_CONTENDED = "lock_contended"
    LOCK_IO_FAILED = "lock_io_failed"
    BACKUP_FAILED = "backup_failed"
    OVERVIEW_WRITE_FAILED = "overview_write_failed"
    STATE_CHANGED = "state_changed"


@dataclass(frozen=True)
class RecoveryInspection:
    project: ProjectConfig
    session_path: Path
    working_dir: Path
    plan_path: Path
    source_phase: Phase
    overview_bytes: bytes
    signal_bytes: bytes
    history_bytes: bytes
    plan_bytes: bytes
    head: str

    @property
    def state_fingerprint(self) -> RecoveryStateFingerprint:
        return RecoveryStateFingerprint(
            session_path=self.session_path,
            working_dir=self.working_dir,
            plan_path=self.plan_path,
            source_phase=self.source_phase,
            overview_bytes=self.overview_bytes,
            signal_bytes=self.signal_bytes,
            history_bytes=self.history_bytes,
            plan_bytes=self.plan_bytes,
            head=self.head,
        )


@dataclass(frozen=True)
class RecoveryStateFingerprint:
    session_path: Path
    working_dir: Path
    plan_path: Path
    source_phase: Phase
    overview_bytes: bytes
    signal_bytes: bytes
    history_bytes: bytes
    plan_bytes: bytes
    head: str


@dataclass(frozen=True)
class RecoveryResult:
    outcome: RecoveryOutcome
    message: str
    rejection: RecoveryRejection | None = None
    receipt_path: Path | None = None
    inspection: RecoveryInspection | None = None

    @property
    def ok(self) -> bool:
        return self.outcome is not RecoveryOutcome.REJECTED


def inspect_final_polish_recovery(
    config_path: Path, session_name: str
) -> RecoveryResult:
    try:
        project = ProjectConfig.from_file(config_path)
    except ValueError as exc:
        return _rejected(RecoveryRejection.CONFIG_INVALID, f"Config error: {exc}")
    config_errors = project.validate()
    if config_errors:
        return _rejected(
            RecoveryRejection.CONFIG_INVALID,
            "Invalid project config: " + "; ".join(config_errors),
        )
    return _inspect_project(project, session_name)


def recover_final_polish(
    config_path: Path, session_name: str, now: datetime | None = None
) -> RecoveryResult:
    preflight = inspect_final_polish_recovery(config_path, session_name)
    if preflight.inspection is None:
        return preflight
    expected = preflight.inspection

    lease = acquire_process_lease(expected.session_path)
    if lease.state is ProcessLeaseState.CONTENDED:
        return _rejected(
            RecoveryRejection.PROCESS_CONTENDED,
            lease.message or "Another process owns this session",
        )
    if lease.state is ProcessLeaseState.FAILED:
        return _rejected(
            RecoveryRejection.LOCK_IO_FAILED,
            lease.message or "Cannot acquire the process lease",
        )
    try:
        with session_lock(expected.session_path) as lock:
            if lock.state is LockState.CONTENDED:
                return _rejected(
                    RecoveryRejection.LOCK_CONTENDED,
                    "Session state is being written; recovery made no change",
                )
            if lock.state is LockState.FAILED:
                return _rejected(
                    RecoveryRejection.LOCK_IO_FAILED,
                    lock.message or "Cannot acquire the session lock",
                )

            locked = _inspect_project(expected.project, session_name)
            if locked.inspection is None:
                return locked
            authoritative = locked.inspection
            if authoritative.state_fingerprint != expected.state_fingerprint:
                return _rejected(
                    RecoveryRejection.STATE_CHANGED,
                    "Workflow state changed after recovery inspection; run --check again",
                )
            return _apply_recovery(authoritative, now)
    finally:
        lease.release()


def recovery_exit_code(result: RecoveryResult) -> int:
    if result.outcome is RecoveryOutcome.RECOVERED_SIGNAL_RETAINED:
        return 5
    if result.outcome is not RecoveryOutcome.REJECTED:
        return 0
    if result.rejection in {
        RecoveryRejection.PROCESS_CONTENDED,
        RecoveryRejection.LOCK_CONTENDED,
    }:
        return 3
    if result.rejection in {
        RecoveryRejection.LOCK_IO_FAILED,
        RecoveryRejection.BACKUP_FAILED,
        RecoveryRejection.OVERVIEW_WRITE_FAILED,
        RecoveryRejection.STATE_CHANGED,
    }:
        return 4
    return 1


def _inspect_project(project: ProjectConfig, session_name: str) -> RecoveryResult:
    session_path = resolve_session_path(project.sessions, session_name)
    if not session_path.is_dir():
        return _rejected(
            RecoveryRejection.SESSION_NOT_FOUND,
            f"Session directory does not exist: {session_path}",
        )
    parsed = read_overview_state(session_path)
    if parsed.state is None:
        return _rejected(
            RecoveryRejection.OVERVIEW_INVALID,
            parsed.message or "Invalid _overview.md",
        )
    state = parsed.state
    if state.phase is not Phase.PR_READINESS or state.blocked != "workflow_error":
        return _rejected(
            RecoveryRejection.STATE_NOT_RECOVERABLE,
            "Recovery requires Phase: pr-readiness and Blocked: workflow_error",
        )
    if latest_applied_recovery_anchor(session_path) is not None:
        return _rejected(
            RecoveryRejection.ALREADY_RECOVERED,
            "This session already has an applied final-polish recovery",
        )

    try:
        signal_bytes = (session_path / SIGNAL_FILENAME).read_bytes()
        raw_signal = json.loads(signal_bytes)
    except (OSError, json.JSONDecodeError) as exc:
        return _rejected(
            RecoveryRejection.SIGNAL_MISMATCH,
            f"Cannot read the retained rejected signal: {exc}",
        )
    if raw_signal != {"status": "continue", "phase": "done"}:
        return _rejected(
            RecoveryRejection.SIGNAL_MISMATCH,
            "Recovery requires exactly the retained rejected continue -> done signal",
        )
    signal = read_signal_file(session_path)
    if signal.status is not SignalStatus.CONTINUE or signal.phase != Phase.DONE.value:
        return _rejected(
            RecoveryRejection.SIGNAL_MISMATCH,
            "Recovery requires the retained rejected continue -> done signal",
        )

    history = read_history(session_path)
    latest = history[-1] if history else None
    if not (
        latest
        and latest.schema_version == 2
        and latest.source_phase == Phase.PR_READINESS.value
        and latest.target_phase == Phase.DONE.value
        and latest.raw_status == SignalStatus.CONTINUE.value
        and latest.accepted is False
        and latest.mutated is False
        and latest.rejection_reason == "final_polish_invalid"
    ):
        return _rejected(
            RecoveryRejection.HISTORY_MISMATCH,
            "Latest history row is not the rejected pr-readiness -> done final-polish event",
        )

    working_dir = project.worktrees / session_path.name
    if not working_dir.is_dir():
        working_dir = project.main_repo
    evidence = validate_final_polish_evidence(session_path, working_dir)
    if not evidence.ok:
        return _rejected(
            RecoveryRejection.EVIDENCE_INVALID,
            "Non-history final-polish evidence is invalid: " + "; ".join(evidence.errors),
        )
    lifecycle = validate_final_polish_lifecycle(session_path)
    allowed = {
        LifecycleIssueCode.FINAL_POLISH_SEQUENCE_MISSING,
        LifecycleIssueCode.LATEST_TRANSITION_INVALID,
    }
    if (
        LifecycleIssueCode.FINAL_POLISH_SEQUENCE_MISSING not in lifecycle.codes
        or not lifecycle.codes.issubset(allowed)
    ):
        return _rejected(
            RecoveryRejection.HISTORY_MISMATCH,
            "Lifecycle failure is not the supported legacy provenance gap",
        )

    try:
        plan = resolve_plan_phase(session_path)
    except (OSError, UnicodeDecodeError, PlanResolutionError) as exc:
        return _rejected(
            RecoveryRejection.PLAN_INCOMPLETE,
            f"Cannot prove implementation complete: {exc}",
        )
    if not plan.all_complete:
        return _rejected(
            RecoveryRejection.PLAN_INCOMPLETE,
            "Active implementation plan still has unchecked work",
        )

    try:
        overview_bytes = (session_path / OVERVIEW_FILENAME).read_bytes()
        history_bytes = (session_path / HISTORY_FILENAME).read_bytes()
        plan_bytes = plan.plan_path.read_bytes()
    except OSError as exc:
        return _rejected(
            RecoveryRejection.STATE_NOT_RECOVERABLE,
            f"Cannot snapshot workflow control files: {exc}",
        )
    head = _git_output(working_dir, "rev-parse", "HEAD")
    if head is None:
        return _rejected(
            RecoveryRejection.EVIDENCE_INVALID,
            "Cannot resolve the project HEAD for the recovery receipt",
        )
    inspection = RecoveryInspection(
        project=project,
        session_path=session_path,
        working_dir=working_dir,
        plan_path=plan.plan_path,
        source_phase=state.phase,
        overview_bytes=overview_bytes,
        signal_bytes=signal_bytes,
        history_bytes=history_bytes,
        plan_bytes=plan_bytes,
        head=head,
    )
    return RecoveryResult(
        RecoveryOutcome.RECOVERABLE,
        (
            "Recoverable legacy final-polish provenance gap: recovery will preserve "
            "history and reset the completed plan to implementation for a full replay"
        ),
        inspection=inspection,
    )


def _apply_recovery(
    inspection: RecoveryInspection, now: datetime | None
) -> RecoveryResult:
    now = now or datetime.now().astimezone()
    recovery_id = uuid.uuid4().hex[:12]
    dirname = f"{now.strftime('%Y%m%dT%H%M%S')}-final-polish-{recovery_id}"
    recovery_root = inspection.session_path / RECOVERY_DIRNAME
    final_dir = recovery_root / dirname
    receipt = {
        "schema": 1,
        "recovery_id": recovery_id,
        "reason": "legacy_final_polish_history_missing",
        "source_phase": inspection.source_phase.value,
        "target_phase": Phase.IMPLEMENTATION.value,
        "working_dir": str(inspection.working_dir),
        "head": inspection.head,
        "history_rows_before": len(read_history(inspection.session_path)),
        "history_bytes_before": len(inspection.history_bytes),
        "history_sha256_before": _digest(inspection.history_bytes),
        "overview_sha256_before": _digest(inspection.overview_bytes),
        "signal_sha256_before": _digest(inspection.signal_bytes),
        "plan": inspection.plan_path.name,
        "plan_sha256_before": _digest(inspection.plan_bytes),
        "created_at": now.isoformat(),
    }
    temp_dir: Path | None = None
    try:
        recovery_root.mkdir(mode=0o700, exist_ok=True)
        temp_dir = Path(tempfile.mkdtemp(prefix=".pending-", dir=recovery_root))
        _write_sync(temp_dir / "_overview.before.md", inspection.overview_bytes)
        _write_sync(temp_dir / "_signal.before.json", inspection.signal_bytes)
        _write_sync(
            temp_dir / "_signal_history.before.jsonl", inspection.history_bytes
        )
        _write_sync(temp_dir / "plan.before.md", inspection.plan_bytes)
        _write_sync(
            temp_dir / RECOVERY_RECEIPT_FILENAME,
            (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode(),
        )
        os.replace(temp_dir, final_dir)
        _fsync_dir(recovery_root)
    except (OSError, UnicodeError) as exc:
        if temp_dir is not None:
            shutil.rmtree(temp_dir, ignore_errors=True)
        return _rejected(
            RecoveryRejection.BACKUP_FAILED,
            f"Could not create immutable recovery snapshot: {exc}",
        )

    receipt_relative = final_dir.relative_to(inspection.session_path)
    return _commit_recovery_overview(
        inspection, now, recovery_id, final_dir, receipt_relative
    )


def _commit_recovery_overview(
    inspection: RecoveryInspection,
    now: datetime,
    recovery_id: str,
    receipt_dir: Path,
    receipt_relative: Path,
) -> RecoveryResult:
    """The overview marker is the commit point that activates the receipt anchor."""
    transition = OverviewTransition(
        target_phase=Phase.IMPLEMENTATION,
        blocked="no",
        last_action=(
            "Recovered legacy final-polish provenance gap; "
            f"audit receipt {receipt_relative}"
        ),
        next_action="Repeat the outer lifecycle from completed implementation",
        flow_log_entry=(
            f"- [recover @ {log_timestamp(now)}] "
            f"{recovery_commit_marker(recovery_id)} Recovery {recovery_id}: "
            "pr-readiness -> implementation; signal history preserved"
        ),
    )
    write = apply_overview_transition_locked(
        inspection.session_path,
        transition,
        expected_source=inspection.source_phase,
    )
    if not write.ok:
        return _rejected(
            RecoveryRejection.OVERVIEW_WRITE_FAILED,
            write.message or "Recovery overview transition failed",
        )
    try:
        atomic_write_text(inspection.session_path / SIGNAL_FILENAME, "{}")
    except OSError as exc:
        return RecoveryResult(
            RecoveryOutcome.RECOVERED_SIGNAL_RETAINED,
            (
                "Recovery committed, but the stale signal could not be cleared: "
                f"{exc}. The next worker clears it before provider execution."
            ),
            receipt_path=receipt_dir / RECOVERY_RECEIPT_FILENAME,
        )
    return RecoveryResult(
        RecoveryOutcome.RECOVERED,
        "Recovery committed; restart Samocode to replay the full final-polish lifecycle",
        receipt_path=receipt_dir / RECOVERY_RECEIPT_FILENAME,
    )


def _rejected(rejection: RecoveryRejection, message: str) -> RecoveryResult:
    return RecoveryResult(RecoveryOutcome.REJECTED, message, rejection=rejection)


def _write_sync(path: Path, content: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _git_output(working_dir: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(working_dir), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()
