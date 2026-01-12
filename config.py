"""Configuration management for Samocode orchestrator."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load .env from samocode directory
load_dotenv(Path(__file__).parent / ".env")


@dataclass(frozen=True)
class SamocodeConfig:
    """Runtime configuration for Samocode orchestrator."""

    sessions_dir: Path
    default_projects_folder: Path
    repo_path: Path | None  # Set per-session via --repo CLI arg
    worktrees_dir: Path
    telegram_bot_token: str
    telegram_chat_id: str
    claude_path: Path
    claude_model: str
    claude_max_turns: int
    claude_timeout: int
    max_retries: int
    retry_delay: int

    @classmethod
    def from_env(cls) -> "SamocodeConfig":
        """Load configuration from environment variables.

        SESSIONS_DIR and WORKTREES_DIR must be passed by samocode-parent
        from project's CLAUDE.md. They have no defaults.
        """
        sessions_dir = os.getenv("SESSIONS_DIR")
        worktrees_dir = os.getenv("WORKTREES_DIR")

        if not sessions_dir:
            raise ValueError(
                "SESSIONS_DIR not set. Must be passed by samocode-parent "
                "from project's CLAUDE.md (## Project Paths section)"
            )
        if not worktrees_dir:
            raise ValueError(
                "WORKTREES_DIR not set. Must be passed by samocode-parent "
                "from project's CLAUDE.md (## Project Paths section)"
            )

        return cls(
            sessions_dir=Path(sessions_dir).expanduser().resolve(),
            default_projects_folder=Path(
                os.getenv("DEFAULT_PROJECTS_FOLDER", str(Path.home() / "projects"))
            )
            .expanduser()
            .resolve(),
            repo_path=None,  # Set per-session via CLI
            worktrees_dir=Path(worktrees_dir).expanduser().resolve(),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            claude_path=Path(
                os.getenv("CLAUDE_PATH", "claude")  # Assumes claude is in PATH
            ),
            claude_model=os.getenv("CLAUDE_MODEL", "opus"),
            claude_max_turns=int(os.getenv("CLAUDE_MAX_TURNS", "120")),
            claude_timeout=int(os.getenv("CLAUDE_TIMEOUT", "600")),
            max_retries=int(os.getenv("SAMOCODE_MAX_RETRIES", "3")),
            retry_delay=int(os.getenv("SAMOCODE_RETRY_DELAY", "5")),
        )

    def validate(self) -> list[str]:
        """Validate configuration. Returns list of error messages."""
        errors: list[str] = []

        if not self.claude_path.exists():
            errors.append(f"Claude CLI not found at {self.claude_path}")

        if self.claude_path.exists() and not self.claude_path.is_file():
            errors.append(f"Claude path is not a file: {self.claude_path}")

        if self.claude_max_turns < 1:
            errors.append(f"Invalid max_turns: {self.claude_max_turns}")

        if self.claude_timeout < 1:
            errors.append(f"Invalid timeout: {self.claude_timeout}")

        if not self.telegram_bot_token:
            errors.append("TELEGRAM_BOT_TOKEN not set (notifications disabled)")

        if not self.telegram_chat_id:
            errors.append("TELEGRAM_CHAT_ID not set (notifications disabled)")

        return errors
