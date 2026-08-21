"""End-to-end model-routing integration tests (Phase 9).

Exercises the full resolve -> adapter -> subprocess path with real fake CLIs
(no mocked Popen): a routed ExecutionTarget is built, an adapter renders argv,
and a fake executable records the exact argv it received. Assertions read the
recorded argv back to prove the correct model/effort reached the CLI and that
retries replay the identical command.

Covers consecutive plan phases with different profiles under both providers,
cross-provider session resumption, legacy no-config mode, no-Profile plans,
workflow overrides, the all-complete transition, and unsupported combinations.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from worker.config import ProjectConfig, RuntimeConfig, SamocodeConfig
from worker.global_config import GlobalConfig, GlobalConfigError, default_config
from worker.plan_resolver import PlanResolutionError
from worker.routing import ExecutionResolutionError
from worker.runner import (
    ExecutionStatus,
    resolve_iteration_plan,
    run_ai_with_retry,
)

# Record separator between successive fake-CLI invocations in the argv file.
_RECORD_SEP = b"\x1e"


def make_fake_cli(script: Path, argv_out: Path, exit_code: int = 0) -> None:
    """Write an executable fake CLI that records its argv and exits `exit_code`.

    Each invocation appends NUL-separated argv plus a trailing record separator,
    so multiple calls (retries) are distinguishable and multiline arguments like
    `--append-system-prompt` survive intact.
    """
    script.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\0" "$@" >> "{argv_out}"\n'
        f'printf "\\x1e" >> "{argv_out}"\n'
        'echo "fake-cli ran"\n'
        f"exit {exit_code}\n"
    )
    script.chmod(0o755)


def read_argv_records(argv_out: Path) -> list[list[str]]:
    """Parse the fake CLI's argv file into one argv list per invocation."""
    if not argv_out.exists():
        return []
    raw = argv_out.read_bytes()
    records = [r for r in raw.split(_RECORD_SEP) if r]
    return [[a.decode() for a in rec.split(b"\0") if a] for rec in records]


def _workflow(tmp_path: Path) -> Path:
    """workflow.md plus an agents/ dir Codex prompt injection can read."""
    workflow = tmp_path / "workflow.md"
    workflow.write_text("# Workflow")
    agents = tmp_path / "agents"
    agents.mkdir(exist_ok=True)
    for name in (
        "init-agent",
        "investigation-agent",
        "implementation-agent",
        "testing-agent",
    ):
        (agents / f"{name}.md").write_text(f"# {name}\nDo the work.")
    return workflow


def _config(
    tmp_path: Path,
    provider: str,
    argv_out: Path,
    *,
    global_config: GlobalConfig | None,
    exit_code: int = 0,
) -> SamocodeConfig:
    """Routed/legacy config whose provider executables are recording fake CLIs."""
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    worktrees = tmp_path / "worktrees"
    worktrees.mkdir(exist_ok=True)
    sessions = tmp_path / "sessions"
    sessions.mkdir(exist_ok=True)

    claude = tmp_path / "claude"
    codex = tmp_path / "codex"
    make_fake_cli(claude, argv_out, exit_code)
    make_fake_cli(codex, argv_out, exit_code)

    project = ProjectConfig(main_repo=repo, worktrees=worktrees, sessions=sessions)
    runtime = RuntimeConfig(
        ai_provider=provider,
        claude_path=claude,
        claude_model="opus",
        codex_path=codex,
        codex_model="gpt-legacy",
        claude_max_turns=10,
        claude_timeout=30,
        codex_timeout=30,
        max_retries=2,
        retry_delay=0,
    )
    return SamocodeConfig(
        project=project,
        runtime=runtime,
        session_path=sessions / "unused",
        provider=provider,
        global_config=global_config,
    )


def _session(
    tmp_path: Path,
    phases_md: str,
    *,
    name: str = "25-01-01-feature",
    phase: str = "implementation",
) -> Path:
    """Session dir with _overview.md (+ plan.md) for routed resolution."""
    session = tmp_path / "sessions" / name
    session.mkdir(parents=True, exist_ok=True)
    (session / "_overview.md").write_text(
        f"Phase: {phase}\nIteration: 1\n\n## Plans\n- plan.md - test plan\n"
    )
    (session / "plan.md").write_text("## Implementation Phases\n" + phases_md)
    return session


def _flag_value(argv: list[str], flag: str) -> str | None:
    """Value following `flag` in argv, or None if the flag is absent."""
    return argv[argv.index(flag) + 1] if flag in argv else None


def _effort(argv: list[str]) -> str | None:
    """Resolved effort from either Claude `--effort` or Codex `-c` override."""
    claude = _flag_value(argv, "--effort")
    if claude is not None:
        return claude
    for arg in argv:
        if arg.startswith("model_reasoning_effort="):
            return arg.split("=", 1)[1]
    return None


class TestConsecutiveImplementationPhasesClaude:
    """Consecutive implementation phases with different profiles under Claude."""

    def test_two_phases_route_distinct_model_effort(self, tmp_path: Path) -> None:
        workflow = _workflow(tmp_path)
        argv_out = tmp_path / "argv.txt"
        config = _config(tmp_path, "claude", argv_out, global_config=default_config())
        session = _session(
            tmp_path,
            "### Phase 1: Foundation\n**Profile:** `strong`\n\n- [ ] build\n\n"
            "### Phase 2: Optimize\n**Profile:** `max`\n\n- [ ] tune\n",
        )

        result = run_ai_with_retry(workflow, session, config)
        assert result.status == ExecutionStatus.SUCCESS
        argv1 = read_argv_records(argv_out)[-1]
        assert _flag_value(argv1, "--model") == "claude-opus-4-8"
        assert _effort(argv1) == "high"

        # Complete Phase 1; the runner now routes Phase 2's `max` profile.
        (session / "plan.md").write_text(
            "## Implementation Phases\n"
            "### Phase 1: Foundation\n**Profile:** `strong`\n\n- [x] build\n\n"
            "### Phase 2: Optimize\n**Profile:** `max`\n\n- [ ] tune\n"
        )
        result = run_ai_with_retry(workflow, session, config)
        assert result.status == ExecutionStatus.SUCCESS
        argv2 = read_argv_records(argv_out)[-1]
        assert _flag_value(argv2, "--model") == "claude-opus-4-8"
        assert _effort(argv2) == "xhigh"


class TestConsecutiveImplementationPhasesCodex:
    """Same consecutive-phase routing under Codex; effort via -c override."""

    def test_two_phases_route_distinct_model_effort(self, tmp_path: Path) -> None:
        workflow = _workflow(tmp_path)
        argv_out = tmp_path / "argv.txt"
        config = _config(tmp_path, "codex", argv_out, global_config=default_config())
        session = _session(
            tmp_path,
            "### Phase 1: Foundation\n**Profile:** `strong`\n\n- [ ] build\n\n"
            "### Phase 2: Optimize\n**Profile:** `max`\n\n- [ ] tune\n",
        )

        result = run_ai_with_retry(workflow, session, config)
        assert result.status == ExecutionStatus.SUCCESS
        argv1 = read_argv_records(argv_out)[-1]
        assert argv1[0] == "exec"  # fake CLI records args only ($@), not argv[0]
        assert _flag_value(argv1, "--model") == "gpt-5.6-sol"
        assert _effort(argv1) == "medium"

        (session / "plan.md").write_text(
            "## Implementation Phases\n"
            "### Phase 1: Foundation\n**Profile:** `strong`\n\n- [x] build\n\n"
            "### Phase 2: Optimize\n**Profile:** `max`\n\n- [ ] tune\n"
        )
        result = run_ai_with_retry(workflow, session, config)
        assert result.status == ExecutionStatus.SUCCESS
        argv2 = read_argv_records(argv_out)[-1]
        assert _flag_value(argv2, "--model") == "gpt-5.6-sol"
        assert _effort(argv2) == "xhigh"


class TestRetryStability:
    """A failing fake CLI exhausts retries replaying the identical argv."""

    def test_retries_replay_same_command(self, tmp_path: Path) -> None:
        workflow = _workflow(tmp_path)
        argv_out = tmp_path / "argv.txt"
        config = _config(
            tmp_path, "claude", argv_out, global_config=default_config(), exit_code=1
        )
        session = _session(
            tmp_path, "### Phase 1: x\n**Profile:** `strong`\n\n- [ ] t\n"
        )

        result = run_ai_with_retry(workflow, session, config)

        assert result.status == ExecutionStatus.RETRY_EXHAUSTED
        records = read_argv_records(argv_out)
        assert len(records) == config.max_retries
        assert records[0] == records[1]
        assert _flag_value(records[0], "--model") == "claude-opus-4-8"


class TestCrossProviderResumption:
    """A later invocation resumes the same session under the other provider."""

    def test_same_plan_phase_other_provider(self, tmp_path: Path) -> None:
        workflow = _workflow(tmp_path)
        argv_out = tmp_path / "argv.txt"
        session = _session(
            tmp_path, "### Phase 1: Wire\n**Profile:** `strong`\n\n- [ ] t\n"
        )

        claude_cfg = _config(
            tmp_path, "claude", argv_out, global_config=default_config()
        )
        claude_plan = resolve_iteration_plan(workflow, session, claude_cfg)
        codex_cfg = _config(tmp_path, "codex", argv_out, global_config=default_config())
        codex_plan = resolve_iteration_plan(workflow, session, codex_cfg)

        # Provider/model differ; the resolved plan phase is identical.
        assert claude_plan.target.provider == "claude"
        assert claude_plan.target.model == "claude-opus-4-8"
        assert codex_plan.target.provider == "codex"
        assert codex_plan.target.model == "gpt-5.6-sol"
        assert claude_plan.target.plan_phase == codex_plan.target.plan_phase
        assert claude_plan.command[0] == str(claude_cfg.claude_path)
        assert codex_plan.command[0] == str(codex_cfg.codex_path)


class TestLegacyNoConfigExecution:
    """No global config -> legacy env-model target still executes end to end."""

    def test_legacy_uses_env_model(self, tmp_path: Path) -> None:
        workflow = _workflow(tmp_path)
        argv_out = tmp_path / "argv.txt"
        config = _config(tmp_path, "claude", argv_out, global_config=None)
        session = _session(
            tmp_path, "### Phase 1: x\n**Profile:** `strong`\n\n- [ ] t\n"
        )

        result = run_ai_with_retry(workflow, session, config)

        assert result.status == ExecutionStatus.SUCCESS
        argv = read_argv_records(argv_out)[-1]
        assert _flag_value(argv, "--model") == "opus"
        assert _effort(argv) is None


class TestNoProfilePlan:
    """A plan phase omitting `**Profile:**` falls back to implementation default."""

    def test_falls_back_to_standard(self, tmp_path: Path) -> None:
        workflow = _workflow(tmp_path)
        argv_out = tmp_path / "argv.txt"
        config = _config(tmp_path, "claude", argv_out, global_config=default_config())
        session = _session(tmp_path, "### Phase 1: x\n\n- [ ] t\n")

        result = run_ai_with_retry(workflow, session, config)

        assert result.status == ExecutionStatus.SUCCESS
        argv = read_argv_records(argv_out)[-1]
        assert _flag_value(argv, "--model") == "claude-sonnet-4-6"
        assert _effort(argv) == "high"


class TestWorkflowOverride:
    """A [workflow_overrides] entry beats the phase default for a no-Profile plan."""

    def test_override_wins(self, tmp_path: Path) -> None:
        workflow = _workflow(tmp_path)
        argv_out = tmp_path / "argv.txt"
        gc = replace(default_config(), workflow_overrides={"implementation": "max"})
        config = _config(tmp_path, "claude", argv_out, global_config=gc)
        session = _session(tmp_path, "### Phase 1: x\n\n- [ ] t\n")

        plan = resolve_iteration_plan(workflow, session, config)

        assert plan.target.profile == "max"
        assert plan.target.model == "claude-opus-4-8"
        assert plan.target.effort == "xhigh"


class TestAllCompleteTransition:
    """All plan tasks checked -> workflow default + transition context."""

    def test_all_complete_falls_back(self, tmp_path: Path) -> None:
        workflow = _workflow(tmp_path)
        argv_out = tmp_path / "argv.txt"
        config = _config(tmp_path, "claude", argv_out, global_config=default_config())
        session = _session(
            tmp_path, "### Phase 1: Done\n**Profile:** `strong`\n\n- [x] finished\n"
        )

        plan = resolve_iteration_plan(workflow, session, config)

        assert plan.target.plan_phase is not None
        assert plan.target.plan_phase.all_complete is True
        assert plan.target.model == "claude-sonnet-4-6"
        assert "all plan tasks complete" in plan.session_context

        result = run_ai_with_retry(workflow, session, config)
        assert result.status == ExecutionStatus.SUCCESS
        argv = read_argv_records(argv_out)[-1]
        assert _flag_value(argv, "--model") == "claude-sonnet-4-6"


class TestUnsupportedCombinations:
    """Fail-fast before any model call for invalid provider/profile combinations."""

    def test_unsupported_provider_raises(self, tmp_path: Path) -> None:
        workflow = _workflow(tmp_path)
        argv_out = tmp_path / "argv.txt"
        config = _config(tmp_path, "gemini", argv_out, global_config=default_config())
        session = _session(
            tmp_path, "### Phase 1: x\n**Profile:** `strong`\n\n- [ ] t\n"
        )

        with pytest.raises(ExecutionResolutionError):
            resolve_iteration_plan(workflow, session, config)
        assert read_argv_records(argv_out) == []

    def test_invalid_plan_profile_raises(self, tmp_path: Path) -> None:
        workflow = _workflow(tmp_path)
        argv_out = tmp_path / "argv.txt"
        config = _config(tmp_path, "claude", argv_out, global_config=default_config())
        session = _session(
            tmp_path, "### Phase 1: x\n**Profile:** `nonesuch`\n\n- [ ] t\n"
        )

        with pytest.raises((GlobalConfigError, PlanResolutionError)):
            resolve_iteration_plan(workflow, session, config)
        assert read_argv_records(argv_out) == []
