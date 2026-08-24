"""Process-wide lease for one autonomous session."""

from __future__ import annotations

import contextlib
import fcntl
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

ORCHESTRATOR_LEASE_FILENAME = "_orchestrator.lock"


class ProcessLeaseState(Enum):
    ACQUIRED = "acquired"
    CONTENDED = "contended"
    FAILED = "failed"


@dataclass
class ProcessLease:
    state: ProcessLeaseState
    message: str | None = None
    _fd: int | None = None

    def release(self) -> None:
        if self._fd is None:
            return
        with contextlib.suppress(OSError):
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        os.close(self._fd)
        self._fd = None


def acquire_process_lease(session_path: Path) -> ProcessLease:
    """Fail fast when another new worker or recovery owns the session."""
    try:
        session_path.mkdir(parents=True, exist_ok=True)
        fd = os.open(
            str(session_path / ORCHESTRATOR_LEASE_FILENAME),
            os.O_CREAT | os.O_RDWR,
            0o644,
        )
    except OSError as exc:
        return ProcessLease(
            ProcessLeaseState.FAILED, f"Cannot open process lease: {exc}"
        )
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return ProcessLease(
            ProcessLeaseState.CONTENDED,
            "Another Samocode worker or recovery owns this session",
        )
    except OSError as exc:
        os.close(fd)
        return ProcessLease(
            ProcessLeaseState.FAILED, f"Cannot acquire process lease: {exc}"
        )
    return ProcessLease(ProcessLeaseState.ACQUIRED, _fd=fd)
