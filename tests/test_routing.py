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
from pathlib import Path

import pytest

from worker.config import RuntimeConfig
from worker.global_config import (
    GlobalConfig,
    GlobalConfigError,
    default_config,
    default_config_toml,
)
from worker.phases import PHASE_CONFIGS, Phase, PhaseConfig
from worker.plan_resolver import PlanProfileSource
from worker.routing import (
    DEFAULT_TIMEOUT_SECONDS,
    ExecutionProfileSource,
    ExecutionResolutionError,
    ExecutionTarget,
    ProfileSource,
    escalate_execution_target,
    next_profile,
    resolve_execution_target,
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
            Phase.TESTING: "strong",
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


# === Phase 5: execution-target resolution ===


def _deep_merge(base: dict[str, object], extra: Mapping[str, object]) -> None:
    for key, value in extra.items():
        existing = base.get(key)
        if isinstance(existing, dict) and isinstance(value, Mapping):
            _deep_merge(existing, value)
        else:
            base[key] = value


def _config(extra_toml: str = "") -> GlobalConfig:
    data = tomllib.loads(default_config_toml())
    if extra_toml:
        _deep_merge(data, tomllib.loads(extra_toml))
    return GlobalConfig.from_mapping(data, source="<test>")


def _write_plan(session_dir: Path, phase_block: str) -> None:
    (session_dir / "plan.md").write_text(f"## Implementation Phases\n\n{phase_block}")
    (session_dir / "_overview.md").write_text("## Plans\n- plan.md - the plan\n")


class TestCustomProfiles:
    def test_custom_profile_resolves(self, tmp_path: Path) -> None:
        cfg = _config(
            '\n[providers.claude.profiles.nightly]\nmodel = "claude-nightly"\n'
            'effort = "low"\n[workflow_overrides]\ntesting = "nightly"\n'
        )
        target = resolve_execution_target(
            provider_name="claude",
            workflow_phase=Phase.TESTING,
            session_dir=tmp_path,
            config=cfg,
            runtime=RuntimeConfig(),
        )
        assert target.model == "claude-nightly"
        assert target.effort == "low"
        assert target.source is ExecutionProfileSource.WORKFLOW_OVERRIDE


class TestUnavailableProfiles:
    def test_workflow_override_profile_unavailable_rejected(self) -> None:
        cfg = _config('\n[workflow_overrides]\nquality = "ultra"\n')
        with pytest.raises(GlobalConfigError, match="not available for provider"):
            resolve_execution_target(
                provider_name="claude",
                workflow_phase=Phase.QUALITY,
                session_dir=Path("/nonexistent"),
                config=cfg,
                runtime=RuntimeConfig(),
            )

    def test_explicit_plan_profile_unavailable_rejected(self, tmp_path: Path) -> None:
        _write_plan(tmp_path, "### Phase 1: X\n**Profile:** `nonexistent`\n- [ ] one\n")
        with pytest.raises(GlobalConfigError, match="implementation-plan phase '1'"):
            resolve_execution_target(
                provider_name="claude",
                workflow_phase=Phase.IMPLEMENTATION,
                session_dir=tmp_path,
                config=_config(),
                runtime=RuntimeConfig(),
            )


class TestInvalidOverrides:
    def test_unknown_override_phase_rejected(self) -> None:
        cfg = _config('\n[workflow_overrides]\nimplementaton = "strong"\n')
        with pytest.raises(GlobalConfigError, match="unknown phase"):
            resolve_execution_target(
                provider_name="claude",
                workflow_phase=Phase.QUALITY,
                session_dir=Path("/nonexistent"),
                config=cfg,
                runtime=RuntimeConfig(),
            )


class TestSelectedOnlyValidation:
    def test_other_providers_override_ignored_when_unselected(self) -> None:
        cfg = _config(
            '\n[providers.codex.profiles.ultra]\nmodel = "gpt-ultra"\n'
            '[workflow_overrides]\nquality = "ultra"\n'
        )
        target = resolve_execution_target(
            provider_name="codex",
            workflow_phase=Phase.QUALITY,
            session_dir=Path("/nonexistent"),
            config=cfg,
            runtime=RuntimeConfig(),
        )
        assert target.model == "gpt-ultra"

    def test_unselected_future_provider_inert(self) -> None:
        cfg = _config(
            '\n[providers.gemini]\nexecutable = "gemini"\n'
            '[providers.gemini.profiles.standard]\nmodel = "gemini-x"\n'
        )
        target = resolve_execution_target(
            provider_name="claude",
            workflow_phase=Phase.DONE,
            session_dir=Path("/nonexistent"),
            config=cfg,
            runtime=RuntimeConfig(),
        )
        assert target.provider == "claude"


class TestSelectedUnsupportedProvider:
    def test_unconfigured_provider_rejected(self) -> None:
        with pytest.raises(ExecutionResolutionError, match=r"no \[providers.gemini\]"):
            resolve_execution_target(
                provider_name="gemini",
                workflow_phase=Phase.DONE,
                session_dir=Path("/nonexistent"),
                config=_config(),
                runtime=RuntimeConfig(),
            )


class TestCrossProviderInvocation:
    def test_same_profile_resolves_independently_per_provider(self) -> None:
        cfg = _config()
        claude_target = resolve_execution_target(
            provider_name="claude",
            workflow_phase=Phase.TESTING,
            session_dir=Path("/nonexistent"),
            config=cfg,
            runtime=RuntimeConfig(),
        )
        codex_target = resolve_execution_target(
            provider_name="codex",
            workflow_phase=Phase.TESTING,
            session_dir=Path("/nonexistent"),
            config=cfg,
            runtime=RuntimeConfig(),
        )
        assert claude_target.profile == codex_target.profile == "strong"
        assert claude_target.model != codex_target.model


class TestImplementationPrecedence:
    def test_explicit_plan_profile_wins(self, tmp_path: Path) -> None:
        _write_plan(tmp_path, "### Phase 1: X\n**Profile:** `strong`\n- [ ] one\n")
        target = resolve_execution_target(
            provider_name="claude",
            workflow_phase=Phase.IMPLEMENTATION,
            session_dir=tmp_path,
            config=_config(),
            runtime=RuntimeConfig(),
        )
        assert target.profile == "strong"
        assert target.source is ExecutionProfileSource.PLAN_PHASE_EXPLICIT
        assert target.plan_phase is not None
        assert target.plan_phase.source is PlanProfileSource.PLAN_PHASE_EXPLICIT

    def test_omitted_plan_profile_falls_back_to_workflow_default(
        self, tmp_path: Path
    ) -> None:
        _write_plan(tmp_path, "### Phase 1: X\n- [ ] one\n")
        target = resolve_execution_target(
            provider_name="claude",
            workflow_phase=Phase.IMPLEMENTATION,
            session_dir=tmp_path,
            config=_config(),
            runtime=RuntimeConfig(),
        )
        assert target.profile == "standard"
        assert target.source is ExecutionProfileSource.PHASE_DEFAULT

    def test_all_complete_falls_back_to_workflow_default(self, tmp_path: Path) -> None:
        _write_plan(tmp_path, "### Phase 1: X\n- [x] one\n")
        target = resolve_execution_target(
            provider_name="claude",
            workflow_phase=Phase.IMPLEMENTATION,
            session_dir=tmp_path,
            config=_config(),
            runtime=RuntimeConfig(),
        )
        assert target.plan_phase is not None
        assert target.plan_phase.all_complete is True
        assert target.source is ExecutionProfileSource.PHASE_DEFAULT

    def test_workflow_override_beats_default_on_omission(self, tmp_path: Path) -> None:
        _write_plan(tmp_path, "### Phase 1: X\n- [ ] one\n")
        cfg = _config('\n[workflow_overrides]\nimplementation = "max"\n')
        target = resolve_execution_target(
            provider_name="claude",
            workflow_phase=Phase.IMPLEMENTATION,
            session_dir=tmp_path,
            config=cfg,
            runtime=RuntimeConfig(),
        )
        assert target.profile == "max"
        assert target.source is ExecutionProfileSource.WORKFLOW_OVERRIDE


class TestNonImplementationPhasesSkipPlanResolution:
    def test_missing_session_dir_does_not_raise_outside_implementation(self) -> None:
        target = resolve_execution_target(
            provider_name="claude",
            workflow_phase=Phase.QUALITY,
            session_dir=Path("/definitely/does/not/exist"),
            config=_config(),
            runtime=RuntimeConfig(),
        )
        assert target.plan_phase is None


class TestPathTimeoutOverrides:
    def test_claude_uses_dedicated_runtime_fields(self) -> None:
        runtime = RuntimeConfig(claude_path=Path("/custom/claude"), claude_timeout=42)
        target = resolve_execution_target(
            provider_name="claude",
            workflow_phase=Phase.DONE,
            session_dir=Path("/nonexistent"),
            config=_config(),
            runtime=runtime,
        )
        assert target.executable == Path("/custom/claude")
        assert target.timeout == 42

    def test_codex_uses_dedicated_runtime_fields(self) -> None:
        runtime = RuntimeConfig(codex_path=Path("/custom/codex"), codex_timeout=99)
        target = resolve_execution_target(
            provider_name="codex",
            workflow_phase=Phase.DONE,
            session_dir=Path("/nonexistent"),
            config=_config(),
            runtime=runtime,
        )
        assert target.executable == Path("/custom/codex")
        assert target.timeout == 99

    def test_unknown_provider_falls_back_to_provider_executable(self) -> None:
        # Phase.TESTING default profile is "strong", which gemini must provide.
        cfg = _config(
            '\n[providers.gemini]\nexecutable = "gemini-cli"\n'
            '[providers.gemini.profiles.strong]\nmodel = "gemini-x"\n'
        )
        target = resolve_execution_target(
            provider_name="gemini",
            workflow_phase=Phase.TESTING,
            session_dir=Path("/nonexistent"),
            config=cfg,
            runtime=RuntimeConfig(),
        )
        assert target.executable == Path("gemini-cli")
        assert target.timeout == DEFAULT_TIMEOUT_SECONDS


class TestExecutionTargetImmutability:
    def test_target_is_frozen(self) -> None:
        import dataclasses

        target = resolve_execution_target(
            provider_name="claude",
            workflow_phase=Phase.DONE,
            session_dir=Path("/nonexistent"),
            config=_config(),
            runtime=RuntimeConfig(),
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            target.profile = "other"  # type: ignore[misc]


# === Phase 2: escalation ladder ===


def _base_target(
    cfg: GlobalConfig,
    workflow_phase: Phase = Phase.DONE,
    provider: str = "claude",
) -> ExecutionTarget:
    return resolve_execution_target(
        provider_name=provider,
        workflow_phase=workflow_phase,
        session_dir=Path("/nonexistent"),
        config=cfg,
        runtime=RuntimeConfig(),
    )


class TestNextProfile:
    def test_ladder_steps(self) -> None:
        assert next_profile("light") == "standard"
        assert next_profile("standard") == "strong"
        assert next_profile("strong") == "max"

    def test_max_returns_none(self) -> None:
        assert next_profile("max") is None

    def test_unknown_profile_returns_none(self) -> None:
        assert next_profile("nightly") is None


class TestEscalateExecutionTarget:
    def test_escalates_each_rung_then_stops_at_max(self) -> None:
        cfg = _config()
        target = _base_target(cfg, Phase.DONE)  # light
        assert target.profile == "light"

        step1 = escalate_execution_target(target, cfg)
        assert step1 is not None
        assert step1.profile == "standard"
        standard = cfg.profile("claude", "standard")
        assert standard is not None
        assert step1.model == standard.model
        assert step1.effort == standard.effort
        assert step1.source is ExecutionProfileSource.ESCALATION
        assert step1.escalated_from == "light"
        assert step1.provider == target.provider
        assert step1.executable == target.executable
        assert step1.timeout == target.timeout
        assert step1.workflow_phase == target.workflow_phase
        assert step1.plan_phase == target.plan_phase

        step2 = escalate_execution_target(step1, cfg)
        assert step2 is not None
        assert step2.profile == "strong"
        assert step2.escalated_from == "standard"

        step3 = escalate_execution_target(step2, cfg)
        assert step3 is not None
        assert step3.profile == "max"
        assert step3.escalated_from == "strong"

        assert escalate_execution_target(step3, cfg) is None

    def test_max_base_returns_none(self) -> None:
        cfg = _config()
        target = _base_target(cfg, Phase.PLANNING)  # planning default is "max"
        assert target.profile == "max"
        assert escalate_execution_target(target, cfg) is None

    def test_missing_next_provider_profile_returns_none(self) -> None:
        # gemini ships "strong" but not "max"; escalating from strong finds no rung.
        cfg = _config(
            '\n[providers.gemini]\nexecutable = "gemini"\n'
            '[providers.gemini.profiles.strong]\nmodel = "gemini-strong"\n'
        )
        target = _base_target(cfg, Phase.TESTING, provider="gemini")  # strong
        assert target.profile == "strong"
        assert escalate_execution_target(target, cfg) is None

    def test_override_base_escalates_to_next_rung(self) -> None:
        cfg = _config('\n[workflow_overrides]\ntesting = "standard"\n')
        target = _base_target(cfg, Phase.TESTING)
        assert target.profile == "standard"
        assert target.source is ExecutionProfileSource.WORKFLOW_OVERRIDE

        escalated = escalate_execution_target(target, cfg)
        assert escalated is not None
        assert escalated.profile == "strong"
        assert escalated.escalated_from == "standard"
        assert escalated.source is ExecutionProfileSource.ESCALATION

    def test_original_target_unchanged(self) -> None:
        cfg = _config()
        target = _base_target(cfg, Phase.DONE)  # light, PHASE_DEFAULT
        escalated = escalate_execution_target(target, cfg)
        assert escalated is not None
        assert target.profile == "light"
        assert target.source is ExecutionProfileSource.PHASE_DEFAULT
        assert target.escalated_from is None
