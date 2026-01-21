"""Tests for worker/timestamps.py - Timestamp formatting functions.

This module tests:
- file_timestamp format
- log_timestamp format
- full_timestamp format
- folder_timestamp format
- jsonl_timestamp format
- iteration_timestamp format (combined iteration + timestamp)
"""

from datetime import datetime

from worker.timestamps import (
    file_timestamp,
    folder_timestamp,
    full_timestamp,
    iteration_timestamp,
    jsonl_timestamp,
    log_timestamp,
)


class TestFileTimestamp:
    """Tests for file_timestamp - MM-DD-HH:mm format."""

    def test_format(self) -> None:
        """Returns MM-DD-HH:mm format."""
        dt = datetime(2026, 1, 15, 14, 30)
        result = file_timestamp(dt)
        assert result == "01-15-14:30"

    def test_uses_now_if_none(self) -> None:
        """Uses current time if dt is None."""
        result = file_timestamp()
        # Just verify it returns a string in the expected format
        assert len(result) == 11  # MM-DD-HH:mm
        assert result[2] == "-"
        assert result[5] == "-"
        assert result[8] == ":"


class TestLogTimestamp:
    """Tests for log_timestamp - MM-DD HH:MM format."""

    def test_format(self) -> None:
        """Returns MM-DD HH:MM format."""
        dt = datetime(2026, 1, 15, 14, 30)
        result = log_timestamp(dt)
        assert result == "01-15 14:30"

    def test_uses_now_if_none(self) -> None:
        """Uses current time if dt is None."""
        result = log_timestamp()
        assert len(result) == 11  # MM-DD HH:MM
        assert result[2] == "-"
        assert result[5] == " "
        assert result[8] == ":"


class TestFullTimestamp:
    """Tests for full_timestamp - YYYY-MM-DD HH:MM format."""

    def test_format(self) -> None:
        """Returns YYYY-MM-DD HH:MM format."""
        dt = datetime(2026, 1, 15, 14, 30)
        result = full_timestamp(dt)
        assert result == "2026-01-15 14:30"

    def test_uses_now_if_none(self) -> None:
        """Uses current time if dt is None."""
        result = full_timestamp()
        assert len(result) == 16  # YYYY-MM-DD HH:MM
        assert result[4] == "-"
        assert result[7] == "-"
        assert result[10] == " "


class TestFolderTimestamp:
    """Tests for folder_timestamp - YY-MM-DD format."""

    def test_format(self) -> None:
        """Returns YY-MM-DD format."""
        dt = datetime(2026, 1, 15, 14, 30)
        result = folder_timestamp(dt)
        assert result == "26-01-15"

    def test_uses_now_if_none(self) -> None:
        """Uses current time if dt is None."""
        result = folder_timestamp()
        assert len(result) == 8  # YY-MM-DD
        assert result[2] == "-"
        assert result[5] == "-"


class TestJsonlTimestamp:
    """Tests for jsonl_timestamp - MM-DD-HHMM format."""

    def test_format(self) -> None:
        """Returns MM-DD-HHMM format."""
        dt = datetime(2026, 1, 15, 14, 37)
        result = jsonl_timestamp(dt)
        assert result == "01-15-1437"

    def test_uses_now_if_none(self) -> None:
        """Uses current time if dt is None."""
        result = jsonl_timestamp()
        assert len(result) == 10  # MM-DD-HHMM
        assert result[2] == "-"
        assert result[5] == "-"


class TestIterationTimestamp:
    """Tests for iteration_timestamp - [NNN @ MM-DD HH:MM] format."""

    def test_format(self) -> None:
        """Returns [NNN @ MM-DD HH:MM] format."""
        dt = datetime(2026, 1, 15, 14, 30)
        result = iteration_timestamp(1, dt)
        assert result == "[001 @ 01-15 14:30]"

    def test_three_digit_iteration(self) -> None:
        """Iteration is zero-padded to 3 digits."""
        dt = datetime(2026, 1, 15, 14, 30)
        assert iteration_timestamp(5, dt) == "[005 @ 01-15 14:30]"
        assert iteration_timestamp(42, dt) == "[042 @ 01-15 14:30]"
        assert iteration_timestamp(123, dt) == "[123 @ 01-15 14:30]"

    def test_large_iteration(self) -> None:
        """Large iterations don't truncate."""
        dt = datetime(2026, 1, 15, 14, 30)
        result = iteration_timestamp(999, dt)
        assert result == "[999 @ 01-15 14:30]"

    def test_uses_now_if_none(self) -> None:
        """Uses current time if dt is None."""
        result = iteration_timestamp(1)
        # Verify format: [NNN @ MM-DD HH:MM]
        assert result.startswith("[001 @ ")
        assert result.endswith("]")
        assert len(result) == 19  # [NNN @ MM-DD HH:MM]
