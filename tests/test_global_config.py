"""Tests for worker/global_config.py - global model-routing configuration."""

import os
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

from worker.global_config import (
    CANONICAL_PROFILES,
    CONFIG_VERSION,
    GlobalConfig,
    GlobalConfigError,
    Provider,
    default_config,
    default_config_toml,
    global_config_path,
)


class TestDefaultConfig:
    """Built-in defaults match the canonical specification."""

    def test_top_level_fields(self) -> None:
        config = default_config()
        assert config.version == CONFIG_VERSION == 1
        assert config.default_provider == "claude"
        assert config.default_profile == "standard"

    def test_has_both_builtin_providers(self) -> None:
        config = default_config()
        assert set(config.providers) == {"claude", "codex"}
        assert config.providers["claude"].executable == "claude"
        assert config.providers["codex"].executable == "codex"

    def test_canonical_profiles_present_per_provider(self) -> None:
        config = default_config()
        for provider in config.providers.values():
            assert set(CANONICAL_PROFILES).issubset(set(provider.profiles))

    def test_claude_profile_models_and_effort(self) -> None:
        config = default_config()
        light = config.profile("claude", "light")
        standard = config.profile("claude", "standard")
        strong = config.profile("claude", "strong")
        maximum = config.profile("claude", "max")
        assert light is not None and light.model == "claude-haiku-4-5-20251001"
        assert light.effort is None
        assert standard is not None and standard.effort == "high"
        assert strong is not None and strong.model == "claude-opus-4-8"
        assert maximum is not None and maximum.effort == "xhigh"

    def test_codex_profile_models_and_effort(self) -> None:
        config = default_config()
        light = config.profile("codex", "light")
        standard = config.profile("codex", "standard")
        maximum = config.profile("codex", "max")
        assert light is not None and light.effort == "low"
        assert standard is not None and standard.model == "gpt-5.6-terra"
        assert maximum is not None and maximum.effort == "xhigh"

    def test_workflow_overrides_empty_by_default(self) -> None:
        # The canonical file only carries a commented example line.
        assert default_config().workflow_overrides == {}

    def test_default_toml_roundtrips_to_default_config(self) -> None:
        from_text = GlobalConfig.from_mapping(
            tomllib.loads(default_config_toml()), source="<t>"
        )
        assert from_text == default_config()


class TestGlobalConfigPath:
    """XDG-aware path resolution edge cases."""

    def _path_with_env(self, env: dict[str, str]) -> Path:
        with (
            patch.dict(os.environ, env, clear=True),
            patch.object(Path, "home", return_value=Path("/home/user")),
        ):
            return global_config_path()

    def test_xdg_unset_uses_default(self) -> None:
        result = self._path_with_env({})
        assert result == Path("/home/user/.config/samocode/config.toml")

    def test_xdg_empty_uses_default(self) -> None:
        result = self._path_with_env({"XDG_CONFIG_HOME": ""})
        assert result == Path("/home/user/.config/samocode/config.toml")

    def test_xdg_relative_uses_default(self) -> None:
        result = self._path_with_env({"XDG_CONFIG_HOME": "relative/dir"})
        assert result == Path("/home/user/.config/samocode/config.toml")

    def test_xdg_absolute_used(self) -> None:
        result = self._path_with_env({"XDG_CONFIG_HOME": "/custom/xdg"})
        assert result == Path("/custom/xdg/samocode/config.toml")


class TestFromFile:
    """Loading from disk: existence, file-ness, malformed TOML."""

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(GlobalConfigError, match="not found"):
            GlobalConfig.from_file(tmp_path / "nope.toml")

    def test_directory_path_raises(self, tmp_path: Path) -> None:
        with pytest.raises(GlobalConfigError, match="not a file"):
            GlobalConfig.from_file(tmp_path)

    def test_malformed_toml_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "config.toml"
        f.write_text("version = = 1\n")
        with pytest.raises(GlobalConfigError, match="Malformed TOML"):
            GlobalConfig.from_file(f)

    def test_valid_file_loads(self, tmp_path: Path) -> None:
        f = tmp_path / "config.toml"
        f.write_text(default_config_toml())
        config = GlobalConfig.from_file(f)
        assert config == default_config()


class TestValidation:
    """Missing/invalid required fields raise aggregated errors."""

    _MINIMAL = (
        "version = 1\n"
        'default_provider = "claude"\n'
        'default_profile = "standard"\n'
        "[providers.claude]\n"
        'executable = "claude"\n'
        "[providers.claude.profiles.standard]\n"
        'model = "m"\n'
    )

    def _load(self, text: str) -> GlobalConfig:
        return GlobalConfig.from_mapping(tomllib.loads(text), source="<test>")

    def test_minimal_valid(self) -> None:
        config = self._load(self._MINIMAL)
        assert config.default_provider == "claude"

    def test_missing_version_raises(self) -> None:
        text = self._MINIMAL.replace("version = 1\n", "")
        with pytest.raises(GlobalConfigError, match="version"):
            self._load(text)

    def test_wrong_version_raises(self) -> None:
        text = self._MINIMAL.replace("version = 1", "version = 2")
        with pytest.raises(GlobalConfigError, match="unsupported version"):
            self._load(text)

    def test_missing_default_provider_raises(self) -> None:
        text = self._MINIMAL.replace('default_provider = "claude"\n', "")
        with pytest.raises(GlobalConfigError, match="default_provider"):
            self._load(text)

    def test_missing_default_profile_raises(self) -> None:
        text = self._MINIMAL.replace('default_profile = "standard"\n', "")
        with pytest.raises(GlobalConfigError, match="default_profile"):
            self._load(text)

    def test_empty_model_raises(self) -> None:
        text = self._MINIMAL.replace('model = "m"', 'model = ""')
        with pytest.raises(GlobalConfigError, match="model"):
            self._load(text)

    def test_dangling_default_provider_raises(self) -> None:
        text = self._MINIMAL.replace(
            'default_provider = "claude"', 'default_provider = "ghost"'
        )
        with pytest.raises(GlobalConfigError, match="no matching"):
            self._load(text)

    def test_dangling_default_profile_raises(self) -> None:
        text = self._MINIMAL.replace(
            'default_profile = "standard"', 'default_profile = "ghost"'
        )
        with pytest.raises(GlobalConfigError, match="not found under"):
            self._load(text)

    def test_no_providers_raises(self) -> None:
        text = (
            'version = 1\ndefault_provider = "claude"\ndefault_profile = "standard"\n'
        )
        with pytest.raises(GlobalConfigError, match="provider"):
            self._load(text)


class TestCustomAndFuture:
    """Custom profiles, future-provider sections, literal 'max' effort."""

    def _load(self, text: str) -> GlobalConfig:
        return GlobalConfig.from_mapping(tomllib.loads(text), source="<test>")

    def test_custom_profile_allowed(self) -> None:
        text = (
            "version = 1\n"
            'default_provider = "claude"\n'
            'default_profile = "standard"\n'
            "[providers.claude]\n"
            'executable = "claude"\n'
            "[providers.claude.profiles.standard]\n"
            'model = "m"\n'
            "[providers.claude.profiles.custom]\n"
            'model = "special"\n'
            'effort = "medium"\n'
        )
        config = self._load(text)
        custom = config.profile("claude", "custom")
        assert custom is not None
        assert custom.model == "special"
        assert custom.effort == "medium"

    def test_literal_max_effort_preserved(self) -> None:
        text = (
            "version = 1\n"
            'default_provider = "claude"\n'
            'default_profile = "standard"\n'
            "[providers.claude]\n"
            'executable = "claude"\n'
            "[providers.claude.profiles.standard]\n"
            'model = "m"\n'
            "[providers.claude.profiles.custom]\n"
            'model = "special"\n'
            'effort = "max"\n'
        )
        config = self._load(text)
        custom = config.profile("claude", "custom")
        assert custom is not None and custom.effort == "max"

    def test_future_provider_section_inert(self) -> None:
        text = (
            "version = 1\n"
            'default_provider = "claude"\n'
            'default_profile = "standard"\n'
            "[providers.claude]\n"
            'executable = "claude"\n'
            "[providers.claude.profiles.standard]\n"
            'model = "m"\n'
            "[providers.futureai]\n"
            'executable = "futureai"\n'
            'region = "eu"\n'  # unknown key ignored
            "[providers.futureai.profiles.standard]\n"
            'model = "future-1"\n'
        )
        config = self._load(text)
        assert "futureai" in config.providers
        future = config.profile("futureai", "standard")
        assert future is not None and future.model == "future-1"
        # Default routing still points at claude; futureai is inert.
        assert config.default_provider == "claude"

    def test_unknown_top_level_key_ignored(self) -> None:
        text = (
            "version = 1\n"
            'default_provider = "claude"\n'
            'default_profile = "standard"\n'
            "experimental = true\n"
            "[providers.claude]\n"
            'executable = "claude"\n'
            "[providers.claude.profiles.standard]\n"
            'model = "m"\n'
        )
        config = self._load(text)
        assert config.default_provider == "claude"


class TestLookup:
    """Profile lookup by provider+name."""

    def test_provider_profile_lookup(self) -> None:
        provider: Provider = default_config().providers["claude"]
        standard = provider.profile("standard")
        assert standard is not None and standard.model == "claude-sonnet-4-6"
        assert provider.profile("absent") is None

    def test_globalconfig_profile_lookup(self) -> None:
        config = default_config()
        strong = config.profile("claude", "strong")
        assert strong is not None and strong.model == "claude-opus-4-8"
        assert config.profile("absent", "standard") is None
        assert config.profile("claude", "absent") is None
