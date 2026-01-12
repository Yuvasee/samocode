"""Logging configuration for Samocode orchestrator."""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(log_dir: Path) -> logging.Logger:
    """Configure logging to both stdout and rotating file."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "samocode.log"

    logger = logging.getLogger("samocode")
    logger.setLevel(logging.INFO)

    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)s - %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=1_000_000,
        backupCount=5,
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


def add_session_handler(
    logger: logging.Logger, session_path: Path
) -> logging.FileHandler:
    """Add a session-specific file handler to the logger.

    Args:
        logger: The logger to add the handler to
        session_path: Path to the session directory

    Returns:
        The created FileHandler so caller can remove it later

    Raises:
        ValueError: If session_path does not exist
    """
    if not session_path.exists():
        raise ValueError(f"Session path does not exist: {session_path}")

    session_name = session_path.name
    log_file = session_path / "session.log"

    formatter = logging.Formatter(
        f"[%(asctime)s] %(levelname)s - [{session_name}] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    handler = logging.FileHandler(log_file)
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return handler
