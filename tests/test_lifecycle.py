"""Tests for worker/lifecycle.py escalation attempt budget and its scoping."""

import hashlib
import json
from pathlib import Path

from worker.lifecycle import (
    LifecycleIssueCode,
    accepted_transitions,
    count_epoch_source_phase_runs_including_current,
    count_escalations_since_phase_entry,
    derive_testing_run,
    has_final_polish_prerequisites,
    recovery_commit_marker,
    validate_phase_provenance,
)
from worker.phases import Phase
from worker.routing import ExecutionProfileSource, ExecutionTarget
from worker.signal_history import (
    read_history,
    record_escalation,
    record_processed_outcome,
)
from worker.signals import Signal, SignalStatus
from worker.workflow_state import ProcessedOutcome


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


def _session(tmp_path: Path) -> Path:
    session = tmp_path / "session"
    session.mkdir()
    return session


def _transition(session: Path, source: Phase, target: Phase, iteration: int) -> None:
    record_processed_outcome(
        session,
        Signal(status=SignalStatus.CONTINUE, phase=target.value),
        iteration,
        ProcessedOutcome.accepted_transition(source, target),
    )


def _escalate(session: Path, phase: Phase, iteration: int) -> None:
    record_escalation(
        session,
        phase,
        iteration,
        _target("strong", "claude-strong"),
        _target("max", "claude-max"),
        "environment",
    )


def _apply_recovery_anchor(session: Path, recovery_id: str = "abcdef012345") -> None:
    """Seal the whole current history behind an applied recovery receipt, matching
    the sealing contract scoped_history enforces (overview marker + receipt with the
    exact byte length and sha256 of the sealed prefix)."""
    history_path = session / "_signal_history.jsonl"
    raw = history_path.read_bytes()
    rows = len(read_history(session))
    receipt_dir = session / "_recovery" / recovery_id
    receipt_dir.mkdir(parents=True)
    (receipt_dir / "receipt.json").write_text(
        json.dumps(
            {
                "recovery_id": recovery_id,
                "history_rows_before": rows,
                "history_bytes_before": len(raw),
                "history_sha256_before": hashlib.sha256(raw).hexdigest(),
            }
        )
    )
    (session / "_overview.md").write_text(
        f"## Flow Log\n- [001 @ 08-26 09:00] {recovery_commit_marker(recovery_id)}\n"
    )


class TestCountEscalationsSincePhaseEntry:
    def test_no_history_is_zero(self, tmp_path: Path) -> None:
        result = count_escalations_since_phase_entry(_session(tmp_path), Phase.TESTING)
        assert result.ok
        assert result.count == 0

    def test_counts_from_start_without_entry_transition(self, tmp_path: Path) -> None:
        session = _session(tmp_path)
        _escalate(session, Phase.TESTING, 1)
        _escalate(session, Phase.TESTING, 2)
        result = count_escalations_since_phase_entry(session, Phase.TESTING)
        assert result.ok
        assert result.count == 2

    def test_budget_resets_on_reentry_into_testing(self, tmp_path: Path) -> None:
        session = _session(tmp_path)
        _transition(session, Phase.IMPLEMENTATION, Phase.TESTING, 1)
        _escalate(session, Phase.TESTING, 2)
        _escalate(session, Phase.TESTING, 3)
        assert count_escalations_since_phase_entry(session, Phase.TESTING).count == 2

        _transition(session, Phase.TESTING, Phase.QUALITY, 4)
        _transition(session, Phase.QUALITY, Phase.TESTING, 5)
        assert count_escalations_since_phase_entry(session, Phase.TESTING).count == 0

        _escalate(session, Phase.TESTING, 6)
        assert count_escalations_since_phase_entry(session, Phase.TESTING).count == 1

    def test_only_matching_target_phase_counts(self, tmp_path: Path) -> None:
        session = _session(tmp_path)
        _escalate(session, Phase.QUALITY, 1)
        assert count_escalations_since_phase_entry(session, Phase.TESTING).count == 0
        assert count_escalations_since_phase_entry(session, Phase.QUALITY).count == 1

    def test_scopes_to_recovery_epoch(self, tmp_path: Path) -> None:
        session = _session(tmp_path)
        _transition(session, Phase.IMPLEMENTATION, Phase.TESTING, 1)
        _escalate(session, Phase.TESTING, 2)  # pre-recovery, excluded by scoping
        _apply_recovery_anchor(session)
        _transition(session, Phase.IMPLEMENTATION, Phase.TESTING, 3)
        _escalate(session, Phase.TESTING, 4)
        _escalate(session, Phase.TESTING, 5)

        result = count_escalations_since_phase_entry(session, Phase.TESTING)
        assert result.count == 2

    def test_recovery_anchor_error_propagates_as_issue(self, tmp_path: Path) -> None:
        session = _session(tmp_path)
        _escalate(session, Phase.TESTING, 1)
        _apply_recovery_anchor(session)
        history_path = session / "_signal_history.jsonl"
        # Corrupt the sealed prefix so scoped_history's sha check fails.
        history_path.write_text("tampered pre-anchor row\n" + history_path.read_text())

        result = count_escalations_since_phase_entry(session, Phase.TESTING)
        assert not result.ok
        assert result.count is None
        assert any(
            issue.code is LifecycleIssueCode.RECOVERY_ANCHOR_INVALID
            for issue in result.issues
        )


class TestEscalationRowsDoNotDisturbExistingChecks:
    def test_epoch_source_phase_count_ignores_escalations(self, tmp_path: Path) -> None:
        session = _session(tmp_path)
        record_processed_outcome(
            session,
            Signal(status=SignalStatus.CONTINUE, phase="testing"),
            1,
            ProcessedOutcome.accepted_no_change(Phase.TESTING, Phase.TESTING),
        )
        _escalate(session, Phase.TESTING, 2)

        result = count_epoch_source_phase_runs_including_current(session, "testing")
        assert result.ok
        assert result.count == 2  # one recorded testing source-run + current in-flight

    def test_phase_provenance_unaffected_by_escalations(self, tmp_path: Path) -> None:
        session = _session(tmp_path)
        _transition(session, Phase.IMPLEMENTATION, Phase.TESTING, 1)
        _escalate(session, Phase.TESTING, 2)
        _transition(session, Phase.TESTING, Phase.QUALITY, 3)
        _transition(session, Phase.QUALITY, Phase.TESTING, 4)
        _escalate(session, Phase.TESTING, 5)
        _transition(session, Phase.TESTING, Phase.PR_READINESS, 6)

        assert validate_phase_provenance(session, Phase.PR_READINESS).ok
        assert accepted_transitions(read_history(session)) == [
            ("implementation", "testing"),
            ("testing", "quality"),
            ("quality", "testing"),
            ("testing", "pr-readiness"),
        ]


def _first_pass(session: Path) -> int:
    """Record the canonical impl->testing->quality->testing->pr-readiness first pass."""
    _transition(session, Phase.IMPLEMENTATION, Phase.TESTING, 1)
    _transition(session, Phase.TESTING, Phase.QUALITY, 2)
    _transition(session, Phase.QUALITY, Phase.TESTING, 3)
    _transition(session, Phase.TESTING, Phase.PR_READINESS, 4)
    return 4


class TestPhaseProvenanceAcceptance:
    def test_quality_accepts_initial_testing_predecessor(self, tmp_path: Path) -> None:
        session = _session(tmp_path)
        _transition(session, Phase.IMPLEMENTATION, Phase.TESTING, 1)
        _transition(session, Phase.TESTING, Phase.QUALITY, 2)
        assert validate_phase_provenance(session, Phase.QUALITY).ok

    def test_quality_accepts_pr_readiness_loop(self, tmp_path: Path) -> None:
        session = _session(tmp_path)
        last = _first_pass(session)
        _transition(session, Phase.PR_READINESS, Phase.QUALITY, last + 1)
        assert validate_phase_provenance(session, Phase.QUALITY).ok

    def test_quality_rejects_without_prior_testing_quality(
        self, tmp_path: Path
    ) -> None:
        session = _session(tmp_path)
        _transition(session, Phase.PR_READINESS, Phase.QUALITY, 1)
        check = validate_phase_provenance(session, Phase.QUALITY)
        assert not check.ok
        message = "; ".join(check.errors)
        assert "testing -> quality" in message
        assert "pr-readiness -> quality" in message
        assert "samocode check final-polish" in message

    def test_pr_readiness_requires_testing_predecessor(self, tmp_path: Path) -> None:
        session = _session(tmp_path)
        last = _first_pass(session)
        _transition(session, Phase.PR_READINESS, Phase.QUALITY, last + 1)
        assert not validate_phase_provenance(session, Phase.PR_READINESS).ok

    def test_pr_readiness_loop_reentry_passes(self, tmp_path: Path) -> None:
        session = _session(tmp_path)
        last = _first_pass(session)
        _transition(session, Phase.PR_READINESS, Phase.QUALITY, last + 1)
        _transition(session, Phase.QUALITY, Phase.TESTING, last + 2)
        _transition(session, Phase.TESTING, Phase.PR_READINESS, last + 3)
        assert validate_phase_provenance(session, Phase.PR_READINESS).ok

    def test_testing_second_run_requires_full_prefix(self, tmp_path: Path) -> None:
        session = _session(tmp_path)
        _transition(session, Phase.IMPLEMENTATION, Phase.TESTING, 1)
        _transition(session, Phase.QUALITY, Phase.TESTING, 2)
        assert not validate_phase_provenance(session, Phase.TESTING).ok

    def test_has_final_polish_prerequisites_predicate(self) -> None:
        full: list[tuple[str | None, str | None]] = [
            ("implementation", "testing"),
            ("testing", "quality"),
            ("quality", "testing"),
        ]
        assert has_final_polish_prerequisites(full)
        assert not has_final_polish_prerequisites(full[:2])


class TestDeriveTestingRun:
    def test_no_history_defaults_to_first(self, tmp_path: Path) -> None:
        session = _session(tmp_path)
        assert derive_testing_run(session) == "first (post-implementation)"

    def test_impl_to_testing_is_first(self, tmp_path: Path) -> None:
        session = _session(tmp_path)
        _transition(session, Phase.IMPLEMENTATION, Phase.TESTING, 1)
        assert derive_testing_run(session) == "first (post-implementation)"

    def test_quality_to_testing_is_second(self, tmp_path: Path) -> None:
        session = _session(tmp_path)
        _transition(session, Phase.IMPLEMENTATION, Phase.TESTING, 1)
        _transition(session, Phase.TESTING, Phase.QUALITY, 2)
        _transition(session, Phase.QUALITY, Phase.TESTING, 3)
        assert derive_testing_run(session) == "second (post-quality)"

    def test_latest_wins_when_multiple_entries(self, tmp_path: Path) -> None:
        session = _session(tmp_path)
        last = _first_pass(session)
        # pr-readiness->quality->testing loop: latest into testing is quality->testing
        _transition(session, Phase.PR_READINESS, Phase.QUALITY, last + 1)
        _transition(session, Phase.QUALITY, Phase.TESTING, last + 2)
        assert derive_testing_run(session) == "second (post-quality)"

    def test_recovery_epoch_respected(self, tmp_path: Path) -> None:
        session = _session(tmp_path)
        _transition(session, Phase.QUALITY, Phase.TESTING, 1)  # pre-recovery
        _apply_recovery_anchor(session)
        _transition(session, Phase.IMPLEMENTATION, Phase.TESTING, 2)  # new epoch
        assert derive_testing_run(session) == "first (post-implementation)"
