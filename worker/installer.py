"""Installer primitives: source resolution, target computation, asset linking/copying.

Phase 2 (install/uninstall orchestration) builds on these atoms.
No file walking or orchestration here.
"""

import os
import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

# === Enums ===


class InstallMode(Enum):
    """How an asset is placed at the target location."""

    SYMLINK = "symlink"  # Symlink src -> target (fast, live edits visible)
    COPY = "copy"  # Deep copy src -> target (for pip-installed packages)
    AUTO = "auto"  # Detect: symlink if inside git repo, copy if site-packages


class InstallOutcome(Enum):
    """What happened when install_asset() ran."""

    INSTALLED = "installed"  # New symlink/copy created
    UPDATED = "updated"  # Existing samocode-owned symlink refreshed
    SKIPPED = "skipped"  # Real (non-symlink) target exists; left untouched


# === Data model ===


@dataclass(frozen=True)
class InstallResult:
    """Result of a single install_asset() call."""

    src: Path
    target: Path
    outcome: InstallOutcome
    mode: InstallMode  # Resolved mode (never AUTO)
    warning: str | None = None


@dataclass(frozen=True)
class AssetClass:
    """Describes one class of installable assets.

    Phase 2 iterates ASSET_CLASSES to drive install/uninstall without
    hard-coding the three asset types.
    """

    name: str  # Human label, e.g. "skills"
    src_subdir: str  # Subdirectory under samocode root, e.g. "skills"
    is_dir_asset: bool  # True -> each entry is a dir; False -> *.md files
    providers: tuple[str, ...]  # Which providers receive this asset class


# Registry - single source of truth for what gets installed where.
# Target dir resolution is deferred to resolve_target_dir() so CODEX_HOME
# is read at call time rather than at import time.
ASSET_CLASSES: tuple[AssetClass, ...] = (
    AssetClass(
        name="skills",
        src_subdir="skills",
        is_dir_asset=True,
        providers=("claude", "codex"),
    ),
    AssetClass(
        name="agents",
        src_subdir="agents",
        is_dir_asset=False,
        providers=("claude",),
    ),
    AssetClass(
        name="commands",
        src_subdir="commands",
        is_dir_asset=False,
        providers=("claude",),
    ),
)


# === Source / target resolution ===


def resolve_asset_source_dir() -> Path:
    """Return the samocode package root containing skills/, agents/, commands/.

    Works in both environments:
    - Repo checkout: installer.py is worker/installer.py; parent.parent is repo root.
    - pip install: same relative layout (hatch bundles assets next to main.py).
    """
    return Path(__file__).parent.parent.resolve()


def resolve_provider_base_dir(provider: str) -> Path:
    """Return the base config dir for a provider (~/.claude or ~/.codex / $CODEX_HOME).

    Reads CODEX_HOME at call time so tests can patch os.environ without
    touching module-level state.
    """
    if provider == "claude":
        return Path.home() / ".claude"
    if provider == "codex":
        codex_home = os.environ.get("CODEX_HOME", "")
        if codex_home:
            return Path(codex_home).expanduser().resolve()
        return Path.home() / ".codex"
    raise ValueError(f"Unknown provider: {provider!r}")


def resolve_target_dir(asset_class: AssetClass, provider: str) -> Path:
    """Return the target directory for an asset class + provider combination.

    e.g. AssetClass(skills, ..., providers=(claude, codex)), provider=codex
         -> ~/.codex/skills
    """
    if provider not in asset_class.providers:
        raise ValueError(
            f"Provider {provider!r} not in asset class {asset_class.name!r} providers"
        )
    return resolve_provider_base_dir(provider) / asset_class.src_subdir


# === Mode detection ===


def _is_inside_git_repo(path: Path) -> bool:
    """Walk up from path looking for a .git entry."""
    current = path.resolve()
    while current != current.parent:
        if (current / ".git").exists():
            return True
        current = current.parent
    return False


def _is_inside_site_packages(path: Path) -> bool:
    """Return True if path contains a site-packages component."""
    return "site-packages" in path.resolve().parts


def resolve_install_mode(src_root: Path) -> InstallMode:
    """Auto-detect the appropriate install mode for a source root.

    - site-packages install -> COPY (symlinks into a package dir are fragile
      across upgrades and virtual-env recreation)
    - git repo checkout -> SYMLINK (live edits are immediately visible)
    - anything else -> COPY (safe default)
    """
    if _is_inside_site_packages(src_root):
        return InstallMode.COPY
    if _is_inside_git_repo(src_root):
        return InstallMode.SYMLINK
    return InstallMode.COPY


# === Core primitive ===


def install_asset(
    src: Path,
    target: Path,
    mode: InstallMode = InstallMode.AUTO,
) -> InstallResult:
    """Symlink or copy a single asset (file or dir) to target.

    Idempotency rules:
    - target does not exist -> create (INSTALLED)
    - target is a symlink (any target) -> refresh: remove + recreate (UPDATED)
    - target is a real file/dir (not a symlink) -> warn and skip (SKIPPED)

    Args:
        src:    Absolute path to the asset inside the samocode source tree.
        target: Absolute path where the asset should appear.
        mode:   InstallMode.AUTO resolves via resolve_install_mode(src.parent).

    Returns:
        InstallResult describing what happened. Never raises on skipped assets.
    """
    if not src.exists():
        # Caller mistake - raise so Phase 2 can surface it clearly.
        raise FileNotFoundError(f"Asset source does not exist: {src}")

    # Resolve AUTO before any branching so result.mode is always concrete.
    resolved_mode = (
        resolve_install_mode(src.parent) if mode is InstallMode.AUTO else mode
    )

    # Existing target handling
    if target.is_symlink():
        target.unlink()
        outcome_on_create = InstallOutcome.UPDATED
    elif target.exists():
        # Real file or directory - never clobber
        warning = (
            f"{target} exists and is not a samocode symlink; skipping. "
            "Remove it manually if you want samocode to manage it."
        )
        return InstallResult(
            src=src,
            target=target,
            outcome=InstallOutcome.SKIPPED,
            mode=resolved_mode,
            warning=warning,
        )
    else:
        outcome_on_create = InstallOutcome.INSTALLED

    # Ensure parent directory exists
    target.parent.mkdir(parents=True, exist_ok=True)

    if resolved_mode is InstallMode.SYMLINK:
        target.symlink_to(src)
    else:
        if src.is_dir():
            shutil.copytree(src, target)
        else:
            shutil.copy2(src, target)

    return InstallResult(
        src=src,
        target=target,
        outcome=outcome_on_create,
        mode=resolved_mode,
    )
