"""Tests for session resolution logic in samocode/session.py.

These tests document the business logic for:
1. is_path_based_session - the core decision: path or name?
2. determine_config_hint_dir - where to look for .samocode file
3. resolve_session_path - how --session arg becomes a path
"""

import tempfile
from pathlib import Path
from unittest.mock import patch

from samocode.session import (
    determine_config_hint_dir,
    is_path_based_session,
    resolve_session_path,
)


class TestIsPathBasedSession:
    """The core decision: is the session arg a path or a name?

    This is the single source of truth for path vs name detection.
    Both determine_config_hint_dir and resolve_session_path use this.

    Rule: Contains "/" OR starts with "~" → path-based
    """

    # Path-based cases (returns True)
    def test_absolute_path(self):
        """/home/dev/project/_sessions/task → path-based"""
        assert is_path_based_session("/home/dev/project/_sessions/task") is True

    def test_tilde_path(self):
        """~/project/_sessions/task → path-based"""
        assert is_path_based_session("~/project/_sessions/task") is True

    def test_relative_path_with_slash(self):
        """project/_sessions/task → path-based (has slash)"""
        assert is_path_based_session("project/_sessions/task") is True

    def test_single_slash(self):
        """sessions/task → path-based (even single slash)"""
        assert is_path_based_session("sessions/task") is True

    # Name-based cases (returns False)
    def test_simple_name(self):
        """my-task → name-based"""
        assert is_path_based_session("my-task") is False

    def test_name_with_spaces(self):
        """my cool task → name-based (spaces don't make it a path)"""
        assert is_path_based_session("my cool task") is False

    def test_name_with_dashes(self):
        """my-cool-task → name-based"""
        assert is_path_based_session("my-cool-task") is False

    def test_name_looking_like_date(self):
        """26-01-13-my-task → name-based (no slash)"""
        assert is_path_based_session("26-01-13-my-task") is False


class TestDetermineConfigHintDir:
    """Where to look for .samocode file.

    Uses is_path_based_session() internally, so we only test
    the BEHAVIOR specific to this function (what directory it returns).
    """

    def test_path_based_returns_parent(self):
        """Path-based: returns parent of session path.

        /home/dev/project/_sessions/my-task
        → /home/dev/project/_sessions/
        """
        result = determine_config_hint_dir(
            "/home/dev/project/_sessions/my-task",
            samocode_dir=Path("/unused"),
        )
        assert result == Path("/home/dev/project/_sessions")

    def test_name_based_returns_samocode_dir(self):
        """Name-based: returns samocode's own directory.

        my-task → /home/dev/samocode (the samocode_dir arg)
        """
        samocode_dir = Path("/home/dev/samocode")
        result = determine_config_hint_dir("my-task", samocode_dir)
        assert result == samocode_dir

    def test_tilde_is_expanded(self):
        """Tilde paths are expanded before getting parent."""
        result = determine_config_hint_dir(
            "~/project/_sessions/task",
            samocode_dir=Path("/unused"),
        )
        assert result == Path.home() / "project/_sessions"


class TestResolveSessionPath:
    """How --session arg becomes an actual path.

    Uses is_path_based_session() internally, so we only test
    the BEHAVIOR specific to this function.
    """

    def test_path_based_used_directly(self):
        """Path-based: used as-is after expansion."""
        result, display_name, is_path_based = resolve_session_path(
            "/home/dev/project/_sessions/my-task",
            sessions_dir=Path("/unused"),
        )
        assert result == Path("/home/dev/project/_sessions/my-task")
        assert is_path_based is True

    def test_path_based_display_name_is_parent(self):
        """Path-based display name is parent folder."""
        _, display_name, _ = resolve_session_path(
            "/home/dev/my-project/_sessions/task",
            sessions_dir=Path("/unused"),
        )
        assert display_name == "_sessions"

    def test_name_based_creates_new_with_date_prefix(self):
        """Name-based new session gets date prefix."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sessions_dir = Path(tmpdir)

            with patch("samocode.session.datetime") as mock_dt:
                mock_dt.now.return_value.strftime.return_value = "26-01-13"
                result, display_name, is_path_based = resolve_session_path(
                    "my-task", sessions_dir
                )

            assert result == sessions_dir / "26-01-13-my-task"
            assert display_name == "26-01-13-my-task"
            assert is_path_based is False

    def test_name_based_finds_existing(self):
        """Name-based finds existing session by suffix."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sessions_dir = Path(tmpdir)
            existing = sessions_dir / "26-01-10-my-task"
            existing.mkdir()

            result, _, _ = resolve_session_path("my-task", sessions_dir)

            assert result == existing

    def test_name_based_picks_most_recent(self):
        """Multiple matches: picks most recent (sorted last)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sessions_dir = Path(tmpdir)
            (sessions_dir / "26-01-05-my-task").mkdir()
            (sessions_dir / "26-01-10-my-task").mkdir()
            (sessions_dir / "26-01-08-my-task").mkdir()

            result, _, _ = resolve_session_path("my-task", sessions_dir)

            assert result == sessions_dir / "26-01-10-my-task"

    def test_name_normalized_lowercase_and_dashes(self):
        """Names are lowercased, spaces become dashes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("samocode.session.datetime") as mock_dt:
                mock_dt.now.return_value.strftime.return_value = "26-01-13"
                result, _, _ = resolve_session_path("My Cool Task", Path(tmpdir))

            assert result.name == "26-01-13-my-cool-task"
