"""Phase configuration and transition rules for Samocode workflows.

This module is the single source of truth for:
- Phase sequence and valid transitions
- Per-phase constraints (iteration limits, allowed signals)
- Phase gates (conditions required to enter a phase)
"""

from dataclasses import dataclass
from enum import Enum


class Phase(Enum):
    """All valid workflow phases."""

    INIT = "init"
    INVESTIGATION = "investigation"
    REQUIREMENTS = "requirements"
    PLANNING = "planning"
    IMPLEMENTATION = "implementation"
    TESTING = "testing"
    QUALITY = "quality"
    DONE = "done"


@dataclass(frozen=True)
class PhaseConfig:
    """Configuration for a single phase."""

    phase: Phase
    agent_name: str
    allowed_next: frozenset[Phase]
    allowed_signals: frozenset[str]  # SignalStatus values
    max_iterations: int
    requires_gate: bool = False  # True if phase requires explicit gate check

    def can_transition_to(self, target: Phase) -> bool:
        """Check if transition to target phase is valid."""
        return target in self.allowed_next

    def is_signal_allowed(self, signal_status: str) -> bool:
        """Check if signal status is valid for this phase."""
        return signal_status.lower() in self.allowed_signals


# Phase configuration registry - single source of truth
PHASE_CONFIGS: dict[Phase, PhaseConfig] = {
    Phase.INIT: PhaseConfig(
        phase=Phase.INIT,
        agent_name="init-agent",
        allowed_next=frozenset({Phase.INVESTIGATION}),
        allowed_signals=frozenset({"continue", "blocked"}),
        max_iterations=5,
    ),
    Phase.INVESTIGATION: PhaseConfig(
        phase=Phase.INVESTIGATION,
        agent_name="investigation-agent",
        allowed_next=frozenset({Phase.REQUIREMENTS}),
        allowed_signals=frozenset({"continue", "blocked"}),
        max_iterations=20,
    ),
    Phase.REQUIREMENTS: PhaseConfig(
        phase=Phase.REQUIREMENTS,
        agent_name="requirements-agent",
        allowed_next=frozenset({Phase.PLANNING}),
        allowed_signals=frozenset({"continue", "waiting", "blocked"}),
        max_iterations=10,
        requires_gate=True,  # Requires Q&A answers
    ),
    Phase.PLANNING: PhaseConfig(
        phase=Phase.PLANNING,
        agent_name="planning-agent",
        allowed_next=frozenset({Phase.IMPLEMENTATION}),
        allowed_signals=frozenset({"continue", "waiting", "blocked"}),
        max_iterations=10,
        requires_gate=True,  # Requires human approval
    ),
    Phase.IMPLEMENTATION: PhaseConfig(
        phase=Phase.IMPLEMENTATION,
        agent_name="implementation-agent",
        allowed_next=frozenset({Phase.TESTING}),
        allowed_signals=frozenset({"continue", "waiting", "blocked"}),
        max_iterations=100,
    ),
    Phase.TESTING: PhaseConfig(
        phase=Phase.TESTING,
        agent_name="testing-agent",
        # Testing can go to quality (first pass) or done (after quality)
        allowed_next=frozenset({Phase.QUALITY, Phase.DONE}),
        allowed_signals=frozenset({"continue", "blocked"}),
        max_iterations=20,
    ),
    Phase.QUALITY: PhaseConfig(
        phase=Phase.QUALITY,
        agent_name="quality-agent",
        allowed_next=frozenset({Phase.TESTING}),  # Back to testing after fixes
        allowed_signals=frozenset({"continue", "blocked"}),
        max_iterations=10,
    ),
    Phase.DONE: PhaseConfig(
        phase=Phase.DONE,
        agent_name="done-agent",
        allowed_next=frozenset(),  # Terminal phase
        allowed_signals=frozenset({"done", "blocked"}),
        max_iterations=3,
    ),
}


def get_phase_config(phase_str: str | None) -> PhaseConfig | None:
    """Get phase configuration by phase name string."""
    if phase_str is None:
        return None
    try:
        phase = Phase(phase_str.lower())
        return PHASE_CONFIGS.get(phase)
    except ValueError:
        return None


def get_agent_for_phase(phase_str: str | None) -> str | None:
    """Get agent name for a phase. Returns None for unknown phases."""
    config = get_phase_config(phase_str)
    return config.agent_name if config else None


def validate_transition(from_phase: str | None, to_phase: str | None) -> tuple[bool, str]:
    """Validate a phase transition.

    Returns (is_valid, error_message).
    """
    if to_phase is None:
        return False, f"Invalid phases: from={from_phase}, to={to_phase}"

    # Allow starting from None (new session)
    if from_phase is None:
        # Only init is valid for new sessions
        if to_phase.lower() == "init":
            return True, ""
        return False, f"New session must start with 'init', got '{to_phase}'"

    from_config = get_phase_config(from_phase)
    if from_config is None:
        return False, f"Unknown source phase: {from_phase}"

    try:
        to_phase_enum = Phase(to_phase.lower())
    except ValueError:
        return False, f"Unknown target phase: {to_phase}"

    # Allow staying in same phase (continue signal)
    if from_phase.lower() == to_phase.lower():
        return True, ""

    if not from_config.can_transition_to(to_phase_enum):
        valid_targets = [p.value for p in from_config.allowed_next]
        return False, (
            f"Invalid transition: {from_phase} -> {to_phase}. "
            f"Valid targets: {valid_targets}"
        )

    return True, ""


def validate_signal_for_phase(phase_str: str | None, signal_status: str) -> tuple[bool, str]:
    """Validate that a signal status is allowed for a phase.

    Returns (is_valid, error_message).
    """
    config = get_phase_config(phase_str)
    if config is None:
        # Unknown phase - don't block, but warn
        return True, f"Unknown phase: {phase_str}"

    if not config.is_signal_allowed(signal_status):
        return False, (
            f"Signal '{signal_status}' not allowed in phase '{phase_str}'. "
            f"Allowed: {sorted(config.allowed_signals)}"
        )

    return True, ""


def is_iteration_limit_exceeded(phase_str: str | None, iteration_count: int) -> tuple[bool, int]:
    """Check if phase iteration limit is exceeded.

    Returns (is_exceeded, max_allowed).
    """
    config = get_phase_config(phase_str)
    if config is None:
        return False, 0  # Unknown phase - don't enforce limit

    return iteration_count > config.max_iterations, config.max_iterations
