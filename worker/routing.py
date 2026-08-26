"""Workflow-phase model-routing resolution.

Composes `worker.phases` (workflow metadata) with `worker.global_config`
(provider/profile data) to answer one question: which profile applies to a
given workflow phase, for the process's selected provider.

Plan-phase resolution (parsing `_overview.md` and plan Markdown) lives in
`worker.plan_resolver` - a distinct concern with no dependency on
`GlobalConfig`/`Provider`. `resolve_execution_target()` below is the single
composition point: it combines `resolve_workflow_profile()`,
`plan_resolver.resolve_plan_phase()`, the selected provider's profile table, and
`worker.config.RuntimeConfig` path/timeout overrides into one immutable
`ExecutionTarget` per iteration. An explicit plan-phase profile wins, else the
`implementation` workflow default. `phases`, `global_config`, and `plan_resolver`
never import each other or this module; only this module needs all vocabularies.
"""

from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path

from worker.config import RuntimeConfig
from worker.global_config import (
    CANONICAL_PROFILES,
    GlobalConfig,
    GlobalConfigError,
    Profile,
    Provider,
)
from worker.phases import PHASE_CONFIGS, Phase
from worker.plan_resolver import PlanPhaseSelection, resolve_plan_phase


class ProfileSource(Enum):
    """Where a resolved profile name came from. Logged every iteration."""

    WORKFLOW_OVERRIDE = "workflow_override"  # [workflow_overrides] table
    PHASE_DEFAULT = "phase_default"  # PhaseConfig.default_profile
    GLOBAL_DEFAULT = "global_default"  # GlobalConfig.default_profile


@dataclass(frozen=True)
class ResolvedProfile:
    """A profile name plus where it came from, for logging and the
    execution target."""

    name: str
    source: ProfileSource


def resolve_workflow_profile(
    phase: Phase, provider: Provider, config: GlobalConfig
) -> ResolvedProfile:
    """Resolve the profile for a workflow phase, for one provider.

    Order: workflow_overrides[phase] -> PhaseConfig.default_profile ->
    GlobalConfig.default_profile. The last tier is unreachable while every Phase
    member has a PHASE_CONFIGS entry; kept for parity with the plan-phase lookup
    order.

    Raises GlobalConfigError if workflow_overrides names an unknown phase
    anywhere in the table, or if the resolved profile is unavailable for
    `provider`.
    """
    validate_workflow_overrides(config, provider)

    override = config.workflow_overrides.get(phase.value)
    if override is not None:
        return _validated(override, ProfileSource.WORKFLOW_OVERRIDE, phase, provider)

    phase_config = PHASE_CONFIGS.get(phase)
    if phase_config is not None:
        return _validated(
            phase_config.default_profile, ProfileSource.PHASE_DEFAULT, phase, provider
        )
    return _validated(
        config.default_profile, ProfileSource.GLOBAL_DEFAULT, phase, provider
    )


def validate_workflow_overrides(config: GlobalConfig, provider: Provider) -> None:
    """Reject an unknown override phase or an override profile unavailable for
    `provider`, across the whole table.

    Public so startup's once-at-startup validation can fail fast before any
    iteration resolves, in addition to the per-call check in
    resolve_workflow_profile.
    """
    known_phases = {p.value for p in Phase}
    errors: list[str] = []
    for phase_name, profile_name in config.workflow_overrides.items():
        if phase_name not in known_phases:
            errors.append(f"workflow_overrides: unknown phase {phase_name!r}")
        elif provider.profile(profile_name) is None:
            errors.append(
                f"workflow_overrides[{phase_name!r}]: profile {profile_name!r} "
                f"not available for provider {provider.name!r}"
            )
    if errors:
        raise GlobalConfigError(
            "Invalid workflow_overrides:\n  - " + "\n  - ".join(errors)
        )


def _validated(
    name: str, source: ProfileSource, phase: Phase, provider: Provider
) -> ResolvedProfile:
    """Confirm `name` is available for `provider` before returning it."""
    if provider.profile(name) is None:
        raise GlobalConfigError(
            f"profile {name!r} for workflow phase {phase.value!r} is not "
            f"available for provider {provider.name!r} "
            f"(known profiles: {sorted(provider.profiles)})"
        )
    return ResolvedProfile(name=name, source=source)


# === Per-iteration execution target ===

DEFAULT_TIMEOUT_SECONDS = 1800  # Fallback for providers with no RuntimeConfig field


class ExecutionResolutionError(ValueError):
    """Raised when the selected provider itself is not configured.

    Distinct from GlobalConfigError (TOML content is wrong) and
    PlanResolutionError (plan Markdown is wrong): the caller asked to run a
    provider the global config never defined.
    """


class ExecutionProfileSource(Enum):
    """Where an ExecutionTarget's resolved profile ultimately came from.

    Collapses ProfileSource (workflow-level) and PlanProfileSource
    (only PLAN_PHASE_EXPLICIT ever surfaces here; an omitted plan profile falls
    through to a ProfileSource-backed member) into one closed vocabulary a single
    log line or session-context field can switch on.

    ESCALATION is the one member with no resolution-time counterpart: it is
    stamped after the fact by escalate_execution_target when an iteration is
    replayed one rung up the canonical ladder, and so is deliberately absent from
    _WORKFLOW_SOURCE_MAP.
    """

    PLAN_PHASE_EXPLICIT = "plan_phase_explicit"
    WORKFLOW_OVERRIDE = "workflow_override"
    PHASE_DEFAULT = "phase_default"
    GLOBAL_DEFAULT = "global_default"
    LEGACY = "legacy"  # env-model synthesized target when global_config is absent
    ESCALATION = "escalation"  # set by escalate_execution_target, not by resolution


_WORKFLOW_SOURCE_MAP: dict[ProfileSource, ExecutionProfileSource] = {
    ProfileSource.WORKFLOW_OVERRIDE: ExecutionProfileSource.WORKFLOW_OVERRIDE,
    ProfileSource.PHASE_DEFAULT: ExecutionProfileSource.PHASE_DEFAULT,
    ProfileSource.GLOBAL_DEFAULT: ExecutionProfileSource.GLOBAL_DEFAULT,
}


@dataclass(frozen=True)
class ExecutionTarget:
    """The fully-resolved, immutable execution target for one iteration.

    Built once by `resolve_execution_target()` and reused verbatim across every
    retry within that iteration, so a retry cannot switch provider, model, or
    plan phase. Adapters build Claude/Codex argv directly from
    `model`/`effort`; the runner injects `plan_phase` into agent session context.
    """

    provider: str  # GlobalConfig.providers key, e.g. "claude", "codex"
    profile: str  # resolved profile name
    model: str  # Profile.model for (provider, profile)
    effort: str | None  # Profile.effort for (provider, profile)
    executable: Path  # resolved CLI path (RuntimeConfig override-aware)
    timeout: int  # seconds (RuntimeConfig override-aware)
    workflow_phase: Phase
    plan_phase: PlanPhaseSelection | None  # None outside `implementation`
    source: ExecutionProfileSource
    escalated_from: str | None = None  # prior rung when source is ESCALATION


def resolve_execution_target(
    *,
    provider_name: str,
    workflow_phase: Phase,
    session_dir: Path,
    config: GlobalConfig,
    runtime: RuntimeConfig,
) -> ExecutionTarget:
    """Resolve the one immutable ExecutionTarget for an iteration.

    Only `implementation` reads `session_dir` (via resolve_plan_phase); every
    other phase leaves plan_phase=None and touches no filesystem. For
    `implementation`, an explicit `**Profile:**` on the active plan phase wins;
    otherwise resolution falls through to resolve_workflow_profile exactly like
    every other phase.

    Raises:
        ExecutionResolutionError: provider_name has no matching
            [providers.<name>] section.
        GlobalConfigError: a resolved profile (workflow override or explicit
            plan profile) is unavailable for the selected provider, or
            workflow_overrides is malformed.
        PlanResolutionError: (implementation only) plan/_overview.md missing,
            stale, or malformed. Never caught here.
    """
    provider = config.providers.get(provider_name)
    if provider is None:
        raise ExecutionResolutionError(
            f"selected provider {provider_name!r} has no "
            f"[providers.{provider_name}] section in the global config "
            f"(known providers: {sorted(config.providers)})"
        )

    plan_phase: PlanPhaseSelection | None = None
    if workflow_phase is Phase.IMPLEMENTATION:
        plan_phase = resolve_plan_phase(session_dir)

    if plan_phase is not None and plan_phase.profile is not None:
        profile_name = plan_phase.profile
        source = ExecutionProfileSource.PLAN_PHASE_EXPLICIT
        profile = _resolve_profile(
            provider,
            profile_name,
            context=f"implementation-plan phase {plan_phase.phase_label!r}",
        )
    else:
        resolved = resolve_workflow_profile(workflow_phase, provider, config)
        profile_name = resolved.name
        source = _WORKFLOW_SOURCE_MAP[resolved.source]
        profile = _resolve_profile(
            provider, profile_name, context=f"workflow phase {workflow_phase.value!r}"
        )

    executable, timeout = _resolve_path_and_timeout(provider_name, provider, runtime)

    return ExecutionTarget(
        provider=provider_name,
        profile=profile_name,
        model=profile.model,
        effort=profile.effort,
        executable=executable,
        timeout=timeout,
        workflow_phase=workflow_phase,
        plan_phase=plan_phase,
        source=source,
    )


def _resolve_profile(provider: Provider, name: str, *, context: str) -> Profile:
    """Look up `name` on `provider`; raise GlobalConfigError if absent."""
    profile = provider.profile(name)
    if profile is None:
        raise GlobalConfigError(
            f"profile {name!r} for {context} is not available for provider "
            f"{provider.name!r} (known profiles: {sorted(provider.profiles)})"
        )
    return profile


def _resolve_path_and_timeout(
    provider_name: str, provider: Provider, runtime: RuntimeConfig
) -> tuple[Path, int]:
    """Map the selected provider to its path/timeout override.

    Only claude/codex have dedicated RuntimeConfig fields today; any other
    configured provider falls back to its global-config `executable` and
    DEFAULT_TIMEOUT_SECONDS. RuntimeConfig may later generalize without
    changing this return shape.
    """
    if provider_name == "claude":
        return runtime.claude_path, runtime.claude_timeout
    if provider_name == "codex":
        return runtime.codex_path, runtime.codex_timeout
    return Path(provider.executable), DEFAULT_TIMEOUT_SECONDS


# === Escalation ===


def next_profile(name: str) -> str | None:
    """Return the next rung up the canonical profile ladder, or None.

    The ladder is CANONICAL_PROFILES (light -> standard -> strong -> max).
    Returns None at the top rung (`max`) and for any name outside the ladder
    (e.g. a custom profile), so a caller treats "already strongest" and "not a
    ladder rung" identically: there is nowhere further to escalate.
    """
    try:
        index = CANONICAL_PROFILES.index(name)
    except ValueError:
        return None
    if index + 1 >= len(CANONICAL_PROFILES):
        return None
    return CANONICAL_PROFILES[index + 1]


def escalate_execution_target(
    target: ExecutionTarget, config: GlobalConfig
) -> ExecutionTarget | None:
    """Return `target` bumped one rung up the canonical ladder, or None.

    Escalation replays the same iteration on a stronger profile after a failure.
    The next rung comes from next_profile(target.profile); its model/effort come
    from the same provider's table. Every other field - provider, executable,
    timeout, workflow_phase, plan_phase - is carried over verbatim, so the retry
    differs only in model/effort. `source` becomes ESCALATION and `escalated_from`
    records the rung stepped up from.

    Returns None (never raises) when there is no next rung - `target` is already
    at `max` or on a non-canonical profile - or when the selected provider does
    not define the next rung. The original `target` is left unchanged (frozen).
    """
    next_name = next_profile(target.profile)
    if next_name is None:
        return None
    profile = config.profile(target.provider, next_name)
    if profile is None:
        return None
    return replace(
        target,
        profile=next_name,
        model=profile.model,
        effort=profile.effort,
        source=ExecutionProfileSource.ESCALATION,
        escalated_from=target.profile,
    )
