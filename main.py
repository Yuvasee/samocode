#!/usr/bin/env python3
"""Samocode - Autonomous Session Orchestrator.

Main orchestrator loop that runs Claude Code CLI iteratively.
Claude reads session state, decides actions via skills, updates state, signals next.
"""

import argparse
import sys
from dataclasses import replace as dataclass_replace
from pathlib import Path

import logging

from worker import (
    ExecutionStatus,
    SamocodeConfig,
    Signal,
    SignalStatus,
    add_session_handler,
    clear_signal_file,
    extract_phase,
    extract_total_iterations,
    get_phase_iteration_count,
    increment_total_iterations,
    is_iteration_limit_exceeded,
    notify_blocked,
    notify_complete,
    notify_error,
    notify_waiting,
    parse_samocode_file,
    read_signal_file,
    record_signal,
    run_claude_with_retry,
    setup_logging,
    validate_signal_for_phase,
    validate_transition,
)


def validate_and_process_signal(
    signal: Signal,
    current_phase: str | None,
    session_path: Path,
    iteration: int,
    logger: logging.Logger,
) -> Signal:
    """Validate signal and enforce phase constraints.

    Returns the signal (possibly modified if invalid).
    Records signal to history.
    """
    # Record signal to history first (even if invalid)
    record_signal(session_path, signal, iteration, current_phase)

    signal_phase = signal.phase or current_phase

    # Validate signal is allowed for phase
    is_valid, error = validate_signal_for_phase(signal_phase, signal.status.value)
    if not is_valid:
        logger.error(f"Invalid signal: {error}")
        return Signal(
            status=SignalStatus.BLOCKED,
            phase=signal_phase,
            reason=f"Invalid signal: {error}",
            needs="investigation",
        )

    # Check per-phase iteration limit
    if signal_phase:
        phase_iterations = get_phase_iteration_count(session_path, signal_phase)
        exceeded, max_allowed = is_iteration_limit_exceeded(signal_phase, phase_iterations)
        if exceeded:
            logger.error(
                f"Phase '{signal_phase}' exceeded iteration limit: "
                f"{phase_iterations} > {max_allowed}"
            )
            return Signal(
                status=SignalStatus.BLOCKED,
                phase=signal_phase,
                reason=f"Phase '{signal_phase}' exceeded {max_allowed} iteration limit",
                needs="investigation",
            )

    # Validate phase transition (if signal indicates phase change)
    if signal.phase and current_phase and signal.phase.lower() != current_phase.lower():
        is_valid, error = validate_transition(current_phase, signal.phase)
        if not is_valid:
            logger.error(f"Invalid transition: {error}")
            return Signal(
                status=SignalStatus.BLOCKED,
                phase=current_phase,
                reason=f"Invalid transition: {error}",
                needs="investigation",
            )

    return signal


def main() -> None:
    """Main orchestrator entry point."""
    parser = argparse.ArgumentParser(
        description="Samocode - Autonomous Session Orchestrator"
    )
    parser.add_argument(
        "--session",
        required=True,
        help="Session path (e.g., '~/project/_sessions/my-task')",
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
    samocode_dir = Path(__file__).parent

    # Session path is always a full path - resolve and get display name
    session_path = Path(args.session).expanduser().resolve()
    session_display_name = session_path.parent.name

    # Look for .samocode config in session's parent directory
    samocode_config = parse_samocode_file(session_path.parent)
    config = SamocodeConfig.from_env(working_dir=session_path.parent)

    # Set repo_path from CLI arg or MAIN_REPO from .samocode (REQUIRED)
    if args.repo:
        repo_path = Path(args.repo).expanduser().resolve()
        repo_source = "--repo CLI arg"
    elif samocode_config.get("MAIN_REPO"):
        repo_path = Path(samocode_config["MAIN_REPO"]).expanduser().resolve()
        repo_source = "MAIN_REPO in .samocode"
    else:
        print(
            "Error: MAIN_REPO is required. Either:\n"
            "  1. Pass --repo /path to the orchestrator, or\n"
            "  2. Set MAIN_REPO in .samocode file"
        )
        sys.exit(1)

    if not repo_path.exists():
        print(f"Error: Repo path does not exist: {repo_path} (from {repo_source})")
        sys.exit(1)
    # Git check is optional - some projects might not be git repos
    config = dataclass_replace(config, repo_path=repo_path)
    log_dir = samocode_dir / "logs"
    workflow_prompt_path = samocode_dir / "workflow.md"

    logger = setup_logging(log_dir)

    validation_errors = config.validate()
    if validation_errors:
        logger.error("Configuration validation failed:")
        for error in validation_errors:
            logger.error(f"  - {error}")
        sys.exit(1)

    if session_path.exists() and not session_path.is_dir():
        logger.error(f"Session path exists but is not a directory: {session_path}")
        sys.exit(1)

    if not workflow_prompt_path.exists():
        logger.error(f"Workflow prompt not found: {workflow_prompt_path}")
        logger.error("Create workflow.md with common session instructions")
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
        logger.info(f"  - Config: {config}")
        return

    iteration = 0
    cumulative_iterations = extract_total_iterations(session_path)
    initial_dive = args.dive
    initial_task = args.task
    session_handler = None

    try:
        while True:
            iteration += 1
            # Track cumulative iterations in _overview.md (persists across restarts)
            if session_path.exists() and (session_path / "_overview.md").exists():
                cumulative_iterations = increment_total_iterations(session_path)

            # Add session handler once session directory exists (created by Claude)
            if session_handler is None and session_path.exists():
                session_handler = add_session_handler(logger, session_path)
                logger.info(f"Session log: {session_path / 'session.log'}")
                # Log startup parameters for transparency
                logger.info(f"Startup args: --session {args.session} --repo {args.repo} --dive {args.dive} --task {args.task}")
                if samocode_config:
                    logger.info(f".samocode: {samocode_config}")
                logger.info(f"Config: {config.to_log_string()}")

            # Get current phase from overview for logging context
            phase = extract_phase(session_path)
            phase_str = f"[{phase}]" if phase else ""

            logger.info(f"\n{'=' * 70}")
            total_str = f" (total: {cumulative_iterations})" if cumulative_iterations > iteration else ""
            logger.info(f"Iteration {iteration}{total_str} {phase_str}")
            logger.info("=" * 70)

            previous_signal = clear_signal_file(session_path)
            if previous_signal:
                logger.info(f"Previous signal: {previous_signal}")
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
                if result.stderr:
                    logger.error(f"Last stderr: {result.stderr[:500]}")
                if result.stdout:
                    logger.error(f"Last stdout (last 500 chars): {result.stdout[-500:]}")
                notify_error(
                    f"Claude execution failed: {result.status.value}",
                    session_display_name,
                    iteration,
                    config.telegram_bot_token,
                    config.telegram_chat_id,
                )
                break

            signal = read_signal_file(session_path)

            # Validate signal and record to history
            signal = validate_and_process_signal(
                signal,
                phase,  # phase from overview
                session_path,
                iteration,
                logger,
            )

            # Use phase from signal if available, otherwise use previously extracted phase
            signal_phase = signal.phase or phase

            phase_log = f"[{signal_phase}] " if signal_phase else ""
            logger.info(f"{phase_log}Signal: {signal.status.value}")

            if signal.status == SignalStatus.DONE:
                logger.info(f"Workflow complete: {signal.summary}")
                notify_complete(
                    signal.summary or "No summary provided",
                    session_display_name,
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
                    session_display_name,
                    signal.needs,
                    config.telegram_bot_token,
                    config.telegram_chat_id,
                )
                break

            if signal.status == SignalStatus.WAITING:
                logger.info(f"Waiting for: {signal.waiting_for}")
                notify_waiting(
                    signal.waiting_for or "Unknown input",
                    session_display_name,
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
        logger.info(f"This run: {iteration} iterations")
        if cumulative_iterations > iteration:
            logger.info(f"Session total: {cumulative_iterations} iterations")
        logger.info("=" * 70)

    except KeyboardInterrupt:
        logger.info("\nOrchestrator interrupted by user")
        sys.exit(1)

    except Exception as e:
        logger.error(f"Orchestrator crashed: {e}", exc_info=True)
        try:
            notify_error(
                f"Orchestrator crashed: {e}",
                session_display_name if "session_display_name" in dir() else "unknown",
                iteration if "iteration" in dir() else 0,
                config.telegram_bot_token if "config" in dir() else "",
                config.telegram_chat_id if "config" in dir() else "",
            )
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
