"""Startup configuration composition.

Single place that loads the global model-routing config once, selects the
process-wide provider by precedence, validates it against the adapter registry
and the global-config sections, and composes one immutable SamocodeConfig. No
TOML is read after this; the runner reuses the carried GlobalConfig per iteration.

Sits above config/global_config/routing/adapters so it can import the registry
freely; config.py must not import adapters (adapters -> routing -> config cycle).
Composition is side-effect-free: it returns warnings/errors as values, leaving
print/exit and logging to main.py.
"""

from dataclasses import dataclass, replace
from pathlib import Path

from .adapters import supported_providers
from .config import ProjectConfig, RuntimeConfig, SamocodeConfig, resolve_session_path
from .global_config import GlobalConfig, GlobalConfigError, global_config_path
from .phases import PHASE_CONFIGS
from .routing import validate_workflow_overrides

# === Constants ===

LEGACY_DEFAULT_PROVIDER = "claude"


# === Result ===


@dataclass(frozen=True)
class StartupComposition:
    """Outcome of startup composition. `config` is None iff `errors` is non-empty."""

    config: SamocodeConfig | None
    warnings: tuple[str, ...]
    errors: tuple[str, ...]


# === Provider selection ===


def select_provider(
    cli_provider: str | None,
    env_provider: str | None,
    global_config: GlobalConfig | None,
) -> str:
    """Select the process-wide provider.

    Precedence: CLI --provider > env SAMOCODE_PROVIDER > global default_provider
    > legacy default ("claude"). env_provider is the RAW os.environ value so an
    unset variable falls through (a defaulted "claude" would not).
    """
    if cli_provider:
        return cli_provider.lower()
    if env_provider:
        return env_provider.lower()
    if global_config is not None:
        return global_config.default_provider
    return LEGACY_DEFAULT_PROVIDER


# === Global config loading (load-if-present, never create) ===


def load_global_config() -> (
    tuple[GlobalConfig | None, tuple[str, ...], tuple[str, ...]]
):
    """Load the global config if present; legacy mode (None) if absent.

    Creation belongs to `samocode install`; `run` only loads. Returns
    (config_or_None, warnings, errors). A present-but-invalid config is a fatal
    error (not silent legacy) so runs fail fast exactly like install does.
    """
    path = global_config_path()
    if not path.exists():
        warning = (
            f"No global model-routing config at {path}; running in legacy mode "
            f"(env CLAUDE_MODEL/CODEX_MODEL). Run `samocode install` to create it."
        )
        return None, (warning,), ()
    try:
        return GlobalConfig.from_file(path), (), ()
    except GlobalConfigError as exc:
        return None, (), (str(exc),)


# === Composition ===


def compose_startup(
    *,
    config_path: Path,
    session_name: str,
    cli_provider: str | None,
    cli_timeout: int | None,
    env_provider: str | None,
) -> StartupComposition:
    """Load global config once, select+validate the provider, compose SamocodeConfig.

    Aggregates every problem into `errors`; builds `config` only when clean.
    """
    errors: list[str] = []
    warnings: list[str] = []

    global_config, gc_warnings, gc_errors = load_global_config()
    warnings.extend(gc_warnings)
    errors.extend(gc_errors)

    project: ProjectConfig | None = None
    try:
        project = ProjectConfig.from_file(config_path)
    except ValueError as exc:
        errors.append(str(exc))
    if project is not None:
        errors.extend(project.validate())

    provider = select_provider(cli_provider, env_provider, global_config)

    runtime = RuntimeConfig.from_env()
    runtime = replace(runtime, ai_provider=provider)
    if cli_timeout:
        runtime = replace(
            runtime, claude_timeout=cli_timeout, codex_timeout=cli_timeout
        )
    errors.extend(runtime.validate())
    errors.extend(_validate_provider(provider, global_config))

    session_path: Path | None = None
    if project is not None:
        session_path = resolve_session_path(project.sessions, session_name)
        if session_path.exists() and not session_path.is_dir():
            errors.append(f"Session path exists but is not a directory: {session_path}")

    if errors or project is None or session_path is None:
        return StartupComposition(None, tuple(warnings), tuple(errors))

    config = SamocodeConfig(
        project=project,
        runtime=runtime,
        session_path=session_path,
        provider=provider,
        global_config=global_config,
    )
    return StartupComposition(config, tuple(warnings), tuple(errors))


def _validate_provider(
    provider: str, global_config: GlobalConfig | None
) -> list[str]:
    """Registry-aware provider validation.

    - Selected provider must have a registered adapter (replaces hard-coded
      {"claude","codex"}).
    - When global config is present, the selected provider must have a
      [providers.<name>] section, its workflow_overrides must be valid, and
      every PhaseConfig.default_profile must be available for it (so no phase
      can pass startup yet crash on its first iteration).
    - Unselected future-provider sections are never inspected (allowed).

    CLI availability and numeric bounds are checked by RuntimeConfig.validate.
    """
    errors: list[str] = []
    supported = supported_providers()
    if provider not in supported:
        errors.append(
            f"Unsupported provider {provider!r}. Registered adapters: "
            f"{sorted(supported)}."
        )
        return errors  # nothing else is meaningful without an adapter

    if global_config is not None:
        selected = global_config.providers.get(provider)
        if selected is None:
            errors.append(
                f"Selected provider {provider!r} has no [providers.{provider}] "
                f"section in the global config ({global_config_path()})."
            )
        else:
            try:
                validate_workflow_overrides(global_config, selected)
            except GlobalConfigError as exc:
                errors.append(str(exc))
            for phase, phase_config in PHASE_CONFIGS.items():
                if selected.profile(phase_config.default_profile) is None:
                    errors.append(
                        f"phase {phase.value!r} default profile "
                        f"{phase_config.default_profile!r} is not available for "
                        f"provider {provider!r} (known profiles: "
                        f"{sorted(selected.profiles)})."
                    )
    return errors
