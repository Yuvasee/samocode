"""Tests for worker/escalation.py - the pure escalation decision service."""

import json
import tomllib
from collections.abc import Mapping
from pathlib import Path

from worker.config import ProjectConfig, RuntimeConfig, SamocodeConfig
from worker.escalation import (
    EscalationDecision,
    EscalationSkip,
    plan_escalation,
)
from worker.global_config import GlobalConfig, default_config, default_config_toml
from worker.phases import PHASE_CONFIGS, Phase
from worker.routing import ExecutionProfileSource, ExecutionTarget
from worker.signal_history import record_escalation
from worker.signals import Signal, SignalStatus


def _session(tmp_path: Path) -> Path:
    session = tmp_path / "session"
    session.mkdir()
    return session


def _config(session: Path, global_config: GlobalConfig | None = None) -> SamocodeConfig:
    project = ProjectConfig(main_repo=session, worktrees=session, sessions=session)
    return SamocodeConfig(
        project=project,
        runtime=RuntimeConfig(),
        session_path=session,
        provider="claude",
        global_config=global_config if global_config is not None else default_config(),
    )


def _config_with_overrides(
    session: Path, overrides: Mapping[str, str]
) -> SamocodeConfig:
    data = tomllib.loads(default_config_toml())
    data["workflow_overrides"] = dict(overrides)
    return _config(session, GlobalConfig.from_mapping(data, source="<test>"))


def _blocked(needs: str | None = "environment", reason: str | None = "boom") -> Signal:
    return Signal(status=SignalStatus.BLOCKED, needs=needs, reason=reason)


def _target(profile: str, model: str) -> ExecutionTarget:
    return ExecutionTarget(
        provider="claude",
        profile=profile,
        model=model,
        effort=None,
        executable=Path("claude"),
        timeout=1800,
        workflow_phase=Phase.TESTING,
        plan_phase=None,
        source=ExecutionProfileSource.PHASE_DEFAULT,
    )


def _seed_source_phase_runs(session: Path, phase: Phase, count: int) -> None:
    """Append `count` recorded runs charged to `phase` as their source phase."""
    with (session / "_signal_history.jsonl").open("a", encoding="utf-8") as handle:
        for _ in range(count):
            handle.write(
                json.dumps({"v": 2, "source_phase": phase.value, "status": "continue"})
                + "\n"
            )


def _record_one_escalation(session: Path, phase: Phase) -> None:
    record_escalation(
        session,
        phase,
        1,
        _target("strong", "claude-strong"),
        _target("max", "claude-max"),
        "environment",
    )


class TestPlanEscalationSkips:
    def test_skip_when_phase_has_no_policy(self, tmp_path: Path) -> None:
        session = _session(tmp_path)
        result = plan_escalation(
            session, Phase.QUALITY, _blocked(), _config(session), once=False
        )
        assert isinstance(result, EscalationSkip)
        assert "declares no escalation policy" in result.reason

    def test_skip_when_status_not_blocked(self, tmp_path: Path) -> None:
        session = _session(tmp_path)
        signal = Signal(status=SignalStatus.CONTINUE, needs="environment")
        result = plan_escalation(
            session, Phase.TESTING, signal, _config(session), once=False
        )
        assert isinstance(result, EscalationSkip)
        assert "not 'blocked'" in result.reason

    def test_skip_when_need_not_a_trigger(self, tmp_path: Path) -> None:
        session = _session(tmp_path)
        signal = _blocked(needs="clarification")
        result = plan_escalation(
            session, Phase.TESTING, signal, _config(session), once=False
        )
        assert isinstance(result, EscalationSkip)
        assert "is not an escalation trigger" in result.reason

    def test_skip_when_need_is_none(self, tmp_path: Path) -> None:
        session = _session(tmp_path)
        result = plan_escalation(
            session, Phase.TESTING, _blocked(needs=None), _config(session), once=False
        )
        assert isinstance(result, EscalationSkip)
        assert "is not an escalation trigger" in result.reason

    def test_skip_in_once_mode(self, tmp_path: Path) -> None:
        session = _session(tmp_path)
        result = plan_escalation(
            session, Phase.TESTING, _blocked(), _config(session), once=True
        )
        assert isinstance(result, EscalationSkip)
        assert "--once" in result.reason

    def test_skip_when_legacy_config(self, tmp_path: Path) -> None:
        session = _session(tmp_path)
        project = ProjectConfig(main_repo=session, worktrees=session, sessions=session)
        legacy = SamocodeConfig(
            project=project,
            runtime=RuntimeConfig(),
            session_path=session,
            provider="claude",
            global_config=None,
        )
        result = plan_escalation(session, Phase.TESTING, _blocked(), legacy, once=False)
        assert isinstance(result, EscalationSkip)
        assert "legacy configuration" in result.reason

    def test_skip_when_budget_exhausted(self, tmp_path: Path) -> None:
        session = _session(tmp_path)
        _record_one_escalation(session, Phase.TESTING)
        result = plan_escalation(
            session, Phase.TESTING, _blocked(), _config(session), once=False
        )
        assert isinstance(result, EscalationSkip)
        assert "budget exhausted" in result.reason
        assert "1 of 1" in result.reason

    def test_skip_when_budget_unavailable(self, tmp_path: Path) -> None:
        session = _session(tmp_path)
        # Overview names an applied recovery with no _recovery directory -> anchor error.
        (session / "_overview.md").write_text(
            "## Flow Log\n- [001 @ 08-26 09:00] [samocode-recovery:abcdef012345]\n"
        )
        result = plan_escalation(
            session, Phase.TESTING, _blocked(), _config(session), once=False
        )
        assert isinstance(result, EscalationSkip)
        assert "cannot count escalations" in result.reason

    def test_skip_when_no_phase_run_capacity(self, tmp_path: Path) -> None:
        session = _session(tmp_path)
        limit = PHASE_CONFIGS[Phase.TESTING].max_iterations
        # `limit` recorded runs leave the replay as run limit+1, past the cap.
        _seed_source_phase_runs(session, Phase.TESTING, limit)
        result = plan_escalation(
            session, Phase.TESTING, _blocked(), _config(session), once=False
        )
        assert isinstance(result, EscalationSkip)
        assert "no phase-run capacity" in result.reason

    def test_skip_when_no_next_rung(self, tmp_path: Path) -> None:
        session = _session(tmp_path)
        config = _config_with_overrides(session, {"testing": "max"})
        result = plan_escalation(session, Phase.TESTING, _blocked(), config, once=False)
        assert isinstance(result, EscalationSkip)
        assert "no higher rung" in result.reason


class TestPlanEscalationPositive:
    def test_positive_decision_builds_context(self, tmp_path: Path) -> None:
        session = _session(tmp_path)
        (session / "01-01-08:00-test-report.md").write_text("old")
        latest = session / "01-01-09:00-test-report.md"
        latest.write_text("new")

        result = plan_escalation(
            session,
            Phase.TESTING,
            _blocked(reason="playwright missing"),
            _config(session),
            once=False,
        )
        assert isinstance(result, EscalationDecision)
        ctx = result.context
        assert ctx.base.profile == "strong"
        assert ctx.target.profile == "max"
        assert ctx.target.source is ExecutionProfileSource.ESCALATION
        assert ctx.target.escalated_from == "strong"
        assert ctx.attempt == 1
        assert ctx.max_attempts == 1
        assert ctx.blocker_reason == "playwright missing"
        assert ctx.previous_report == latest

    def test_previous_report_none_when_absent(self, tmp_path: Path) -> None:
        session = _session(tmp_path)
        result = plan_escalation(
            session, Phase.TESTING, _blocked(), _config(session), once=False
        )
        assert isinstance(result, EscalationDecision)
        assert result.context.previous_report is None

    def test_blocker_reason_falls_back_to_needs(self, tmp_path: Path) -> None:
        session = _session(tmp_path)
        result = plan_escalation(
            session,
            Phase.TESTING,
            _blocked(needs="environment", reason=None),
            _config(session),
            once=False,
        )
        assert isinstance(result, EscalationDecision)
        assert result.context.blocker_reason == "environment"


class TestPlanEscalationCheckOrder:
    def test_once_wins_over_budget(self, tmp_path: Path) -> None:
        session = _session(tmp_path)
        _record_one_escalation(session, Phase.TESTING)
        result = plan_escalation(
            session, Phase.TESTING, _blocked(), _config(session), once=True
        )
        assert isinstance(result, EscalationSkip)
        assert "--once" in result.reason

    def test_no_policy_wins_over_not_blocked(self, tmp_path: Path) -> None:
        session = _session(tmp_path)
        signal = Signal(status=SignalStatus.CONTINUE, needs="environment")
        result = plan_escalation(
            session, Phase.QUALITY, signal, _config(session), once=False
        )
        assert isinstance(result, EscalationSkip)
        assert "declares no escalation policy" in result.reason
