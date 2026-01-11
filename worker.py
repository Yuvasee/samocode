#!/usr/bin/env python3
"""Samocode - Autonomous Session Orchestrator.

Main orchestrator loop that runs Claude Code CLI iteratively.
Claude reads session state, decides actions via skills, updates state, signals next.
"""

import argparse
import logging
import sys
from dataclasses import replace as dataclass_replace
from datetime import datetime
from pathlib import Path
from typing import Any

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
        help="Session name (e.g., 'my-task') or full path (e.g., '~/project/_samocode')",
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
    parser.add_argument(
        "--complexity",
        type=int,
        choices=[0, 1, 2, 3, 4],
        help="Complexity level (0-4): 0=quick-fix, 1-2=standard, 3-4=complex. Skips auto-detection.",
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

    # Build session path
    # If --session contains '/' or '~', treat as full path
    # Otherwise, use SESSIONS_DIR with date prefix
    if "/" in args.session or args.session.startswith("~"):
        # Full path mode: use directly
        session_path = Path(args.session).expanduser().resolve()
        # Display name is parent folder name (e.g., ~/code/project/_samocode -> "project")
        session_display_name = session_path.parent.name
        is_path_based_session = True
    else:
        # Name mode: {SESSIONS_DIR}/YY-MM-DD-{session_name}
        session_name = args.session.lower().replace(" ", "-")
        existing = list(config.sessions_dir.glob(f"*-{session_name}"))
        if existing:
            # Use most recent existing session
            session_path = sorted(existing)[-1]
        else:
            # Create new session with today's date
            date_prefix = datetime.now().strftime("%y-%m-%d")
            session_path = config.sessions_dir / f"{date_prefix}-{session_name}"
        session_display_name = session_path.name
        is_path_based_session = False

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
    if args.complexity is not None:
        logger.info(f"Complexity override: {args.complexity}")
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

    # State graph and state tracking
    state_graph = load_state_graph(samocode_dir)
    current_state = get_initial_state(state_graph)
    context: dict[str, str | int | bool] = {}

    # Initialize context with complexity if provided via CLI
    if args.complexity is not None:
        context["complexity"] = args.complexity
        logger.info(f"Initial context set: complexity={args.complexity}")

    if state_graph:
        logger.info(f"State graph loaded, initial state: {current_state}")
    else:
        logger.info("No state graph found, using default behavior")

    try:
        while True:
            iteration += 1
            logger.info(f"\n{'=' * 70}")
            logger.info(f"Iteration {iteration} | State: {current_state}")
            logger.info("=" * 70)

            clear_signal_file(session_path)
            logger.info("Cleared signal file")

            # Resolve model for current state
            state_model = get_model_for_state(state_graph, current_state)
            if state_model:
                logger.info(
                    f"Using state-based model: {state_model} (state: {current_state})"
                )
            else:
                logger.info(
                    f"Using default model: {config.claude_model} (no state override)"
                )

            result = run_claude_with_retry(
                workflow_prompt_path,
                session_path,
                config,
                initial_dive if iteration == 1 else None,
                initial_task if iteration == 1 else None,
                is_path_based_session,
                model_override=state_model,
            )

            if result.status != ExecutionStatus.SUCCESS:
                logger.error("Claude execution failed after retries")
                logger.error(f"Status: {result.status.value}")
                logger.error(f"Last stderr: {result.stderr[:500]}")
                notify_error(
                    f"Claude execution failed: {result.status.value}",
                    session_display_name,
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
                # Update state from signal
                if signal.next_state:
                    if is_valid_state(state_graph, signal.next_state):
                        logger.info(
                            f"State transition: {current_state} -> {signal.next_state}"
                        )
                        current_state = signal.next_state
                    else:
                        fallback = get_default_transition(state_graph, current_state)
                        if fallback:
                            logger.warning(
                                f"Invalid next_state '{signal.next_state}', "
                                f"using fallback: {fallback}"
                            )
                            current_state = fallback
                        else:
                            logger.warning(
                                f"Invalid next_state '{signal.next_state}', "
                                "no fallback available"
                            )
                            notify_error(
                                f"Invalid state '{signal.next_state}' with no fallback",
                                session_display_name,
                                iteration,
                                config.telegram_bot_token,
                                config.telegram_chat_id,
                            )

                # Merge context from signal
                if signal.context:
                    context.update(signal.context)
                    logger.info(f"Context updated: {context}")

                logger.info("Continuing to next iteration...")
                continue

            logger.error(f"Unknown signal status: {signal.status}")
            notify_error(
                f"Unknown signal status: {signal.status}",
                session_display_name,
                iteration,
                config.telegram_bot_token,
                config.telegram_chat_id,
            )
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


# State graph utilities (loaded lazily, cached globally)
_state_graph_cache: dict[str, Any] | None = None


def load_state_graph(samocode_dir: Path) -> dict[str, Any] | None:
    """Load state graph from YAML. Returns None if file doesn't exist.

    Caches the result for subsequent calls.
    """
    global _state_graph_cache
    if _state_graph_cache is not None:
        return _state_graph_cache

    graph_path = samocode_dir / "state_graph.yaml"
    if not graph_path.exists():
        return None

    try:
        import yaml

        data = yaml.safe_load(graph_path.read_text())
        _state_graph_cache = data
        return data
    except Exception as e:
        graph_logger = logging.getLogger("samocode")
        graph_logger.warning(f"Failed to load state graph: {e}")
        return None


def get_model_for_state(graph: dict[str, Any] | None, state: str) -> str | None:
    """Get the model for a given state. Returns None if not found."""
    if graph is None:
        return None

    states = graph.get("states")
    if not isinstance(states, dict):
        return None

    state_config = states.get(state)
    if not isinstance(state_config, dict):
        return None

    model = state_config.get("model")
    if isinstance(model, str):
        return model

    default_model = graph.get("default_model")
    return default_model if isinstance(default_model, str) else None


def get_default_transition(graph: dict[str, Any] | None, state: str) -> str | None:
    """Get the default transition for a state (fallback when next_state invalid)."""
    if graph is None:
        return None

    states = graph.get("states")
    if not isinstance(states, dict):
        return None

    state_config = states.get(state)
    if not isinstance(state_config, dict):
        return None

    transitions = state_config.get("transitions")
    if not isinstance(transitions, list):
        return None

    # Find transition with condition "default"
    for transition in transitions:
        if isinstance(transition, dict) and transition.get("condition") == "default":
            to_state = transition.get("to")
            return to_state if isinstance(to_state, str) else None

    # If no default, return first transition's target
    first_transition = transitions[0] if transitions else None
    if isinstance(first_transition, dict):
        to_state = first_transition.get("to")
        return to_state if isinstance(to_state, str) else None

    return None


def get_initial_state(graph: dict[str, Any] | None) -> str:
    """Get the initial state from graph, defaults to 'investigation'."""
    if graph is None:
        return "investigation"

    initial = graph.get("initial_state")
    return initial if isinstance(initial, str) else "investigation"


def is_valid_state(graph: dict[str, Any] | None, state: str) -> bool:
    """Check if a state exists in the graph."""
    if graph is None:
        return False

    states = graph.get("states")
    if not isinstance(states, dict):
        return False

    return state in states
