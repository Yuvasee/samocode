"""Pure escalation decision: replay this iteration one rung up, and with what
context? Side effects live in worker/cli.py. Gates run cheap-first (no-IO checks,
then history, then target resolution); the first failing gate names itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import SamocodeConfig
from .lifecycle import (
    count_epoch_source_phase_runs_including_current,
    count_escalations_since_phase_entry,
)
from .phases import Phase, get_phase_config, is_iteration_limit_exceeded
from .routing import escalate_execution_target, resolve_execution_target
from .runner import EscalationContext, latest_test_report
from .signals import Signal, SignalStatus

_DEFAULT_BLOCKER_REASON = "unspecified blocker"
_SKIP_ONCE = "--once mode runs a single iteration; escalation is disabled"
_SKIP_LEGACY = "legacy configuration (no global config) cannot resolve profile rungs"


@dataclass(frozen=True)
class EscalationDecision:
    """Escalate: replay this iteration on `context.target`, one rung up."""

    context: EscalationContext


@dataclass(frozen=True)
class EscalationSkip:
    """Do not escalate. `reason` names the first failing gate, for logs."""

    reason: str


def plan_escalation(
    session_path: Path,
    phase: Phase,
    signal: Signal,
    config: SamocodeConfig,
    *,
    once: bool,
) -> EscalationDecision | EscalationSkip:
    """EscalationDecision with a ready context, or EscalationSkip naming the first
    failing gate. Base re-resolution is deterministic, so a misconfiguration raises
    rather than skips; only a missing next rung skips."""
    phase_config = get_phase_config(phase.value)
    policy = phase_config.escalation if phase_config is not None else None
    if policy is None:
        return EscalationSkip(f"phase '{phase.value}' declares no escalation policy")

    if signal.status is not SignalStatus.BLOCKED:
        return EscalationSkip(
            f"signal status is '{signal.status.value}', not 'blocked'"
        )

    if signal.needs not in policy.trigger_needs:
        return EscalationSkip(
            f"signal need {signal.needs!r} is not an escalation trigger "
            f"for phase '{phase.value}'"
        )

    if once:
        return EscalationSkip(_SKIP_ONCE)

    global_config = config.global_config
    if global_config is None:
        return EscalationSkip(_SKIP_LEGACY)

    budget = count_escalations_since_phase_entry(session_path, phase)
    if not budget.ok:
        return EscalationSkip(
            "cannot count escalations for this phase entry: " + "; ".join(budget.errors)
        )
    assert budget.count is not None
    if budget.count >= policy.max_attempts:
        return EscalationSkip(
            f"escalation budget exhausted for phase '{phase.value}': "
            f"{budget.count} of {policy.max_attempts} attempt(s) already used"
        )

    # The replay is the next phase run; without capacity it would be rejected before
    # its signal could advance the phase.
    replay_run = count_epoch_source_phase_runs_including_current(
        session_path, phase.value
    )
    if not replay_run.ok:
        return EscalationSkip(
            "cannot count phase runs for this escalation: "
            + "; ".join(replay_run.errors)
        )
    assert replay_run.count is not None
    exceeded, max_runs = is_iteration_limit_exceeded(phase.value, replay_run.count)
    if exceeded:
        return EscalationSkip(
            f"no phase-run capacity for an escalated replay of '{phase.value}': "
            f"the replay would be run {replay_run.count} past the {max_runs}-run limit"
        )

    base = resolve_execution_target(
        provider_name=config.ai_provider,
        workflow_phase=phase,
        session_dir=session_path,
        config=global_config,
        runtime=config.runtime,
    )
    escalated = escalate_execution_target(base, global_config)
    if escalated is None:
        return EscalationSkip(
            f"profile '{base.profile}' has no higher rung to escalate to"
        )

    context = EscalationContext(
        base=base,
        target=escalated,
        blocker_reason=signal.reason or signal.needs or _DEFAULT_BLOCKER_REASON,
        previous_report=latest_test_report(session_path),
        attempt=budget.count + 1,
        max_attempts=policy.max_attempts,
    )
    return EscalationDecision(context)
