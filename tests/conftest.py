"""Shared test fixtures for worker package tests."""

import pytest
from pathlib import Path


@pytest.fixture
def temp_session(tmp_path: Path) -> Path:
    """Create a temporary session directory."""
    session_path = tmp_path / "test-session"
    session_path.mkdir()
    return session_path


@pytest.fixture
def sample_overview(temp_session: Path) -> Path:
    """Create a sample _overview.md file."""
    content = """# Session: Test

Working Dir: /home/dev/project

## Status
Phase: implementation
Iteration: 5
Blocked: no
"""
    overview = temp_session / "_overview.md"
    overview.write_text(content)
    return overview
