"""Tests for worker/config.py - Configuration management.

This module tests:
- ProjectConfig loading from explicit file
- RuntimeConfig loading from environment
- SamocodeConfig validation and composition
- Session path resolution
- Config file parsing
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from worker.config import (
    ProjectConfig,
    RuntimeConfig,
    SamocodeConfig,
    _parse_config_file,
    parse_samocode_file,
    resolve_session_path,
)


class TestProjectConfigFromFile:
    """Tests for ProjectConfig.from_file - loading project paths."""

    def test_loads_all_required_fields(self, tmp_path: Path) -> None:
        """Successfully loads when all three fields present."""
        repo = tmp_path / "repo"
        worktrees = tmp_path / "worktrees"
        sessions = tmp_path / "sessions"
        repo.mkdir()
        worktrees.mkdir()
        sessions.mkdir()

        config_file = tmp_path / ".samocode"
        config_file.write_text(
            f"MAIN_REPO={repo}\n" f"WORKTREES={worktrees}\n" f"SESSIONS={sessions}\n"
        )

        config = ProjectConfig.from_file(config_file)

        assert config.main_repo == repo
        assert config.worktrees == worktrees
        assert config.sessions == sessions

    def test_raises_when_file_missing(self, tmp_path: Path) -> None:
        """Raises ValueError when config file doesn't exist."""
        missing = tmp_path / "nonexistent"

        with pytest.raises(ValueError, match="not found"):
            ProjectConfig.from_file(missing)

    def test_raises_when_path_is_directory(self, tmp_path: Path) -> None:
        """Raises ValueError when config path is a directory."""
        tmp_path.mkdir(exist_ok=True)

        with pytest.raises(ValueError, match="not a file"):
            ProjectConfig.from_file(tmp_path)

    def test_raises_when_main_repo_missing(self, tmp_path: Path) -> None:
        """Raises ValueError when MAIN_REPO field is missing."""
        config_file = tmp_path / ".samocode"
        config_file.write_text("WORKTREES=/foo\nSESSIONS=/bar\n")

        with pytest.raises(ValueError, match="MAIN_REPO"):
            ProjectConfig.from_file(config_file)

    def test_raises_when_worktrees_missing(self, tmp_path: Path) -> None:
        """Raises ValueError when WORKTREES field is missing."""
        config_file = tmp_path / ".samocode"
        config_file.write_text("MAIN_REPO=/foo\nSESSIONS=/bar\n")

        with pytest.raises(ValueError, match="WORKTREES"):
            ProjectConfig.from_file(config_file)

    def test_raises_when_sessions_missing(self, tmp_path: Path) -> None:
        """Raises ValueError when SESSIONS field is missing."""
        config_file = tmp_path / ".samocode"
        config_file.write_text("MAIN_REPO=/foo\nWORKTREES=/bar\n")

        with pytest.raises(ValueError, match="SESSIONS"):
            ProjectConfig.from_file(config_file)

    def test_expands_tilde_paths(self, tmp_path: Path) -> None:
        """Tilde in paths is expanded to home directory."""
        config_file = tmp_path / ".samocode"
        config_file.write_text(
            "MAIN_REPO=~/repo\n" "WORKTREES=~/worktrees\n" "SESSIONS=~/sessions\n"
        )

        config = ProjectConfig.from_file(config_file)

        home = Path.home()
        assert config.main_repo == home / "repo"
        assert config.worktrees == home / "worktrees"
        assert config.sessions == home / "sessions"


class TestProjectConfigValidate:
    """Tests for ProjectConfig.validate - path validation."""

    def test_valid_paths_return_empty(self, tmp_path: Path) -> None:
        """All paths existing returns no errors."""
        repo = tmp_path / "repo"
        worktrees = tmp_path / "worktrees"
        sessions = tmp_path / "sessions"
        repo.mkdir()
        worktrees.mkdir()
        sessions.mkdir()

        config = ProjectConfig(
            main_repo=repo,
            worktrees=worktrees,
            sessions=sessions,
        )

        assert config.validate() == []

    def test_missing_main_repo_returns_error(self, tmp_path: Path) -> None:
        """Missing MAIN_REPO directory returns appropriate error."""
        worktrees = tmp_path / "worktrees"
        sessions = tmp_path / "sessions"
        worktrees.mkdir()
        sessions.mkdir()

        config = ProjectConfig(
            main_repo=tmp_path / "missing",
            worktrees=worktrees,
            sessions=sessions,
        )

        errors = config.validate()

        assert len(errors) >= 1
        assert any("MAIN_REPO" in e and "does not exist" in e for e in errors)

    def test_missing_worktrees_returns_error(self, tmp_path: Path) -> None:
        """Missing WORKTREES directory returns appropriate error."""
        repo = tmp_path / "repo"
        sessions = tmp_path / "sessions"
        repo.mkdir()
        sessions.mkdir()

        config = ProjectConfig(
            main_repo=repo,
            worktrees=tmp_path / "missing",
            sessions=sessions,
        )

        errors = config.validate()

        assert len(errors) >= 1
        assert any("WORKTREES" in e and "does not exist" in e for e in errors)

    def test_missing_sessions_returns_error(self, tmp_path: Path) -> None:
        """Missing SESSIONS directory returns appropriate error."""
        repo = tmp_path / "repo"
        worktrees = tmp_path / "worktrees"
        repo.mkdir()
        worktrees.mkdir()

        config = ProjectConfig(
            main_repo=repo,
            worktrees=worktrees,
            sessions=tmp_path / "missing",
        )

        errors = config.validate()

        assert len(errors) >= 1
        assert any("SESSIONS" in e and "does not exist" in e for e in errors)

    def test_path_is_file_not_directory(self, tmp_path: Path) -> None:
        """Path existing as file (not directory) returns appropriate error."""
        repo = tmp_path / "repo"
        worktrees = tmp_path / "worktrees"
        sessions = tmp_path / "sessions"
        repo.mkdir()
        worktrees.touch()  # File, not directory
        sessions.mkdir()

        config = ProjectConfig(
            main_repo=repo,
            worktrees=worktrees,
            sessions=sessions,
        )

        errors = config.validate()

        assert len(errors) >= 1
        assert any("WORKTREES" in e and "not a directory" in e for e in errors)


class TestRuntimeConfigFromEnv:
    """Tests for RuntimeConfig.from_env - environment loading."""

    def test_default_values(self) -> None:
        """Default values used when env vars not set."""
        with patch.dict(os.environ, {}, clear=True):
            config = RuntimeConfig.from_env()

        assert config.claude_model == "opus"
        assert config.claude_max_turns == 300
        assert config.claude_timeout == 1800
        assert config.max_retries == 3
        assert config.retry_delay == 5
        assert config.telegram_bot_token == ""
        assert config.telegram_chat_id == ""

    def test_reads_claude_env_vars(self) -> None:
        """Values read from CLAUDE_* environment variables."""
        env = {
            "CLAUDE_MODEL": "sonnet",
            "CLAUDE_MAX_TURNS": "50",
            "CLAUDE_TIMEOUT": "300",
        }
        with patch.dict(os.environ, env, clear=False):
            config = RuntimeConfig.from_env()

        assert config.claude_model == "sonnet"
        assert config.claude_max_turns == 50
        assert config.claude_timeout == 300

    def test_reads_samocode_env_vars(self) -> None:
        """Values read from SAMOCODE_* environment variables."""
        env = {
            "SAMOCODE_MAX_RETRIES": "5",
            "SAMOCODE_RETRY_DELAY": "10",
        }
        with patch.dict(os.environ, env, clear=False):
            config = RuntimeConfig.from_env()

        assert config.max_retries == 5
        assert config.retry_delay == 10

    def test_reads_telegram_env_vars(self) -> None:
        """Values read from TELEGRAM_* environment variables."""
        env = {
            "TELEGRAM_BOT_TOKEN": "my-token",
            "TELEGRAM_CHAT_ID": "12345",
        }
        with patch.dict(os.environ, env, clear=False):
            config = RuntimeConfig.from_env()

        assert config.telegram_bot_token == "my-token"
        assert config.telegram_chat_id == "12345"


class TestRuntimeConfigValidate:
    """Tests for RuntimeConfig.validate - runtime validation."""

    def test_valid_config_returns_empty(self, tmp_path: Path) -> None:
        """Valid configuration returns no errors."""
        claude = tmp_path / "claude"
        claude.touch()

        config = RuntimeConfig(
            telegram_bot_token="",
            telegram_chat_id="",
            claude_path=claude,
            claude_model="opus",
            claude_max_turns=100,
            claude_timeout=600,
            max_retries=3,
            retry_delay=5,
        )

        assert config.validate() == []

    def test_claude_path_not_found(self, tmp_path: Path) -> None:
        """Error when Claude CLI path doesn't exist."""
        config = RuntimeConfig(
            telegram_bot_token="",
            telegram_chat_id="",
            claude_path=tmp_path / "nonexistent",
            claude_model="opus",
            claude_max_turns=100,
            claude_timeout=600,
            max_retries=3,
            retry_delay=5,
        )

        errors = config.validate()

        assert any("not found" in e for e in errors)

    def test_claude_path_is_directory(self, tmp_path: Path) -> None:
        """Error when Claude path is a directory, not a file."""
        claude_dir = tmp_path / "claude"
        claude_dir.mkdir()

        config = RuntimeConfig(
            telegram_bot_token="",
            telegram_chat_id="",
            claude_path=claude_dir,
            claude_model="opus",
            claude_max_turns=100,
            claude_timeout=600,
            max_retries=3,
            retry_delay=5,
        )

        errors = config.validate()

        assert any("not a file" in e for e in errors)

    def test_invalid_max_turns(self, tmp_path: Path) -> None:
        """Error when max_turns is less than 1."""
        claude = tmp_path / "claude"
        claude.touch()

        config = RuntimeConfig(
            telegram_bot_token="",
            telegram_chat_id="",
            claude_path=claude,
            claude_model="opus",
            claude_max_turns=0,
            claude_timeout=600,
            max_retries=3,
            retry_delay=5,
        )

        errors = config.validate()

        assert any("max_turns" in e for e in errors)

    def test_invalid_timeout(self, tmp_path: Path) -> None:
        """Error when timeout is less than 1."""
        claude = tmp_path / "claude"
        claude.touch()

        config = RuntimeConfig(
            telegram_bot_token="",
            telegram_chat_id="",
            claude_path=claude,
            claude_model="opus",
            claude_max_turns=100,
            claude_timeout=0,
            max_retries=3,
            retry_delay=5,
        )

        errors = config.validate()

        assert any("timeout" in e for e in errors)


class TestResolveSessionPath:
    """Tests for resolve_session_path - session name to path."""

    def test_exact_match(self, tmp_path: Path) -> None:
        """Finds session with exact name match."""
        session = tmp_path / "my-task"
        session.mkdir()

        result = resolve_session_path(tmp_path, "my-task")

        assert result == session

    def test_dated_match(self, tmp_path: Path) -> None:
        """Finds session with date prefix."""
        session = tmp_path / "26-01-21-my-task"
        session.mkdir()

        result = resolve_session_path(tmp_path, "my-task")

        assert result == session

    def test_most_recent_dated_match(self, tmp_path: Path) -> None:
        """Selects most recent when multiple dated sessions."""
        old = tmp_path / "26-01-01-my-task"
        new = tmp_path / "26-01-15-my-task"
        old.mkdir()
        new.mkdir()

        result = resolve_session_path(tmp_path, "my-task")

        assert result == new

    def test_exact_match_preferred_over_dated(self, tmp_path: Path) -> None:
        """Exact match takes precedence over dated match."""
        exact = tmp_path / "my-task"
        dated = tmp_path / "26-01-15-my-task"
        exact.mkdir()
        dated.mkdir()

        result = resolve_session_path(tmp_path, "my-task")

        assert result == exact

    def test_new_session_returns_dated_path(self, tmp_path: Path) -> None:
        """Returns new dated path when no match exists."""
        result = resolve_session_path(tmp_path, "new-task")

        assert result.parent == tmp_path
        assert "new-task" in result.name
        # Format: YY-MM-DD-new-task
        assert result.name.count("-") >= 4  # Date has 2 dashes + name dashes

    def test_ignores_files_not_directories(self, tmp_path: Path) -> None:
        """Only matches directories, not files."""
        file_match = tmp_path / "26-01-15-my-task"
        file_match.touch()  # File, not directory

        result = resolve_session_path(tmp_path, "my-task")

        # Should create new path since the match was a file
        assert result != file_match


class TestParseConfigFile:
    """Tests for _parse_config_file - file format parsing."""

    def test_basic_key_value(self, tmp_path: Path) -> None:
        """Parses simple key=value format."""
        f = tmp_path / "config"
        f.write_text("KEY=value\n")

        assert _parse_config_file(f) == {"KEY": "value"}

    def test_multiple_key_values(self, tmp_path: Path) -> None:
        """Parses multiple key=value pairs."""
        f = tmp_path / "config"
        f.write_text("KEY1=value1\nKEY2=value2\n")

        assert _parse_config_file(f) == {"KEY1": "value1", "KEY2": "value2"}

    def test_ignores_comments(self, tmp_path: Path) -> None:
        """Lines starting with # are ignored."""
        f = tmp_path / "config"
        f.write_text("# comment\nKEY=value\n")

        assert _parse_config_file(f) == {"KEY": "value"}

    def test_ignores_empty_lines(self, tmp_path: Path) -> None:
        """Empty lines are ignored."""
        f = tmp_path / "config"
        f.write_text("KEY1=value1\n\n   \nKEY2=value2\n")

        assert _parse_config_file(f) == {"KEY1": "value1", "KEY2": "value2"}

    def test_handles_equals_in_value(self, tmp_path: Path) -> None:
        """Only first = splits key from value."""
        f = tmp_path / "config"
        f.write_text("PATH=/foo=bar\n")

        assert _parse_config_file(f) == {"PATH": "/foo=bar"}

    def test_strips_whitespace(self, tmp_path: Path) -> None:
        """Whitespace around keys and values is stripped."""
        f = tmp_path / "config"
        f.write_text("  KEY  =  value with spaces  \n")

        assert _parse_config_file(f) == {"KEY": "value with spaces"}

    def test_empty_file(self, tmp_path: Path) -> None:
        """Empty file returns empty dict."""
        f = tmp_path / "config"
        f.write_text("")

        assert _parse_config_file(f) == {}


class TestParseSamocodeFileDeprecated:
    """Tests for parse_samocode_file - deprecated search function."""

    def test_finds_file_in_start_directory(self, tmp_path: Path) -> None:
        """File in the start directory is found immediately."""
        samocode = tmp_path / ".samocode"
        samocode.write_text("KEY=value\n")

        result = parse_samocode_file(tmp_path)

        assert result == {"KEY": "value"}

    def test_finds_file_in_parent_directory(self, tmp_path: Path) -> None:
        """File in a parent directory is found via traversal."""
        samocode = tmp_path / ".samocode"
        samocode.write_text("PARENT=found\n")
        subdir = tmp_path / "child" / "grandchild"
        subdir.mkdir(parents=True)

        result = parse_samocode_file(subdir)

        assert result == {"PARENT": "found"}

    def test_returns_empty_dict_when_not_found(self, tmp_path: Path) -> None:
        """Returns empty dict when no .samocode file exists."""
        subdir = tmp_path / "empty"
        subdir.mkdir()

        result = parse_samocode_file(subdir)

        assert result == {}


class TestSamocodeConfigIntegration:
    """Integration tests for complete config loading."""

    def test_convenience_accessors(self, tmp_path: Path) -> None:
        """Config provides convenient accessors for common values."""
        project = ProjectConfig(
            main_repo=tmp_path / "repo",
            worktrees=tmp_path / "worktrees",
            sessions=tmp_path / "sessions",
        )
        runtime = RuntimeConfig(
            telegram_bot_token="",
            telegram_chat_id="",
            claude_path=Path("claude"),
            claude_model="opus",
            claude_max_turns=100,
            claude_timeout=600,
            max_retries=3,
            retry_delay=5,
        )
        config = SamocodeConfig(
            project=project,
            runtime=runtime,
            session_path=tmp_path / "sessions" / "test",
        )

        # Direct accessors work
        assert config.main_repo == project.main_repo
        assert config.worktrees_dir == project.worktrees
        assert config.sessions_dir == project.sessions
        assert config.claude_model == "opus"
        assert config.claude_timeout == 600

        # Backward compatibility
        assert config.repo_path == project.main_repo

    def test_validate_combines_project_and_runtime(self, tmp_path: Path) -> None:
        """SamocodeConfig.validate() combines errors from project and runtime."""
        project = ProjectConfig(
            main_repo=tmp_path / "missing-repo",
            worktrees=tmp_path / "missing-worktrees",
            sessions=tmp_path / "missing-sessions",
        )
        runtime = RuntimeConfig(
            telegram_bot_token="",
            telegram_chat_id="",
            claude_path=tmp_path / "missing-claude",
            claude_model="opus",
            claude_max_turns=100,
            claude_timeout=600,
            max_retries=3,
            retry_delay=5,
        )
        config = SamocodeConfig(
            project=project,
            runtime=runtime,
            session_path=tmp_path / "sessions" / "test",
        )

        errors = config.validate()

        # Should have errors from both project (3 paths) and runtime (1 claude path)
        assert len(errors) >= 4

    def test_to_log_string(self, tmp_path: Path) -> None:
        """to_log_string includes key config values."""
        project = ProjectConfig(
            main_repo=tmp_path / "repo",
            worktrees=tmp_path / "worktrees",
            sessions=tmp_path / "sessions",
        )
        runtime = RuntimeConfig(
            telegram_bot_token="",
            telegram_chat_id="",
            claude_path=Path("claude"),
            claude_model="opus",
            claude_max_turns=120,
            claude_timeout=600,
            max_retries=3,
            retry_delay=5,
        )
        config = SamocodeConfig(
            project=project,
            runtime=runtime,
            session_path=tmp_path / "sessions" / "test",
        )

        result = config.to_log_string()

        assert "repo=" in result
        assert "worktrees=" in result
        assert "sessions=" in result
        assert "model=opus" in result
        assert "timeout=600s" in result
        assert "max_turns=120" in result
        assert "telegram=none" in result

    def test_to_log_string_with_telegram(self, tmp_path: Path) -> None:
        """to_log_string shows telegram=configured when token set."""
        project = ProjectConfig(
            main_repo=tmp_path / "repo",
            worktrees=tmp_path / "worktrees",
            sessions=tmp_path / "sessions",
        )
        runtime = RuntimeConfig(
            telegram_bot_token="secret",
            telegram_chat_id="12345",
            claude_path=Path("claude"),
            claude_model="opus",
            claude_max_turns=120,
            claude_timeout=600,
            max_retries=3,
            retry_delay=5,
        )
        config = SamocodeConfig(
            project=project,
            runtime=runtime,
            session_path=tmp_path / "sessions" / "test",
        )

        result = config.to_log_string()

        assert "telegram=configured" in result
        assert "secret" not in result  # Token should not be exposed
