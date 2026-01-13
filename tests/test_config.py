"""Tests for worker/config.py - Configuration management.

This module tests:
- .samocode file discovery and parsing
- SamocodeConfig loading from environment and files
- Configuration validation
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from worker.config import (
    SamocodeConfig,
    _parse_samocode_contents,
    parse_samocode_file,
)


class TestParseSamocodeFile:
    """Tests for parse_samocode_file - finding and loading .samocode config."""

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

    def test_stops_at_home_directory(self, tmp_path: Path) -> None:
        """Does not traverse above home directory."""
        with patch.object(Path, "home", return_value=tmp_path):
            subdir = tmp_path / "below_home"
            subdir.mkdir()

            result = parse_samocode_file(subdir)

            assert result == {}


class TestParseSamocodeContents:
    """Tests for _parse_samocode_contents - parsing config file format."""

    def test_parses_key_value_pairs(self, tmp_path: Path) -> None:
        """Basic key=value pairs are parsed correctly."""
        config = tmp_path / ".samocode"
        config.write_text("KEY1=value1\nKEY2=value2\n")

        result = _parse_samocode_contents(config)

        assert result == {"KEY1": "value1", "KEY2": "value2"}

    def test_ignores_empty_lines(self, tmp_path: Path) -> None:
        """Empty lines and whitespace-only lines are ignored."""
        config = tmp_path / ".samocode"
        config.write_text("KEY1=value1\n\n   \nKEY2=value2\n")

        result = _parse_samocode_contents(config)

        assert result == {"KEY1": "value1", "KEY2": "value2"}

    def test_ignores_comments(self, tmp_path: Path) -> None:
        """Lines starting with # are treated as comments."""
        config = tmp_path / ".samocode"
        config.write_text("# This is a comment\nKEY=value\n# Another comment\n")

        result = _parse_samocode_contents(config)

        assert result == {"KEY": "value"}

    def test_handles_equals_in_value(self, tmp_path: Path) -> None:
        """Values containing = are handled correctly (only first = splits)."""
        config = tmp_path / ".samocode"
        config.write_text("PATH=/foo=bar/baz\n")

        result = _parse_samocode_contents(config)

        assert result == {"PATH": "/foo=bar/baz"}

    def test_strips_whitespace(self, tmp_path: Path) -> None:
        """Whitespace around keys and values is stripped."""
        config = tmp_path / ".samocode"
        config.write_text("  KEY  =  value with spaces  \n")

        result = _parse_samocode_contents(config)

        assert result == {"KEY": "value with spaces"}

    def test_empty_file(self, tmp_path: Path) -> None:
        """Empty file returns empty dict."""
        config = tmp_path / ".samocode"
        config.write_text("")

        result = _parse_samocode_contents(config)

        assert result == {}


class TestSamocodeConfigFromEnv:
    """Tests for SamocodeConfig.from_env - loading configuration."""

    def test_loads_worktrees_from_samocode_file(self, tmp_path: Path) -> None:
        """WORKTREES from .samocode file is used."""
        worktrees = tmp_path / "worktrees"
        worktrees.mkdir()
        samocode = tmp_path / ".samocode"
        samocode.write_text(f"WORKTREES={worktrees}\n")

        config = SamocodeConfig.from_env(working_dir=tmp_path)

        assert config.worktrees_dir == worktrees

    def test_falls_back_to_env_var(self, tmp_path: Path) -> None:
        """Falls back to WORKTREES_DIR env var when .samocode not found."""
        worktrees = tmp_path / "worktrees"
        worktrees.mkdir()

        with patch.dict(os.environ, {"WORKTREES_DIR": str(worktrees)}, clear=False):
            config = SamocodeConfig.from_env(working_dir=tmp_path)

        assert config.worktrees_dir == worktrees

    def test_raises_when_worktrees_missing(self, tmp_path: Path) -> None:
        """Raises ValueError when WORKTREES not configured anywhere."""
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="WORKTREES not found"):
                SamocodeConfig.from_env(working_dir=tmp_path)

    def test_default_values(self, tmp_path: Path) -> None:
        """Default values are applied for optional config."""
        worktrees = tmp_path / "worktrees"
        worktrees.mkdir()
        samocode = tmp_path / ".samocode"
        samocode.write_text(f"WORKTREES={worktrees}\n")

        with patch.dict(os.environ, {}, clear=True):
            config = SamocodeConfig.from_env(working_dir=tmp_path)

        assert config.claude_model == "opus"
        assert config.claude_max_turns == 120
        assert config.claude_timeout == 600
        assert config.max_retries == 3
        assert config.retry_delay == 5
        assert config.telegram_bot_token == ""
        assert config.telegram_chat_id == ""

    def test_repo_path_is_none_by_default(self, tmp_path: Path) -> None:
        """repo_path is None (set per-session via CLI)."""
        worktrees = tmp_path / "worktrees"
        worktrees.mkdir()
        samocode = tmp_path / ".samocode"
        samocode.write_text(f"WORKTREES={worktrees}\n")

        config = SamocodeConfig.from_env(working_dir=tmp_path)

        assert config.repo_path is None


class TestSamocodeConfigValidate:
    """Tests for SamocodeConfig.validate - configuration validation."""

    def test_valid_config_returns_empty_list(self, tmp_path: Path) -> None:
        """Valid configuration returns no errors."""
        claude = tmp_path / "claude"
        claude.touch()

        config = SamocodeConfig(
            repo_path=None,
            worktrees_dir=tmp_path,
            telegram_bot_token="",
            telegram_chat_id="",
            claude_path=claude,
            claude_model="opus",
            claude_max_turns=120,
            claude_timeout=600,
            max_retries=3,
            retry_delay=5,
        )

        errors = config.validate()

        assert errors == []

    def test_claude_path_not_found(self, tmp_path: Path) -> None:
        """Error when Claude CLI path doesn't exist."""
        config = SamocodeConfig(
            repo_path=None,
            worktrees_dir=tmp_path,
            telegram_bot_token="",
            telegram_chat_id="",
            claude_path=tmp_path / "nonexistent",
            claude_model="opus",
            claude_max_turns=120,
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

        config = SamocodeConfig(
            repo_path=None,
            worktrees_dir=tmp_path,
            telegram_bot_token="",
            telegram_chat_id="",
            claude_path=claude_dir,
            claude_model="opus",
            claude_max_turns=120,
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

        config = SamocodeConfig(
            repo_path=None,
            worktrees_dir=tmp_path,
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

        config = SamocodeConfig(
            repo_path=None,
            worktrees_dir=tmp_path,
            telegram_bot_token="",
            telegram_chat_id="",
            claude_path=claude,
            claude_model="opus",
            claude_max_turns=120,
            claude_timeout=0,
            max_retries=3,
            retry_delay=5,
        )

        errors = config.validate()

        assert any("timeout" in e for e in errors)
