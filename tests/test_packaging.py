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


def test_no_command_shadows_a_skill() -> None:
    """Skills and commands share the /<name> namespace in Claude Code; a
    same-named pair renders as duplicate slash-command suggestions."""
    skill_names = {p.name for p in Path("skills").iterdir() if p.is_dir()}
    command_names = {p.stem for p in Path("commands").glob("*.md")}

    assert not skill_names & command_names
