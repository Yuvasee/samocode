"""Tests for worker/signals.py - Signal file operations.

This module tests:
- SignalStatus enum values
- Signal dataclass serialization
- Signal file creation and reading
- Error handling for invalid/missing signals
"""

import json
from pathlib import Path


from worker.signals import (
    Signal,
    SignalStatus,
    clear_signal_file,
    read_signal_file,
)


class TestSignalStatus:
    """Tests for SignalStatus enum."""

    def test_all_status_values(self) -> None:
        """All expected status values exist."""
        assert SignalStatus.CONTINUE.value == "continue"
        assert SignalStatus.DONE.value == "done"
        assert SignalStatus.BLOCKED.value == "blocked"
        assert SignalStatus.WAITING.value == "waiting"

    def test_status_count(self) -> None:
        """Exactly 4 status values exist."""
        assert len(SignalStatus) == 4


class TestSignalToDict:
    """Tests for Signal.to_dict serialization."""

    def test_minimal_signal(self) -> None:
        """Signal with only status serializes correctly."""
        signal = Signal(status=SignalStatus.CONTINUE)

        result = signal.to_dict()

        assert result["status"] == "continue"
        assert result["summary"] is None
        assert result["reason"] is None
        assert result["needs"] is None
        assert result["for"] is None
        assert result["phase"] is None

    def test_full_signal(self) -> None:
        """Signal with all fields serializes correctly."""
        signal = Signal(
            status=SignalStatus.DONE,
            summary="Task completed",
            reason="All tests pass",
            needs="human_review",
            waiting_for="approval",
            phase="testing",
        )

        result = signal.to_dict()

        assert result["status"] == "done"
        assert result["summary"] == "Task completed"
        assert result["reason"] == "All tests pass"
        assert result["needs"] == "human_review"
        assert result["for"] == "approval"
        assert result["phase"] == "testing"

    def test_blocked_signal(self) -> None:
        """Blocked signal with reason and needs."""
        signal = Signal(
            status=SignalStatus.BLOCKED,
            reason="Test failure",
            needs="error_resolution",
            phase="testing",
        )

        result = signal.to_dict()

        assert result["status"] == "blocked"
        assert result["reason"] == "Test failure"
        assert result["needs"] == "error_resolution"


class TestClearSignalFile:
    """Tests for clear_signal_file - creating empty signal."""

    def test_creates_empty_signal(self, tmp_path: Path) -> None:
        """Creates _signal.json with empty object, returns None."""
        session = tmp_path / "session"
        session.mkdir()

        result = clear_signal_file(session)

        signal_file = session / "_signal.json"
        assert signal_file.exists()
        assert json.loads(signal_file.read_text()) == {}
        assert result is None

    def test_creates_directory_if_needed(self, tmp_path: Path) -> None:
        """Creates session directory if it doesn't exist, returns None."""
        session = tmp_path / "new-session"

        result = clear_signal_file(session)

        assert session.exists()
        assert (session / "_signal.json").exists()
        assert result is None

    def test_overwrites_existing_signal(self, tmp_path: Path) -> None:
        """Overwrites existing signal file, returns previous contents."""
        session = tmp_path / "session"
        session.mkdir()
        signal_file = session / "_signal.json"
        signal_file.write_text('{"status": "done", "summary": "old"}')

        result = clear_signal_file(session)

        assert json.loads(signal_file.read_text()) == {}
        assert result == '{"status": "done", "summary": "old"}'

    def test_returns_none_for_empty_signal(self, tmp_path: Path) -> None:
        """Returns None when previous signal was empty object."""
        session = tmp_path / "session"
        session.mkdir()
        signal_file = session / "_signal.json"
        signal_file.write_text("{}")

        result = clear_signal_file(session)

        assert result is None


class TestReadSignalFile:
    """Tests for read_signal_file - parsing signal files."""

    def test_file_not_exists(self, tmp_path: Path) -> None:
        """Returns BLOCKED when signal file doesn't exist."""
        session = tmp_path / "session"
        session.mkdir()

        signal = read_signal_file(session)

        assert signal.status == SignalStatus.BLOCKED
        assert signal.reason is not None
        assert "not created" in signal.reason

    def test_empty_object_returns_continue(self, tmp_path: Path) -> None:
        """Empty {} signal is interpreted as CONTINUE."""
        session = tmp_path / "session"
        session.mkdir()
        (session / "_signal.json").write_text("{}")

        signal = read_signal_file(session)

        assert signal.status == SignalStatus.CONTINUE

    def test_parses_continue_signal(self, tmp_path: Path) -> None:
        """Parses CONTINUE signal correctly."""
        session = tmp_path / "session"
        session.mkdir()
        (session / "_signal.json").write_text('{"status": "continue", "phase": "impl"}')

        signal = read_signal_file(session)

        assert signal.status == SignalStatus.CONTINUE
        assert signal.phase == "impl"

    def test_parses_done_signal(self, tmp_path: Path) -> None:
        """Parses DONE signal with summary."""
        session = tmp_path / "session"
        session.mkdir()
        (session / "_signal.json").write_text(
            '{"status": "done", "summary": "All done", "phase": "done"}'
        )

        signal = read_signal_file(session)

        assert signal.status == SignalStatus.DONE
        assert signal.summary == "All done"
        assert signal.phase == "done"

    def test_parses_blocked_signal(self, tmp_path: Path) -> None:
        """Parses BLOCKED signal with reason and needs."""
        session = tmp_path / "session"
        session.mkdir()
        (session / "_signal.json").write_text(
            '{"status": "blocked", "reason": "Error", "needs": "help"}'
        )

        signal = read_signal_file(session)

        assert signal.status == SignalStatus.BLOCKED
        assert signal.reason == "Error"
        assert signal.needs == "help"

    def test_parses_waiting_signal(self, tmp_path: Path) -> None:
        """Parses WAITING signal with for field."""
        session = tmp_path / "session"
        session.mkdir()
        (session / "_signal.json").write_text(
            '{"status": "waiting", "for": "qa_answers"}'
        )

        signal = read_signal_file(session)

        assert signal.status == SignalStatus.WAITING
        assert signal.waiting_for == "qa_answers"

    def test_invalid_status_returns_blocked(self, tmp_path: Path) -> None:
        """Returns BLOCKED for invalid status string."""
        session = tmp_path / "session"
        session.mkdir()
        (session / "_signal.json").write_text('{"status": "invalid_status"}')

        signal = read_signal_file(session)

        assert signal.status == SignalStatus.BLOCKED
        assert signal.reason is not None
        assert "Invalid signal status" in signal.reason

    def test_invalid_json_returns_blocked(self, tmp_path: Path) -> None:
        """Returns BLOCKED for malformed JSON."""
        session = tmp_path / "session"
        session.mkdir()
        (session / "_signal.json").write_text("not valid json")

        signal = read_signal_file(session)

        assert signal.status == SignalStatus.BLOCKED
        assert signal.reason is not None
        assert "Invalid signal JSON" in signal.reason

    def test_case_insensitive_status(self, tmp_path: Path) -> None:
        """Status is case-insensitive."""
        session = tmp_path / "session"
        session.mkdir()
        (session / "_signal.json").write_text('{"status": "DONE", "summary": "ok"}')

        signal = read_signal_file(session)

        assert signal.status == SignalStatus.DONE
