"""Tests for worker/signal_history.py - Signal history tracking.

This module tests:
- Signal recording to history file
- Phase iteration counting
- History file reading
"""

from pathlib import Path

import pytest

from worker.phases import Phase
from worker.signals import Signal, SignalStatus
from worker.signal_history import (
    HistoryRecord,
    SignalHistoryEntry,
    count_source_phase_iterations,
    get_phase_iteration_count,
    read_history,
    read_signal_history,
    record_processed_outcome,
    record_signal,
)
from worker.workflow_event import RejectionReason
from worker.workflow_state import OutcomeKind, ProcessedOutcome


def _accepted_transition(source: Phase, target: Phase) -> ProcessedOutcome:
    return ProcessedOutcome(
        kind=OutcomeKind.ACCEPTED_TRANSITION,
        accepted=True,
        source_phase=source,
        target_phase=target,
        mutated=True,
    )


def _rejected_limit(source: Phase) -> ProcessedOutcome:
    return ProcessedOutcome(
        kind=OutcomeKind.REJECTED_VALIDATION,
        accepted=False,
        source_phase=source,
        target_phase=None,
        mutated=False,
        rejection_reason=RejectionReason.ITERATION_LIMIT_EXCEEDED,
        validation_error="Phase 'implementation' exceeded 100 iteration limit",
    )


class TestRecordSignal:
    """Tests for record_signal function."""

    def test_creates_history_file(self, tmp_path: Path) -> None:
        """Creates _signal_history.jsonl if not exists."""
        session = tmp_path / "session"
        session.mkdir()

        signal = Signal(status=SignalStatus.CONTINUE, phase="init")
        record_signal(session, signal, iteration=1)

        history_file = session / "_signal_history.jsonl"
        assert history_file.exists()

    def test_appends_to_existing(self, tmp_path: Path) -> None:
        """Appends to existing history file."""
        session = tmp_path / "session"
        session.mkdir()

        signal1 = Signal(status=SignalStatus.CONTINUE, phase="init")
        signal2 = Signal(status=SignalStatus.CONTINUE, phase="investigation")

        record_signal(session, signal1, iteration=1)
        record_signal(session, signal2, iteration=2)

        history_file = session / "_signal_history.jsonl"
        lines = history_file.read_text().strip().split("\n")
        assert len(lines) == 2

    def test_uses_overview_phase_as_fallback(self, tmp_path: Path) -> None:
        """Uses phase_from_overview when signal has no phase."""
        session = tmp_path / "session"
        session.mkdir()

        signal = Signal(status=SignalStatus.CONTINUE)  # No phase
        record_signal(session, signal, iteration=1, phase_from_overview="testing")

        entries = read_signal_history(session)
        assert len(entries) == 1
        assert entries[0].phase == "testing"

    def test_records_all_signal_fields(self, tmp_path: Path) -> None:
        """Records all signal fields correctly."""
        session = tmp_path / "session"
        session.mkdir()

        signal = Signal(
            status=SignalStatus.BLOCKED,
            phase="testing",
            reason="Tests failed",
            needs="error_resolution",
            summary="Some summary",
            waiting_for=None,
        )
        record_signal(session, signal, iteration=5)

        entries = read_signal_history(session)
        assert len(entries) == 1
        entry = entries[0]
        assert entry.status == "blocked"
        assert entry.phase == "testing"
        assert entry.reason == "Tests failed"
        assert entry.needs == "error_resolution"
        assert entry.summary == "Some summary"
        assert entry.iteration == 5

    def test_records_waiting_signal(self, tmp_path: Path) -> None:
        """Records waiting signal with waiting_for field."""
        session = tmp_path / "session"
        session.mkdir()

        signal = Signal(
            status=SignalStatus.WAITING,
            phase="requirements",
            waiting_for="qa_answers",
        )
        record_signal(session, signal, iteration=3)

        entries = read_signal_history(session)
        assert len(entries) == 1
        assert entries[0].waiting_for == "qa_answers"


class TestGetPhaseIterationCount:
    """Tests for get_phase_iteration_count function."""

    def test_no_history_file(self, tmp_path: Path) -> None:
        """Returns 0 when no history file exists."""
        session = tmp_path / "session"
        session.mkdir()

        count = get_phase_iteration_count(session, "init")
        assert count == 0

    def test_counts_matching_phase(self, tmp_path: Path) -> None:
        """Counts entries with matching phase."""
        session = tmp_path / "session"
        session.mkdir()

        # Record multiple signals in different phases
        for i in range(3):
            record_signal(
                session, Signal(status=SignalStatus.CONTINUE, phase="init"), i + 1
            )
        for i in range(5):
            record_signal(
                session,
                Signal(status=SignalStatus.CONTINUE, phase="investigation"),
                i + 4,
            )

        assert get_phase_iteration_count(session, "init") == 3
        assert get_phase_iteration_count(session, "investigation") == 5
        assert get_phase_iteration_count(session, "planning") == 0

    def test_case_insensitive(self, tmp_path: Path) -> None:
        """Phase matching is case-insensitive."""
        session = tmp_path / "session"
        session.mkdir()

        record_signal(session, Signal(status=SignalStatus.CONTINUE, phase="Init"), 1)
        record_signal(session, Signal(status=SignalStatus.CONTINUE, phase="INIT"), 2)

        assert get_phase_iteration_count(session, "init") == 2
        assert get_phase_iteration_count(session, "INIT") == 2

    def test_handles_empty_lines(self, tmp_path: Path) -> None:
        """Handles empty lines in history file."""
        session = tmp_path / "session"
        session.mkdir()

        history_file = session / "_signal_history.jsonl"
        history_file.write_text(
            '{"phase": "init", "status": "continue", "iteration": 1, "timestamp": "now"}\n'
            "\n"
            '{"phase": "init", "status": "continue", "iteration": 2, "timestamp": "later"}\n'
        )

        assert get_phase_iteration_count(session, "init") == 2

    def test_handles_corrupted_json(self, tmp_path: Path) -> None:
        """Skips corrupted JSON lines."""
        session = tmp_path / "session"
        session.mkdir()

        history_file = session / "_signal_history.jsonl"
        history_file.write_text(
            '{"phase": "init", "status": "continue", "iteration": 1, "timestamp": "now"}\n'
            "not valid json\n"
            '{"phase": "init", "status": "continue", "iteration": 2, "timestamp": "later"}\n'
        )

        assert get_phase_iteration_count(session, "init") == 2


class TestReadSignalHistory:
    """Tests for read_signal_history function."""

    def test_empty_history(self, tmp_path: Path) -> None:
        """Returns empty list when no history."""
        session = tmp_path / "session"
        session.mkdir()

        entries = read_signal_history(session)
        assert entries == []

    def test_reads_all_entries(self, tmp_path: Path) -> None:
        """Reads all history entries in order."""
        session = tmp_path / "session"
        session.mkdir()

        signals = [
            Signal(status=SignalStatus.CONTINUE, phase="init"),
            Signal(status=SignalStatus.CONTINUE, phase="investigation"),
            Signal(
                status=SignalStatus.WAITING,
                phase="requirements",
                waiting_for="qa_answers",
            ),
        ]

        for i, signal in enumerate(signals, 1):
            record_signal(session, signal, i)

        entries = read_signal_history(session)
        assert len(entries) == 3
        assert entries[0].phase == "init"
        assert entries[1].phase == "investigation"
        assert entries[2].status == "waiting"
        assert entries[2].waiting_for == "qa_answers"

    def test_handles_corrupted_lines(self, tmp_path: Path) -> None:
        """Skips corrupted JSON lines gracefully."""
        session = tmp_path / "session"
        session.mkdir()

        history_file = session / "_signal_history.jsonl"
        history_file.write_text(
            '{"phase": "init", "status": "continue", "iteration": 1, "timestamp": "now"}\n'
            "not valid json\n"
            '{"phase": "done", "status": "done", "iteration": 2, "timestamp": "later"}\n'
        )

        entries = read_signal_history(session)
        assert len(entries) == 2
        assert entries[0].phase == "init"
        assert entries[1].phase == "done"

    def test_handles_empty_file(self, tmp_path: Path) -> None:
        """Returns empty list for empty file."""
        session = tmp_path / "session"
        session.mkdir()

        history_file = session / "_signal_history.jsonl"
        history_file.write_text("")

        entries = read_signal_history(session)
        assert entries == []

    def test_handles_missing_fields(self, tmp_path: Path) -> None:
        """Handles entries with missing optional fields."""
        session = tmp_path / "session"
        session.mkdir()

        history_file = session / "_signal_history.jsonl"
        # Minimal entry with only required fields
        history_file.write_text(
            '{"phase": "init", "status": "continue", "iteration": 1, "timestamp": "now"}\n'
        )

        entries = read_signal_history(session)
        assert len(entries) == 1
        assert entries[0].phase == "init"
        assert entries[0].summary is None
        assert entries[0].reason is None


class TestSignalHistoryEntry:
    """Tests for SignalHistoryEntry dataclass."""

    def test_to_dict(self) -> None:
        """to_dict produces correct JSON-serializable dict."""
        entry = SignalHistoryEntry(
            timestamp="2026-01-20 10:00:00",
            iteration=5,
            phase="testing",
            status="blocked",
            summary="Test summary",
            reason="Test reason",
            needs="error_resolution",
            waiting_for=None,
        )

        d = entry.to_dict()
        assert d["timestamp"] == "2026-01-20 10:00:00"
        assert d["iteration"] == 5
        assert d["phase"] == "testing"
        assert d["status"] == "blocked"
        assert d["summary"] == "Test summary"
        assert d["reason"] == "Test reason"
        assert d["needs"] == "error_resolution"
        assert d["for"] is None  # waiting_for serializes as "for"

    def test_frozen_dataclass(self) -> None:
        """SignalHistoryEntry is immutable."""
        entry = SignalHistoryEntry(
            timestamp="now",
            iteration=1,
            phase="init",
            status="continue",
            summary=None,
            reason=None,
            needs=None,
            waiting_for=None,
        )

        with pytest.raises(AttributeError):
            entry.iteration = 2  # type: ignore[misc]


class TestRecordProcessedOutcome:
    """v2 recorder consuming ProcessedOutcome."""

    def test_new_schema_round_trip(self, tmp_path: Path) -> None:
        """Accepted transition round-trips through read_history with truthful fields."""
        session = tmp_path / "s"
        session.mkdir()
        signal = Signal(
            status=SignalStatus.CONTINUE, phase="testing", summary="done impl"
        )
        outcome = _accepted_transition(Phase.IMPLEMENTATION, Phase.TESTING)

        written = record_processed_outcome(
            session, signal, iteration=7, outcome=outcome
        )

        records = read_history(session)
        assert len(records) == 1
        rec = records[0]
        assert rec.schema_version == 2
        assert rec.source_phase == "implementation"
        assert rec.target_phase == "testing"
        assert rec.raw_status == "continue"
        assert rec.accepted is True
        assert rec.validation_error is None
        assert rec.outcome_kind == "accepted_transition"
        assert rec.mutated is True
        assert rec.summary == "done impl"
        assert rec == written

    def test_records_rejected_audit_row(self, tmp_path: Path) -> None:
        """Rejected validation preserves accepted=False, reason, and error."""
        session = tmp_path / "s"
        session.mkdir()
        signal = Signal(status=SignalStatus.CONTINUE, phase="testing")
        outcome = ProcessedOutcome(
            kind=OutcomeKind.REJECTED_VALIDATION,
            accepted=False,
            source_phase=Phase.IMPLEMENTATION,
            target_phase=Phase.TESTING,
            mutated=False,
            rejection_reason=RejectionReason.TRANSITION_REQUIRES_APPROVAL,
            validation_error="requires approval",
        )

        record_processed_outcome(session, signal, iteration=3, outcome=outcome)

        rec = read_history(session)[0]
        assert rec.accepted is False
        assert rec.rejection_reason == "transition_requires_approval"
        assert rec.validation_error == "requires approval"
        assert rec.mutated is False

    def test_accepted_and_rejected_flags_distinct(self, tmp_path: Path) -> None:
        """Both audit rows persist with their own accepted flags."""
        session = tmp_path / "s"
        session.mkdir()
        record_processed_outcome(
            session,
            Signal(status=SignalStatus.CONTINUE, phase="testing"),
            1,
            _accepted_transition(Phase.IMPLEMENTATION, Phase.TESTING),
        )
        record_processed_outcome(
            session,
            Signal(status=SignalStatus.CONTINUE),
            2,
            _rejected_limit(Phase.IMPLEMENTATION),
        )

        flags = [r.accepted for r in read_history(session)]
        assert flags == [True, False]


class TestSourcePhaseCounting:
    """Counting is by source phase and includes rejected / boundary rows."""

    def test_counts_by_source_not_target(self, tmp_path: Path) -> None:
        """Transition iteration counts toward source, not target (regression)."""
        session = tmp_path / "s"
        session.mkdir()
        record_processed_outcome(
            session,
            Signal(status=SignalStatus.CONTINUE, phase="testing"),
            1,
            _accepted_transition(Phase.IMPLEMENTATION, Phase.TESTING),
        )

        assert count_source_phase_iterations(session, "implementation") == 1
        assert count_source_phase_iterations(session, "testing") == 0

    def test_counts_rejected_and_limit_boundary(self, tmp_path: Path) -> None:
        """Rejected event and the limit-boundary iteration both count by source."""
        session = tmp_path / "s"
        session.mkdir()
        for i in range(2):
            record_processed_outcome(
                session,
                Signal(status=SignalStatus.CONTINUE),
                i + 1,
                ProcessedOutcome(
                    kind=OutcomeKind.ACCEPTED_NO_CHANGE,
                    accepted=True,
                    source_phase=Phase.IMPLEMENTATION,
                    target_phase=Phase.IMPLEMENTATION,
                    mutated=False,
                ),
            )
        record_processed_outcome(
            session,
            Signal(status=SignalStatus.CONTINUE),
            3,
            _rejected_limit(Phase.IMPLEMENTATION),  # boundary iteration
        )

        assert count_source_phase_iterations(session, "implementation") == 3

    def test_get_phase_iteration_count_delegates(self, tmp_path: Path) -> None:
        """Legacy shim equals source-phase count across mixed schemas."""
        session = tmp_path / "s"
        session.mkdir()
        record_signal(session, Signal(status=SignalStatus.CONTINUE, phase="init"), 1)
        record_processed_outcome(
            session,
            Signal(status=SignalStatus.CONTINUE, phase="investigation"),
            2,
            _accepted_transition(Phase.INIT, Phase.INVESTIGATION),
        )

        assert get_phase_iteration_count(session, "init") == 2
        assert get_phase_iteration_count(session, "investigation") == 0


class TestHistoryCompatibility:
    """Normalizing reader over mixed / corrupt rows."""

    def test_reads_mixed_legacy_and_v2(self, tmp_path: Path) -> None:
        session = tmp_path / "s"
        session.mkdir()
        record_signal(session, Signal(status=SignalStatus.CONTINUE, phase="init"), 1)
        record_processed_outcome(
            session,
            Signal(status=SignalStatus.CONTINUE, phase="investigation"),
            2,
            _accepted_transition(Phase.INIT, Phase.INVESTIGATION),
        )

        records = read_history(session)
        assert [r.schema_version for r in records] == [1, 2]
        assert records[0].source_phase == "init"
        assert records[0].accepted is None  # legacy: unknown
        assert records[0].target_phase is None
        assert records[1].source_phase == "init"
        assert records[1].target_phase == "investigation"

    def test_skips_corrupt_and_nonobject_rows(self, tmp_path: Path) -> None:
        session = tmp_path / "s"
        session.mkdir()
        history = session / "_signal_history.jsonl"
        history.write_text(
            '{"phase": "init", "status": "continue", "iteration": 1, "timestamp": "t"}\n'
            "not valid json\n"
            "[1, 2, 3]\n"
            '{"v": 2, "source_phase": "init", "status": "continue", '
            '"iteration": 2, "timestamp": "t", "accepted": false}\n'
        )

        records = read_history(session)
        assert len(records) == 2
        assert records[0].schema_version == 1
        assert records[1].accepted is False

    def test_legacy_minimal_row_normalizes(self, tmp_path: Path) -> None:
        session = tmp_path / "s"
        session.mkdir()
        history = session / "_signal_history.jsonl"
        history.write_text('{"phase": "init"}\n')

        rec = read_history(session)[0]
        assert rec.source_phase == "init"
        assert rec.raw_status == ""
        assert rec.iteration == 0
        assert rec.accepted is None

    def test_read_signal_history_projects_v2(self, tmp_path: Path) -> None:
        """Legacy reader still works over v2 rows (phase <- source_phase)."""
        session = tmp_path / "s"
        session.mkdir()
        record_processed_outcome(
            session,
            Signal(status=SignalStatus.WAITING, waiting_for="plan_approval"),
            1,
            ProcessedOutcome(
                kind=OutcomeKind.ACCEPTED_NO_CHANGE,
                accepted=True,
                source_phase=Phase.PLANNING,
                target_phase=Phase.PLANNING,
                mutated=False,
            ),
        )

        entries = read_signal_history(session)
        assert len(entries) == 1
        assert entries[0].phase == "planning"
        assert entries[0].status == "waiting"
        assert entries[0].waiting_for == "plan_approval"

    def test_history_record_is_frozen(self) -> None:
        rec = HistoryRecord(
            timestamp="t",
            iteration=1,
            source_phase="init",
            target_phase=None,
            raw_status="continue",
            accepted=None,
            validation_error=None,
        )
        with pytest.raises(AttributeError):
            rec.iteration = 2  # type: ignore[misc]
