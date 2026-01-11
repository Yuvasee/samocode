#!/usr/bin/env python3
"""Tests for Adaptive State Graph implementation."""

import json
from pathlib import Path

import pytest

from signals import Signal, SignalStatus, read_signal_file
from worker import (
    get_default_transition,
    get_initial_state,
    get_model_for_state,
    is_valid_state,
    load_state_graph,
)


# =============================================================================
# Test Signal Parsing
# =============================================================================


class TestSignalParsing:
    """Tests for signal parsing with next_state and context fields."""

    def test_signal_with_next_state(self, tmp_path: Path) -> None:
        """Signal with next_state field should parse correctly."""
        signal_file = tmp_path / "_signal.json"
        signal_file.write_text(
            json.dumps({"status": "continue", "next_state": "requirements"})
        )

        signal = read_signal_file(tmp_path)

        assert signal.status == SignalStatus.CONTINUE
        assert signal.next_state == "requirements"

    def test_signal_with_context(self, tmp_path: Path) -> None:
        """Signal with context field should parse correctly."""
        signal_file = tmp_path / "_signal.json"
        signal_file.write_text(
            json.dumps(
                {
                    "status": "continue",
                    "context": {"complexity": 2, "tests_pass": True},
                }
            )
        )

        signal = read_signal_file(tmp_path)

        assert signal.status == SignalStatus.CONTINUE
        assert signal.context is not None
        assert signal.context["complexity"] == 2
        assert signal.context["tests_pass"] is True

    def test_signal_with_both_fields(self, tmp_path: Path) -> None:
        """Signal with both next_state and context should parse correctly."""
        signal_file = tmp_path / "_signal.json"
        signal_file.write_text(
            json.dumps(
                {
                    "status": "continue",
                    "next_state": "implementation_complex",
                    "context": {"complexity": 3, "qa_ready": True},
                }
            )
        )

        signal = read_signal_file(tmp_path)

        assert signal.status == SignalStatus.CONTINUE
        assert signal.next_state == "implementation_complex"
        assert signal.context is not None
        assert signal.context["complexity"] == 3
        assert signal.context["qa_ready"] is True

    def test_empty_signal_returns_blocked(self, tmp_path: Path) -> None:
        """Empty signal {} should return BLOCKED (not CONTINUE) to prevent loops."""
        signal_file = tmp_path / "_signal.json"
        signal_file.write_text("{}")

        signal = read_signal_file(tmp_path)

        assert signal.status == SignalStatus.BLOCKED
        assert signal.reason is not None
        assert "crash" in signal.reason.lower() or "empty" in signal.reason.lower()

    def test_signal_without_new_fields(self, tmp_path: Path) -> None:
        """Signal without new fields should parse correctly (backward compat)."""
        signal_file = tmp_path / "_signal.json"
        signal_file.write_text(json.dumps({"status": "done", "summary": "All done"}))

        signal = read_signal_file(tmp_path)

        assert signal.status == SignalStatus.DONE
        assert signal.summary == "All done"
        assert signal.next_state is None
        assert signal.context is None

    def test_signal_to_dict_serializes_new_fields(self) -> None:
        """Signal.to_dict() should serialize next_state and context."""
        signal = Signal(
            status=SignalStatus.CONTINUE,
            next_state="testing",
            context={"tests_pass": True},
        )

        data = signal.to_dict()

        assert data["status"] == "continue"
        assert data["next_state"] == "testing"
        assert data["context"] == {"tests_pass": True}


# =============================================================================
# Test Graph Loading
# =============================================================================


class TestGraphLoading:
    """Tests for state graph loading from YAML."""

    def test_load_existing_graph(self) -> None:
        """Should load state_graph.yaml from samocode directory."""
        samocode_dir = Path(__file__).parent
        graph = load_state_graph(samocode_dir)

        assert graph is not None
        assert "states" in graph
        assert "initial_state" in graph
        assert graph["initial_state"] == "investigation"

    def test_load_graph_has_all_expected_states(self) -> None:
        """Graph should have all expected states."""
        samocode_dir = Path(__file__).parent
        graph = load_state_graph(samocode_dir)

        assert graph is not None
        states = graph["states"]

        expected_states = [
            "investigation",
            "quick_fix",
            "requirements",
            "waiting_qa",
            "planning",
            "implementation_simple",
            "implementation_complex",
            "testing",
            "quality",
            "quality_fix",
            "testing_regression",
            "done",
            "blocked",
        ]

        for state in expected_states:
            assert state in states, f"Missing state: {state}"

    def test_load_nonexistent_graph(self, tmp_path: Path) -> None:
        """Should return None for nonexistent graph."""
        # Clear the cache first
        import worker

        worker._state_graph_cache = None

        graph = load_state_graph(tmp_path)

        assert graph is None

        # Restore the cache
        worker._state_graph_cache = None


# =============================================================================
# Test Model Selection
# =============================================================================


class TestModelSelection:
    """Tests for per-state model selection."""

    def test_model_for_investigation(self) -> None:
        """Investigation state should use sonnet."""
        samocode_dir = Path(__file__).parent
        graph = load_state_graph(samocode_dir)

        model = get_model_for_state(graph, "investigation")

        assert model == "sonnet"

    def test_model_for_quick_fix(self) -> None:
        """Quick_fix state should use haiku."""
        samocode_dir = Path(__file__).parent
        graph = load_state_graph(samocode_dir)

        model = get_model_for_state(graph, "quick_fix")

        assert model == "haiku"

    def test_model_for_implementation_complex(self) -> None:
        """Implementation_complex state should use opus."""
        samocode_dir = Path(__file__).parent
        graph = load_state_graph(samocode_dir)

        model = get_model_for_state(graph, "implementation_complex")

        assert model == "opus"

    def test_model_for_done(self) -> None:
        """Done state should use haiku."""
        samocode_dir = Path(__file__).parent
        graph = load_state_graph(samocode_dir)

        model = get_model_for_state(graph, "done")

        assert model == "haiku"

    def test_model_for_unknown_state(self) -> None:
        """Unknown state should return None (config.claude_model used instead)."""
        samocode_dir = Path(__file__).parent
        graph = load_state_graph(samocode_dir)

        model = get_model_for_state(graph, "nonexistent_state")

        # Unknown state returns None - orchestrator falls back to config.claude_model
        assert model is None

    def test_model_with_none_graph(self) -> None:
        """None graph should return None."""
        model = get_model_for_state(None, "investigation")

        assert model is None


# =============================================================================
# Test State Validation
# =============================================================================


class TestStateValidation:
    """Tests for state validation."""

    def test_valid_state(self) -> None:
        """Known state should be valid."""
        samocode_dir = Path(__file__).parent
        graph = load_state_graph(samocode_dir)

        assert is_valid_state(graph, "investigation") is True
        assert is_valid_state(graph, "requirements") is True
        assert is_valid_state(graph, "implementation_complex") is True
        assert is_valid_state(graph, "done") is True

    def test_invalid_state(self) -> None:
        """Unknown state should be invalid."""
        samocode_dir = Path(__file__).parent
        graph = load_state_graph(samocode_dir)

        assert is_valid_state(graph, "nonexistent") is False
        assert is_valid_state(graph, "typo_state") is False
        assert is_valid_state(graph, "") is False

    def test_validation_with_none_graph(self) -> None:
        """None graph should always return False."""
        assert is_valid_state(None, "investigation") is False


# =============================================================================
# Test Fallback Transitions
# =============================================================================


class TestFallbackTransitions:
    """Tests for fallback transition behavior."""

    def test_default_transition_for_investigation(self) -> None:
        """Investigation should fallback to requirements."""
        samocode_dir = Path(__file__).parent
        graph = load_state_graph(samocode_dir)

        default = get_default_transition(graph, "investigation")

        # The default transition is "requirements" (condition: default)
        assert default == "requirements"

    def test_default_transition_for_quick_fix(self) -> None:
        """Quick_fix should fallback to testing."""
        samocode_dir = Path(__file__).parent
        graph = load_state_graph(samocode_dir)

        default = get_default_transition(graph, "quick_fix")

        assert default == "testing"

    def test_default_transition_for_done(self) -> None:
        """Done has no transitions, should return None."""
        samocode_dir = Path(__file__).parent
        graph = load_state_graph(samocode_dir)

        default = get_default_transition(graph, "done")

        assert default is None

    def test_default_transition_for_unknown_state(self) -> None:
        """Unknown state should return None."""
        samocode_dir = Path(__file__).parent
        graph = load_state_graph(samocode_dir)

        default = get_default_transition(graph, "nonexistent")

        assert default is None

    def test_default_transition_with_none_graph(self) -> None:
        """None graph should return None."""
        default = get_default_transition(None, "investigation")

        assert default is None


# =============================================================================
# Test Initial State
# =============================================================================


class TestInitialState:
    """Tests for initial state retrieval."""

    def test_initial_state_from_graph(self) -> None:
        """Should return initial_state from graph."""
        samocode_dir = Path(__file__).parent
        graph = load_state_graph(samocode_dir)

        initial = get_initial_state(graph)

        assert initial == "investigation"

    def test_initial_state_without_graph(self) -> None:
        """Should default to 'investigation' when graph is None."""
        initial = get_initial_state(None)

        assert initial == "investigation"


# =============================================================================
# Run Tests
# =============================================================================


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
