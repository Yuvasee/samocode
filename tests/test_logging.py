"""Tests for worker/logging.py - Logging configuration.

This module tests:
- setup_logging creates directory and handlers
- add_session_handler adds session-specific logging
- Handler configuration and formatting
"""

import logging
from pathlib import Path

import pytest

from worker.logging import add_session_handler, setup_logging


class TestSetupLogging:
    """Tests for setup_logging - main logger configuration."""

    def test_creates_log_directory(self, tmp_path: Path) -> None:
        """Creates log directory if it doesn't exist."""
        log_dir = tmp_path / "logs"
        assert not log_dir.exists()

        setup_logging(log_dir)

        assert log_dir.exists()

    def test_creates_log_file(self, tmp_path: Path) -> None:
        """Creates samocode.log file after logging a message."""
        log_dir = tmp_path / "logs"
        # Clear existing handlers to ensure fresh setup
        logger = logging.getLogger("samocode")
        logger.handlers.clear()

        logger = setup_logging(log_dir)
        logger.info("Test message to create file")
        # Flush all handlers
        for handler in logger.handlers:
            handler.flush()

        assert (log_dir / "samocode.log").exists()

    def test_returns_logger(self, tmp_path: Path) -> None:
        """Returns the samocode logger."""
        logger = setup_logging(tmp_path / "logs")

        assert logger.name == "samocode"
        assert logger.level == logging.INFO

    def test_idempotent_handler_setup(self, tmp_path: Path) -> None:
        """Calling twice doesn't add duplicate handlers."""
        log_dir = tmp_path / "logs"

        logger1 = setup_logging(log_dir)
        handler_count1 = len(logger1.handlers)

        logger2 = setup_logging(log_dir)
        handler_count2 = len(logger2.handlers)

        assert logger1 is logger2
        assert handler_count1 == handler_count2

    def test_has_console_and_file_handlers(self, tmp_path: Path) -> None:
        """Sets up both console (StreamHandler) and file handlers."""
        # Clear any existing handlers first
        logger = logging.getLogger("samocode")
        logger.handlers.clear()

        logger = setup_logging(tmp_path / "logs")

        handler_types = [type(h).__name__ for h in logger.handlers]
        assert "StreamHandler" in handler_types
        assert "RotatingFileHandler" in handler_types


class TestAddSessionHandler:
    """Tests for add_session_handler - session-specific logging."""

    def test_raises_if_session_not_exists(self, tmp_path: Path) -> None:
        """Raises ValueError if session path doesn't exist."""
        logger = logging.getLogger("test_session")
        nonexistent = tmp_path / "nonexistent"

        with pytest.raises(ValueError, match="does not exist"):
            add_session_handler(logger, nonexistent)

    def test_creates_session_log_file(self, tmp_path: Path) -> None:
        """Creates session.log in session directory."""
        session = tmp_path / "test-session"
        session.mkdir()
        logger = logging.getLogger("test_session2")

        add_session_handler(logger, session)

        assert (session / "session.log").exists()

    def test_returns_handler(self, tmp_path: Path) -> None:
        """Returns the created FileHandler for later removal."""
        session = tmp_path / "test-session"
        session.mkdir()
        logger = logging.getLogger("test_session3")

        handler = add_session_handler(logger, session)

        assert isinstance(handler, logging.FileHandler)
        assert handler in logger.handlers

    def test_handler_includes_session_name(self, tmp_path: Path) -> None:
        """Handler formatter includes session name."""
        session = tmp_path / "my-feature-session"
        session.mkdir()
        logger = logging.getLogger("test_session4")

        handler = add_session_handler(logger, session)
        formatter = handler.formatter

        # Formatter should include session name in format string
        assert formatter is not None
        assert formatter._fmt is not None
        assert "my-feature-session" in formatter._fmt

    def test_logs_to_session_file(self, tmp_path: Path) -> None:
        """Messages are written to session log file."""
        session = tmp_path / "test-session"
        session.mkdir()
        logger = logging.getLogger("test_session5")
        logger.setLevel(logging.INFO)

        handler = add_session_handler(logger, session)
        logger.info("Test message")
        handler.flush()

        log_content = (session / "session.log").read_text()
        assert "Test message" in log_content
