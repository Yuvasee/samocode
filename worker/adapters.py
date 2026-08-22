"""Provider CLI adapters and registry.

Each adapter builds a complete provider CLI argv from a resolved
`ExecutionTarget` (routing decision from `worker.routing`) plus the per-invocation
`AdapterInputs` (agent selection, injected prompt, Claude turn budget). Adapters
build argv only; child-environment safety stays centralized in
`worker.runner._execute_process`, and CLI availability checks stay in
`worker.config`.

The registry (`register_adapter`/`get_adapter`/`supported_providers`) is the
extension point for future providers and the seam startup validation reuses to
replace the hard-coded `{"claude", "codex"}` provider validation.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from worker.routing import ExecutionTarget

# === Adapter inputs ===


@dataclass(frozen=True)
class AdapterInputs:
    """Per-invocation runtime inputs an adapter needs beyond the routing decision.

    Deliberately separate from ExecutionTarget: these are not model-routing
    decisions but per-invocation runtime values (agent selection, injected
    prompt) or a provider-local knob (Claude max_turns). Each adapter reads only
    the subset it needs.
    """

    agent_name: str  # both providers
    session_context: str  # both providers (built by runner.build_session_context)
    agents_dir: Path  # Codex only: dir holding <agent_name>.md
    max_turns: int  # Claude only: --max-turns


# === Adapter protocol ===


class ProviderAdapter(Protocol):
    """Builds a full provider CLI command from a resolved ExecutionTarget."""

    name: str

    def build_command(
        self, target: ExecutionTarget, inputs: AdapterInputs
    ) -> list[str]:
        """Return the complete argv (executable first) for subprocess.Popen."""
        ...


# === Codex prompt builder (single source of truth) ===


def build_codex_agent_prompt(
    agent_name: str, session_context: str, agents_dir: Path
) -> str:
    """Full Codex prompt: session context + execution-mode banner + agent spec.

    Raises ValueError if <agent_name>.md is absent under agents_dir.
    """
    agent_path = agents_dir / f"{agent_name}.md"
    if not agent_path.exists():
        raise ValueError(f"Agent prompt not found for codex mode: {agent_path}")

    agent_instructions = agent_path.read_text().strip()
    return (
        f"{session_context}\n\n"
        "## Execution Mode\n"
        "You are running in samocode codex-provider mode. Execute one full phase iteration.\n"
        "Follow the agent spec exactly. The agent frontmatter was written for Claude Code; "
        "treat unavailable tool/model names as role metadata and use your available Codex "
        "tools instead. Update session files and write the "
        "final `_signal.json` status before exiting.\n\n"
        f"## Agent Spec: {agent_name}\n"
        f"{agent_instructions}\n"
    )


# === Built-in adapters ===


class ClaudeAdapter:
    """Claude CLI adapter: native --agent loading and --append-system-prompt."""

    name = "claude"

    def build_command(
        self, target: ExecutionTarget, inputs: AdapterInputs
    ) -> list[str]:
        args = [
            str(target.executable),
            "--dangerously-skip-permissions",
            "--model",
            target.model,
        ]
        if target.effort is not None:
            args += ["--effort", target.effort]
        args += [
            "--max-turns",
            str(inputs.max_turns),
            "--verbose",
            "--output-format",
            "stream-json",
            "--agent",
            inputs.agent_name,
            "--append-system-prompt",
            inputs.session_context,
            "-p",
            "Start",
        ]
        return args


class CodexAdapter:
    """Codex CLI adapter: agent-instruction prompt injection.

    Resolved effort becomes a one-off `-c model_reasoning_effort=<effort>`
    override, authoritative over any provider/config default reasoning effort.
    """

    name = "codex"

    def build_command(
        self, target: ExecutionTarget, inputs: AdapterInputs
    ) -> list[str]:
        args = [
            str(target.executable),
            "exec",
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
        ]
        if target.model:
            args += ["--model", target.model]
        if target.effort is not None:
            args += ["-c", f"model_reasoning_effort={target.effort}"]
        args.append(
            build_codex_agent_prompt(
                inputs.agent_name, inputs.session_context, inputs.agents_dir
            )
        )
        return args


# === Registry ===

_ADAPTERS: dict[str, ProviderAdapter] = {}


def register_adapter(adapter: ProviderAdapter) -> None:
    """Register a provider adapter. Raises ValueError on duplicate name."""
    if adapter.name in _ADAPTERS:
        raise ValueError(f"adapter already registered: {adapter.name!r}")
    _ADAPTERS[adapter.name] = adapter


def get_adapter(name: str) -> ProviderAdapter:
    """Look up a registered adapter. Raises KeyError if unknown."""
    try:
        return _ADAPTERS[name]
    except KeyError:
        raise KeyError(
            f"no adapter for provider {name!r} (registered: {sorted(_ADAPTERS)})"
        ) from None


def supported_providers() -> frozenset[str]:
    """Provider names with a registered adapter."""
    return frozenset(_ADAPTERS)


register_adapter(ClaudeAdapter())
register_adapter(CodexAdapter())
