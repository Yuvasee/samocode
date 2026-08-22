"""Tests for worker/runner.py - AI CLI execution.

This module tests:
- Phase to agent mapping
- Session context building
- Overview extraction utilities
- Log filename generation
- Prompt building
- Log streaming (mocked)
- CLI execution (mocked)
"""

import logging
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


from worker.config import ProjectConfig, RuntimeConfig, SamocodeConfig
from worker.global_config import default_config
from worker.phases import Phase, get_agent_for_phase
from worker.routing import ExecutionProfileSource
from worker.runner import (
    ExecutionResult,
    ExecutionStatus,
    _build_cli_args,
    _build_codex_prompt,
    build_session_context,
    extract_iteration,
    extract_phase,
    generate_log_filename,
    resolve_iteration_plan,
    run_claude_once,
    run_claude_with_retry,
    update_phase,
)


def make_config(tmp_path: Path, repo_path: Path | None = None) -> SamocodeConfig:
    """Create a test configuration."""
    claude = tmp_path / "claude"
    claude.touch()
    worktrees = tmp_path / "worktrees"
    worktrees.mkdir(exist_ok=True)
    sessions = tmp_path / "sessions"
    sessions.mkdir(exist_ok=True)

    project = ProjectConfig(
        main_repo=repo_path or tmp_path / "repo",
        worktrees=worktrees,
        sessions=sessions,
    )
    runtime = RuntimeConfig(
        telegram_bot_token="",
        telegram_chat_id="",
        claude_path=claude,
        claude_model="opus",
        claude_max_turns=10,
        claude_timeout=30,
        max_retries=2,
        retry_delay=0,
    )
    return SamocodeConfig(
        project=project,
        runtime=runtime,
        session_path=sessions / "test-session",
    )


def make_codex_config(tmp_path: Path, repo_path: Path | None = None) -> SamocodeConfig:
    """Create a Codex test configuration."""
    config = make_config(tmp_path, repo_path)
    codex = tmp_path / "codex"
    codex.touch()
    runtime = RuntimeConfig(
        ai_provider="codex",
        codex_path=codex,
        codex_model="gpt-5.5",
        codex_timeout=45,
        max_retries=2,
        retry_delay=0,
    )
    return SamocodeConfig(
        project=config.project,
        runtime=runtime,
        session_path=config.session_path,
    )


class TestGetAgentForPhase:
    """Tests for get_agent_for_phase - mapping phases to agents."""

    def test_all_known_phases(self) -> None:
        """All phases in Phase enum are mapped correctly."""
        for phase in Phase:
            assert get_agent_for_phase(phase.value) == f"{phase.value}-agent"

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
        repo = tmp_path / "repo"
        repo.mkdir()
        worktrees = tmp_path / "worktrees"
        worktrees.mkdir()
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        claude = tmp_path / "claude"
        claude.touch()
        project = ProjectConfig(
            main_repo=repo,
            worktrees=worktrees,
            sessions=sessions,
        )
        runtime = RuntimeConfig(
            telegram_bot_token="",
            telegram_chat_id="",
            claude_path=claude,
            claude_model="opus",
            claude_max_turns=10,
            claude_timeout=30,
            max_retries=2,
            retry_delay=0,
        )
        config = SamocodeConfig(
            project=project,
            runtime=runtime,
            session_path=sessions / "test",
        )
        session = tmp_path / "worktrees" / "25-01-13-feature"
        session.mkdir(parents=True)

        context = build_session_context(workflow, session, config)

        assert "Worktree Configuration" in context
        assert str(config.repo_path) in context

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


class TestCodexPromptBuilding:
    """Tests for Codex provider prompt and CLI construction."""

    def test_build_codex_prompt_includes_agent_spec(self, tmp_path: Path) -> None:
        """Codex prompt injects the selected phase agent instructions."""
        workflow = tmp_path / "workflow.md"
        workflow.write_text("# Workflow")
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "testing-agent.md").write_text("# Testing Agent\nRun tests.")

        prompt = _build_codex_prompt(
            "testing-agent",
            "SESSION CONTEXT",
            workflow,
        )

        assert "samocode codex-provider mode" in prompt
        assert "SESSION CONTEXT" in prompt
        assert "Agent Spec: testing-agent" in prompt
        assert "Run tests." in prompt

    def test_build_codex_cli_args(self, tmp_path: Path) -> None:
        """Codex CLI args use codex exec with configured model and prompt."""
        workflow = tmp_path / "workflow.md"
        workflow.write_text("# Workflow")
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "init-agent.md").write_text("# Init Agent")
        config = make_codex_config(tmp_path)

        args = _build_cli_args(config, "init-agent", "SESSION CONTEXT", workflow)

        assert args[:4] == [
            str(config.codex_path),
            "exec",
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
        ]
        assert "--model" in args
        assert "gpt-5.5" in args
        assert "SESSION CONTEXT" in args[-1]


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


class TestUpdatePhase:
    """Tests for update_phase - updating phase in _overview.md."""

    def test_overview_not_exists(self, tmp_path: Path) -> None:
        """Returns False when _overview.md doesn't exist."""
        session = tmp_path / "session"
        session.mkdir()

        result = update_phase(session, "testing")

        assert result is False

    def test_phase_not_found(self, tmp_path: Path) -> None:
        """Returns False when Phase line not present."""
        session = tmp_path / "session"
        session.mkdir()
        (session / "_overview.md").write_text("# Session\nNo phase here")

        result = update_phase(session, "testing")

        assert result is False
        # Content unchanged
        assert "No phase here" in (session / "_overview.md").read_text()

    def test_phase_updated(self, tmp_path: Path) -> None:
        """Updates phase when found."""
        session = tmp_path / "session"
        session.mkdir()
        (session / "_overview.md").write_text("Phase: implementation\nOther: stuff")

        result = update_phase(session, "testing")

        assert result is True
        content = (session / "_overview.md").read_text()
        assert "Phase: testing" in content
        assert "Phase: implementation" not in content
        assert "Other: stuff" in content

    def test_preserves_other_content(self, tmp_path: Path) -> None:
        """Preserves all other content in the file."""
        session = tmp_path / "session"
        session.mkdir()
        original = """# Session
## Status
Phase: implementation
Iteration: 5
Blocked: no

## Flow Log
- Entry 1
"""
        (session / "_overview.md").write_text(original)

        result = update_phase(session, "quality")

        assert result is True
        content = (session / "_overview.md").read_text()
        assert "Phase: quality" in content
        assert "Iteration: 5" in content
        assert "Blocked: no" in content
        assert "## Flow Log" in content


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

    def test_format_with_phase_and_iteration(self, tmp_path: Path) -> None:
        """Generates MM-DD-HHMM-NNN-phase.jsonl format in _logs/ subfolder."""
        session = tmp_path / "session"

        filename = generate_log_filename(session, "testing", iteration=5)

        assert filename.suffix == ".jsonl"
        assert "testing" in filename.name
        assert "-005-" in filename.name  # Iteration formatted as 3 digits
        assert filename.parent == session / "_logs"

    def test_none_phase_becomes_unknown(self, tmp_path: Path) -> None:
        """None phase becomes 'unknown' in filename."""
        session = tmp_path / "session"

        filename = generate_log_filename(session, None, iteration=1)

        assert "unknown" in filename.name
        assert "-001-" in filename.name
        assert filename.parent == session / "_logs"

    def test_none_iteration_becomes_000(self, tmp_path: Path) -> None:
        """None iteration becomes '000' in filename."""
        session = tmp_path / "session"

        filename = generate_log_filename(session, "init", iteration=None)

        assert "-000-" in filename.name
        assert "init" in filename.name


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


def _result(status: ExecutionStatus, attempt: int) -> ExecutionResult:
    return ExecutionResult(
        status=status, stdout="", stderr="", returncode=0, attempt=attempt
    )


class TestRunClaudeWithRetry:
    """Tests for run_claude_with_retry - resolve-once + retry wrapper."""

    def test_success_first_attempt(self, tmp_path: Path) -> None:
        """Returns immediately on first success; resolves the plan once."""
        workflow = tmp_path / "workflow.md"
        workflow.write_text("# Workflow")
        session = tmp_path / "session"
        session.mkdir()
        config = make_config(tmp_path)

        with patch("worker.runner._execute_plan") as mock_exec:
            mock_exec.return_value = _result(ExecutionStatus.SUCCESS, 1)
            result = run_claude_with_retry(workflow, session, config)

        assert result.status == ExecutionStatus.SUCCESS
        assert mock_exec.call_count == 1

    def test_success_after_retry(self, tmp_path: Path) -> None:
        """Returns SUCCESS after a failed attempt then success."""
        workflow = tmp_path / "workflow.md"
        workflow.write_text("# Workflow")
        session = tmp_path / "session"
        session.mkdir()
        config = make_config(tmp_path)

        with patch("worker.runner._execute_plan") as mock_exec:
            mock_exec.side_effect = [
                _result(ExecutionStatus.FAILURE, 1),
                _result(ExecutionStatus.SUCCESS, 2),
            ]
            result = run_claude_with_retry(workflow, session, config)

        assert result.status == ExecutionStatus.SUCCESS
        assert mock_exec.call_count == 2

    def test_retry_exhausted(self, tmp_path: Path) -> None:
        """Returns RETRY_EXHAUSTED when all attempts fail."""
        workflow = tmp_path / "workflow.md"
        workflow.write_text("# Workflow")
        session = tmp_path / "session"
        session.mkdir()
        config = make_config(tmp_path)

        with patch("worker.runner._execute_plan") as mock_exec:
            mock_exec.return_value = _result(ExecutionStatus.FAILURE, 1)
            result = run_claude_with_retry(workflow, session, config)

        assert result.status == ExecutionStatus.RETRY_EXHAUSTED
        assert mock_exec.call_count == config.max_retries

    def test_same_plan_object_across_retries(self, tmp_path: Path) -> None:
        """The identical IterationPlan is replayed on every attempt (resolve-once).

        Proves retry stability and no provider switch: the command/target cannot
        change between attempts because the same frozen object is reused.
        """
        workflow = tmp_path / "workflow.md"
        workflow.write_text("# Workflow")
        session = tmp_path / "session"
        session.mkdir()
        config = make_config(tmp_path)

        seen = []
        with patch("worker.runner._execute_plan") as mock_exec:
            mock_exec.side_effect = lambda plan, attempt, on_line: (
                seen.append(plan) or _result(ExecutionStatus.FAILURE, attempt)
            )
            run_claude_with_retry(workflow, session, config)

        assert len(seen) == config.max_retries
        assert all(p is seen[0] for p in seen)
        assert all(p.command is seen[0].command for p in seen)
        assert all(p.target.provider == config.ai_provider for p in seen)


def make_routed_config(tmp_path: Path, provider: str = "claude") -> SamocodeConfig:
    """Config with a real GlobalConfig (canonical profiles) and selected provider."""
    base = make_config(tmp_path)
    runtime = base.runtime
    if provider == "codex":
        codex = tmp_path / "codex"
        codex.touch()
        runtime = RuntimeConfig(
            ai_provider="codex",
            claude_path=base.runtime.claude_path,
            codex_path=codex,
            codex_model="",
            claude_timeout=30,
            codex_timeout=45,
            claude_max_turns=10,
            max_retries=2,
            retry_delay=0,
        )
    return SamocodeConfig(
        project=base.project,
        runtime=runtime,
        session_path=base.session_path,
        provider=provider,
        global_config=default_config(),
    )


def write_impl_session(
    tmp_path: Path, phases_md: str, *, phase: str = "implementation"
) -> Path:
    """Create a session dir with _overview.md (+ plan) for routed resolution."""
    session = tmp_path / "routed-session"
    session.mkdir()
    (session / "_overview.md").write_text(
        f"Phase: {phase}\nIteration: 3\n\n## Plans\n- plan.md - test plan\n"
    )
    (session / "plan.md").write_text("## Implementation Phases\n" + phases_md)
    return session


class TestRoutedIterationResolution:
    """Phase 8: routed ExecutionTarget resolution + session-context injection."""

    def _workflow(self, tmp_path: Path) -> Path:
        workflow = tmp_path / "workflow.md"
        workflow.write_text("# Workflow")
        return workflow

    def test_explicit_plan_profile_wins(self, tmp_path: Path) -> None:
        """An explicit `**Profile:**` on the active phase selects that profile."""
        workflow = self._workflow(tmp_path)
        session = write_impl_session(
            tmp_path, "### Phase 1: Foundation\n**Profile:** `strong`\n\n- [ ] do it\n"
        )
        config = make_routed_config(tmp_path)

        plan = resolve_iteration_plan(workflow, session, config)

        assert plan.target.profile == "strong"
        assert plan.target.model == "claude-opus-4-8"
        assert plan.target.source == ExecutionProfileSource.PLAN_PHASE_EXPLICIT
        assert plan.target.plan_phase is not None
        assert plan.target.plan_phase.phase_label == "1"

    def test_plan_phase_injected_into_context(self, tmp_path: Path) -> None:
        """Implementation context carries routing plus the selected plan phase."""
        workflow = self._workflow(tmp_path)
        session = write_impl_session(
            tmp_path, "### Phase 2: Wire it\n**Profile:** `strong`\n\n- [ ] task\n"
        )
        config = make_routed_config(tmp_path)

        plan = resolve_iteration_plan(workflow, session, config)

        ctx = plan.session_context
        assert "Execution Routing (authoritative)" in ctx
        assert "**Provider:** `claude`" in ctx
        assert "Active Implementation Plan Phase" in ctx
        assert "plan.md" in ctx
        assert "`2`" in ctx
        assert "Wire it" in ctx
        assert "strong" in ctx
        assert "plan_phase_explicit" in ctx

    def test_all_complete_falls_back_to_workflow_default(self, tmp_path: Path) -> None:
        """When every task is checked, the implementation workflow default applies."""
        workflow = self._workflow(tmp_path)
        session = write_impl_session(
            tmp_path, "### Phase 1: Done\n**Profile:** `strong`\n\n- [x] finished\n"
        )
        config = make_routed_config(tmp_path)

        plan = resolve_iteration_plan(workflow, session, config)

        assert plan.target.plan_phase is not None
        assert plan.target.plan_phase.all_complete is True
        assert plan.target.profile == "standard"
        assert plan.target.model == "claude-sonnet-4-6"
        assert plan.target.source == ExecutionProfileSource.PHASE_DEFAULT

    def test_non_implementation_has_no_plan_phase(self, tmp_path: Path) -> None:
        """Outside implementation, no plan is read and no plan block is injected."""
        workflow = self._workflow(tmp_path)
        session = write_impl_session(
            tmp_path, "### Phase 1: x\n\n- [ ] t\n", phase="investigation"
        )
        config = make_routed_config(tmp_path)

        plan = resolve_iteration_plan(workflow, session, config)

        assert plan.target.plan_phase is None
        assert plan.target.workflow_phase == Phase.INVESTIGATION
        assert plan.target.profile == "strong"  # investigation default
        assert "Active Implementation Plan Phase" not in plan.session_context
        assert "Execution Routing (authoritative)" in plan.session_context
        assert "**Workflow phase:** `investigation`" in plan.session_context
        assert "**Profile:** `strong`" in plan.session_context

    def test_no_provider_switch_uses_selected_provider(self, tmp_path: Path) -> None:
        """The resolved provider is the process-selected one, never the phase's."""
        workflow = self._workflow(tmp_path)
        agents = workflow.parent / "agents"
        agents.mkdir()
        (agents / "implementation-agent.md").write_text("# Impl")
        session = write_impl_session(
            tmp_path, "### Phase 1: x\n**Profile:** `max`\n\n- [ ] t\n"
        )
        config = make_routed_config(tmp_path, provider="codex")

        plan = resolve_iteration_plan(workflow, session, config)

        assert plan.target.provider == "codex"
        assert plan.command[0] == str(config.codex_path)
        assert plan.target.model == "gpt-5.6-sol"  # codex max

    def test_routing_log_line_emitted(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """One per-iteration routing line carries all routing fields."""
        workflow = self._workflow(tmp_path)
        session = write_impl_session(
            tmp_path, "### Phase 1: x\n**Profile:** `strong`\n\n- [ ] t\n"
        )
        config = make_routed_config(tmp_path)

        with caplog.at_level(logging.INFO, logger="samocode"):
            with patch("worker.runner._execute_plan") as mock_exec:
                mock_exec.return_value = _result(ExecutionStatus.SUCCESS, 1)
                run_claude_with_retry(workflow, session, config)

        routing = [r for r in caplog.records if r.getMessage().startswith("Routing |")]
        assert len(routing) == 1
        msg = routing[0].getMessage()
        for token in (
            "provider=claude",
            "profile=strong",
            "model=",
            "effort=",
            "workflow=implementation",
            "source=plan_phase_explicit",
        ):
            assert token in msg


class TestLegacyIterationResolution:
    """Legacy mode (no global config) synthesizes a target and stays unchanged."""

    def test_legacy_synthesizes_target(self, tmp_path: Path) -> None:
        """global_config=None yields a LEGACY target with the env model."""
        workflow = tmp_path / "workflow.md"
        workflow.write_text("# Workflow")
        session = tmp_path / "s"
        session.mkdir()
        (session / "_overview.md").write_text("Phase: implementation\nIteration: 2\n")
        config = make_config(tmp_path)  # no global_config

        plan = resolve_iteration_plan(workflow, session, config)

        assert plan.target.source == ExecutionProfileSource.LEGACY
        assert plan.target.profile == "(legacy)"
        assert plan.target.model == "opus"
        assert plan.target.plan_phase is None
        assert "Active Implementation Plan Phase" not in plan.session_context
        assert "Execution Routing (authoritative)" in plan.session_context
        assert "**Profile:** `(legacy)`" in plan.session_context
        assert "opus" in plan.command

    def test_legacy_codex_empty_model_omits_flag(self, tmp_path: Path) -> None:
        """A legacy Codex target with empty model produces no --model flag."""
        workflow = tmp_path / "workflow.md"
        workflow.write_text("# Workflow")
        agents = workflow.parent / "agents"
        agents.mkdir()
        (agents / "init-agent.md").write_text("# Init")
        session = tmp_path / "s"
        session.mkdir()
        codex = tmp_path / "codex"
        codex.touch()
        base = make_config(tmp_path)
        runtime = RuntimeConfig(
            ai_provider="codex", codex_path=codex, codex_model="", max_retries=2
        )
        config = SamocodeConfig(
            project=base.project, runtime=runtime, session_path=base.session_path
        )

        plan = resolve_iteration_plan(workflow, session, config)

        assert "--model" not in plan.command
