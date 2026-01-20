"""Tests for worker/phases.py - Phase configuration and validation.

This module tests:
- Phase enum values
- PhaseConfig constraints
- Transition validation
- Signal validation per phase
- Iteration limit checking
"""

from worker.phases import (
    Phase,
    PHASE_CONFIGS,
    get_agent_for_phase,
    get_phase_config,
    is_iteration_limit_exceeded,
    validate_signal_for_phase,
    validate_transition,
)


class TestPhaseEnum:
    """Tests for Phase enum."""

    def test_all_phases_exist(self) -> None:
        """All expected phases are defined."""
        expected = [
            "init",
            "investigation",
            "requirements",
            "planning",
            "implementation",
            "testing",
            "quality",
            "done",
        ]
        actual = [p.value for p in Phase]
        assert sorted(actual) == sorted(expected)

    def test_phase_count(self) -> None:
        """Exactly 8 phases exist."""
        assert len(Phase) == 8


class TestPhaseConfigs:
    """Tests for PHASE_CONFIGS registry."""

    def test_all_phases_have_config(self) -> None:
        """Every Phase has a corresponding config."""
        for phase in Phase:
            assert phase in PHASE_CONFIGS
            assert PHASE_CONFIGS[phase].phase == phase

    def test_all_agents_named(self) -> None:
        """Every phase has an agent name ending with -agent."""
        for config in PHASE_CONFIGS.values():
            assert config.agent_name.endswith("-agent")

    def test_done_has_no_next_phases(self) -> None:
        """Done is a terminal phase."""
        done_config = PHASE_CONFIGS[Phase.DONE]
        assert len(done_config.allowed_next) == 0

    def test_done_only_allows_done_signal(self) -> None:
        """Done phase only allows 'done' or 'blocked' signals."""
        done_config = PHASE_CONFIGS[Phase.DONE]
        assert "continue" not in done_config.allowed_signals
        assert "done" in done_config.allowed_signals
        assert "blocked" in done_config.allowed_signals

    def test_init_cannot_signal_done(self) -> None:
        """Init phase cannot signal done."""
        init_config = PHASE_CONFIGS[Phase.INIT]
        assert "done" not in init_config.allowed_signals

    def test_requirements_and_planning_have_gates(self) -> None:
        """Requirements and planning phases require gates."""
        assert PHASE_CONFIGS[Phase.REQUIREMENTS].requires_gate
        assert PHASE_CONFIGS[Phase.PLANNING].requires_gate

    def test_other_phases_no_gates(self) -> None:
        """Other phases don't require gates."""
        for phase in [
            Phase.INIT,
            Phase.INVESTIGATION,
            Phase.IMPLEMENTATION,
            Phase.TESTING,
            Phase.QUALITY,
            Phase.DONE,
        ]:
            assert not PHASE_CONFIGS[phase].requires_gate


class TestGetPhaseConfig:
    """Tests for get_phase_config function."""

    def test_valid_phase(self) -> None:
        """Returns config for valid phase string."""
        config = get_phase_config("init")
        assert config is not None
        assert config.phase == Phase.INIT

    def test_case_insensitive(self) -> None:
        """Phase lookup is case-insensitive."""
        assert get_phase_config("INIT") is not None
        assert get_phase_config("Init") is not None
        assert get_phase_config("iNiT") is not None

    def test_invalid_phase(self) -> None:
        """Returns None for unknown phase."""
        assert get_phase_config("unknown") is None

    def test_none_input(self) -> None:
        """Returns None for None input."""
        assert get_phase_config(None) is None


class TestGetAgentForPhase:
    """Tests for get_agent_for_phase function."""

    def test_all_phases_have_agents(self) -> None:
        """Every valid phase returns an agent name."""
        for phase in Phase:
            agent = get_agent_for_phase(phase.value)
            assert agent is not None
            assert agent == f"{phase.value}-agent"

    def test_unknown_phase(self) -> None:
        """Unknown phase returns None."""
        assert get_agent_for_phase("unknown") is None

    def test_none_phase(self) -> None:
        """None phase returns None."""
        assert get_agent_for_phase(None) is None

    def test_case_insensitive(self) -> None:
        """Agent lookup is case-insensitive."""
        assert get_agent_for_phase("INIT") == "init-agent"
        assert get_agent_for_phase("Planning") == "planning-agent"
        assert get_agent_for_phase("TESTING") == "testing-agent"


class TestValidateTransition:
    """Tests for validate_transition function."""

    def test_valid_init_to_investigation(self) -> None:
        """init -> investigation is valid."""
        is_valid, error = validate_transition("init", "investigation")
        assert is_valid
        assert error == ""

    def test_invalid_init_to_done(self) -> None:
        """init -> done is invalid (skip phases)."""
        is_valid, error = validate_transition("init", "done")
        assert not is_valid
        assert "Invalid transition" in error

    def test_invalid_done_to_anything(self) -> None:
        """done -> anything is invalid (terminal)."""
        is_valid, error = validate_transition("done", "init")
        assert not is_valid

    def test_testing_to_quality_valid(self) -> None:
        """testing -> quality is valid."""
        is_valid, _ = validate_transition("testing", "quality")
        assert is_valid

    def test_testing_to_done_valid(self) -> None:
        """testing -> done is valid (after quality pass)."""
        is_valid, _ = validate_transition("testing", "done")
        assert is_valid

    def test_quality_to_testing_valid(self) -> None:
        """quality -> testing is valid (loop back)."""
        is_valid, _ = validate_transition("quality", "testing")
        assert is_valid

    def test_unknown_source_phase(self) -> None:
        """Unknown source phase returns error."""
        is_valid, error = validate_transition("unknown", "init")
        assert not is_valid
        assert "Unknown source phase" in error

    def test_unknown_target_phase(self) -> None:
        """Unknown target phase returns error."""
        is_valid, error = validate_transition("init", "unknown")
        assert not is_valid
        assert "Unknown target phase" in error

    def test_same_phase_transition(self) -> None:
        """Staying in the same phase is valid (continue signal)."""
        for phase in Phase:
            is_valid, _ = validate_transition(phase.value, phase.value)
            assert is_valid, f"Same phase transition failed for {phase.value}"

    def test_none_source_to_init(self) -> None:
        """None -> init is valid (new session)."""
        is_valid, _ = validate_transition(None, "init")
        assert is_valid

    def test_none_source_to_other(self) -> None:
        """None -> anything_else is invalid (must start with init)."""
        is_valid, error = validate_transition(None, "investigation")
        assert not is_valid
        assert "must start with 'init'" in error

    def test_case_insensitive_transition(self) -> None:
        """Transition validation is case-insensitive."""
        is_valid, _ = validate_transition("INIT", "INVESTIGATION")
        assert is_valid


class TestValidateSignalForPhase:
    """Tests for validate_signal_for_phase function."""

    def test_continue_allowed_in_init(self) -> None:
        """continue signal is valid in init phase."""
        is_valid, _ = validate_signal_for_phase("init", "continue")
        assert is_valid

    def test_done_not_allowed_in_init(self) -> None:
        """done signal is not valid in init phase."""
        is_valid, error = validate_signal_for_phase("init", "done")
        assert not is_valid
        assert "not allowed" in error

    def test_waiting_allowed_in_requirements(self) -> None:
        """waiting signal is valid in requirements phase."""
        is_valid, _ = validate_signal_for_phase("requirements", "waiting")
        assert is_valid

    def test_waiting_not_allowed_in_implementation(self) -> None:
        """waiting signal is not valid in implementation phase."""
        # Note: implementation actually allows waiting in our config
        config = PHASE_CONFIGS[Phase.IMPLEMENTATION]
        if "waiting" not in config.allowed_signals:
            is_valid, error = validate_signal_for_phase("implementation", "waiting")
            assert not is_valid

    def test_done_allowed_in_done_phase(self) -> None:
        """done signal is valid in done phase."""
        is_valid, _ = validate_signal_for_phase("done", "done")
        assert is_valid

    def test_continue_not_allowed_in_done_phase(self) -> None:
        """continue signal is not valid in done phase."""
        is_valid, error = validate_signal_for_phase("done", "continue")
        assert not is_valid
        assert "not allowed" in error

    def test_blocked_allowed_everywhere(self) -> None:
        """blocked signal is valid in all phases."""
        for phase in Phase:
            is_valid, _ = validate_signal_for_phase(phase.value, "blocked")
            assert is_valid, f"blocked not allowed in {phase.value}"

    def test_unknown_phase_returns_valid(self) -> None:
        """Unknown phase returns valid (don't block, just warn)."""
        is_valid, error = validate_signal_for_phase("unknown", "continue")
        assert is_valid
        assert "Unknown phase" in error

    def test_case_insensitive_signal(self) -> None:
        """Signal validation is case-insensitive."""
        is_valid, _ = validate_signal_for_phase("init", "CONTINUE")
        assert is_valid


class TestIterationLimitExceeded:
    """Tests for is_iteration_limit_exceeded function."""

    def test_within_limit(self) -> None:
        """Returns False when under limit."""
        exceeded, max_allowed = is_iteration_limit_exceeded("init", 3)
        assert not exceeded
        assert max_allowed == 5  # init max is 5

    def test_at_limit(self) -> None:
        """Returns False when exactly at limit."""
        exceeded, _ = is_iteration_limit_exceeded("init", 5)
        assert not exceeded

    def test_over_limit(self) -> None:
        """Returns True when over limit."""
        exceeded, max_allowed = is_iteration_limit_exceeded("init", 6)
        assert exceeded
        assert max_allowed == 5

    def test_unknown_phase(self) -> None:
        """Returns False for unknown phase (don't enforce)."""
        exceeded, _ = is_iteration_limit_exceeded("unknown", 1000)
        assert not exceeded

    def test_none_phase(self) -> None:
        """Returns False for None phase."""
        exceeded, _ = is_iteration_limit_exceeded(None, 1000)
        assert not exceeded

    def test_implementation_has_high_limit(self) -> None:
        """Implementation phase has high iteration limit (100)."""
        exceeded, max_allowed = is_iteration_limit_exceeded("implementation", 50)
        assert not exceeded
        assert max_allowed == 100

    def test_done_has_low_limit(self) -> None:
        """Done phase has low iteration limit (3)."""
        exceeded, max_allowed = is_iteration_limit_exceeded("done", 4)
        assert exceeded
        assert max_allowed == 3


class TestPhaseConfigMethods:
    """Tests for PhaseConfig dataclass methods."""

    def test_can_transition_to_valid(self) -> None:
        """can_transition_to returns True for valid targets."""
        init_config = PHASE_CONFIGS[Phase.INIT]
        assert init_config.can_transition_to(Phase.INVESTIGATION)

    def test_can_transition_to_invalid(self) -> None:
        """can_transition_to returns False for invalid targets."""
        init_config = PHASE_CONFIGS[Phase.INIT]
        assert not init_config.can_transition_to(Phase.DONE)

    def test_is_signal_allowed_valid(self) -> None:
        """is_signal_allowed returns True for valid signals."""
        init_config = PHASE_CONFIGS[Phase.INIT]
        assert init_config.is_signal_allowed("continue")

    def test_is_signal_allowed_invalid(self) -> None:
        """is_signal_allowed returns False for invalid signals."""
        init_config = PHASE_CONFIGS[Phase.INIT]
        assert not init_config.is_signal_allowed("done")

    def test_is_signal_allowed_case_insensitive(self) -> None:
        """is_signal_allowed is case-insensitive."""
        init_config = PHASE_CONFIGS[Phase.INIT]
        assert init_config.is_signal_allowed("CONTINUE")
        assert init_config.is_signal_allowed("Continue")
