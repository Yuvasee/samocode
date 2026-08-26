"""AI CLI execution with proper error handling and retries."""

import logging
import os
import re
import select
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import IO, TextIO

from .adapters import AdapterInputs, build_codex_agent_prompt, get_adapter
from .config import SamocodeConfig
from .phases import Phase, get_agent_for_phase
from .routing import (
    ExecutionProfileSource,
    ExecutionTarget,
    resolve_execution_target,
)
from .signals import OVERVIEW_FILENAME
from .timestamps import (
    file_timestamp,
    iteration_timestamp,
    jsonl_timestamp,
    log_timestamp,
)

logger = logging.getLogger("samocode")


class SessionStructureError(Exception):
    """Raised when session has invalid structure (e.g., nested _samocode subfolder)."""

    pass


def validate_session_structure(session_path: Path) -> list[str]:
    """Validate session folder structure. Returns list of warnings.

    Raises SessionStructureError for critical issues.

    Valid structure: session files directly in session_path
    Invalid structure: nested _samocode subfolder (deprecated pattern)
    """
    warnings: list[str] = []

    # Check for nested _samocode subfolder (invalid pattern)
    nested_samocode = session_path / "_samocode"
    if nested_samocode.exists() and nested_samocode.is_dir():
        nested_overview = nested_samocode / OVERVIEW_FILENAME
        root_overview = session_path / OVERVIEW_FILENAME

        if nested_overview.exists():
            if root_overview.exists():
                raise SessionStructureError(
                    f"CRITICAL: Duplicate _overview.md found at both "
                    f"{root_overview} and {nested_overview}. "
                    f"The nested _samocode/ pattern is deprecated. "
                    f"Migration required: Move files from {nested_samocode} to "
                    f"{session_path} and remove the _samocode/ subfolder."
                )
            else:
                raise SessionStructureError(
                    f"CRITICAL: Session uses deprecated nested _samocode/ structure. "
                    f"Migration required: Move files from {nested_samocode} to "
                    f"{session_path} and remove the _samocode/ subfolder."
                )

    return warnings


class ExecutionStatus(Enum):
    """Result of AI CLI execution."""

    SUCCESS = "success"
    TIMEOUT = "timeout"
    FAILURE = "failure"
    RETRY_EXHAUSTED = "retry_exhausted"


@dataclass
class ExecutionResult:
    """Result of running AI CLI."""

    status: ExecutionStatus
    stdout: str
    stderr: str
    returncode: int | None
    attempt: int
    log_file: Path | None = field(default=None)


# =============================================================================
# Public API - Main execution functions
# =============================================================================


@dataclass(frozen=True)
class IterationPlan:
    """Every routing decision for one orchestration iteration, resolved once.

    `run_ai_with_retry` builds this before the retry loop; each attempt replays
    the identical object. Because `target` and `command` are fixed here, a retry
    cannot re-resolve, switch provider/model, or advance the plan phase.
    """

    target: ExecutionTarget  # routed target, or synthesized legacy target
    provider_name: str
    agent_name: str
    phase: str
    iteration: int
    session_path: Path
    working_dir: Path
    session_context: str
    command: list[str]
    timeout: int


@dataclass(frozen=True)
class EscalationContext:
    """One escalated replay of a failed testing iteration: `base` failed, `target`
    is one rung up. The orchestrator decides budget and rung; the runner only
    routes and injects the recovery contract."""

    base: ExecutionTarget
    target: ExecutionTarget
    blocker_reason: str
    previous_report: Path | None
    attempt: int
    max_attempts: int


def resolve_iteration_plan(
    workflow_prompt_path: Path,
    session_path: Path,
    config: SamocodeConfig,
    initial_dive: str | None = None,
    initial_task: str | None = None,
    escalation: EscalationContext | None = None,
) -> IterationPlan:
    """Resolve the single immutable plan for one orchestration iteration.

    Determines phase/agent (new session -> init-agent; else current phase),
    working dir, the execution target (routed via global config, or a synthesized
    legacy target), the frozen session context, and the full provider argv built
    through the adapter registry.

    With `escalation`, `escalation.target` is used verbatim and its recovery
    contract is injected into session context.

    Raises:
        SessionStructureError: deprecated nested _samocode structure.
        ValueError: unknown phase without an agent, or missing MAIN_REPO.
        PlanResolutionError/GlobalConfigError/ExecutionResolutionError: routed
            resolution failed (fail-fast, before any model call).
    """
    for warning in validate_session_structure(session_path):
        logger.warning(warning)

    if not (session_path / OVERVIEW_FILENAME).exists():
        phase: str | None = "init"
        iteration: int | None = 1
        agent_name: str | None = "init-agent"
        logger.info("New session detected, using init-agent")
    else:
        phase = extract_phase(session_path)
        iteration = extract_iteration(session_path)
        agent_name = get_agent_for_phase(phase)
        if agent_name is None:
            raise ValueError(
                f"Unknown phase '{phase}' has no agent. "
                f"Valid phases: {', '.join(p.value for p in Phase)}"
            )

    # Never parse Working Dir from _overview.md - it's AI-generated and unreliable.
    if config.repo_path is None:
        raise ValueError(
            "MAIN_REPO is required. Either:\n"
            "  1. Pass --repo /path to the orchestrator, or\n"
            "  2. Set MAIN_REPO in .samocode file"
        )
    working_dir = resolve_working_dir(config, session_path, phase)
    logger.info(f"Working Dir: {working_dir}")
    logger.info(f"Using agent: {agent_name} (phase: {phase})")

    target = (
        escalation.target
        if escalation is not None
        else _resolve_target(config, phase, session_path)
    )
    session_context = build_session_context(
        workflow_prompt_path=workflow_prompt_path,
        session_path=session_path,
        config=config,
        phase=phase,
        iteration=iteration,
        initial_dive=initial_dive,
        initial_task=initial_task,
        target=target,
        escalation=escalation,
    )
    inputs = AdapterInputs(
        agent_name=agent_name,
        session_context=session_context,
        agents_dir=workflow_prompt_path.parent / "agents",
        max_turns=config.claude_max_turns,
    )
    command = get_adapter(target.provider).build_command(target, inputs)

    return IterationPlan(
        target=target,
        provider_name=target.provider,
        agent_name=agent_name,
        phase=phase or "unknown",
        iteration=iteration or 1,
        session_path=session_path,
        working_dir=working_dir,
        session_context=session_context,
        command=command,
        timeout=target.timeout,
    )


def run_ai_with_retry(
    workflow_prompt_path: Path,
    session_path: Path,
    config: SamocodeConfig,
    initial_dive: str | None = None,
    initial_task: str | None = None,
    on_line: Callable[[str], None] | None = None,
    escalation: EscalationContext | None = None,
) -> ExecutionResult:
    """Execute configured AI CLI with retry logic for transient failures.

    Resolves the iteration plan ONCE, then replays the same plan on every attempt
    so a retry never re-resolves the provider, model, or plan phase; an
    `escalation` pins every retry to the escalated target.
    """
    plan = resolve_iteration_plan(
        workflow_prompt_path,
        session_path,
        config,
        initial_dive,
        initial_task,
        escalation,
    )
    _log_iteration_target(plan)

    result: ExecutionResult | None = None
    for attempt in range(1, config.max_retries + 1):
        result = _execute_plan(plan, attempt, on_line)

        if result.status == ExecutionStatus.SUCCESS:
            return result

        if attempt < config.max_retries:
            logger.warning(
                f"Attempt {attempt}/{config.max_retries} failed, "
                f"retrying in {config.retry_delay}s..."
            )
            time.sleep(config.retry_delay)

    logger.error(f"All {config.max_retries} attempts failed")
    return _retry_exhausted(result, config.max_retries)


def run_ai_once(
    workflow_prompt_path: Path,
    session_path: Path,
    config: SamocodeConfig,
    attempt: int,
    initial_dive: str | None = None,
    initial_task: str | None = None,
    on_line: Callable[[str], None] | None = None,
    escalation: EscalationContext | None = None,
) -> ExecutionResult:
    """Resolve the iteration plan and execute it once.

    Kept as a standalone entrypoint for direct callers; the retry loop uses
    `resolve_iteration_plan` + `_execute_plan` so it resolves exactly once.
    """
    plan = resolve_iteration_plan(
        workflow_prompt_path,
        session_path,
        config,
        initial_dive,
        initial_task,
        escalation,
    )
    return _execute_plan(plan, attempt, on_line)


def run_claude_with_retry(
    workflow_prompt_path: Path,
    session_path: Path,
    config: SamocodeConfig,
    initial_dive: str | None = None,
    initial_task: str | None = None,
    on_line: Callable[[str], None] | None = None,
    escalation: EscalationContext | None = None,
) -> ExecutionResult:
    """Backward-compatible alias for run_ai_with_retry."""
    return run_ai_with_retry(
        workflow_prompt_path,
        session_path,
        config,
        initial_dive,
        initial_task,
        on_line,
        escalation,
    )


def run_claude_once(
    workflow_prompt_path: Path,
    session_path: Path,
    config: SamocodeConfig,
    attempt: int,
    initial_dive: str | None = None,
    initial_task: str | None = None,
    on_line: Callable[[str], None] | None = None,
    escalation: EscalationContext | None = None,
) -> ExecutionResult:
    """Backward-compatible alias for run_ai_once."""
    return run_ai_once(
        workflow_prompt_path,
        session_path,
        config,
        attempt,
        initial_dive,
        initial_task,
        on_line,
        escalation,
    )


def _execute_plan(
    plan: IterationPlan,
    attempt: int,
    on_line: Callable[[str], None] | None,
) -> ExecutionResult:
    """Run the frozen plan's command once with timeout protection and streaming."""
    logger.info(f"Executing {plan.provider_name} CLI (attempt {attempt})...")
    log_file = generate_log_filename(plan.session_path, plan.phase, plan.iteration)
    logger.info(f"Streaming logs to: {log_file}")
    return _execute_process(
        cli_args=plan.command,
        working_dir=plan.working_dir,
        log_file=log_file,
        timeout=plan.timeout,
        attempt=attempt,
        provider_name=plan.provider_name,
        on_line=on_line,
    )


def _retry_exhausted(last: ExecutionResult | None, max_retries: int) -> ExecutionResult:
    """Build the RETRY_EXHAUSTED result from the last attempt (or none)."""
    if last is None:
        return ExecutionResult(
            status=ExecutionStatus.RETRY_EXHAUSTED,
            stdout="",
            stderr="No attempts made",
            returncode=None,
            attempt=0,
            log_file=None,
        )
    return ExecutionResult(
        status=ExecutionStatus.RETRY_EXHAUSTED,
        stdout=last.stdout,
        stderr=last.stderr,
        returncode=last.returncode,
        attempt=max_retries,
        log_file=last.log_file,
    )


def resolve_working_dir(
    config: SamocodeConfig, session_path: Path, phase: str | None
) -> Path:
    """Worktree if it exists (and not init), else the main repo."""
    worktree_path = config.worktrees_dir / session_path.name
    if phase == "init" or not worktree_path.exists():
        return config.repo_path
    return worktree_path


def _resolve_target(
    config: SamocodeConfig, phase: str | None, session_path: Path
) -> ExecutionTarget:
    """Routed target from the global config, or a synthesized legacy target."""
    phase_enum = Phase((phase or "init").lower())
    if config.global_config is not None:
        return resolve_execution_target(
            provider_name=config.ai_provider,
            workflow_phase=phase_enum,
            session_dir=session_path,
            config=config.global_config,
            runtime=config.runtime,
        )
    return _legacy_target(config, phase_enum)


def _legacy_target(config: SamocodeConfig, phase_enum: Phase) -> ExecutionTarget:
    """Synthesize an ExecutionTarget from legacy env settings (no global config).

    Lets legacy and routed modes share the one adapter-driven execution path.
    """
    if config.ai_provider == "claude":
        model, executable, timeout = (
            config.claude_model,
            config.claude_path,
            config.claude_timeout,
        )
    else:
        model, executable, timeout = (
            config.codex_model,
            config.codex_path,
            config.codex_timeout,
        )
    return ExecutionTarget(
        provider=config.ai_provider,
        profile="(legacy)",
        model=model,
        effort=None,
        executable=executable,
        timeout=timeout,
        workflow_phase=phase_enum,
        plan_phase=None,
        source=ExecutionProfileSource.LEGACY,
    )


def _log_iteration_target(plan: IterationPlan) -> None:
    """Emit the one routing log line for this iteration."""
    target = plan.target
    pp = target.plan_phase
    plan_label = f"{pp.phase_label}:{pp.phase_title}" if pp and pp.phase_label else "-"
    message = (
        f"Routing | provider={target.provider} profile={target.profile} "
        f"model={target.model} effort={target.effort or '-'} "
        f"workflow={target.workflow_phase.value} plan={plan_label} "
        f"source={target.source.value}"
    )
    if target.escalated_from is not None:
        message += f" escalated_from={target.escalated_from}"
    logger.info(message)


# =============================================================================
# Overview extraction utilities
# =============================================================================


def extract_phase(session_path: Path) -> str | None:
    """Extract Phase from session _overview.md Status section."""
    content = _read_overview(session_path)
    if content is None:
        return None

    match = re.search(r"^Phase:\s*(.+)$", content, re.MULTILINE)
    return match.group(1).strip() if match else None


def extract_iteration(session_path: Path) -> int | None:
    """Extract Iteration from session _overview.md Status section."""
    content = _read_overview(session_path)
    if content is None:
        return None

    match = re.search(r"^Iteration:\s*(\d+)$", content, re.MULTILINE)
    return int(match.group(1)) if match else None


def extract_total_iterations(session_path: Path) -> int:
    """Extract Total Iterations from session _overview.md."""
    content = _read_overview(session_path)
    if content is None:
        return 0

    match = re.search(r"^Total Iterations:\s*(\d+)$", content, re.MULTILINE)
    return int(match.group(1)) if match else 0


def latest_test_report(session_path: Path) -> Path | None:
    """Newest `*-test-report.md`; names start with `MM-DD-HH:MM`, so a descending
    name sort is chronological."""
    reports = sorted(
        session_path.glob("*-test-report.md"), key=lambda p: p.name, reverse=True
    )
    return reports[0] if reports else None


def increment_total_iterations(session_path: Path) -> int:
    """Increment Total Iterations in _overview.md, return new value.

    If Total Iterations line doesn't exist, adds it after Iteration line.
    """
    overview_path = session_path / OVERVIEW_FILENAME
    if not overview_path.exists():
        return 1

    content = overview_path.read_text()

    # Try to find and increment existing counter
    match = re.search(r"^(Total Iterations:\s*)(\d+)$", content, re.MULTILINE)
    if match:
        current = int(match.group(2))
        new_value = current + 1
        new_content = (
            content[: match.start(2)] + str(new_value) + content[match.end(2) :]
        )
        overview_path.write_text(new_content)
        return new_value

    # Add Total Iterations after Iteration line
    iteration_match = re.search(r"^(Iteration:\s*\d+)$", content, re.MULTILINE)
    if iteration_match:
        insert_pos = iteration_match.end()
        new_content = (
            content[:insert_pos] + "\nTotal Iterations: 1" + content[insert_pos:]
        )
        overview_path.write_text(new_content)
        return 1

    return 1


# =============================================================================
# Context and prompt building
# =============================================================================


def build_session_context(
    workflow_prompt_path: Path,
    session_path: Path,
    config: SamocodeConfig,
    phase: str | None = None,
    iteration: int | None = None,
    initial_dive: str | None = None,
    initial_task: str | None = None,
    target: ExecutionTarget | None = None,
    escalation: EscalationContext | None = None,
) -> str:
    """Build session context for --append-system-prompt injection.

    Includes workflow.md (common context for all phases), the immutable execution
    target, and session-specific details. When `target` carries a plan phase, that
    active plan selection is injected as an additional implementation contract;
    an `escalation` adds the `## Escalated Testing Attempt` section.
    """
    # Start with workflow.md - common context for all phases
    lines = [workflow_prompt_path.read_text().strip()]

    # Add session-specific context
    lines.append("\n\n# Session Context")
    lines.append(f"**Session path:** {session_path}")

    # Determine working directory: worktree if exists, else main repo
    # init phase needs main repo to create worktree FROM there
    session_name = session_path.name
    worktree_path = config.worktrees_dir / session_name
    if phase == "init":
        working_dir = config.repo_path
    elif worktree_path.exists():
        working_dir = worktree_path
    else:
        working_dir = config.repo_path

    if working_dir:
        lines.append(f"**Working directory:** {working_dir}")

    if phase:
        lines.append(f"**Phase:** {phase}")
    if iteration:
        lines.append(f"**Iteration:** {iteration}")

    # Add time limit so agent knows constraints; prefer the resolved target timeout.
    timeout = target.timeout if target is not None else config.ai_timeout
    lines.append(f"**Time limit:** {timeout}s ({timeout // 60} min)")

    # Add injected timestamps section
    lines.append("")
    lines.append("## Injected Timestamps")
    lines.append(f"- `TIMESTAMP_FILE`: `{file_timestamp()}`")
    lines.append(f"- `TIMESTAMP_LOG`: `{log_timestamp()}`")
    if iteration is not None:
        lines.append(f"- `TIMESTAMP_ITERATION`: `{iteration_timestamp(iteration)}`")
    else:
        lines.append(f"- `TIMESTAMP_ITERATION`: `{iteration_timestamp(1)}`")

    lines.append("")
    lines.extend(_build_config_section(session_path, config))

    if target is not None:
        lines.append("")
        lines.extend(_build_execution_routing_section(target))

    if target is not None and target.plan_phase is not None:
        lines.append("")
        lines.extend(_build_plan_phase_section(target))

    if escalation is not None:
        lines.append("")
        lines.extend(_build_escalation_section(escalation))

    if initial_dive or initial_task:
        lines.append("")
        lines.extend(_build_initial_instructions(initial_dive, initial_task))

    return "\n".join(lines)


def _build_execution_routing_section(target: ExecutionTarget) -> list[str]:
    return [
        "## Execution Routing (authoritative)",
        f"- **Provider:** `{target.provider}`",
        f"- **Profile:** `{target.profile}`",
        f"- **Model:** `{target.model or 'provider default'}`",
        f"- **Effort:** `{target.effort or 'provider default'}`",
        f"- **Workflow phase:** `{target.workflow_phase.value}`",
        f"- **Profile source:** `{target.source.value}`",
        "Do not re-resolve or override provider, profile, model, or effort in this iteration.",
    ]


def _build_plan_phase_section(target: ExecutionTarget) -> list[str]:
    """Active implementation-plan phase context (implementation iterations only)."""
    plan = target.plan_phase
    assert plan is not None
    lines = [
        "## Active Implementation Plan Phase",
        f"- **Plan file:** `{plan.plan_path}`",
    ]
    if plan.all_complete:
        lines.append(
            "- **Status:** all plan tasks complete; perform the outer workflow "
            "transition (testing/quality) per workflow.md."
        )
    else:
        lines.append(f"- **Plan phase:** `{plan.phase_label}` — {plan.phase_title}")
    lines.append("Execute exactly this phase; do not independently pick another.")
    return lines


_RECOVERY_CONTRACT: tuple[str, ...] = (
    "**Recovery contract for this escalated attempt:**",
    "- Separate a product failure (the change under test is genuinely wrong) from "
    "an environment failure (the harness, tooling, or setup is wrong). They need "
    "different fixes, and only a product failure is a real test result.",
    "- Before improvising with ad-hoc paths or environment variables, inspect the "
    "project's own test environment and use it as intended: service-specific "
    "virtualenvs, dev containers, editable installs, already-installed browser "
    "binaries, and setup docs.",
    "- Discover and apply the project's own skills and docs for local development, "
    "testing, and frontend verification whenever they are present.",
    "- You may change temporary, untracked, or user-level configuration; you may "
    "not modify tracked project files; and you commit nothing.",
    "- Confirm any remaining blocker with reproducible commands, and record those "
    "commands in the report.",
    "- Produce a complete test report covering every gate, not only the gate that "
    "first failed.",
    "- An environment failure is never a PASS, and mandatory browser E2E is never "
    "skipped silently. Signal blocked for a human decision instead of deferring.",
)


def _build_escalation_section(escalation: EscalationContext) -> list[str]:
    base = escalation.base
    target = escalation.target
    lines = [
        "## Escalated Testing Attempt",
        f"- **Attempt:** {escalation.attempt} of {escalation.max_attempts}",
        f"- **Base profile:** `{base.profile}` "
        f"(model `{base.model or 'provider default'}`, "
        f"effort `{base.effort or 'provider default'}`)",
        f"- **Escalated profile:** `{target.profile}` "
        f"(model `{target.model or 'provider default'}`, "
        f"effort `{target.effort or 'provider default'}`)",
        f"- **Blocker reason:** {escalation.blocker_reason}",
    ]
    if escalation.previous_report is not None:
        lines.append(f"- **Previous test report:** `{escalation.previous_report}`")
    else:
        lines.append("- **Previous test report:** none recorded")
    lines.append("")
    lines.extend(_RECOVERY_CONTRACT)
    return lines


def generate_log_filename(
    session_path: Path, phase: str | None, iteration: int | None = None
) -> Path:
    """Generate timestamped JSONL filename for this invocation.

    Format: {MM-DD-HHMM}-{NNN}-{phase}.jsonl
    Example: 01-15-1437-001-investigation.jsonl

    Logs are stored in _logs/ subfolder to keep session root clean.
    """
    timestamp = jsonl_timestamp()
    iteration_str = f"{iteration:03d}" if iteration else "000"
    phase_slug = phase.lower() if phase else "unknown"
    logs_dir = session_path / "_logs"
    return logs_dir / f"{timestamp}-{iteration_str}-{phase_slug}.jsonl"


# =============================================================================
# Log streaming
# =============================================================================


def stream_logs(
    process: subprocess.Popen[str],
    log_file: Path,
    timeout: float,
    command_name: str,
    on_line: Callable[[str], None] | None = None,
) -> tuple[str, str]:
    """Stream stdout from process to JSONL file with timeout support."""
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    deadline = time.time() + timeout

    stdout_pipe = process.stdout
    stderr_pipe = process.stderr
    if stdout_pipe is None or stderr_pipe is None:
        raise RuntimeError("Process stdout/stderr pipes not available")

    log_file.parent.mkdir(parents=True, exist_ok=True)

    with open(log_file, "w", encoding="utf-8") as f:
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(cmd=command_name, timeout=timeout)

            if process.poll() is not None:
                _drain_remaining(
                    stdout_pipe, stderr_pipe, stdout_lines, stderr_lines, f, on_line
                )
                break

            readable, _, _ = select.select(
                [stdout_pipe, stderr_pipe], [], [], min(remaining, 1.0)
            )

            for stream in readable:
                line = stream.readline()
                if line:
                    if stream is stdout_pipe:
                        stdout_lines.append(line)
                        f.write(line)
                        f.flush()
                        if on_line:
                            on_line(line)
                    else:
                        stderr_lines.append(line)

    return "".join(stdout_lines), "".join(stderr_lines)


# =============================================================================
# Private helpers
# =============================================================================


def _read_overview(session_path: Path) -> str | None:
    """Read _overview.md content, returns None if not exists."""
    overview_path = session_path / OVERVIEW_FILENAME
    if not overview_path.exists():
        return None
    return overview_path.read_text()


def _build_config_section(session_path: Path, config: SamocodeConfig) -> list[str]:
    """Build configuration section for prompts."""
    lines: list[str] = []

    session_name = session_path.name
    worktree_path = config.worktrees_dir / session_name
    branch_prefix = os.getenv("GIT_BRANCH_PREFIX", "")
    branch_name = session_name.split("-", 3)[-1]

    lines.append("## Worktree Configuration")
    lines.append(f"- Base repo (create worktrees FROM here): `{config.repo_path}`")
    lines.append(
        "- Base branch: `origin/main` or `origin/master` (detect with `git remote show origin`)"
    )
    lines.append(f"- Worktree path: `{worktree_path}`")
    if branch_prefix:
        lines.append(f"- Branch name: `{branch_prefix}/{branch_name}`")
    else:
        lines.append(f"- Branch name: `{branch_name}`")

    return lines


def _build_initial_instructions(
    initial_dive: str | None, initial_task: str | None
) -> list[str]:
    """Build initial instructions section for prompts."""
    lines = [
        "## Initial Session Data",
        "Store the following in _overview.md for later phases:",
    ]

    if initial_dive:
        lines.append(f"- **Dive topic:** {initial_dive}")
    if initial_task:
        lines.append(f"- **Task:** {initial_task}")

    lines.append("")
    lines.append(
        "**IMPORTANT**: Do NOT execute dive or task now. Only store them in _overview.md. "
        "Set Phase: investigation and signal continue. Later agents will execute these."
    )
    return lines


def _build_cli_args(
    config: SamocodeConfig,
    agent_name: str,
    session_context: str,
    workflow_prompt_path: Path,
) -> list[str]:
    """Build provider CLI arguments via the adapter registry (legacy target).

    Thin shim over the adapter registry, kept for direct callers/tests. The
    per-iteration path builds argv through `resolve_iteration_plan`; this reuses a
    synthesized legacy target so both share the one adapter code path.
    """
    target = _legacy_target(config, Phase.INIT)
    inputs = AdapterInputs(
        agent_name=agent_name,
        session_context=session_context,
        agents_dir=workflow_prompt_path.parent / "agents",
        max_turns=config.claude_max_turns,
    )
    return get_adapter(config.ai_provider).build_command(target, inputs)


def _build_codex_prompt(
    agent_name: str, session_context: str, workflow_prompt_path: Path
) -> str:
    """Backward-compat delegator to adapters.build_codex_agent_prompt."""
    return build_codex_agent_prompt(
        agent_name, session_context, workflow_prompt_path.parent / "agents"
    )


def _execute_process(
    cli_args: list[str],
    working_dir: Path,
    log_file: Path,
    timeout: int,
    attempt: int,
    provider_name: str,
    on_line: Callable[[str], None] | None,
) -> ExecutionResult:
    """Execute subprocess and return result."""
    process: subprocess.Popen[str] | None = None
    try:
        env = os.environ.copy()
        env.pop("CLAUDECODE", None)

        process = subprocess.Popen(
            cli_args,
            cwd=str(working_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )

        stdout, stderr = stream_logs(process, log_file, timeout, provider_name, on_line)
        process.wait()

        if process.returncode == 0:
            logger.info(f"{provider_name} CLI completed successfully")
            return ExecutionResult(
                status=ExecutionStatus.SUCCESS,
                stdout=stdout,
                stderr=stderr,
                returncode=process.returncode,
                attempt=attempt,
                log_file=log_file,
            )

        logger.error(f"{provider_name} CLI failed with code {process.returncode}")
        if stderr:
            logger.error(f"stderr: {stderr[:500]}")
        if stdout:
            # Log last 500 chars of stdout for debugging when stderr is empty
            logger.error(f"stdout (last 500 chars): {stdout[-500:]}")
        return ExecutionResult(
            status=ExecutionStatus.FAILURE,
            stdout=stdout,
            stderr=stderr,
            returncode=process.returncode,
            attempt=attempt,
            log_file=log_file,
        )

    except subprocess.TimeoutExpired:
        if process is not None:
            process.kill()
            process.wait()
        logger.error(f"{provider_name} CLI timed out after {timeout}s")
        return ExecutionResult(
            status=ExecutionStatus.TIMEOUT,
            stdout="",
            stderr=f"Timeout after {timeout}s",
            returncode=None,
            attempt=attempt,
            log_file=log_file,
        )

    except Exception as e:
        if process is not None:
            process.kill()
            process.wait()
        logger.error(f"{provider_name} CLI execution failed: {e}")
        return ExecutionResult(
            status=ExecutionStatus.FAILURE,
            stdout="",
            stderr=str(e),
            returncode=None,
            attempt=attempt,
            log_file=log_file if log_file.exists() else None,
        )


def _drain_remaining(
    stdout_pipe: IO[str],
    stderr_pipe: IO[str],
    stdout_lines: list[str],
    stderr_lines: list[str],
    log_file: TextIO,
    on_line: Callable[[str], None] | None,
) -> None:
    """Drain remaining output from pipes after process finishes."""
    for line in stdout_pipe:
        stdout_lines.append(line)
        log_file.write(line)
        if on_line:
            on_line(line)
    for line in stderr_pipe:
        stderr_lines.append(line)
