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


def get_agent_for_phase(phase: str | None) -> str | None:
    """Map workflow phase to agent name.

    Returns agent name if phase has a dedicated agent, None otherwise.
    When None, caller should fall back to workflow.md prompt mode.
    """
    if phase is None:
        return None
    return PHASE_AGENTS.get(phase.lower())


def build_session_context(
    session_path: Path,
    config: SamocodeConfig,
    phase: str | None = None,
    iteration: int | None = None,
    is_path_based_session: bool = False,
    initial_dive: str | None = None,
    initial_task: str | None = None,
) -> str:
    """Build session context for --append-system-prompt injection.

    This context is appended to agent prompts to provide session-specific
    information that agents need but shouldn't have hardcoded.
    """
    lines = ["# Session Context"]
    lines.append(f"**Session path:** {session_path}")

    working_dir = extract_working_dir(session_path)
    if working_dir:
        lines.append(f"**Working directory:** {working_dir}")

    if phase:
        lines.append(f"**Phase:** {phase}")
    if iteration:
        lines.append(f"**Iteration:** {iteration}")

    if config.repo_path:
        session_name = session_path.name
        worktree_path = config.worktrees_dir / session_name
        lines.append("")
        lines.append("## Worktree Configuration")
        lines.append(f"- Base repo: `{config.repo_path}`")
        lines.append(f"- Worktree path: `{worktree_path}`")
        branch_prefix = os.getenv("GIT_BRANCH_PREFIX", "")
        branch_name = session_name.split("-", 3)[-1]
        if branch_prefix:
            lines.append(f"- Branch name: `{branch_prefix}/{branch_name}`")
        else:
            lines.append(f"- Branch name: `{branch_name}`")
    elif is_path_based_session:
        project_path = session_path.parent
        lines.append("")
        lines.append("## Standalone Project Configuration")
        lines.append(f"- Project folder: `{project_path}`")
        lines.append("- No git worktree (standalone project)")
    else:
        project_path = config.default_projects_folder / session_path.name
        lines.append("")
        lines.append("## Standalone Project Configuration")
        lines.append(f"- Project folder: `{project_path}`")
        lines.append("- No git worktree (standalone project)")

    if initial_dive or initial_task:
        lines.append("")
        lines.append("## Initial Instructions")
        lines.append("This is a NEW session. After initialization:")
        if initial_dive:
            lines.append(f"1. Run dive skill with topic: **{initial_dive}**")
        if initial_task:
            step = "2" if initial_dive else "1"
            lines.append(f"{step}. Define task: **{initial_task}**")
        lines.append("")
        lines.append(
            "**MANDATORY**: After these steps, continue through ALL workflow phases "
            "(requirements -> planning -> implementation -> testing -> quality -> done). "
            "Do NOT signal `done` after just the dive - that's only phase 1 of 7."
        )

    return "\n".join(lines)


def extract_working_dir(session_path: Path) -> Path | None:
    """Extract Working Dir from session _overview.md."""
    overview_path = session_path / "_overview.md"
    if not overview_path.exists():
        return None

    content = overview_path.read_text()
    match = re.search(r"^Working Dir:\s*(.+)$", content, re.MULTILINE)
    if not match:
        return None

    working_dir_str = match.group(1).strip()
    working_dir = Path(working_dir_str).expanduser().resolve()

    return working_dir if working_dir.exists() else None


def extract_phase(session_path: Path) -> str | None:
    """Extract Phase from session _overview.md Status section."""
    overview_path = session_path / "_overview.md"
    if not overview_path.exists():
        return None

    content = overview_path.read_text()
    match = re.search(r"^Phase:\s*(.+)$", content, re.MULTILINE)
    if not match:
        return None

    return match.group(1).strip()


def extract_iteration(session_path: Path) -> int | None:
    """Extract Iteration from session _overview.md Status section."""
    overview_path = session_path / "_overview.md"
    if not overview_path.exists():
        return None

    content = overview_path.read_text()
    match = re.search(r"^Iteration:\s*(\d+)$", content, re.MULTILINE)
    if not match:
        return None

    return int(match.group(1))


def generate_log_filename(session_path: Path, phase: str | None) -> Path:
    """Generate timestamped JSONL filename for this invocation."""
    timestamp = datetime.now().strftime("%m-%d-%H%M%S")
    phase_slug = phase.lower() if phase else "unknown"
    return session_path / f"{timestamp}-{phase_slug}.jsonl"


def stream_logs(
    process: subprocess.Popen[str],
    log_file: Path,
    timeout: float,
    on_line: Callable[[str], None] | None = None,
) -> tuple[str, str]:
    """Stream stdout from process to JSONL file with timeout support.

    Uses select() for non-blocking reads with timeout awareness.
    """
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
                # Process finished - drain remaining output
                for line in stdout_pipe:
                    stdout_lines.append(line)
                    f.write(line)
                    if on_line:
                        on_line(line)
                for line in stderr_pipe:
                    stderr_lines.append(line)
                break

            readable, _, _ = select.select(
                [stdout_pipe, stderr_pipe],
                [],
                [],
                min(remaining, 1.0),
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


def build_prompt(
    workflow_prompt_path: Path,
    session_path: Path,
    config: SamocodeConfig,
    initial_dive: str | None = None,
    initial_task: str | None = None,
    is_path_based_session: bool = False,
) -> str:
    """Build the full prompt for workflow.md fallback mode.

    This is used when no phase-specific agent is available.
    For agent mode, build_session_context() is used instead.
    """
    prompt = workflow_prompt_path.read_text()
    prompt += f"\n\n## Session Path\n\n`{session_path}`\n"

    if config.repo_path:
        session_name = session_path.name
        worktree_path = config.worktrees_dir / session_name
        prompt += "\n## Worktree Configuration\n\n"
        prompt += f"- Base repo: `{config.repo_path}`\n"
        prompt += f"- Worktree path: `{worktree_path}`\n"
        branch_prefix = os.getenv("GIT_BRANCH_PREFIX", "")
        branch_name = session_name.split("-", 3)[-1]
        if branch_prefix:
            prompt += f"- Branch name: `{branch_prefix}/{branch_name}`\n"
        else:
            prompt += f"- Branch name: `{branch_name}`\n"
    elif is_path_based_session:
        project_path = session_path.parent
        prompt += "\n## Standalone Project Configuration\n\n"
        prompt += f"- Project folder: `{project_path}`\n"
        prompt += "- No git worktree (standalone project)\n"
    else:
        project_path = config.default_projects_folder / session_path.name
        prompt += "\n## Standalone Project Configuration\n\n"
        prompt += f"- Project folder: `{project_path}`\n"
        prompt += "- No git worktree (standalone project)\n"

    if initial_dive or initial_task:
        prompt += "\n## Initial Instructions\n\n"
        prompt += "This is a NEW session. After initialization:\n\n"
        if initial_dive:
            prompt += f"1. Run dive skill with topic: **{initial_dive}**\n"
        if initial_task:
            prompt += (
                f"{'2' if initial_dive else '1'}. Define task: **{initial_task}**\n"
            )
        prompt += (
            "\n**MANDATORY**: After these steps, continue through ALL workflow phases "
            "(requirements -> planning -> implementation -> testing -> quality -> done). "
            "Do NOT signal `done` after just the dive - that's only phase 1 of 7.\n"
        )

    return prompt


def run_claude_once(
    workflow_prompt_path: Path,
    session_path: Path,
    config: SamocodeConfig,
    attempt: int,
    initial_dive: str | None = None,
    initial_task: str | None = None,
    is_path_based_session: bool = False,
    on_line: Callable[[str], None] | None = None,
) -> ExecutionResult:
    """Execute Claude CLI once with timeout protection and log streaming.

    Uses phase-specific agent if available, falls back to workflow.md prompt.
    Streams stdout to a JSONL file for real-time monitoring.
    """
    logger.info(f"Executing Claude CLI (attempt {attempt})...")

    phase = extract_phase(session_path)
    iteration = extract_iteration(session_path)
    agent_name = get_agent_for_phase(phase)

    working_dir = extract_working_dir(session_path)
    if working_dir is None:
        if is_path_based_session:
            working_dir = session_path.parent
            logger.info(f"Using parent as Working Dir (path-based): {working_dir}")
        else:
            logger.warning("Working Dir not found in _overview.md, using session path")
            working_dir = session_path
    else:
        logger.info(f"Using Working Dir: {working_dir}")

    cli_args = [
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

    if agent_name:
        logger.info(f"Using agent: {agent_name} (phase: {phase})")
        session_context = build_session_context(
            session_path=session_path,
            config=config,
            phase=phase,
            iteration=iteration,
            is_path_based_session=is_path_based_session,
            initial_dive=initial_dive,
            initial_task=initial_task,
        )
        cli_args.extend(
            [
                "--agent",
                agent_name,
                "--append-system-prompt",
                session_context,
            ]
        )
    else:
        logger.info(f"No agent for phase '{phase}', using workflow.md fallback")
        prompt = build_prompt(
            workflow_prompt_path,
            session_path,
            config,
            initial_dive,
            initial_task,
            is_path_based_session,
        )
        cli_args.extend(["-p", prompt])

    env = os.environ.copy()
    env["SAMOCODE_SESSION_PATH"] = str(session_path)

    log_file = generate_log_filename(session_path, phase)
    logger.info(f"Streaming logs to: {log_file}")

    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            cli_args,
            cwd=str(working_dir),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        stdout, stderr = stream_logs(process, log_file, config.claude_timeout, on_line)
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
        logger.error(f"Claude CLI timed out after {config.claude_timeout}s")
        return ExecutionResult(
            status=ExecutionStatus.TIMEOUT,
            stdout="",
            stderr=f"Timeout after {config.claude_timeout}s",
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


def run_claude_with_retry(
    workflow_prompt_path: Path,
    session_path: Path,
    config: SamocodeConfig,
    initial_dive: str | None = None,
    initial_task: str | None = None,
    is_path_based_session: bool = False,
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
            is_path_based_session,
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
