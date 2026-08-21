#!/usr/bin/env python3
"""Samocode - Autonomous Session Orchestrator.

Main orchestrator loop that runs an AI CLI iteratively.
The child AI reads session state, decides actions, updates state, and signals next.
"""

import argparse
import logging
import os
import sys
from pathlib import Path

from worker import (
    ExecutionStatus,
    GlobalConfigError,
    Signal,
    SignalStatus,
    add_session_handler,
    clear_signal_file,
    compose_startup,
    extract_phase,
    extract_total_iterations,
    get_phase_config,
    get_phase_iteration_count,
    global_config_path,
    increment_total_iterations,
    install,
    is_iteration_limit_exceeded,
    notify_blocked,
    notify_complete,
    notify_error,
    notify_waiting,
    read_signal_file,
    record_signal,
    run_ai_with_retry,
    setup_logging,
    supported_providers,
    uninstall,
    update_phase,
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
    # Use current_phase for validation (where agent IS), not target phase (where it wants to GO)
    # This allows "continue" signal when transitioning to done phase
    validation_phase = current_phase or signal_phase
    is_valid, error = validate_signal_for_phase(validation_phase, signal.status.value)
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
        exceeded, max_allowed = is_iteration_limit_exceeded(
            signal_phase, phase_iterations
        )
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
        # Enforce gate: gated phases must signal 'waiting' before transitioning
        current_config = get_phase_config(current_phase)
        if (
            current_config
            and current_config.requires_gate
            and signal.status != SignalStatus.WAITING
        ):
            logger.error(
                f"Phase '{current_phase}' requires gate: must signal 'waiting' before transitioning"
            )
            return Signal(
                status=SignalStatus.BLOCKED,
                phase=current_phase,
                reason=f"Phase '{current_phase}' requires human approval before transitioning",
                needs="human_decision",
            )

        is_valid, error = validate_transition(current_phase, signal.phase)
        if not is_valid:
            logger.error(error)
            return Signal(
                status=SignalStatus.BLOCKED,
                phase=current_phase,
                reason=error,
                needs="investigation",
            )
        # Update _overview.md Phase field to match signal (single source of truth)
        if update_phase(session_path, signal.phase):
            logger.info(f"Phase updated: {current_phase} -> {signal.phase}")

    return signal


# === CLI ===

SUBCOMMANDS = ("run", "install", "uninstall")


def add_run_arguments(parser: argparse.ArgumentParser) -> None:
    """Attach orchestrator-run flags to a parser (the `run` subparser)."""
    parser.add_argument(
        "--config",
        required=True,
        help="Path to .samocode config file (e.g., ~/project/.samocode)",
    )
    parser.add_argument(
        "--session",
        required=True,
        help="Session name, not path (e.g., 'my-task' or '26-01-21-my-task')",
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
        "--timeout",
        type=int,
        help="Override timeout in seconds (default: 1800 = 30 min)",
    )
    parser.add_argument(
        "--provider",
        choices=sorted(supported_providers()),
        help="AI CLI provider to run orchestrator iterations with",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run exactly one orchestrator iteration, then stop even on continue.",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level parser with run/install/uninstall subcommands."""
    parser = argparse.ArgumentParser(
        prog="samocode",
        description="Samocode - Autonomous Session Orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Start or continue a session (subcommand optional, defaults to `run`)
  samocode run --config ~/project/.samocode --session my-task
  python main.py --config ~/project/.samocode --session my-task

  # With an initial dive topic
  samocode run --config ~/project/.samocode --session explore-api --dive "auth endpoints"

  # Install/uninstall samocode skills, agents, and commands
  samocode install
  samocode install --copy
  samocode uninstall
""",
    )
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser(
        "run",
        help="Run the orchestrator loop (default when no subcommand is given)",
    )
    add_run_arguments(run_parser)

    install_parser = subparsers.add_parser(
        "install",
        help="Install samocode skills/agents/commands into provider dirs",
    )
    install_parser.add_argument(
        "--copy",
        action="store_true",
        help="Force copy instead of symlink (e.g. for pip installs)",
    )

    subparsers.add_parser(
        "uninstall",
        help="Remove samocode-owned skills/agents/commands",
    )

    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI args, defaulting to the `run` subcommand.

    Backward compatibility: if no subcommand is supplied (the legacy
    `--config/--session` invocation), an implicit `run` is injected so the
    existing entrypoint keeps working without typing `run`.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in SUBCOMMANDS:
        # No subcommand: a bare `-h`/`--help` still hits the top-level parser;
        # everything else is treated as the implicit `run` command.
        if not (argv and argv[0] in ("-h", "--help")):
            argv = ["run", *argv]
    return build_parser().parse_args(argv)


def run_orchestrator(args: argparse.Namespace) -> None:
    """Run the autonomous orchestrator loop (the `run` command)."""
    samocode_dir = Path(__file__).parent

    # Load global config once, select the provider, compose one runtime object.
    composition = compose_startup(
        config_path=Path(args.config).expanduser().resolve(),
        session_name=args.session,
        cli_provider=args.provider,
        cli_timeout=args.timeout,
        env_provider=os.environ.get("SAMOCODE_PROVIDER"),
    )
    if composition.errors:
        print("Configuration errors:")
        for error in composition.errors:
            print(f"  - {error}")
        sys.exit(1)
    config = composition.config
    assert config is not None  # guaranteed when errors is empty

    session_path = config.session_path
    session_display_name = session_path.name

    log_dir = samocode_dir / "logs"
    workflow_prompt_path = samocode_dir / "workflow.md"

    logger = setup_logging(log_dir)

    for warning in composition.warnings:
        logger.warning(warning)

    if not workflow_prompt_path.exists():
        logger.error(f"Workflow prompt not found: {workflow_prompt_path}")
        logger.error("Create workflow.md with common session instructions")
        sys.exit(1)

    logger.info("=" * 70)
    logger.info("Samocode Orchestrator Started")
    logger.info(f"Config: {args.config}")
    logger.info(f"Session: {session_path}")
    logger.info(f"Repo: {config.main_repo}")
    model = config.ai_model or "default"
    config_mode = (
        "legacy (no config)"
        if config.global_config is None
        else str(global_config_path())
    )
    logger.info(f"Provider: {config.ai_provider}")
    logger.info(f"Model: {model}")
    logger.info(f"Global config: {config_mode}")
    if config.ai_provider == "claude":
        logger.info(f"Max turns: {config.claude_max_turns}")
    logger.info(f"Timeout: {config.ai_timeout}s")
    if args.dive:
        logger.info(f"Initial dive: {args.dive}")
    if args.task:
        logger.info(f"Initial task: {args.task}")
    logger.info("=" * 70)

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

            # Add session handler once the provider creates the session directory
            if session_handler is None and session_path.exists():
                session_handler = add_session_handler(logger, session_path)
                logger.info(f"Session log: {session_path / 'session.log'}")
                logger.info(f"Config: {config.to_log_string()}")

            # Get current phase from overview for logging context
            phase = extract_phase(session_path)
            phase_str = f"[{phase}]" if phase else ""

            logger.info(f"\n{'=' * 70}")
            total_str = (
                f" (total: {cumulative_iterations})"
                if cumulative_iterations > iteration
                else ""
            )
            logger.info(f"Iteration {iteration}{total_str} {phase_str}")
            logger.info("=" * 70)

            previous_signal = clear_signal_file(session_path)
            if previous_signal:
                logger.info(f"Previous signal: {previous_signal}")
            logger.info("Cleared signal file")

            result = run_ai_with_retry(
                workflow_prompt_path,
                session_path,
                config,
                initial_dive if iteration == 1 else None,
                initial_task if iteration == 1 else None,
            )

            if result.status != ExecutionStatus.SUCCESS:
                logger.error(f"{config.ai_provider} execution failed after retries")
                logger.error(f"Status: {result.status.value}")
                if result.stderr:
                    logger.error(f"Last stderr: {result.stderr[:500]}")
                if result.stdout:
                    logger.error(
                        f"Last stdout (last 500 chars): {result.stdout[-500:]}"
                    )
                notify_error(
                    f"{config.ai_provider} execution failed: {result.status.value}",
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
                phase,
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
                if args.once:
                    logger.info("One-shot mode - pausing after continue signal")
                    break
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
                session_display_name,
                iteration,
                config.telegram_bot_token,
                config.telegram_chat_id,
            )
        except (OSError, RuntimeError):
            # Network/system errors during notification are non-critical
            pass
        sys.exit(1)


def cmd_install(args: argparse.Namespace) -> None:
    """Install samocode assets into provider directories."""
    # args.copy is True only with --copy; pass None for AUTO otherwise.
    try:
        install(copy=True if args.copy else None)
    except GlobalConfigError as exc:
        print(f"\nError: existing global config is invalid.\n{exc}", file=sys.stderr)
        print(
            "Fix the file above or delete it to regenerate defaults.",
            file=sys.stderr,
        )
        sys.exit(1)


def cmd_uninstall(_args: argparse.Namespace) -> None:
    """Remove samocode-owned assets from provider directories."""
    uninstall()


def main() -> None:
    """CLI entry point: parse args and dispatch to the chosen command."""
    args = parse_args()

    handlers = {
        "run": run_orchestrator,
        "install": cmd_install,
        "uninstall": cmd_uninstall,
    }
    # parse_args() guarantees a command (defaults to "run"); the get() guard
    # keeps the dispatcher total and future-proof.
    handler = handlers.get(args.command or "run")
    if handler is None:
        build_parser().print_help()
        sys.exit(2)
    handler(args)


if __name__ == "__main__":
    main()
