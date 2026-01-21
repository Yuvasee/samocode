"""Configuration management for Samocode orchestrator."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from .timestamps import folder_timestamp

# Load .env from samocode root directory (parent of worker/)
load_dotenv(Path(__file__).parent.parent / ".env")


@dataclass(frozen=True)
class ProjectConfig:
    """Project-specific paths from .samocode file.

    All three paths are required and must exist as directories.
    """

    main_repo: Path
    worktrees: Path
    sessions: Path

    @classmethod
    def from_file(cls, config_path: Path) -> "ProjectConfig":
        """Load project config from explicit .samocode path.

        Raises ValueError if file missing, unreadable, or missing required fields.
        """
        if not config_path.exists():
            raise ValueError(f"Config file not found: {config_path}")

        if not config_path.is_file():
            raise ValueError(f"Config path is not a file: {config_path}")

        values = _parse_config_file(config_path)

        required = {"MAIN_REPO", "WORKTREES", "SESSIONS"}
        missing = required - set(values.keys())
        if missing:
            raise ValueError(
                f"Missing required fields in {config_path}: {', '.join(sorted(missing))}"
            )

        return cls(
            main_repo=Path(values["MAIN_REPO"]).expanduser().resolve(),
            worktrees=Path(values["WORKTREES"]).expanduser().resolve(),
            sessions=Path(values["SESSIONS"]).expanduser().resolve(),
        )

    def validate(self) -> list[str]:
        """Validate that all paths exist as directories."""
        errors: list[str] = []

        for name, path in [
            ("MAIN_REPO", self.main_repo),
            ("WORKTREES", self.worktrees),
            ("SESSIONS", self.sessions),
        ]:
            if not path.exists():
                errors.append(f"{name} does not exist: {path}")
            elif not path.is_dir():
                errors.append(f"{name} is not a directory: {path}")

        return errors


@dataclass(frozen=True)
class RuntimeConfig:
    """Runtime settings from environment variables."""

    telegram_bot_token: str
    telegram_chat_id: str
    claude_path: Path
    claude_model: str
    claude_max_turns: int
    claude_timeout: int
    max_retries: int
    retry_delay: int

    @classmethod
    def from_env(cls) -> "RuntimeConfig":
        """Load runtime configuration from environment variables."""
        return cls(
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            claude_path=Path(os.getenv("CLAUDE_PATH", "claude")),
            claude_model=os.getenv("CLAUDE_MODEL", "opus"),
            claude_max_turns=int(os.getenv("CLAUDE_MAX_TURNS", "300")),
            claude_timeout=int(os.getenv("CLAUDE_TIMEOUT", "1800")),
            max_retries=int(os.getenv("SAMOCODE_MAX_RETRIES", "3")),
            retry_delay=int(os.getenv("SAMOCODE_RETRY_DELAY", "5")),
        )

    def validate(self) -> list[str]:
        """Validate runtime configuration."""
        errors: list[str] = []

        if not self.claude_path.exists():
            errors.append(f"Claude CLI not found at {self.claude_path}")
        elif not self.claude_path.is_file():
            errors.append(f"Claude path is not a file: {self.claude_path}")

        if self.claude_max_turns < 1:
            errors.append(f"Invalid max_turns: {self.claude_max_turns}")

        if self.claude_timeout < 1:
            errors.append(f"Invalid timeout: {self.claude_timeout}")

        return errors


@dataclass(frozen=True)
class SamocodeConfig:
    """Complete configuration combining project and runtime settings."""

    project: ProjectConfig
    runtime: RuntimeConfig
    session_path: Path

    @property
    def main_repo(self) -> Path:
        """Convenience accessor for project.main_repo."""
        return self.project.main_repo

    @property
    def worktrees_dir(self) -> Path:
        """Convenience accessor for project.worktrees."""
        return self.project.worktrees

    @property
    def sessions_dir(self) -> Path:
        """Convenience accessor for project.sessions."""
        return self.project.sessions

    # Forward runtime properties for backward compatibility
    @property
    def repo_path(self) -> Path:
        """Alias for main_repo (backward compatibility)."""
        return self.project.main_repo

    @property
    def telegram_bot_token(self) -> str:
        return self.runtime.telegram_bot_token

    @property
    def telegram_chat_id(self) -> str:
        return self.runtime.telegram_chat_id

    @property
    def claude_path(self) -> Path:
        return self.runtime.claude_path

    @property
    def claude_model(self) -> str:
        return self.runtime.claude_model

    @property
    def claude_max_turns(self) -> int:
        return self.runtime.claude_max_turns

    @property
    def claude_timeout(self) -> int:
        return self.runtime.claude_timeout

    @property
    def max_retries(self) -> int:
        return self.runtime.max_retries

    @property
    def retry_delay(self) -> int:
        return self.runtime.retry_delay

    def validate(self) -> list[str]:
        """Validate complete configuration."""
        return self.project.validate() + self.runtime.validate()

    def to_log_string(self) -> str:
        """Return loggable config string (excludes secrets)."""
        telegram_status = "configured" if self.telegram_bot_token else "none"
        return (
            f"repo={self.main_repo}, worktrees={self.worktrees_dir}, "
            f"sessions={self.sessions_dir}, session={self.session_path}, "
            f"model={self.claude_model}, timeout={self.claude_timeout}s, "
            f"max_turns={self.claude_max_turns}, telegram={telegram_status}"
        )


def _parse_config_file(path: Path) -> dict[str, str]:
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


def resolve_session_path(sessions_dir: Path, session_name: str) -> Path:
    """Resolve session name to full path.

    Resolution order:
    1. Exact match: {sessions_dir}/{session_name}/
    2. Dated match: {sessions_dir}/*-{session_name}/ (most recent if multiple)
    3. New session: returns {sessions_dir}/{YY-MM-DD}-{session_name}/ (not created yet)
    """
    # 1. Exact match
    exact = sessions_dir / session_name
    if exact.exists() and exact.is_dir():
        return exact

    # 2. Dated match (pattern: YY-MM-DD-name)
    pattern = f"*-{session_name}"
    matches = sorted(sessions_dir.glob(pattern), reverse=True)  # Most recent first
    for match in matches:
        if match.is_dir():
            return match

    # 3. New session with date prefix
    dated_name = f"{folder_timestamp()}-{session_name}"
    return sessions_dir / dated_name


# Keep for backward compatibility during transition
def parse_samocode_file(start_path: Path) -> dict[str, str]:
    """DEPRECATED: Find and parse .samocode file starting from given path.

    Use ProjectConfig.from_file() with explicit path instead.
    """
    current = start_path.resolve()
    home = Path.home()

    while current != current.parent and current >= home:
        samocode_path = current / ".samocode"
        if samocode_path.exists():
            return _parse_config_file(samocode_path)
        current = current.parent

    return {}
