"""Global model-routing configuration for Samocode.

Reads the user-global TOML config that maps providers to named profiles
(model + optional reasoning effort). Distinct from the per-project
`.samocode` KEY=VALUE file parsed in `worker/config.py`.

Location: `$XDG_CONFIG_HOME/samocode/config.toml` when XDG_CONFIG_HOME is a
valid absolute path, else `~/.config/samocode/config.toml`.
"""

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

# === Constants ===

CONFIG_VERSION = 1
DEFAULT_PROVIDER = "claude"
DEFAULT_PROFILE = "standard"

# Canonical profile names every built-in provider ships in generated defaults.
CANONICAL_PROFILES: tuple[str, ...] = ("light", "standard", "strong", "max")

# Single source of truth for built-in defaults and installer output.
_DEFAULT_CONFIG_TOML = """version = 1
default_provider = "claude"
default_profile = "standard"

[providers.claude]
executable = "claude"
[providers.claude.profiles.light]
model = "claude-haiku-4-5-20251001"
[providers.claude.profiles.standard]
model = "claude-sonnet-4-6"
effort = "high"
[providers.claude.profiles.strong]
model = "claude-opus-4-8"
effort = "high"
[providers.claude.profiles.max]
model = "claude-opus-4-8"
effort = "xhigh"

[providers.codex]
executable = "codex"
[providers.codex.profiles.light]
model = "gpt-5.6-luna"
effort = "low"
[providers.codex.profiles.standard]
model = "gpt-5.6-terra"
effort = "medium"
[providers.codex.profiles.strong]
model = "gpt-5.6-sol"
effort = "medium"
[providers.codex.profiles.max]
model = "gpt-5.6-sol"
effort = "xhigh"

[workflow_overrides]
# investigation = "max"
"""


class GlobalConfigError(ValueError):
    """Raised when the global config is missing, malformed, or invalid."""


# === Data structures ===


@dataclass(frozen=True)
class Profile:
    """A named execution profile: a model and optional reasoning effort.

    `effort` is a free-form string when set; the semantic `max` profile uses
    `xhigh`, while `"max"` itself remains a legal custom effort value.
    """

    name: str
    model: str
    effort: str | None = None


@dataclass(frozen=True)
class Provider:
    """A provider (e.g. claude, codex) with its executable and profiles."""

    name: str
    executable: str
    profiles: Mapping[str, Profile]

    def profile(self, name: str) -> Profile | None:
        """Look up a profile by name; None if absent."""
        return self.profiles.get(name)


@dataclass(frozen=True)
class GlobalConfig:
    """Parsed, validated global model-routing configuration."""

    version: int
    default_provider: str
    default_profile: str
    providers: Mapping[str, Provider]
    workflow_overrides: Mapping[str, str]

    def profile(self, provider: str, name: str) -> Profile | None:
        """Look up a profile by provider name and profile name."""
        found = self.providers.get(provider)
        return found.profile(name) if found is not None else None

    @classmethod
    def from_file(cls, path: Path) -> "GlobalConfig":
        """Load and validate a global config file.

        Raises GlobalConfigError if missing, unreadable, malformed, or invalid.
        """
        if not path.exists():
            raise GlobalConfigError(f"Global config not found: {path}")
        if not path.is_file():
            raise GlobalConfigError(f"Global config path is not a file: {path}")
        try:
            data = tomllib.loads(path.read_text())
        except tomllib.TOMLDecodeError as exc:
            raise GlobalConfigError(f"Malformed TOML in {path}: {exc}") from exc
        return cls.from_mapping(data, source=str(path))

    @classmethod
    def from_mapping(cls, data: Mapping[str, object], source: str) -> "GlobalConfig":
        """Validate a parsed TOML mapping into a GlobalConfig.

        Aggregates every problem and raises a single GlobalConfigError.
        """
        errors: list[str] = []

        version = _validate_version(data.get("version"), errors)
        providers = _parse_providers(data.get("providers"), errors)
        default_provider = _require_nonempty_str(
            data.get("default_provider"), "default_provider", errors
        )
        default_profile = _require_nonempty_str(
            data.get("default_profile"), "default_profile", errors
        )
        overrides = _parse_workflow_overrides(data.get("workflow_overrides"), errors)

        _validate_defaults(default_provider, default_profile, providers, errors)

        if errors:
            raise GlobalConfigError(
                f"Invalid global config ({source}):\n  - " + "\n  - ".join(errors)
            )

        return cls(
            version=version,
            default_provider=default_provider,
            default_profile=default_profile,
            providers=providers,
            workflow_overrides=overrides,
        )


# === Public helpers ===


def global_config_path() -> Path:
    """Resolve the global config path from XDG_CONFIG_HOME or the default.

    Uses `$XDG_CONFIG_HOME/samocode/config.toml` only when XDG_CONFIG_HOME is a
    valid absolute path; otherwise `~/.config/samocode/config.toml`.
    """
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg and Path(xdg).is_absolute():
        base = Path(xdg)
    else:
        base = Path.home() / ".config"
    return base / "samocode" / "config.toml"


def default_config() -> GlobalConfig:
    """Return the built-in default config (parsed from the canonical TOML)."""
    data = tomllib.loads(_DEFAULT_CONFIG_TOML)
    return GlobalConfig.from_mapping(data, source="<built-in defaults>")


def default_config_toml() -> str:
    """Return the canonical default config as TOML text (for the installer)."""
    return _DEFAULT_CONFIG_TOML


# === Validation internals ===


def _validate_version(value: object, errors: list[str]) -> int:
    """Require version present and equal to the supported version."""
    if value is None:
        errors.append("missing required field: version")
        return CONFIG_VERSION
    if value != CONFIG_VERSION:
        errors.append(f"unsupported version: {value!r} (expected {CONFIG_VERSION})")
    return value if isinstance(value, int) else CONFIG_VERSION


def _require_nonempty_str(value: object, field: str, errors: list[str]) -> str:
    """Require a non-empty string top-level field."""
    if not isinstance(value, str) or not value:
        errors.append(f"missing or invalid required field: {field}")
        return ""
    return value


def _parse_providers(raw: object, errors: list[str]) -> dict[str, Provider]:
    """Parse the [providers] table; any provider name is accepted."""
    if raw is None:
        errors.append("missing required field: providers")
        return {}
    if not isinstance(raw, dict):
        errors.append("providers must be a table")
        return {}
    providers: dict[str, Provider] = {}
    for name, value in raw.items():
        parsed = _parse_provider(name, value, errors)
        if parsed is not None:
            providers[name] = parsed
    if not providers:
        errors.append("at least one provider is required")
    return providers


def _parse_provider(name: str, raw: object, errors: list[str]) -> Provider | None:
    """Parse one provider table. Unknown keys are ignored (forward-compat)."""
    if not isinstance(raw, dict):
        errors.append(f"provider {name!r} must be a table")
        return None
    executable = raw.get("executable")
    if not isinstance(executable, str) or not executable:
        errors.append(f"provider {name!r}: 'executable' must be a non-empty string")
        return None
    profiles_raw = raw.get("profiles")
    if not isinstance(profiles_raw, dict):
        errors.append(f"provider {name!r}: 'profiles' must be a table")
        return None
    profiles: dict[str, Profile] = {}
    for prof_name, prof_value in profiles_raw.items():
        profile = _parse_profile(name, prof_name, prof_value, errors)
        if profile is not None:
            profiles[prof_name] = profile
    if not profiles:
        errors.append(f"provider {name!r}: at least one profile is required")
        return None
    return Provider(name=name, executable=executable, profiles=profiles)


def _parse_profile(
    provider: str, name: str, raw: object, errors: list[str]
) -> Profile | None:
    """Parse one profile table. Requires non-empty model; effort optional."""
    if not isinstance(raw, dict):
        errors.append(f"profile {provider}.{name} must be a table")
        return None
    model = raw.get("model")
    if not isinstance(model, str) or not model:
        errors.append(f"profile {provider}.{name}: 'model' must be a non-empty string")
        return None
    effort_raw = raw.get("effort")
    effort: str | None = None
    if effort_raw is not None:
        if not isinstance(effort_raw, str) or not effort_raw:
            errors.append(
                f"profile {provider}.{name}: 'effort' must be a non-empty string when set"
            )
        else:
            effort = effort_raw
    return Profile(name=name, model=model, effort=effort)


def _parse_workflow_overrides(raw: object, errors: list[str]) -> dict[str, str]:
    """Parse [workflow_overrides] as phase-name -> profile-name strings."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        errors.append("workflow_overrides must be a table")
        return {}
    overrides: dict[str, str] = {}
    for phase, profile in raw.items():
        if not isinstance(profile, str) or not profile:
            errors.append(f"workflow_overrides[{phase}] must be a non-empty string")
        else:
            overrides[phase] = profile
    return overrides


def _validate_defaults(
    default_provider: str,
    default_profile: str,
    providers: Mapping[str, Provider],
    errors: list[str],
) -> None:
    """Ensure default_provider/default_profile reference existing entries."""
    if not default_provider:
        return
    provider = providers.get(default_provider)
    if provider is None:
        errors.append(
            f"default_provider {default_provider!r} has no matching "
            f"[providers.{default_provider}] section"
        )
        return
    if default_profile and default_profile not in provider.profiles:
        errors.append(
            f"default_profile {default_profile!r} not found under "
            f"provider {default_provider!r}"
        )
