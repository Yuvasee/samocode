"""Centralized timestamp formatting for Samocode sessions.

All session files should use these formats for consistency:
- File timestamps: MM-DD-HH:mm (e.g., 01-15-14:30)
- Flow log timestamps: MM-DD HH:MM (e.g., 01-15 14:30)
- Full timestamps: YYYY-MM-DD HH:MM (e.g., 2026-01-15 14:30)
- Folder timestamps: YY-MM-DD (e.g., 26-01-15)
"""

from datetime import datetime


def file_timestamp(dt: datetime | None = None) -> str:
    """Generate timestamp for filenames: MM-DD-HH:mm

    Example: 01-15-14:30
    """
    if dt is None:
        dt = datetime.now()
    return dt.strftime("%m-%d-%H:%M")


def log_timestamp(dt: datetime | None = None) -> str:
    """Generate timestamp for flow logs: MM-DD HH:MM

    Example: 01-15 14:30
    """
    if dt is None:
        dt = datetime.now()
    return dt.strftime("%m-%d %H:%M")


def iteration_timestamp(iteration: int, dt: datetime | None = None) -> str:
    """Generate combined iteration and timestamp: [NNN @ MM-DD HH:MM]

    Example: [001 @ 01-15 14:30]

    Args:
        iteration: Iteration number (1-999). Values outside this range
            will still work but may break 3-digit format expectations.
        dt: Optional datetime, defaults to now()
    """
    if iteration < 1:
        raise ValueError(f"Iteration must be >= 1, got {iteration}")
    if dt is None:
        dt = datetime.now()
    return f"[{iteration:03d} @ {dt.strftime('%m-%d %H:%M')}]"


def full_timestamp(dt: datetime | None = None) -> str:
    """Generate full timestamp for headers: YYYY-MM-DD HH:MM

    Example: 2026-01-15 14:30
    """
    if dt is None:
        dt = datetime.now()
    return dt.strftime("%Y-%m-%d %H:%M")


def folder_timestamp(dt: datetime | None = None) -> str:
    """Generate timestamp for session folders: YY-MM-DD

    Example: 26-01-15
    """
    if dt is None:
        dt = datetime.now()
    return dt.strftime("%y-%m-%d")


def jsonl_timestamp(dt: datetime | None = None) -> str:
    """Generate timestamp for JSONL log files: MM-DD-HHMM

    Example: 01-15-1437
    """
    if dt is None:
        dt = datetime.now()
    return dt.strftime("%m-%d-%H%M")


# Valid timestamp patterns for validation
FILE_TIMESTAMP_PATTERN = r"^\d{2}-\d{2}-\d{2}:\d{2}"  # MM-DD-HH:mm
FOLDER_TIMESTAMP_PATTERN = r"^\d{2}-\d{2}-\d{2}"  # YY-MM-DD
