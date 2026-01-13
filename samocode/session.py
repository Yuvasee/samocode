"""Session resolution logic for samocode.

This module contains the business logic for:
1. Determining if session arg is path-based or name-based
2. Determining where to look for .samocode config file
3. Resolving session paths from CLI arguments
"""

from datetime import datetime
from pathlib import Path


def is_path_based_session(session_arg: str) -> bool:
    """Determine if session argument is a path or a name.

    Args:
        session_arg: The --session CLI argument value

    Returns:
        True if path-based (contains "/" or starts with "~")
        False if name-based (just a name like "my-task")

    Examples:
        "/home/dev/project/_sessions/task" → True (absolute path)
        "~/project/_sessions/task" → True (tilde path)
        "project/_sessions/task" → True (relative path with slash)
        "my-task" → False (name only)
        "my cool task" → False (name with spaces, no slash)
    """
    return "/" in session_arg or session_arg.startswith("~")


def determine_config_hint_dir(session_arg: str, samocode_dir: Path) -> Path:
    """Determine directory to use as hint for .samocode file lookup.

    Args:
        session_arg: The --session CLI argument value
        samocode_dir: Path to samocode installation directory

    Returns:
        Directory path to use as hint for config lookup

    Business Logic:
        - Path-based sessions: Use parent of session path.
          This allows finding .samocode in the project directory.

        - Name-based sessions: Use samocode's own directory.
          Config will be loaded from environment variables.
    """
    if is_path_based_session(session_arg):
        return Path(session_arg).expanduser().resolve().parent
    return samocode_dir


def resolve_session_path(
    session_arg: str, sessions_dir: Path
) -> tuple[Path, str, bool]:
    """Resolve session argument to actual session path.

    Args:
        session_arg: The --session CLI argument value
        sessions_dir: Directory where name-based sessions are stored

    Returns:
        Tuple of (session_path, display_name, is_path_based)
        - session_path: Full path to session directory
        - display_name: Human-readable session name for notifications
        - is_path_based: True if session was specified as path, False if name

    Business Logic:
        Path-based:
            - Use the path directly after expansion
            - Display name is parent folder name (project context)

        Name-based:
            - Normalize: lowercase, spaces become dashes
            - Search sessions_dir for existing "*-{name}" pattern
            - If found: use most recent match (sorted alphabetically)
            - If not found: create new path with "YY-MM-DD-{name}"
            - Display name is the full session folder name
    """
    if is_path_based_session(session_arg):
        session_path = Path(session_arg).expanduser().resolve()
        display_name = session_path.parent.name
        return session_path, display_name, True

    # Name-based: normalize and search
    session_name = session_arg.lower().replace(" ", "-")
    existing = list(sessions_dir.glob(f"*-{session_name}"))

    if existing:
        session_path = sorted(existing)[-1]
    else:
        date_prefix = datetime.now().strftime("%y-%m-%d")
        session_path = sessions_dir / f"{date_prefix}-{session_name}"

    display_name = session_path.name
    return session_path, display_name, False
