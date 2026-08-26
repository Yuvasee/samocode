"""Pure escalation decision service for a just-processed phase signal.

Answers one question with no side effects: should the orchestrator replay this
iteration one rung up the profile ladder, and with what context? The loop in
worker/cli.py owns the effects (record_escalation, notify_escalation, and the
escalated re-run); this module only decides.

Composition over three earlier primitives:
- count_escalations_since_phase_entry -> attempt budget for this phase entry
- resolve_execution_target            -> the base target the iteration just ran
- escalate_execution_target           -> that target bumped one rung, or None

Skip checks run cheap-first: the pure policy/status/needs/once/legacy gates (no
IO) precede the budget history read, which precedes base resolution and the
next-rung lookup. The first failing gate wins and names itself in the reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import SamocodeConfig
from .lifecycle import count_escalations_since_phase_entry
from .phases import Phase, get_phase_config
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
    """Decide whether the current `phase` iteration should be escalated.

    Pure: reads session history and the global config, writes nothing. Returns
    an EscalationDecision carrying a ready-to-run EscalationContext, or an
    EscalationSkip whose reason names the first gate that stopped escalation.

    Base resolution re-runs resolve_execution_target for `phase`; it is
    deterministic (the iteration that just ran already resolved this exact
    target), so a genuine misconfiguration still raises rather than skips - only
    a `None` next rung is treated as "nowhere to escalate".
    """
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
    assert budget.count is not None  # budget.ok guarantees this
    if budget.count >= policy.max_attempts:
        return EscalationSkip(
            f"escalation budget exhausted for phase '{phase.value}': "
            f"{budget.count} of {policy.max_attempts} attempt(s) already used"
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
