"""Tests for packaging configuration."""

from pathlib import Path


def test_wheel_includes_runtime_prompt_assets() -> None:
    """The installed CLI needs prompt assets at runtime."""
    pyproject = Path("pyproject.toml").read_text()

    assert '"workflow.md" = "workflow.md"' in pyproject
    assert '"agents" = "agents"' in pyproject
    assert '"skills" = "skills"' in pyproject
    assert '"commands" = "commands"' in pyproject


def test_pytest_ignores_session_worktrees() -> None:
    """Nested worktree tests should not be collected with the root suite."""
    pyproject = Path("pyproject.toml").read_text()

    assert 'testpaths = ["tests"]' in pyproject
    assert '"worktrees"' in pyproject
