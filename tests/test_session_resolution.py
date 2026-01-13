"""Tests for session resolution logic in main.py.

These tests document the business logic for:
1. Config hint directory - where to look for .samocode file
2. Session path resolution - how --session arg becomes a path
3. Session display name - human-readable session identifier
"""

import tempfile
from pathlib import Path
from unittest.mock import patch


# Import the functions we'll extract from main.py
from samocode.session import (
    determine_config_hint_dir,
    resolve_session_path,
)


class TestConfigHintDirectory:
    """Config hint directory determines where to look for .samocode file.

    Business Logic:
    - Path-based sessions (contain "/" or start with "~") → use parent of session path
    - Name-based sessions (just a name) → use samocode's own directory

    Why this matters:
    - .samocode file contains project-specific config (SESSIONS_DIR, WORKTREES_DIR)
    - For path-based sessions, we infer the project from the path
    - For name-based sessions, we rely on environment variables
    """

    def test_path_based_absolute(self):
        """Absolute path: /home/dev/project/_sessions/my-task

        Expected: Parent is /home/dev/project/_sessions/
        This allows finding .samocode in /home/dev/project/
        """
        session_arg = "/home/dev/project/_sessions/my-task"
        samocode_dir = Path("/home/dev/samocode")

        result = determine_config_hint_dir(session_arg, samocode_dir)

        assert result == Path("/home/dev/project/_sessions")

    def test_path_based_with_tilde(self):
        """Tilde path: ~/project/_sessions/my-task

        Expected: Expands ~ and uses parent directory
        """
        session_arg = "~/project/_sessions/my-task"
        samocode_dir = Path("/home/dev/samocode")

        result = determine_config_hint_dir(session_arg, samocode_dir)

        # ~ expands to home directory
        expected_parent = Path.home() / "project/_sessions"
        assert result == expected_parent

    def test_path_based_relative_with_slash(self):
        """Relative path with slash: ./sessions/my-task or sessions/my-task

        Expected: Resolved to absolute, then parent used
        """
        session_arg = "project/_sessions/my-task"
        samocode_dir = Path("/home/dev/samocode")

        result = determine_config_hint_dir(session_arg, samocode_dir)

        # Should resolve relative to cwd and get parent
        expected = (Path.cwd() / "project/_sessions").resolve()
        assert result == expected

    def test_name_based_simple(self):
        """Simple name: my-task

        Expected: Uses samocode's own directory
        This relies on SESSIONS_DIR environment variable
        """
        session_arg = "my-task"
        samocode_dir = Path("/home/dev/samocode")

        result = determine_config_hint_dir(session_arg, samocode_dir)

        assert result == samocode_dir

    def test_name_based_with_spaces(self):
        """Name with spaces (no slash): my cool task

        Expected: Still name-based, uses samocode directory
        """
        session_arg = "my cool task"
        samocode_dir = Path("/home/dev/samocode")

        result = determine_config_hint_dir(session_arg, samocode_dir)

        assert result == samocode_dir

    def test_name_based_with_dashes(self):
        """Name with dashes: my-cool-task

        Expected: Name-based (no slash), uses samocode directory
        """
        session_arg = "my-cool-task"
        samocode_dir = Path("/home/dev/samocode")

        result = determine_config_hint_dir(session_arg, samocode_dir)

        assert result == samocode_dir


class TestSessionPathResolution:
    """Session path resolution converts --session arg to actual path.

    Business Logic:
    - Path-based (contains "/" or starts with "~") → use directly
    - Name-based → look in SESSIONS_DIR with date prefix pattern
      - If existing session matches "*-{name}" → use most recent
      - If no match → create new with "YY-MM-DD-{name}"
    """

    def test_path_based_absolute_returns_directly(self):
        """Absolute path used as-is.

        --session /home/dev/project/_sessions/my-task
        → /home/dev/project/_sessions/my-task
        """
        session_arg = "/home/dev/project/_sessions/my-task"
        sessions_dir = Path("/unused")

        result, display_name, is_path_based = resolve_session_path(
            session_arg, sessions_dir
        )

        assert result == Path("/home/dev/project/_sessions/my-task")
        assert is_path_based is True

    def test_path_based_display_name_is_parent(self):
        """Path-based display name is parent folder name.

        /home/dev/my-project/_sessions/task → "my-project"
        This gives context about which project the session belongs to.
        """
        session_arg = "/home/dev/my-project/_sessions/task"
        sessions_dir = Path("/unused")

        result, display_name, is_path_based = resolve_session_path(
            session_arg, sessions_dir
        )

        assert display_name == "_sessions"  # Parent of session folder

    def test_name_based_creates_new_with_date_prefix(self):
        """New name-based session gets date prefix.

        --session my-task (no existing)
        → {SESSIONS_DIR}/26-01-13-my-task (using today's date)
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            sessions_dir = Path(tmpdir)
            session_arg = "my-task"

            with patch("samocode.session.datetime") as mock_dt:
                mock_dt.now.return_value.strftime.return_value = "26-01-13"

                result, display_name, is_path_based = resolve_session_path(
                    session_arg, sessions_dir
                )

            assert result == sessions_dir / "26-01-13-my-task"
            assert display_name == "26-01-13-my-task"
            assert is_path_based is False

    def test_name_based_finds_existing_session(self):
        """Existing session is found by name suffix.

        --session my-task
        + existing: 26-01-10-my-task/
        → uses existing 26-01-10-my-task/
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            sessions_dir = Path(tmpdir)
            existing = sessions_dir / "26-01-10-my-task"
            existing.mkdir()

            result, display_name, is_path_based = resolve_session_path(
                "my-task", sessions_dir
            )

            assert result == existing
            assert display_name == "26-01-10-my-task"
            assert is_path_based is False

    def test_name_based_picks_most_recent_of_multiple(self):
        """Multiple matches: picks most recent (alphabetically last).

        --session my-task
        + existing: 26-01-05-my-task/, 26-01-10-my-task/, 26-01-08-my-task/
        → uses 26-01-10-my-task/ (sorted last)
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            sessions_dir = Path(tmpdir)
            (sessions_dir / "26-01-05-my-task").mkdir()
            (sessions_dir / "26-01-10-my-task").mkdir()
            (sessions_dir / "26-01-08-my-task").mkdir()

            result, display_name, is_path_based = resolve_session_path(
                "my-task", sessions_dir
            )

            assert result == sessions_dir / "26-01-10-my-task"

    def test_name_based_normalizes_spaces_to_dashes(self):
        """Spaces in name become dashes.

        --session "my cool task" → my-cool-task
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            sessions_dir = Path(tmpdir)

            with patch("samocode.session.datetime") as mock_dt:
                mock_dt.now.return_value.strftime.return_value = "26-01-13"

                result, _, _ = resolve_session_path("my cool task", sessions_dir)

            assert result.name == "26-01-13-my-cool-task"

    def test_name_based_normalizes_to_lowercase(self):
        """Name is lowercased.

        --session "My-Task" → my-task
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            sessions_dir = Path(tmpdir)

            with patch("samocode.session.datetime") as mock_dt:
                mock_dt.now.return_value.strftime.return_value = "26-01-13"

                result, _, _ = resolve_session_path("My-Task", sessions_dir)

            assert result.name == "26-01-13-my-task"

    def test_tilde_expansion(self):
        """Tilde is expanded to home directory.

        --session ~/project/_sessions/task
        → /home/user/project/_sessions/task
        """
        session_arg = "~/project/_sessions/task"
        sessions_dir = Path("/unused")

        result, _, is_path_based = resolve_session_path(session_arg, sessions_dir)

        expected = Path.home() / "project/_sessions/task"
        assert result == expected
        assert is_path_based is True


class TestEdgeCases:
    """Edge cases and potential gotchas."""

    def test_single_slash_is_path_based(self):
        """Even a simple relative path with slash is path-based.

        --session sessions/task → path-based (has slash)
        """
        session_arg = "sessions/task"
        samocode_dir = Path("/home/dev/samocode")

        result = determine_config_hint_dir(session_arg, samocode_dir)

        # Contains slash, so path-based
        assert result != samocode_dir

    def test_name_starting_with_date_doesnt_confuse(self):
        """Session name that looks like date prefix still works.

        --session "26-01-13-my-task" (no slash, just looks like dated name)
        → treated as name, searches for *-26-01-13-my-task
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            sessions_dir = Path(tmpdir)

            with patch("samocode.session.datetime") as mock_dt:
                mock_dt.now.return_value.strftime.return_value = "26-01-15"

                result, _, is_path_based = resolve_session_path(
                    "26-01-13-my-task", sessions_dir
                )

            # Creates new with today's date prefix
            assert result.name == "26-01-15-26-01-13-my-task"
            assert is_path_based is False
