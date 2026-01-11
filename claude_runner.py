"""Claude CLI execution with proper error handling and retries."""

import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from config import SamocodeConfig

logger = logging.getLogger("samocode")


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


def build_prompt(
    workflow_prompt_path: Path,
    session_path: Path,
    config: SamocodeConfig,
    initial_dive: str | None = None,
    initial_task: str | None = None,
    is_path_based_session: bool = False,
) -> str:
    """Build the full prompt with optional initial instructions."""
    prompt = workflow_prompt_path.read_text()
    prompt += f"\n\n## Session Path\n\n`{session_path}`\n"

    if config.repo_path:
        # Repo-based session: create worktree
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
        # Path-based session: project folder is parent of session path
        project_path = session_path.parent
        prompt += "\n## Standalone Project Configuration\n\n"
        prompt += f"- Project folder: `{project_path}`\n"
        prompt += "- No git worktree (standalone project)\n"
    else:
        # Name-based session: create folder in default projects
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
            "(requirements → planning → implementation → testing → quality → done). "
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
    model_override: str | None = None,
) -> ExecutionResult:
    """Execute Claude CLI once with timeout protection.

    Args:
        model_override: If provided, use this model instead of config.claude_model
    """
    effective_model = model_override or config.claude_model
    logger.info(
        f"Executing Claude CLI (attempt {attempt}, model: {effective_model})..."
    )

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

    prompt = build_prompt(
        workflow_prompt_path,
        session_path,
        config,
        initial_dive,
        initial_task,
        is_path_based_session,
    )

    env = os.environ.copy()
    env["SAMOCODE_SESSION_PATH"] = str(session_path)

    try:
        result = subprocess.run(
            [
                str(config.claude_path),
                "-p",
                prompt,
                "--dangerously-skip-permissions",
                "--model",
                effective_model,
                "--max-turns",
                str(config.claude_max_turns),
            ],
            cwd=str(working_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=config.claude_timeout,
        )

        if result.returncode == 0:
            logger.info("Claude CLI completed successfully")
            return ExecutionResult(
                status=ExecutionStatus.SUCCESS,
                stdout=result.stdout,
                stderr=result.stderr,
                returncode=result.returncode,
                attempt=attempt,
            )

        logger.error(f"Claude CLI failed with code {result.returncode}")
        logger.error(f"stderr: {result.stderr[:500]}")
        return ExecutionResult(
            status=ExecutionStatus.FAILURE,
            stdout=result.stdout,
            stderr=result.stderr,
            returncode=result.returncode,
            attempt=attempt,
        )

    except subprocess.TimeoutExpired:
        logger.error(f"Claude CLI timed out after {config.claude_timeout}s")
        return ExecutionResult(
            status=ExecutionStatus.TIMEOUT,
            stdout="",
            stderr=f"Timeout after {config.claude_timeout}s",
            returncode=None,
            attempt=attempt,
        )

    except Exception as e:
        logger.error(f"Claude CLI execution failed: {e}")
        return ExecutionResult(
            status=ExecutionStatus.FAILURE,
            stdout="",
            stderr=str(e),
            returncode=None,
            attempt=attempt,
        )


def run_claude_with_retry(
    workflow_prompt_path: Path,
    session_path: Path,
    config: SamocodeConfig,
    initial_dive: str | None = None,
    initial_task: str | None = None,
    is_path_based_session: bool = False,
    model_override: str | None = None,
) -> ExecutionResult:
    """Execute Claude CLI with retry logic for transient failures.

    Args:
        model_override: If provided, use this model instead of config.claude_model
    """
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
            model_override,
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
        )

    return ExecutionResult(
        status=ExecutionStatus.RETRY_EXHAUSTED,
        stdout=result.stdout,
        stderr=result.stderr,
        returncode=result.returncode,
        attempt=config.max_retries,
    )
