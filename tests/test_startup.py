"""Tests for startup configuration composition (worker.startup)."""

import tomllib
from collections.abc import Callable
from pathlib import Path

import pytest

from worker import cli as main
from worker.global_config import (
    GlobalConfig,
    default_config_toml,
)
from worker.startup import (
    LEGACY_DEFAULT_PROVIDER,
    StartupComposition,
    compose_startup,
    load_global_config,
    select_provider,
)


# === Helpers ===


def _config(default_provider: str = "claude", extra: str = "") -> GlobalConfig:
    """Build a GlobalConfig from the canonical default TOML, tweaked."""
    text = default_config_toml().replace(
        'default_provider = "claude"', f'default_provider = "{default_provider}"'
    )
    text += extra
    return GlobalConfig.from_mapping(tomllib.loads(text), source="<test>")


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Project .samocode + real dirs, fake CLIs, isolated XDG (no config file)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    wt = tmp_path / "wt"
    wt.mkdir()
    sess = tmp_path / "sess"
    sess.mkdir()
    samocode = tmp_path / ".samocode"
    samocode.write_text(f"MAIN_REPO={repo}\nWORKTREES={wt}\nSESSIONS={sess}\n")

    claude = tmp_path / "claude"
    claude.write_text("#!/bin/sh\n")
    claude.chmod(0o755)
    codex = tmp_path / "codex"
    codex.write_text("#!/bin/sh\n")
    codex.chmod(0o755)

    xdg = tmp_path / "xdg"
    xdg.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setenv("CLAUDE_PATH", str(claude))
    monkeypatch.setenv("CODEX_PATH", str(codex))
    for var in ("SAMOCODE_PROVIDER", "CLAUDE_MODEL", "CODEX_MODEL", "CLAUDE_TIMEOUT"):
        monkeypatch.delenv(var, raising=False)

    config_file = xdg / "samocode" / "config.toml"

    def write_config(text: str = default_config_toml()) -> Path:
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(text)
        return config_file

    return _Project(
        samocode=samocode,
        config_file=config_file,
        write_config=write_config,
        claude=claude,
        codex=codex,
    )


class _Project:
    def __init__(
        self,
        samocode: Path,
        config_file: Path,
        write_config: Callable[..., Path],
        claude: Path,
        codex: Path,
    ) -> None:
        self.samocode = samocode
        self.config_file = config_file
        self.write_config = write_config
        self.claude = claude
        self.codex = codex

    def compose(
        self,
        *,
        config_path: Path | None = None,
        cli_provider: str | None = None,
        cli_timeout: int | None = None,
        env_provider: str | None = None,
    ) -> StartupComposition:
        return compose_startup(
            config_path=config_path if config_path is not None else self.samocode,
            session_name="task",
            cli_provider=cli_provider,
            cli_timeout=cli_timeout,
            env_provider=env_provider,
        )


# === select_provider precedence ===


class TestSelectProvider:
    def test_cli_beats_all(self) -> None:
        assert select_provider("codex", "claude", _config("claude")) == "codex"

    def test_env_beats_config_and_legacy(self) -> None:
        assert select_provider(None, "codex", _config("claude")) == "codex"

    def test_config_default_beats_legacy(self) -> None:
        assert select_provider(None, None, _config("codex")) == "codex"

    def test_legacy_default_when_nothing(self) -> None:
        assert select_provider(None, None, None) == LEGACY_DEFAULT_PROVIDER

    def test_empty_env_falls_through(self) -> None:
        assert select_provider(None, "", _config("codex")) == "codex"

    def test_cli_lowercased(self) -> None:
        assert select_provider("CODEX", None, None) == "codex"


# === load_global_config warning/error paths ===


class TestLoadGlobalConfig:
    def test_absent_returns_warning_and_legacy(self, project: _Project) -> None:
        config, warnings, errors = load_global_config()
        assert config is None
        assert errors == ()
        assert len(warnings) == 1
        assert str(project.config_file) in warnings[0]
        assert "samocode install" in warnings[0]

    def test_present_valid_loads(self, project: _Project) -> None:
        project.write_config()
        config, warnings, errors = load_global_config()
        assert config is not None
        assert warnings == ()
        assert errors == ()

    def test_present_invalid_is_error(self, project: _Project) -> None:
        project.write_config("version = = 1\n")
        config, warnings, errors = load_global_config()
        assert config is None
        assert warnings == ()
        assert len(errors) == 1


# === compose_startup precedence ===


class TestComposePrecedence:
    def test_cli_provider_wins(self, project: _Project) -> None:
        project.write_config()  # default_provider=claude
        result = project.compose(cli_provider="codex", env_provider="claude")
        assert result.config is not None
        assert result.config.ai_provider == "codex"

    def test_env_provider_over_config(self, project: _Project) -> None:
        project.write_config()  # default_provider=claude
        result = project.compose(env_provider="codex")
        assert result.config is not None
        assert result.config.ai_provider == "codex"

    def test_config_default_used(self, project: _Project) -> None:
        text = default_config_toml().replace(
            'default_provider = "claude"', 'default_provider = "codex"'
        )
        project.write_config(text)
        result = project.compose()
        assert result.config is not None
        assert result.config.ai_provider == "codex"

    def test_legacy_default_no_config(self, project: _Project) -> None:
        result = project.compose()
        assert result.config is not None
        assert result.config.ai_provider == "claude"
        assert result.config.global_config is None


# === Model authority + env overrides ===


class TestModelAuthority:
    def test_profile_model_authoritative(
        self, project: _Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CLAUDE_MODEL", "env-override")
        project.write_config()  # claude standard profile
        result = project.compose()
        assert result.config is not None
        # standard profile model, not the env override
        assert result.config.ai_model == "claude-sonnet-4-6"

    def test_legacy_uses_env_model(
        self, project: _Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CLAUDE_MODEL", "env-override")
        result = project.compose()  # no config file -> legacy
        assert result.config is not None
        assert result.config.ai_model == "env-override"

    def test_path_and_timeout_env_always_apply(self, project: _Project) -> None:
        project.write_config()
        result = project.compose(cli_timeout=99)
        assert result.config is not None
        assert result.config.claude_path == project.claude
        assert result.config.claude_timeout == 99


# === Provider validation ===


class TestProviderValidation:
    def test_unsupported_provider_errors(self, project: _Project) -> None:
        project.write_config()
        result = project.compose(cli_provider="gemini")
        assert result.config is None
        assert any("Unsupported provider" in e for e in result.errors)

    def test_missing_provider_section_errors(self, project: _Project) -> None:
        # Config with only codex; default_provider=codex is valid, but select claude.
        text = (
            'version = 1\n'
            'default_provider = "codex"\n'
            'default_profile = "standard"\n'
            '[providers.codex]\n'
            'executable = "codex"\n'
            '[providers.codex.profiles.standard]\n'
            'model = "gpt-5.6-terra"\n'
        )
        project.write_config(text)
        result = project.compose(cli_provider="claude")
        assert result.config is None
        assert any("no [providers.claude] section" in e for e in result.errors)

    def test_future_provider_section_allowed(self, project: _Project) -> None:
        extra = (
            "\n[providers.gemini]\n"
            'executable = "gemini"\n'
            "[providers.gemini.profiles.standard]\n"
            'model = "gemini-x"\n'
        )
        project.write_config(default_config_toml() + extra)
        result = project.compose()  # default claude
        assert result.config is not None
        assert result.config.ai_provider == "claude"

    def test_invalid_workflow_override_errors(self, project: _Project) -> None:
        extra = '\n[workflow_overrides]\ninvestigation = "nonexistent"\n'
        text = default_config_toml().rstrip() + extra
        project.write_config(text)
        result = project.compose()
        assert result.config is None
        assert any("workflow_overrides" in e.lower() for e in result.errors)


# === CLI availability ===


class TestCliAvailability:
    def test_missing_selected_cli_errors(
        self, project: _Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CLAUDE_PATH", str(project.claude) + "-missing")
        project.write_config()
        result = project.compose()  # claude
        assert result.config is None
        assert any("Claude CLI not found" in e for e in result.errors)

    def test_other_provider_cli_irrelevant(
        self, project: _Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Codex CLI missing, but selected provider is claude -> no error.
        monkeypatch.setenv("CODEX_PATH", str(project.codex) + "-missing")
        project.write_config()
        result = project.compose()  # claude
        assert result.config is not None


# === No runtime reload ===


class TestNoReload:
    def test_single_toml_read(
        self, project: _Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project.write_config()
        calls: list[Path] = []
        original = GlobalConfig.from_file.__func__  # type: ignore[attr-defined]

        def counting(cls, path: Path) -> GlobalConfig:
            calls.append(path)
            return original(cls, path)

        monkeypatch.setattr(GlobalConfig, "from_file", classmethod(counting))
        result = project.compose()
        assert result.config is not None
        assert len(calls) == 1
        assert result.config.global_config is not None


# === Composition end-to-end ===


class TestComposition:
    def test_clean_composition(self, project: _Project) -> None:
        project.write_config()
        result = project.compose()
        assert result.config is not None
        assert result.errors == ()
        assert result.config.provider == "claude"
        assert result.config.global_config is not None
        assert result.config.session_path is not None

    def test_missing_samocode_errors(self, project: _Project) -> None:
        result = project.compose(config_path=project.samocode.parent / "nope.samocode")
        assert result.config is None
        assert len(result.errors) >= 1


# === Registry-driven CLI choices ===


def test_provider_choices_from_registry() -> None:
    from worker import supported_providers

    parser = main.build_parser()
    run_action = next(
        a for a in parser._subparsers._group_actions[0].choices["run"]._actions  # type: ignore[attr-defined]
        if getattr(a, "dest", "") == "provider"
    )
    assert set(run_action.choices) == set(supported_providers())
