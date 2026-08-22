"""Tests for provider adapters and the adapter registry."""

import os
from pathlib import Path

import pytest

from worker.adapters import (
    AdapterInputs,
    ClaudeAdapter,
    CodexAdapter,
    ProviderAdapter,
    build_codex_agent_prompt,
    get_adapter,
    register_adapter,
    supported_providers,
)
from worker.phases import Phase
from worker.routing import ExecutionProfileSource, ExecutionTarget


def make_target(
    *,
    provider: str = "claude",
    model: str = "claude-opus-4-8",
    effort: str | None = "xhigh",
    executable: Path = Path("/opt/claude"),
) -> ExecutionTarget:
    """Build a real ExecutionTarget (no mocks)."""
    return ExecutionTarget(
        provider=provider,
        profile="max",
        model=model,
        effort=effort,
        executable=executable,
        timeout=1800,
        workflow_phase=Phase.IMPLEMENTATION,
        plan_phase=None,
        source=ExecutionProfileSource.PHASE_DEFAULT,
    )


def make_inputs(agents_dir: Path, *, max_turns: int = 300) -> AdapterInputs:
    return AdapterInputs(
        agent_name="init-agent",
        session_context="SESSION CONTEXT",
        agents_dir=agents_dir,
        max_turns=max_turns,
    )


@pytest.fixture
def agents_dir(tmp_path: Path) -> Path:
    d = tmp_path / "agents"
    d.mkdir()
    (d / "init-agent.md").write_text("# Init Agent\nDo init work.")
    return d


# === Claude argv ===


class TestClaudeArgv:
    def test_effort_present(self, agents_dir: Path) -> None:
        target = make_target(effort="xhigh", executable=Path("/opt/claude"))
        args = ClaudeAdapter().build_command(target, make_inputs(agents_dir))
        assert args == [
            "/opt/claude",
            "--dangerously-skip-permissions",
            "--model",
            "claude-opus-4-8",
            "--effort",
            "xhigh",
            "--max-turns",
            "300",
            "--verbose",
            "--output-format",
            "stream-json",
            "--agent",
            "init-agent",
            "--append-system-prompt",
            "SESSION CONTEXT",
            "-p",
            "Start",
        ]

    def test_effort_absent(self, agents_dir: Path) -> None:
        target = make_target(effort=None)
        args = ClaudeAdapter().build_command(target, make_inputs(agents_dir))
        assert "--effort" not in args
        # everything else preserved
        assert args[:4] == [
            str(target.executable),
            "--dangerously-skip-permissions",
            "--model",
            "claude-opus-4-8",
        ]
        assert args[-2:] == ["-p", "Start"]

    def test_effort_immediately_after_model(self, agents_dir: Path) -> None:
        args = ClaudeAdapter().build_command(
            make_target(effort="high"), make_inputs(agents_dir)
        )
        i = args.index("--model")
        assert args[i + 2] == "--effort"
        assert args[i + 3] == "high"

    def test_model_authority(self, agents_dir: Path) -> None:
        target = make_target(model="custom-model-x")
        args = ClaudeAdapter().build_command(target, make_inputs(agents_dir))
        assert args[args.index("--model") + 1] == "custom-model-x"

    def test_native_agent_loading(self, agents_dir: Path) -> None:
        args = ClaudeAdapter().build_command(make_target(), make_inputs(agents_dir))
        assert args[args.index("--agent") + 1] == "init-agent"

    def test_max_turns_from_inputs(self, agents_dir: Path) -> None:
        args = ClaudeAdapter().build_command(
            make_target(), make_inputs(agents_dir, max_turns=42)
        )
        assert args[args.index("--max-turns") + 1] == "42"

    def test_session_context_injected(self, agents_dir: Path) -> None:
        args = ClaudeAdapter().build_command(make_target(), make_inputs(agents_dir))
        assert args[args.index("--append-system-prompt") + 1] == "SESSION CONTEXT"

    def test_executable_from_target(self, agents_dir: Path) -> None:
        target = make_target(executable=Path("/custom/bin/claude"))
        args = ClaudeAdapter().build_command(target, make_inputs(agents_dir))
        assert args[0] == "/custom/bin/claude"


# === Codex argv ===


class TestCodexArgv:
    def test_effort_present(self, agents_dir: Path) -> None:
        target = make_target(
            provider="codex",
            model="gpt-5.6-sol",
            effort="xhigh",
            executable=Path("/opt/codex"),
        )
        args = CodexAdapter().build_command(target, make_inputs(agents_dir))
        assert args[:7] == [
            "/opt/codex",
            "exec",
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
            "--model",
            "gpt-5.6-sol",
            "-c",
        ]
        assert args[7] == "model_reasoning_effort=xhigh"

    def test_effort_absent(self, agents_dir: Path) -> None:
        target = make_target(provider="codex", model="gpt-5.6-terra", effort=None)
        args = CodexAdapter().build_command(target, make_inputs(agents_dir))
        assert "-c" not in args
        assert "--model" in args
        assert args[args.index("--model") + 1] == "gpt-5.6-terra"

    def test_model_always_present(self, agents_dir: Path) -> None:
        args = CodexAdapter().build_command(
            make_target(provider="codex", model="gpt-5.6-terra", effort=None),
            make_inputs(agents_dir),
        )
        assert args.count("--model") == 1

    def test_effort_before_prompt(self, agents_dir: Path) -> None:
        args = CodexAdapter().build_command(
            make_target(provider="codex", model="gpt-5.6-sol", effort="medium"),
            make_inputs(agents_dir),
        )
        effort_idx = args.index("model_reasoning_effort=medium")
        assert effort_idx < len(args) - 1  # prompt is after

    def test_prompt_is_last_and_contains_spec(self, agents_dir: Path) -> None:
        args = CodexAdapter().build_command(
            make_target(provider="codex", model="gpt-5.6-sol", effort="medium"),
            make_inputs(agents_dir),
        )
        prompt = args[-1]
        assert "## Agent Spec: init-agent" in prompt
        assert "SESSION CONTEXT" in prompt
        assert "## Execution Mode" in prompt

    def test_missing_agent_file_raises(self, tmp_path: Path) -> None:
        empty = tmp_path / "agents"
        empty.mkdir()
        with pytest.raises(ValueError, match="Agent prompt not found"):
            CodexAdapter().build_command(
                make_target(provider="codex", model="gpt-5.6-sol", effort=None),
                make_inputs(empty),
            )


# === Registry ===


class TestRegistry:
    def test_get_builtin_adapters(self) -> None:
        assert isinstance(get_adapter("claude"), ClaudeAdapter)
        assert isinstance(get_adapter("codex"), CodexAdapter)

    def test_get_unknown_raises(self) -> None:
        with pytest.raises(KeyError, match="no adapter for provider 'gemini'"):
            get_adapter("gemini")

    def test_supported_providers(self) -> None:
        assert supported_providers() >= frozenset({"claude", "codex"})

    def test_register_and_lookup(self) -> None:
        class FakeAdapter:
            name = "fake-test-provider"

            def build_command(
                self, target: ExecutionTarget, inputs: AdapterInputs
            ) -> list[str]:
                return [str(target.executable)]

        adapter: ProviderAdapter = FakeAdapter()
        register_adapter(adapter)
        try:
            assert get_adapter("fake-test-provider") is adapter
            assert "fake-test-provider" in supported_providers()
        finally:
            from worker import adapters as adapters_mod

            adapters_mod._ADAPTERS.pop("fake-test-provider", None)

    def test_duplicate_registration_raises(self) -> None:
        with pytest.raises(ValueError, match="already registered"):
            register_adapter(ClaudeAdapter())


# === Environment safety ===


class TestEnvironmentSafety:
    def test_build_command_does_not_mutate_env(self, agents_dir: Path) -> None:
        before = dict(os.environ)
        ClaudeAdapter().build_command(make_target(), make_inputs(agents_dir))
        CodexAdapter().build_command(
            make_target(provider="codex", model="gpt-5.6-sol", effort=None),
            make_inputs(agents_dir),
        )
        assert dict(os.environ) == before


# === Backward compat ===


class TestBackwardCompat:
    def test_runner_prompt_delegates(self, agents_dir: Path) -> None:
        from worker.runner import _build_codex_prompt

        workflow_path = agents_dir.parent / "workflow.md"
        direct = build_codex_agent_prompt("init-agent", "CTX", agents_dir)
        via_runner = _build_codex_prompt("init-agent", "CTX", workflow_path)
        assert direct == via_runner
