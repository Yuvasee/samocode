"""Configuration management for Samocode orchestrator."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load .env from samocode root directory (parent of worker/)
load_dotenv(Path(__file__).parent.parent / ".env")


def parse_samocode_file(start_path: Path) -> dict[str, str]:
    """Find and parse .samocode file starting from given path.

    Searches start_path and parent directories up to home.
    Returns dict of key-value pairs, or empty dict if not found.
    """
    current = start_path.resolve()
    home = Path.home()

    while current != current.parent and current >= home:
        samocode_path = current / ".samocode"
        if samocode_path.exists():
            return _parse_samocode_contents(samocode_path)
        current = current.parent

    return {}


def _parse_samocode_contents(path: Path) -> dict[str, str]:
    """Parse .samocode file contents into key-value dict."""
    result: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip()
    return result


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
    def from_env(cls, working_dir: Path | None = None) -> "SamocodeConfig":
        """Load configuration from .samocode file and environment.

        If working_dir provided, searches for .samocode file there first.
        Falls back to environment variables if .samocode not found.
        """
        samocode_config: dict[str, str] = {}
        if working_dir:
            samocode_config = parse_samocode_file(working_dir)

        # Map .samocode keys to our config (SESSIONS->sessions_dir, WORKTREES->worktrees_dir)
        sessions_dir = samocode_config.get("SESSIONS") or os.getenv("SESSIONS_DIR")
        worktrees_dir = samocode_config.get("WORKTREES") or os.getenv("WORKTREES_DIR")

        if not sessions_dir:
            raise ValueError(
                "SESSIONS not found in .samocode and SESSIONS_DIR env var not set. "
                "Create .samocode file with SESSIONS=path or set SESSIONS_DIR."
            )
        if not worktrees_dir:
            raise ValueError(
                "WORKTREES not found in .samocode and WORKTREES_DIR env var not set. "
                "Create .samocode file with WORKTREES=path or set WORKTREES_DIR."
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

        # Telegram is optional - no error if not configured

        return errors
