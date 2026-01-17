"""Tests for worker/runner.py - Claude CLI execution.

This module tests:
- Phase to agent mapping
- Session context building
- Overview extraction utilities
- Log filename generation
- Prompt building
- Log streaming (mocked)
- CLI execution (mocked)
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch


from worker.config import SamocodeConfig
from worker.runner import (
    ExecutionResult,
    ExecutionStatus,
    PHASE_AGENTS,
    build_session_context,
    extract_iteration,
    extract_phase,
    generate_log_filename,
    get_agent_for_phase,
    run_claude_once,
    run_claude_with_retry,
)


def make_config(tmp_path: Path, repo_path: Path | None = None) -> SamocodeConfig:
    """Create a test configuration."""
    claude = tmp_path / "claude"
    claude.touch()
    return SamocodeConfig(
        repo_path=repo_path,
        worktrees_dir=tmp_path / "worktrees",
        telegram_bot_token="",
        telegram_chat_id="",
        claude_path=claude,
        claude_model="opus",
        claude_max_turns=10,
        claude_timeout=30,
        max_retries=2,
        retry_delay=0,
    )


class TestGetAgentForPhase:
    """Tests for get_agent_for_phase - mapping phases to agents."""

    def test_all_known_phases(self) -> None:
        """All phases in PHASE_AGENTS are mapped correctly."""
        for phase, expected_agent in PHASE_AGENTS.items():
            assert get_agent_for_phase(phase) == expected_agent

    def test_unknown_phase_returns_none(self) -> None:
        """Unknown phase returns None."""
        assert get_agent_for_phase("unknown") is None
        assert get_agent_for_phase("nonexistent") is None

    def test_none_input_returns_none(self) -> None:
        """None input returns None."""
        assert get_agent_for_phase(None) is None

    def test_case_insensitive(self) -> None:
        """Phase matching is case-insensitive."""
        assert get_agent_for_phase("INIT") == "init-agent"
        assert get_agent_for_phase("Planning") == "planning-agent"
        assert get_agent_for_phase("TESTING") == "testing-agent"


class TestBuildSessionContext:
    """Tests for build_session_context - system prompt injection."""

    def test_basic_context(self, tmp_path: Path) -> None:
        """Basic context includes workflow.md and session path."""
        workflow = tmp_path / "workflow.md"
        workflow.write_text("# Samocode Workflow\nCommon instructions here.")
        config = make_config(tmp_path)
        session = tmp_path / "test-session"
        session.mkdir()

        context = build_session_context(workflow, session, config)

        assert "Samocode Workflow" in context  # From workflow.md
        assert "Session Context" in context
        assert str(session) in context

    def test_with_phase_and_iteration(self, tmp_path: Path) -> None:
        """Context includes phase and iteration when provided."""
        workflow = tmp_path / "workflow.md"
        workflow.write_text("# Workflow")
        config = make_config(tmp_path)
        session = tmp_path / "test-session"
        session.mkdir()

        context = build_session_context(
            workflow, session, config, phase="implementation", iteration=5
        )

        assert "implementation" in context
        assert "5" in context

    def test_with_repo_path(self, tmp_path: Path) -> None:
        """Context includes worktree config when repo_path set."""
        workflow = tmp_path / "workflow.md"
        workflow.write_text("# Workflow")
        config = make_config(tmp_path)
        config = SamocodeConfig(
            repo_path=tmp_path / "repo",
            worktrees_dir=tmp_path / "worktrees",
            telegram_bot_token="",
            telegram_chat_id="",
            claude_path=config.claude_path,
            claude_model="opus",
            claude_max_turns=10,
            claude_timeout=30,
            max_retries=2,
            retry_delay=0,
        )
        session = tmp_path / "worktrees" / "25-01-13-feature"
        session.mkdir(parents=True)

        context = build_session_context(workflow, session, config)

        assert "Worktree Configuration" in context
        assert str(config.repo_path) in context

    def test_without_repo_path(self, tmp_path: Path) -> None:
        """Context shows standalone project when no repo_path."""
        workflow = tmp_path / "workflow.md"
        workflow.write_text("# Workflow")
        config = make_config(tmp_path)
        session = tmp_path / "project" / "_samocode" / "session"
        session.mkdir(parents=True)

        context = build_session_context(workflow, session, config)

        assert "Standalone Project" in context

    def test_with_initial_instructions(self, tmp_path: Path) -> None:
        """Context includes initial dive/task instructions."""
        workflow = tmp_path / "workflow.md"
        workflow.write_text("# Workflow")
        config = make_config(tmp_path)
        session = tmp_path / "test-session"
        session.mkdir()

        context = build_session_context(
            workflow,
            session,
            config,
            initial_dive="architecture",
            initial_task="add feature",
        )

        assert "Initial Session Data" in context
        assert "architecture" in context
        assert "add feature" in context
        assert "IMPORTANT" in context


class TestExtractPhase:
    """Tests for extract_phase - parsing phase from _overview.md."""

    def test_overview_not_exists(self, tmp_path: Path) -> None:
        """Returns None when _overview.md doesn't exist."""
        session = tmp_path / "session"
        session.mkdir()

        result = extract_phase(session)

        assert result is None

    def test_phase_not_found(self, tmp_path: Path) -> None:
        """Returns None when Phase line not present."""
        session = tmp_path / "session"
        session.mkdir()
        (session / "_overview.md").write_text("# Session\nNo phase here")

        result = extract_phase(session)

        assert result is None

    def test_phase_found(self, tmp_path: Path) -> None:
        """Returns phase when found."""
        session = tmp_path / "session"
        session.mkdir()
        (session / "_overview.md").write_text("Phase: implementation\n")

        result = extract_phase(session)

        assert result == "implementation"


class TestExtractIteration:
    """Tests for extract_iteration - parsing iteration from _overview.md."""

    def test_overview_not_exists(self, tmp_path: Path) -> None:
        """Returns None when _overview.md doesn't exist."""
        session = tmp_path / "session"
        session.mkdir()

        result = extract_iteration(session)

        assert result is None

    def test_iteration_not_found(self, tmp_path: Path) -> None:
        """Returns None when Iteration line not present."""
        session = tmp_path / "session"
        session.mkdir()
        (session / "_overview.md").write_text("# Session\nNo iteration")

        result = extract_iteration(session)

        assert result is None

    def test_iteration_found(self, tmp_path: Path) -> None:
        """Returns iteration number when found."""
        session = tmp_path / "session"
        session.mkdir()
        (session / "_overview.md").write_text("Iteration: 42\n")

        result = extract_iteration(session)

        assert result == 42


class TestGenerateLogFilename:
    """Tests for generate_log_filename - timestamped log paths."""

    def test_format_with_phase(self, tmp_path: Path) -> None:
        """Generates MM-DD-HHMMSS-phase.jsonl format."""
        session = tmp_path / "session"

        filename = generate_log_filename(session, "testing")

        assert filename.suffix == ".jsonl"
        assert "testing" in filename.name
        assert filename.parent == session

    def test_none_phase_becomes_unknown(self, tmp_path: Path) -> None:
        """None phase becomes 'unknown' in filename."""
        session = tmp_path / "session"

        filename = generate_log_filename(session, None)

        assert "unknown" in filename.name


class TestRunClaudeOnce:
    """Tests for run_claude_once - single CLI execution."""

    def test_success_case(self, tmp_path: Path) -> None:
        """Returns SUCCESS status on successful execution."""
        workflow = tmp_path / "workflow.md"
        workflow.write_text("# Workflow")
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        session = sessions_dir / "test-session"
        session.mkdir()
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (session / "_overview.md").write_text("Phase: init\n")
        config = make_config(tmp_path, repo_path=project_dir)

        with patch("worker.runner.subprocess.Popen") as mock_popen:
            mock_process = MagicMock()
            mock_process.poll.return_value = None
            mock_process.wait.return_value = 0
            mock_process.returncode = 0
            mock_popen.return_value = mock_process

            with patch("worker.runner.stream_logs") as mock_stream:
                mock_stream.return_value = ("stdout output", "")

                result = run_claude_once(workflow, session, config, 1)

        assert result.status == ExecutionStatus.SUCCESS
        assert result.attempt == 1

    def test_failure_case(self, tmp_path: Path) -> None:
        """Returns FAILURE status on non-zero return code."""
        workflow = tmp_path / "workflow.md"
        workflow.write_text("# Workflow")
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        session = sessions_dir / "test-session"
        session.mkdir()
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (session / "_overview.md").write_text("Phase: init\n")
        config = make_config(tmp_path, repo_path=project_dir)

        with patch("worker.runner.subprocess.Popen") as mock_popen:
            mock_process = MagicMock()
            mock_process.poll.return_value = None
            mock_process.wait.return_value = 1
            mock_process.returncode = 1
            mock_popen.return_value = mock_process

            with patch("worker.runner.stream_logs") as mock_stream:
                mock_stream.return_value = ("", "error output")

                result = run_claude_once(workflow, session, config, 1)

        assert result.status == ExecutionStatus.FAILURE
        assert result.returncode == 1

    def test_timeout_case(self, tmp_path: Path) -> None:
        """Returns TIMEOUT status when execution times out."""
        workflow = tmp_path / "workflow.md"
        workflow.write_text("# Workflow")
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        session = sessions_dir / "test-session"
        session.mkdir()
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (session / "_overview.md").write_text("Phase: init\n")
        config = make_config(tmp_path, repo_path=project_dir)

        with patch("worker.runner.subprocess.Popen") as mock_popen:
            mock_process = MagicMock()
            mock_process.poll.return_value = None
            mock_process.stdout = MagicMock()
            mock_process.stderr = MagicMock()
            mock_process.kill = MagicMock()
            mock_process.wait = MagicMock()
            mock_popen.return_value = mock_process

            with patch("worker.runner.stream_logs") as mock_stream:
                mock_stream.side_effect = subprocess.TimeoutExpired(
                    cmd="claude", timeout=30
                )

                result = run_claude_once(workflow, session, config, 1)

        assert result.status == ExecutionStatus.TIMEOUT

    def test_missing_repo_path_raises_error(self, tmp_path: Path) -> None:
        """Raises ValueError when repo_path is not set."""
        workflow = tmp_path / "workflow.md"
        workflow.write_text("# Workflow")
        session = tmp_path / "session"
        session.mkdir()
        (session / "_overview.md").write_text("Phase: init\n")
        config = make_config(tmp_path)  # No repo_path

        import pytest

        with pytest.raises(ValueError) as exc_info:
            run_claude_once(workflow, session, config, 1)

        assert "MAIN_REPO is required" in str(exc_info.value)


class TestRunClaudeWithRetry:
    """Tests for run_claude_with_retry - retry wrapper."""

    def test_success_first_attempt(self, tmp_path: Path) -> None:
        """Returns immediately on first success."""
        workflow = tmp_path / "workflow.md"
        workflow.write_text("# Workflow")
        session = tmp_path / "session"
        session.mkdir()
        config = make_config(tmp_path)

        with patch("worker.runner.run_claude_once") as mock_run:
            mock_run.return_value = ExecutionResult(
                status=ExecutionStatus.SUCCESS,
                stdout="ok",
                stderr="",
                returncode=0,
                attempt=1,
            )

            result = run_claude_with_retry(workflow, session, config)

        assert result.status == ExecutionStatus.SUCCESS
        assert mock_run.call_count == 1

    def test_success_after_retry(self, tmp_path: Path) -> None:
        """Returns SUCCESS after failed attempt then success."""
        workflow = tmp_path / "workflow.md"
        workflow.write_text("# Workflow")
        session = tmp_path / "session"
        session.mkdir()
        config = make_config(tmp_path)

        with patch("worker.runner.run_claude_once") as mock_run:
            mock_run.side_effect = [
                ExecutionResult(
                    status=ExecutionStatus.FAILURE,
                    stdout="",
                    stderr="error",
                    returncode=1,
                    attempt=1,
                ),
                ExecutionResult(
                    status=ExecutionStatus.SUCCESS,
                    stdout="ok",
                    stderr="",
                    returncode=0,
                    attempt=2,
                ),
            ]

            result = run_claude_with_retry(workflow, session, config)

        assert result.status == ExecutionStatus.SUCCESS
        assert mock_run.call_count == 2

    def test_retry_exhausted(self, tmp_path: Path) -> None:
        """Returns RETRY_EXHAUSTED when all attempts fail."""
        workflow = tmp_path / "workflow.md"
        workflow.write_text("# Workflow")
        session = tmp_path / "session"
        session.mkdir()
        config = make_config(tmp_path)

        with patch("worker.runner.run_claude_once") as mock_run:
            mock_run.return_value = ExecutionResult(
                status=ExecutionStatus.FAILURE,
                stdout="",
                stderr="error",
                returncode=1,
                attempt=1,
            )

            result = run_claude_with_retry(workflow, session, config)

        assert result.status == ExecutionStatus.RETRY_EXHAUSTED
        assert mock_run.call_count == config.max_retries
