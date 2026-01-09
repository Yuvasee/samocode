#!/usr/bin/env python3
"""Samocode - Autonomous Session Orchestrator.

Main orchestrator loop that runs Claude Code CLI iteratively.
Claude reads session state, decides actions via skills, updates state, signals next.
"""

import argparse
import sys
from dataclasses import replace as dataclass_replace
from datetime import datetime
from pathlib import Path

from claude_runner import ExecutionStatus, run_claude_with_retry
from config import SamocodeConfig
from logging_setup import setup_logging
from signals import SignalStatus, clear_signal_file, read_signal_file
from telegram import notify_blocked, notify_complete, notify_error, notify_waiting


def main() -> None:
    """Main orchestrator entry point."""
    parser = argparse.ArgumentParser(
        description="Samocode - Autonomous Session Orchestrator"
    )
    parser.add_argument(
        "--session",
        required=True,
        help="Session name (e.g., 'my-task') - creates YY-MM-DD-{name} in _sessions/",
    )
    parser.add_argument(
        "--repo",
        help="Base git repo path (creates worktree). If not provided, creates standalone project folder.",
    )
    parser.add_argument(
        "--dive",
        help="Initial dive topic (optional, for first run)",
    )
    parser.add_argument(
        "--task",
        help="Initial task definition (optional, for first run)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would execute without running Claude",
    )

    args = parser.parse_args()

    config = SamocodeConfig.from_env()

    # Set repo_path from CLI arg if provided
    if args.repo:
        repo_path = Path(args.repo).expanduser().resolve()
        if not repo_path.exists():
            print(f"Error: Repo path does not exist: {repo_path}")
            sys.exit(1)
        if not (repo_path / ".git").exists():
            print(f"Error: Not a git repository: {repo_path}")
            sys.exit(1)
        config = dataclass_replace(config, repo_path=repo_path)

    # Build session path: {SESSIONS_DIR}/YY-MM-DD-{session_name}
    # First check for existing session with any date prefix
    session_name = args.session.lower().replace(" ", "-")
    existing = list(config.sessions_dir.glob(f"*-{session_name}"))
    if existing:
        # Use most recent existing session
        session_path = sorted(existing)[-1]
    else:
        # Create new session with today's date
        date_prefix = datetime.now().strftime("%y-%m-%d")
        session_path = config.sessions_dir / f"{date_prefix}-{session_name}"

    samocode_dir = Path(__file__).parent
    log_dir = samocode_dir / "logs"
    workflow_prompt_path = samocode_dir / "workflow.md"

    logger = setup_logging(log_dir)

    validation_errors = config.validate()
    if validation_errors:
        logger.error("Configuration validation failed:")
        for error in validation_errors:
            logger.error(f"  - {error}")
        fatal_errors = [e for e in validation_errors if "TELEGRAM" not in e]
        if fatal_errors:
            sys.exit(1)

    if session_path.exists() and not session_path.is_dir():
        logger.error(f"Session path exists but is not a directory: {session_path}")
        sys.exit(1)

    if not workflow_prompt_path.exists():
        logger.error(f"Workflow prompt not found: {workflow_prompt_path}")
        logger.error("Create workflow.md with Claude instructions")
        sys.exit(1)

    logger.info("=" * 70)
    logger.info("Samocode Orchestrator Started")
    logger.info(f"Session: {session_path}")
    logger.info(f"Repo: {config.repo_path or 'none (standalone project)'}")
    logger.info(f"Model: {config.claude_model}")
    logger.info(f"Max turns: {config.claude_max_turns}")
    logger.info(f"Timeout: {config.claude_timeout}s")
    logger.info(f"Dry run: {args.dry_run}")
    if args.dive:
        logger.info(f"Initial dive: {args.dive}")
    if args.task:
        logger.info(f"Initial task: {args.task}")
    logger.info("=" * 70)

    if args.dry_run:
        logger.info("DRY RUN: Would start orchestrator loop")
        logger.info(f"  - Session: {session_path}")
        logger.info(f"  - Workflow: {workflow_prompt_path}")
        logger.info(f"  - Config: {config}")
        return

    iteration = 0
    initial_dive = args.dive
    initial_task = args.task

    try:
        while True:
            iteration += 1
            logger.info(f"\n{'=' * 70}")
            logger.info(f"Iteration {iteration}")
            logger.info("=" * 70)

            clear_signal_file(session_path)
            logger.info("Cleared signal file")

            result = run_claude_with_retry(
                workflow_prompt_path,
                session_path,
                config,
                initial_dive if iteration == 1 else None,
                initial_task if iteration == 1 else None,
            )

            if result.status != ExecutionStatus.SUCCESS:
                logger.error("Claude execution failed after retries")
                logger.error(f"Status: {result.status.value}")
                logger.error(f"Last stderr: {result.stderr[:500]}")
                notify_error(
                    f"Claude execution failed: {result.status.value}",
                    session_path.name,
                    iteration,
                    config.telegram_bot_token,
                    config.telegram_chat_id,
                )
                break

            signal = read_signal_file(session_path)
            logger.info(f"Signal: {signal.status.value}")

            if signal.status == SignalStatus.DONE:
                logger.info(f"Workflow complete: {signal.summary}")
                notify_complete(
                    signal.summary or "No summary provided",
                    session_path.name,
                    iteration,
                    config.telegram_bot_token,
                    config.telegram_chat_id,
                )
                break

            if signal.status == SignalStatus.BLOCKED:
                logger.warning(f"Blocked: {signal.reason}")
                logger.warning(f"Needs: {signal.needs}")
                notify_blocked(
                    signal.reason or "Unknown reason",
                    session_path.name,
                    signal.needs,
                    config.telegram_bot_token,
                    config.telegram_chat_id,
                )
                break

            if signal.status == SignalStatus.WAITING:
                logger.info(f"Waiting for: {signal.waiting_for}")
                notify_waiting(
                    signal.waiting_for or "Unknown input",
                    session_path.name,
                    config.telegram_bot_token,
                    config.telegram_chat_id,
                )
                logger.info("Waiting state - pausing orchestrator")
                break

            if signal.status == SignalStatus.CONTINUE:
                logger.info("Continuing to next iteration...")
                continue

            logger.error(f"Unknown signal status: {signal.status}")
            break

        logger.info("=" * 70)
        logger.info("Orchestrator finished")
        logger.info(f"Total iterations: {iteration}")
        logger.info("=" * 70)

    except KeyboardInterrupt:
        logger.info("\nOrchestrator interrupted by user")
        sys.exit(1)

    except Exception as e:
        logger.error(f"Orchestrator crashed: {e}", exc_info=True)
        try:
            notify_error(
                f"Orchestrator crashed: {e}",
                session_path.name if "session_path" in dir() else "unknown",
                iteration if "iteration" in dir() else 0,
                config.telegram_bot_token if "config" in dir() else "",
                config.telegram_chat_id if "config" in dir() else "",
            )
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
