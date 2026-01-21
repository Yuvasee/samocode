"""Samocode worker package - core orchestrator components."""

from .config import (
    ProjectConfig,
    RuntimeConfig,
    SamocodeConfig,
    parse_samocode_file,
    resolve_session_path,
)
from .logging import add_session_handler, setup_logging
from .notifications import notify_blocked, notify_complete, notify_error, notify_waiting
from .phases import (
    Phase,
    PhaseConfig,
    PHASE_CONFIGS,
    get_agent_for_phase,
    get_phase_config,
    is_iteration_limit_exceeded,
    validate_signal_for_phase,
    validate_transition,
)
from .runner import (
    ExecutionResult,
    ExecutionStatus,
    extract_phase,
    extract_total_iterations,
    increment_total_iterations,
    run_claude_with_retry,
    validate_session_structure,
)
from .signal_history import (
    SignalHistoryEntry,
    get_phase_iteration_count,
    read_signal_history,
    record_signal,
)
from .signals import (
    Signal,
    SignalStatus,
    clear_signal_file,
    read_signal_file,
)
from .timestamps import (
    FILE_TIMESTAMP_PATTERN,
    FOLDER_TIMESTAMP_PATTERN,
    file_timestamp,
    folder_timestamp,
    full_timestamp,
    iteration_timestamp,
    jsonl_timestamp,
    log_timestamp,
)

__all__ = [
    # Config
    "ProjectConfig",
    "RuntimeConfig",
    "SamocodeConfig",
    "parse_samocode_file",
    "resolve_session_path",
    # Logging
    "add_session_handler",
    "setup_logging",
    # Notifications
    "notify_blocked",
    "notify_complete",
    "notify_error",
    "notify_waiting",
    # Phases
    "Phase",
    "PhaseConfig",
    "PHASE_CONFIGS",
    "get_agent_for_phase",
    "get_phase_config",
    "is_iteration_limit_exceeded",
    "validate_signal_for_phase",
    "validate_transition",
    # Runner
    "ExecutionResult",
    "ExecutionStatus",
    "extract_phase",
    "extract_total_iterations",
    "increment_total_iterations",
    "run_claude_with_retry",
    "validate_session_structure",
    # Signal history
    "SignalHistoryEntry",
    "get_phase_iteration_count",
    "read_signal_history",
    "record_signal",
    # Signals
    "Signal",
    "SignalStatus",
    "clear_signal_file",
    "read_signal_file",
    # Timestamps
    "FILE_TIMESTAMP_PATTERN",
    "FOLDER_TIMESTAMP_PATTERN",
    "file_timestamp",
    "folder_timestamp",
    "full_timestamp",
    "iteration_timestamp",
    "jsonl_timestamp",
    "log_timestamp",
]
