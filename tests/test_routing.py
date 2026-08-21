"""Tests for worker/routing.py - workflow-phase profile resolution.

Covers:
- Built-in phase-default mapping for every phase
- Workflow override precedence and isolation
- Unknown override phase rejection
- Override profile unavailable for the selected provider
- Per-provider independence
- PhaseConfig required default_profile field
"""

import tomllib
from collections.abc import Mapping

import pytest

from worker.global_config import (
    GlobalConfig,
    GlobalConfigError,
    default_config,
    default_config_toml,
)
from worker.phases import PHASE_CONFIGS, Phase, PhaseConfig
from worker.routing import (
    ProfileSource,
    resolve_workflow_profile,
)


def _toml_dict() -> dict[str, object]:
    """Fresh mutable copy of the default config as a parsed TOML dict."""
    return tomllib.loads(default_config_toml())


def _config_with_overrides(overrides: Mapping[str, str]) -> GlobalConfig:
    data = _toml_dict()
    data["workflow_overrides"] = dict(overrides)
    return GlobalConfig.from_mapping(data, source="<test>")


class TestPhaseDefaults:
    def test_every_builtin_mapping_resolves_to_phase_default(self) -> None:
        """No overrides: every phase resolves PHASE_CONFIGS[phase].default_profile."""
        cfg = default_config()
        provider = cfg.providers["claude"]
        for phase in Phase:
            resolved = resolve_workflow_profile(phase, provider, cfg)
            assert resolved.name == PHASE_CONFIGS[phase].default_profile
            assert resolved.source is ProfileSource.PHASE_DEFAULT

    def test_expected_default_mapping(self) -> None:
        """Defaults match the accepted nine-phase mapping."""
        expected = {
            Phase.INIT: "light",
            Phase.INVESTIGATION: "strong",
            Phase.REQUIREMENTS: "strong",
            Phase.PLANNING: "max",
            Phase.IMPLEMENTATION: "standard",
            Phase.TESTING: "standard",
            Phase.QUALITY: "strong",
            Phase.PR_READINESS: "strong",
            Phase.DONE: "light",
        }
        assert {p: c.default_profile for p, c in PHASE_CONFIGS.items()} == expected


class TestOverrides:
    def test_override_wins_over_builtin_default(self) -> None:
        cfg = _config_with_overrides({"investigation": "max"})
        provider = cfg.providers["claude"]
        resolved = resolve_workflow_profile(Phase.INVESTIGATION, provider, cfg)
        assert resolved.name == "max"
        assert resolved.source is ProfileSource.WORKFLOW_OVERRIDE

    def test_unrelated_phases_unaffected_by_override(self) -> None:
        cfg = _config_with_overrides({"investigation": "light"})
        provider = cfg.providers["claude"]
        resolved = resolve_workflow_profile(Phase.PLANNING, provider, cfg)
        assert resolved.name == "max"  # planning's built-in default, not the override
        assert resolved.source is ProfileSource.PHASE_DEFAULT

    def test_unknown_override_phase_rejected(self) -> None:
        cfg = _config_with_overrides({"implementaton": "strong"})
        provider = cfg.providers["claude"]
        with pytest.raises(GlobalConfigError, match="unknown phase"):
            resolve_workflow_profile(Phase.IMPLEMENTATION, provider, cfg)

    def test_override_profile_unavailable_for_provider_rejected(self) -> None:
        cfg = _config_with_overrides({"quality": "ultra"})
        provider = cfg.providers["claude"]
        with pytest.raises(GlobalConfigError, match="not available for provider"):
            resolve_workflow_profile(Phase.QUALITY, provider, cfg)


class TestProviderIndependence:
    def test_same_phase_resolves_independently_per_provider(self) -> None:
        cfg = default_config()
        claude = resolve_workflow_profile(Phase.DONE, cfg.providers["claude"], cfg)
        codex = resolve_workflow_profile(Phase.DONE, cfg.providers["codex"], cfg)
        assert claude.name == codex.name == "light"


class TestPhaseConfigContract:
    def test_phaseconfig_requires_default_profile(self) -> None:
        """Omitting default_profile is a TypeError at construction."""
        with pytest.raises(TypeError):
            PhaseConfig(  # type: ignore[call-arg]
                phase=Phase.INIT,
                agent_name="x-agent",
                allowed_next=frozenset(),
                allowed_signals=frozenset({"continue"}),
                max_iterations=1,
            )
