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
    src_root: Path | None = None,
) -> InstallResult:
    """Symlink or copy a single asset (file or dir) to target.

    Idempotency rules:
    - target does not exist -> create (INSTALLED)
    - target is a samocode-owned symlink -> refresh: remove + recreate (UPDATED)
    - target is a foreign symlink (points outside src_root) -> warn and skip (SKIPPED)
    - target is a real file/dir (not a symlink) -> warn and skip (SKIPPED)

    Only symlinks resolving into the samocode source tree are refreshed; foreign
    symlinks (user-managed or installed by another tool) are never clobbered.

    Args:
        src:      Absolute path to the asset inside the samocode source tree.
        target:   Absolute path where the asset should appear.
        mode:     InstallMode.AUTO resolves via resolve_install_mode(src.parent).
        src_root: Samocode source root used for the ownership check. Defaults to
                  resolve_asset_source_dir() so the primitive works standalone.

    Returns:
        InstallResult describing what happened. Never raises on skipped assets.
    """
    if not src.exists():
        # Caller mistake - raise so callers can surface it clearly.
        raise FileNotFoundError(f"Asset source does not exist: {src}")

    owner_root = src_root if src_root is not None else resolve_asset_source_dir()

    # Resolve AUTO before any branching so result.mode is always concrete.
    resolved_mode = (
        resolve_install_mode(src.parent) if mode is InstallMode.AUTO else mode
    )

    # Existing target handling
    if target.is_symlink():
        if _is_samocode_owned(target, owner_root):
            target.unlink()
            outcome_on_create = InstallOutcome.UPDATED
        else:
            # Foreign symlink - never clobber a link we do not own.
            warning = (
                f"{target} is a symlink not managed by samocode; skipping. "
                "Remove it manually if you want samocode to manage it."
            )
            return InstallResult(
                src=src,
                target=target,
                outcome=InstallOutcome.SKIPPED,
                mode=resolved_mode,
                warning=warning,
            )
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


# === Uninstall data model ===


class UninstallOutcome(Enum):
    """What happened when _uninstall_asset() ran."""

    REMOVED = "removed"  # samocode-owned symlink deleted
    SKIPPED = "skipped"  # target present but not samocode-owned; left untouched
    NOT_FOUND = "not_found"  # target does not exist


@dataclass(frozen=True)
class UninstallResult:
    """Result of a single _uninstall_asset() call."""

    target: Path
    outcome: UninstallOutcome
    note: str | None = None


# === Asset enumeration ===


def _enumerate_asset_entries(asset_class: AssetClass, src_root: Path) -> list[Path]:
    """Return the source items to install for an asset class.

    - is_dir_asset=True  -> immediate subdirectories (mirrors `skills/*/`)
    - is_dir_asset=False -> *.md files (mirrors `agents/*.md`, `commands/*.md`)

    Returns [] (no error) when the source subdir is missing or empty so callers
    report zero items instead of crashing.
    """
    src_dir = src_root / asset_class.src_subdir
    if not src_dir.is_dir():
        return []
    if asset_class.is_dir_asset:
        return sorted(p for p in src_dir.iterdir() if p.is_dir())
    # Exclude *_TEMPLATE.md placeholders (e.g. agents/AGENT_TEMPLATE.md) - they
    # are scaffolding, not real installable assets.
    return sorted(
        p for p in src_dir.glob("*.md") if not p.name.endswith("_TEMPLATE.md")
    )


# === Ownership check ===


def _is_samocode_owned(target: Path, src_root: Path) -> bool:
    """Return True if target is a symlink resolving into the samocode source tree.

    Symlink mode: only links pointing into src_root are samocode-owned and safe
    to remove. Foreign symlinks (pointing elsewhere) and symlinks from a
    different samocode checkout (different src_root) are not matched.

    Copy mode: a copied file is indistinguishable from a user file of the same
    name, so real (non-symlink) targets always return False. See uninstall()
    docstring for the documented limitation.
    """
    if not target.is_symlink():
        return False
    try:
        resolved = target.resolve()
    except OSError:
        # Symlink loop / permission error - conservatively leave it alone.
        return False
    # Path-aware containment (not string prefix) so a sibling tree like
    # "<src_root>-backup" is never mistaken for the samocode source tree.
    return resolved == src_root or src_root in resolved.parents


# === Single-asset uninstall primitive ===


def _uninstall_asset(target: Path, src_root: Path) -> UninstallResult:
    """Remove a single asset if samocode-owned. Never raises."""
    if not target.exists() and not target.is_symlink():
        return UninstallResult(target=target, outcome=UninstallOutcome.NOT_FOUND)

    if _is_samocode_owned(target, src_root):
        target.unlink()
        return UninstallResult(target=target, outcome=UninstallOutcome.REMOVED)

    if target.is_symlink():
        note = (
            f"{target} is a symlink but does not point into the samocode "
            "source tree; left untouched."
        )
    else:
        note = (
            f"{target} is a real file/directory (not a symlink); samocode cannot "
            "verify ownership. Left untouched - remove manually if installed via copy."
        )
    return UninstallResult(target=target, outcome=UninstallOutcome.SKIPPED, note=note)


# === Printing helpers ===


def _print_install_section(asset_class_name: str, provider: str) -> None:
    """Print a section header, e.g. 'Installing claude skills...'."""
    print(f"\nInstalling {provider} {asset_class_name}...")


def _print_uninstall_section(asset_class_name: str, provider: str) -> None:
    """Print a section header, e.g. 'Removing claude skills...'."""
    print(f"\nRemoving {provider} {asset_class_name}...")


def _print_install_item(result: InstallResult) -> None:
    """Print one line per asset, mirroring install.sh output."""
    name = result.target.name
    if result.outcome is InstallOutcome.INSTALLED:
        print(f"  Installing: {name}")
    elif result.outcome is InstallOutcome.UPDATED:
        print(f"  Updating: {name}")
    else:
        print(f"  Warning: {name} exists and is not a symlink, skipping")


def _print_uninstall_item(result: UninstallResult) -> None:
    """Print one line per asset during uninstall (silent on NOT_FOUND)."""
    if result.outcome is UninstallOutcome.REMOVED:
        print(f"  Removing: {result.target.name}")
    elif result.outcome is UninstallOutcome.SKIPPED:
        print(f"  Skipping: {result.target.name}")
        if result.note:
            print(f"    {result.note}")


def _print_install_summary(results: list[InstallResult], src_root: Path) -> None:
    """Print counts summary + .samocode project-setup reminder (bash parity)."""

    def _count(subdir: str) -> int:
        marker = src_root / subdir
        return len(
            {
                r.src
                for r in results
                if r.outcome is not InstallOutcome.SKIPPED and marker in r.src.parents
            }
        )

    print("\nInstallation complete!\n")
    print("Installed:")
    print(f"  - {_count('skills')} skills")
    print(f"  - {_count('agents')} Claude agents")
    print(f"  - {_count('commands')} Claude commands")
    print("")
    print("============================================================")
    print("IMPORTANT: Project Setup Required")
    print("============================================================")
    print("")
    print("For each project where you use samocode, create a .samocode file:")
    print("")
    print("  MAIN_REPO=~/your-project/repo")
    print("  WORKTREES=~/your-project/worktrees/")
    print("  SESSIONS=~/your-project/_sessions/")
    print("")
    print("Without this file, samocode will refuse to run.")
    print("")
    print("============================================================")
    print("")
    print("Restart Claude Code and/or Codex to apply changes.")


def _print_uninstall_summary(results: list[UninstallResult]) -> None:
    """Print removal counts."""
    n_removed = sum(1 for r in results if r.outcome is UninstallOutcome.REMOVED)
    n_skipped = sum(1 for r in results if r.outcome is UninstallOutcome.SKIPPED)
    print(f"\nUninstall complete! Removed {n_removed}, skipped {n_skipped}.")


# === Orchestration ===


def install(copy: bool | None = None) -> list[InstallResult]:
    """Install all samocode assets (skills, agents, commands) to provider dirs.

    Walks ASSET_CLASSES x providers, enumerates source entries, and calls
    install_asset() for each (src, target) pair. Prints per-item lines, a
    summary, and the .samocode project-setup reminder to stdout.

    Args:
        copy: None  -> InstallMode.AUTO    (symlink in git repo; copy elsewhere)
              True  -> InstallMode.COPY     (force copy; e.g. pip-installed)
              False -> InstallMode.SYMLINK  (force symlink; dev override)

    Returns:
        All InstallResult objects, in order, so callers can aggregate by
        outcome without re-parsing stdout.
    """
    if copy is None:
        mode = InstallMode.AUTO
    elif copy:
        mode = InstallMode.COPY
    else:
        mode = InstallMode.SYMLINK

    src_root = resolve_asset_source_dir()
    results: list[InstallResult] = []

    for asset_class in ASSET_CLASSES:
        entries = _enumerate_asset_entries(asset_class, src_root)
        for provider in asset_class.providers:
            target_dir = resolve_target_dir(asset_class, provider)
            _print_install_section(asset_class.name, provider)
            for src in entries:
                target = target_dir / src.name
                try:
                    result = install_asset(src, target, mode, src_root=src_root)
                except OSError as e:
                    # Permission denied, read-only dir, disk full, etc. Report
                    # and keep going so one bad target does not abort the rest.
                    print(f"  Error: failed to install {src.name}: {e}")
                    continue
                results.append(result)
                _print_install_item(result)

    _print_install_summary(results, src_root)
    return results


def uninstall() -> list[UninstallResult]:
    """Remove samocode-owned assets from all provider target dirs.

    Mirrors install(): walks the same ASSET_CLASSES x providers in the same
    order. Only removes targets that are samocode-owned symlinks (see
    _is_samocode_owned). Real (non-symlink) files - whether user-created or
    copy-mode installs - are left untouched with a SKIPPED result.

    Copy-mode installs are a documented limitation: samocode cannot prove
    ownership of a copied file, so uninstall() never deletes real files.

    Returns:
        All UninstallResult objects, in order.
    """
    src_root = resolve_asset_source_dir()
    results: list[UninstallResult] = []

    for asset_class in ASSET_CLASSES:
        entries = _enumerate_asset_entries(asset_class, src_root)
        for provider in asset_class.providers:
            target_dir = resolve_target_dir(asset_class, provider)
            _print_uninstall_section(asset_class.name, provider)
            for src in entries:
                try:
                    result = _uninstall_asset(target_dir / src.name, src_root)
                except OSError as e:
                    print(f"  Error: failed to remove {src.name}: {e}")
                    continue
                results.append(result)
                _print_uninstall_item(result)

    _print_uninstall_summary(results)
    return results
