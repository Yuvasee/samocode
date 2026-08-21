"""Workflow-phase model-routing resolution.

Composes `worker.phases` (workflow metadata) with `worker.global_config`
(provider/profile data) to answer one question: which profile applies to a
given workflow phase, for the process's selected provider.

Intended home for Phase 4's plan-phase resolution and Phase 5's immutable
per-iteration execution target - both extend the functions here rather than
relocate them. `phases` and `global_config` never import each other; only this
module needs both vocabularies.
"""

from dataclasses import dataclass
from enum import Enum

from worker.global_config import GlobalConfig, GlobalConfigError, Provider
from worker.phases import PHASE_CONFIGS, Phase


class ProfileSource(Enum):
    """Where a resolved profile name came from. Logged every iteration."""

    WORKFLOW_OVERRIDE = "workflow_override"  # [workflow_overrides] table
    PHASE_DEFAULT = "phase_default"  # PhaseConfig.default_profile
    GLOBAL_DEFAULT = "global_default"  # GlobalConfig.default_profile


@dataclass(frozen=True)
class ResolvedProfile:
    """A profile name plus where it came from, for logging and the Phase 5
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
    order Phase 4/5 add.

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

    Public so Phase 7's once-at-startup validation can fail fast before any
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
