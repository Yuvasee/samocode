#!/usr/bin/env python3
"""Samocode - Autonomous Session Orchestrator.

Main orchestrator loop that runs an AI CLI iteratively.
The child AI reads session state, decides actions, updates state, and signals next.
"""

import argparse
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from worker import (
    OVERVIEW_FILENAME,
    ApprovalOutcome,
    ExecutionResolutionError,
    ExecutionStatus,
    GlobalConfigError,
    OutcomeKind,
    OverviewParseError,
    OverviewTransition,
    OverviewWriteError,
    Phase,
    PlanResolutionError,
    ProcessedOutcome,
    RejectionReason,
    Signal,
    SignalStatus,
    add_session_handler,
    apply_overview_transition,
    approve,
    clear_signal_file,
    compose_startup,
    count_source_phase_iterations,
    exit_code_for,
    extract_phase,
    extract_total_iterations,
    get_phase_config,
    global_config_path,
    increment_total_iterations,
    install,
    notify_blocked,
    notify_complete,
    notify_error,
    notify_waiting,
    process_workflow_event,
    read_overview_state,
    read_signal_file,
    record_processed_outcome,
    run_ai_with_retry,
    setup_logging,
    supported_providers,
    uninstall,
)


def process_signal(
    signal: Signal,
    source_phase: str | None,
    session_path: Path,
    iteration: int,
    logger: logging.Logger,
) -> Signal:
    """Use one authority to validate, mutate, audit, and surface truthful rejection."""
    if source_phase is None:
        bootstrap = _resolve_bootstrap_phase(session_path, logger)
        if bootstrap.phase is None:
            assert bootstrap.block_reason is not None
            return Signal(
                status=SignalStatus.BLOCKED,
                phase=None,
                reason=bootstrap.block_reason,
                needs="investigation",
            )
        source_phase = bootstrap.phase

    # History is written later, so +1 includes this run in the limit check.
    source_iterations = count_source_phase_iterations(session_path, source_phase) + 1

    outcome = process_workflow_event(
        session_path, signal, source_phase, source_iterations, iteration
    )
    record_processed_outcome(session_path, signal, iteration, outcome)

    return _signal_for_outcome(signal, outcome, source_phase, logger)


@dataclass(frozen=True)
class BootstrapPhase:
    phase: str | None
    block_reason: str | None = None


def _resolve_bootstrap_phase(
    session_path: Path, logger: logging.Logger
) -> BootstrapPhase:
    """Keep init authoritative during bootstrap so one run cannot jump two phases."""
    parsed = read_overview_state(session_path)
    if parsed.error is OverviewParseError.FILE_NOT_FOUND:
        return BootstrapPhase(phase=Phase.INIT.value)
    if parsed.state is None:
        if parsed.error is OverviewParseError.READ_FAILED:
            reason = f"Overview exists but cannot be read: {parsed.message}"
        else:
            reason = f"Overview exists but is unparseable: {parsed.message}"
        logger.error(reason)
        return BootstrapPhase(phase=None, block_reason=reason)

    written = parsed.state.phase
    init_config = get_phase_config(Phase.INIT.value)
    assert init_config is not None
    if written is not Phase.INIT and not init_config.can_transition_to(written):
        # A retained phase would bypass bootstrap validation after restart; CAS-reset it.
        overview_path = session_path / OVERVIEW_FILENAME
        reset = apply_overview_transition(
            session_path,
            OverviewTransition(
                target_phase=Phase.INIT,
                blocked="no",
                last_action=(
                    f"Bootstrap rejected smuggled phase '{written.value}'; reset to init"
                ),
                next_action="Re-run init",
            ),
            expected_source=written,
        )
        if reset.ok:
            reset_note = "; overview reset to init"
        elif (
            reset.error is OverviewWriteError.PHASE_MOVED
            and reset.observed_phase is Phase.INIT
        ):
            # Another writer already established the safe state.
            reset_note = "; overview already at init"
        else:
            # Renaming is an independent recovery path when the in-place write fails.
            try:
                os.replace(overview_path, session_path / "_overview.rejected.md")
                reset_note = (
                    f"; overview reset FAILED ({reset.message}); overview quarantined "
                    "(renamed to _overview.rejected.md) so restart re-bootstraps to init"
                )
            except OSError as exc:
                reset_note = (
                    f"; overview reset FAILED ({reset.message}); quarantine rename also "
                    f"FAILED ({exc}); manual recovery required before restart: remove "
                    f"{OVERVIEW_FILENAME} or restore its Phase to init"
                )
        reason = (
            f"Bootstrap overview declares phase '{written.value}', which init cannot "
            f"reach; refusing to trust a smuggled phase{reset_note}"
        )
        logger.error(reason)
        return BootstrapPhase(phase=None, block_reason=reason)
    return BootstrapPhase(phase=Phase.INIT.value)


# Rejections a human (not the agent) must resolve; all others need more investigation.
_HUMAN_DECISION_REJECTIONS: frozenset[RejectionReason] = frozenset(
    {
        RejectionReason.TRANSITION_REQUIRES_APPROVAL,
        RejectionReason.ITERATION_LIMIT_EXCEEDED,
    }
)


def _needs_for_rejection(reason: RejectionReason | None) -> str:
    return "human_decision" if reason in _HUMAN_DECISION_REJECTIONS else "investigation"


def _signal_for_outcome(
    signal: Signal,
    outcome: ProcessedOutcome,
    source_phase: str,
    logger: logging.Logger,
) -> Signal:
    """Preserve accepted signals and convert rejections into truthful blocks."""
    if outcome.accepted:
        if outcome.kind is OutcomeKind.ACCEPTED_TRANSITION and outcome.target_phase:
            logger.info(
                f"Phase updated: {source_phase} -> {outcome.target_phase.value}"
            )
        return signal

    reason = outcome.validation_error or "Workflow event rejected"
    needs = _needs_for_rejection(outcome.rejection_reason)
    logger.error(f"Signal rejected in '{source_phase}': {reason}")
    return Signal(
        status=SignalStatus.BLOCKED,
        phase=source_phase,
        reason=reason,
        needs=needs,
    )


# === CLI ===

SUBCOMMANDS = ("run", "install", "uninstall", "approve")


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

  # Select one orchestration provider for the whole process
  samocode run --config ~/project/.samocode --session my-task --provider codex

  # With an initial dive topic
  samocode run --config ~/project/.samocode --session explore-api --dive "auth endpoints"

  # Install assets and create/preserve the global model config
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

    approve_parser = subparsers.add_parser(
        "approve",
        help="Approve the current phase's pending approval gate and advance it",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exit codes:\n"
            "  0  approved: this call advanced the phase and consumed the signal\n"
            "  1  rejected: precondition failed (no gate, wrong/absent signal, etc.)\n"
            "  3  lock contended: another approval is in progress; retry\n"
            "  4  state/IO fault: overview-write fault (may be transient), lock I/O "
            "(not retryable), or the phase moved off the gate target (an external "
            "writer may have moved it); not advanced by this call - read stderr\n"
            "  5  advanced but signal retained: phase advanced; retained _signal.json "
            "is inert, cleanup optional\n"
            "  6  already advanced: another approval reached the gate target; this "
            "call made no change\n"
            "  (2 is reserved by argparse for CLI usage errors)"
        ),
    )
    approve_parser.add_argument(
        "--config",
        required=True,
        help="Path to .samocode config file (e.g., ~/project/.samocode)",
    )
    approve_parser.add_argument(
        "--session",
        required=True,
        help="Session name, not path (e.g., 'my-task' or '26-01-21-my-task')",
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
            if session_path.exists() and (session_path / OVERVIEW_FILENAME).exists():
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

            try:
                result = run_ai_with_retry(
                    workflow_prompt_path,
                    session_path,
                    config,
                    initial_dive if iteration == 1 else None,
                    initial_task if iteration == 1 else None,
                )
            except (
                PlanResolutionError,
                GlobalConfigError,
                ExecutionResolutionError,
            ) as exc:
                # Config/plan misconfiguration is recoverable by a human, not a
                # crash: fail fast to a blocked state instead of the generic
                # "Orchestrator crashed" handler.
                logger.error(f"Resolution failed ({type(exc).__name__}): {exc}")
                notify_blocked(
                    str(exc),
                    session_display_name,
                    "human_decision",
                    config.telegram_bot_token,
                    config.telegram_chat_id,
                )
                break

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

            signal = process_signal(signal, phase, session_path, iteration, logger)

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


def cmd_approve(args: argparse.Namespace) -> None:
    result = approve(Path(args.config).expanduser().resolve(), args.session)
    # A concurrent winner is intentionally fail-fast, not success for this caller.
    succeeded = result.outcome in (
        ApprovalOutcome.APPROVED,
        ApprovalOutcome.APPROVED_SIGNAL_RETAINED,
    )
    stream = sys.stdout if succeeded else sys.stderr
    detail = result.message or (
        result.rejection.value if result.rejection else result.outcome.value
    )
    print(detail, file=stream)
    sys.exit(exit_code_for(result))


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
        "approve": cmd_approve,
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
