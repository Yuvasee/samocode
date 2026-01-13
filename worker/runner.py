"""Claude CLI execution with proper error handling and retries."""

import logging
import os
import re
import select
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

from .config import SamocodeConfig

logger = logging.getLogger("samocode")

PHASE_AGENTS: dict[str, str] = {
    "init": "init-agent",
    "investigation": "investigation-agent",
    "requirements": "requirements-agent",
    "planning": "planning-agent",
    "implementation": "implementation-agent",
    "testing": "testing-agent",
    "quality": "quality-agent",
    "done": "done-agent",
}


class ExecutionStatus(Enum):
    """Result of Claude CLI execution."""

    SUCCESS = "success"
    TIMEOUT = "timeout"
    FAILURE = "failure"
    RETRY_EXHAUSTED = "retry_exhausted"


@dataclass
class ExecutionResult:
    """Result of running Claude CLI."""

    status: ExecutionStatus
    stdout: str
    stderr: str
    returncode: int | None
    attempt: int
    log_file: Path | None = field(default=None)


# =============================================================================
# Public API - Main execution functions
# =============================================================================


def run_claude_with_retry(
    workflow_prompt_path: Path,
    session_path: Path,
    config: SamocodeConfig,
    initial_dive: str | None = None,
    initial_task: str | None = None,
    on_line: Callable[[str], None] | None = None,
) -> ExecutionResult:
    """Execute Claude CLI with retry logic for transient failures."""
    result: ExecutionResult | None = None

    for attempt in range(1, config.max_retries + 1):
        result = run_claude_once(
            workflow_prompt_path,
            session_path,
            config,
            attempt,
            initial_dive if attempt == 1 else None,
            initial_task if attempt == 1 else None,
            on_line,
        )

        if result.status == ExecutionStatus.SUCCESS:
            return result

        if attempt < config.max_retries:
            logger.warning(
                f"Attempt {attempt}/{config.max_retries} failed, "
                f"retrying in {config.retry_delay}s..."
            )
            time.sleep(config.retry_delay)

    logger.error(f"All {config.max_retries} attempts failed")

    if result is None:
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
        stdout=result.stdout,
        stderr=result.stderr,
        returncode=result.returncode,
        attempt=config.max_retries,
        log_file=result.log_file,
    )


def run_claude_once(
    workflow_prompt_path: Path,
    session_path: Path,
    config: SamocodeConfig,
    attempt: int,
    initial_dive: str | None = None,
    initial_task: str | None = None,
    on_line: Callable[[str], None] | None = None,
) -> ExecutionResult:
    """Execute Claude CLI once with timeout protection and log streaming.

    Uses phase-specific agent based on session state:
    - New session (no _overview.md): uses init-agent
    - Existing session: uses agent for current phase
    - Unknown phase: raises error (no fallback)
    """
    logger.info(f"Executing Claude CLI (attempt {attempt})...")

    # Determine agent based on session state
    is_new_session = not (session_path / "_overview.md").exists()
    if is_new_session:
        phase = "init"
        iteration = 1
        agent_name = "init-agent"
        logger.info("New session detected, using init-agent")
    else:
        phase = extract_phase(session_path)
        iteration = extract_iteration(session_path)
        agent_name = get_agent_for_phase(phase)
        if agent_name is None:
            raise ValueError(
                f"Unknown phase '{phase}' has no agent. "
                f"Valid phases: {', '.join(PHASE_AGENTS.keys())}"
            )

    working_dir = extract_working_dir(session_path)
    if working_dir is None:
        working_dir = session_path.parent
        logger.info(f"Using session parent as Working Dir: {working_dir}")
    else:
        logger.info(f"Using Working Dir: {working_dir}")

    cli_args = _build_cli_args(config)

    logger.info(f"Using agent: {agent_name} (phase: {phase})")
    session_context = build_session_context(
        workflow_prompt_path=workflow_prompt_path,
        session_path=session_path,
        config=config,
        phase=phase,
        iteration=iteration,
        initial_dive=initial_dive,
        initial_task=initial_task,
    )
    cli_args.extend(["--agent", agent_name, "--append-system-prompt", session_context])
    cli_args.extend(["-p", "Start"])

    log_file = generate_log_filename(session_path, phase)
    logger.info(f"Streaming logs to: {log_file}")

    return _execute_process(
        cli_args, working_dir, log_file, config.claude_timeout, attempt, on_line
    )


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


def extract_working_dir(session_path: Path) -> Path | None:
    """Extract Working Dir from session _overview.md."""
    content = _read_overview(session_path)
    if content is None:
        return None

    match = re.search(r"^Working Dir:\s*(.+)$", content, re.MULTILINE)
    if not match:
        return None

    working_dir = Path(match.group(1).strip()).expanduser().resolve()
    return working_dir if working_dir.exists() else None


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
) -> str:
    """Build session context for --append-system-prompt injection.

    Includes workflow.md (common context for all phases) plus session-specific details.
    """
    # Start with workflow.md - common context for all phases
    lines = [workflow_prompt_path.read_text().strip()]

    # Add session-specific context
    lines.append("\n\n# Session Context")
    lines.append(f"**Session path:** {session_path}")

    working_dir = extract_working_dir(session_path)
    if working_dir:
        lines.append(f"**Working directory:** {working_dir}")

    if phase:
        lines.append(f"**Phase:** {phase}")
    if iteration:
        lines.append(f"**Iteration:** {iteration}")

    lines.append("")
    lines.extend(_build_config_section(session_path, config))

    if initial_dive or initial_task:
        lines.append("")
        lines.extend(_build_initial_instructions(initial_dive, initial_task))

    return "\n".join(lines)


def get_agent_for_phase(phase: str | None) -> str | None:
    """Map workflow phase to agent name."""
    if phase is None:
        return None
    return PHASE_AGENTS.get(phase.lower())


def generate_log_filename(session_path: Path, phase: str | None) -> Path:
    """Generate timestamped JSONL filename for this invocation."""
    timestamp = datetime.now().strftime("%m-%d-%H%M%S")
    phase_slug = phase.lower() if phase else "unknown"
    return session_path / f"{timestamp}-{phase_slug}.jsonl"


# =============================================================================
# Log streaming
# =============================================================================


def stream_logs(
    process: subprocess.Popen[str],
    log_file: Path,
    timeout: float,
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
                raise subprocess.TimeoutExpired(cmd="claude", timeout=timeout)

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
    overview_path = session_path / "_overview.md"
    if not overview_path.exists():
        return None
    return overview_path.read_text()


def _build_config_section(session_path: Path, config: SamocodeConfig) -> list[str]:
    """Build configuration section for prompts."""
    lines: list[str] = []

    if config.repo_path:
        session_name = session_path.name
        worktree_path = config.worktrees_dir / session_name
        branch_prefix = os.getenv("GIT_BRANCH_PREFIX", "")
        branch_name = session_name.split("-", 3)[-1]

        lines.append("## Worktree Configuration")
        lines.append(f"- Base repo: `{config.repo_path}`")
        lines.append(f"- Worktree path: `{worktree_path}`")
        if branch_prefix:
            lines.append(f"- Branch name: `{branch_prefix}/{branch_name}`")
        else:
            lines.append(f"- Branch name: `{branch_name}`")
    else:
        project_path = session_path.parent
        lines.append("## Standalone Project Configuration")
        lines.append(f"- Project folder: `{project_path}`")
        lines.append("- No git worktree (standalone project)")

    return lines


def _build_initial_instructions(
    initial_dive: str | None, initial_task: str | None
) -> list[str]:
    """Build initial instructions section for prompts."""
    lines = ["## Initial Session Data", "Store the following in _overview.md for later phases:"]

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


def _build_cli_args(config: SamocodeConfig) -> list[str]:
    """Build base CLI arguments."""
    return [
        str(config.claude_path),
        "--dangerously-skip-permissions",
        "--model",
        config.claude_model,
        "--max-turns",
        str(config.claude_max_turns),
        "--verbose",
        "--output-format",
        "stream-json",
    ]


def _execute_process(
    cli_args: list[str],
    working_dir: Path,
    log_file: Path,
    timeout: int,
    attempt: int,
    on_line: Callable[[str], None] | None,
) -> ExecutionResult:
    """Execute subprocess and return result."""
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            cli_args,
            cwd=str(working_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        stdout, stderr = stream_logs(process, log_file, timeout, on_line)
        process.wait()

        if process.returncode == 0:
            logger.info("Claude CLI completed successfully")
            return ExecutionResult(
                status=ExecutionStatus.SUCCESS,
                stdout=stdout,
                stderr=stderr,
                returncode=process.returncode,
                attempt=attempt,
                log_file=log_file,
            )

        logger.error(f"Claude CLI failed with code {process.returncode}")
        logger.error(f"stderr: {stderr[:500]}")
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
        logger.error(f"Claude CLI timed out after {timeout}s")
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
        logger.error(f"Claude CLI execution failed: {e}")
        return ExecutionResult(
            status=ExecutionStatus.FAILURE,
            stdout="",
            stderr=str(e),
            returncode=None,
            attempt=attempt,
            log_file=log_file if log_file.exists() else None,
        )


def _drain_remaining(
    stdout_pipe: object,
    stderr_pipe: object,
    stdout_lines: list[str],
    stderr_lines: list[str],
    f: object,
    on_line: Callable[[str], None] | None,
) -> None:
    """Drain remaining output from pipes after process finishes."""
    for line in stdout_pipe:  # type: ignore[union-attr]
        stdout_lines.append(line)
        f.write(line)  # type: ignore[union-attr]
        if on_line:
            on_line(line)
    for line in stderr_pipe:  # type: ignore[union-attr]
        stderr_lines.append(line)
