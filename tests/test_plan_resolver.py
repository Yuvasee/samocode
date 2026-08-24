"""Tests for worker/plan_resolver.py.

Covers:
- `## Plans` parsing and last-entry active-plan selection, incl. stale ref
- `## Implementation Phases` parsing: valid, legacy (no Profile), partial,
  complete, duplicate/malformed Profile, out-of-section content, multi-plan,
  alphanumeric `Phase 3a` labels
- resolve_plan_phase end-to-end selection and all-complete fallback
"""

from pathlib import Path

import pytest

from worker.plan_resolver import (
    PlanEntry,
    PlanPhase,
    PlanProfileSource,
    PlanResolutionError,
    PlanTask,
    parse_implementation_phases,
    parse_plan_entries,
    resolve_plan_phase,
    select_active_phase,
    select_active_plan,
    validate_pending_implementation_scope,
    validate_plan_contract,
)


class TestActivePlanSelection:
    def test_single_plan_entry_selected(self, tmp_path: Path) -> None:
        (tmp_path / "plan-a.md").write_text("## Implementation Phases\n")
        overview = "## Plans\n- plan-a.md - first plan\n"
        assert select_active_plan(tmp_path, overview) == tmp_path / "plan-a.md"

    def test_multi_plan_last_entry_wins(self, tmp_path: Path) -> None:
        (tmp_path / "plan-a.md").write_text("## Implementation Phases\n")
        (tmp_path / "plan-b.md").write_text("## Implementation Phases\n")
        overview = "## Plans\n- plan-a.md - superseded\n- plan-b.md - current\n"
        assert select_active_plan(tmp_path, overview) == tmp_path / "plan-b.md"

    def test_missing_plans_section_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(PlanResolutionError, match="no `## Plans`"):
            select_active_plan(tmp_path, "## Status\nsomething\n")

    def test_empty_plans_section_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(PlanResolutionError, match="no `## Plans`"):
            select_active_plan(tmp_path, "## Plans\n\n## Files\n- x.md\n")

    def test_stale_plan_reference_rejected(self, tmp_path: Path) -> None:
        overview = "## Plans\n- missing.md - gone\n"
        with pytest.raises(PlanResolutionError, match="does not exist"):
            select_active_plan(tmp_path, overview)

    def test_entries_stop_at_next_level2_heading(self) -> None:
        overview = (
            "## Plans\n- plan-a.md - a\n"
            "## Linear Tasks\n- plan-b.md - not a plan entry\n"
        )
        entries = parse_plan_entries(overview)
        assert [e.filename for e in entries] == ["plan-a.md"]

    def test_entry_description_parsed(self) -> None:
        entries = parse_plan_entries("## Plans\n- plan-a.md - the description\n")
        assert entries == [PlanEntry("plan-a.md", "the description")]


class TestPhaseParsing:
    def test_valid_explicit_profile(self) -> None:
        text = (
            "## Implementation Phases\n\n"
            "### Phase 1: Foundation\n"
            "**Profile:** `strong`\n"
            "- [ ] Do the thing\n"
        )
        assert parse_implementation_phases(text) == [
            PlanPhase(
                "1",
                "Foundation",
                "strong",
                1,
                0,
                (PlanTask("Do the thing", False),),
            )
        ]

    def test_legacy_omitted_profile(self) -> None:
        text = (
            "## Implementation Phases\n\n### Phase 1: Foundation\n"
            "- [x] Done\n- [ ] Todo\n"
        )
        phase = parse_implementation_phases(text)[0]
        assert phase.profile is None
        assert phase.total_tasks == 2
        assert phase.completed_tasks == 1

    def test_partial_completion_keeps_profile(self) -> None:
        text = (
            "## Implementation Phases\n\n### Phase 1: X\n**Profile:** `max`\n"
            "- [x] one\n- [ ] two\n"
        )
        phase = parse_implementation_phases(text)[0]
        assert phase.profile == "max"
        assert phase.has_unchecked_task is True

    def test_all_complete_phase(self) -> None:
        text = "## Implementation Phases\n\n### Phase 1: X\n- [x] one\n- [x] two\n"
        assert parse_implementation_phases(text)[0].has_unchecked_task is False

    def test_duplicate_profile_line_rejected(self) -> None:
        text = (
            "## Implementation Phases\n\n### Phase 1: X\n**Profile:** `strong`\n"
            "- [ ] one\n**Profile:** `max`\n"
        )
        with pytest.raises(PlanResolutionError, match="second or misplaced"):
            parse_implementation_phases(text)

    def test_misplaced_profile_line_rejected(self) -> None:
        text = (
            "## Implementation Phases\n\n### Phase 1: X\n- [ ] one\n"
            "**Profile:** `strong`\n"
        )
        with pytest.raises(PlanResolutionError, match="second or misplaced"):
            parse_implementation_phases(text)

    def test_malformed_profile_no_backticks_rejected(self) -> None:
        text = "## Implementation Phases\n\n### Phase 1: X\n**Profile:** strong\n- [ ] one\n"
        with pytest.raises(PlanResolutionError, match="malformed"):
            parse_implementation_phases(text)

    def test_malformed_profile_empty_rejected(self) -> None:
        text = (
            "## Implementation Phases\n\n### Phase 1: X\n**Profile:** ``\n- [ ] one\n"
        )
        with pytest.raises(PlanResolutionError, match="malformed"):
            parse_implementation_phases(text)

    def test_missing_section_rejected(self) -> None:
        with pytest.raises(PlanResolutionError, match="no `## Implementation Phases`"):
            parse_implementation_phases("## Requirements\n- [ ] req\n")

    def test_out_of_section_profile_and_checkboxes_ignored(self) -> None:
        text = (
            "## Requirements\n**Profile:** not-a-real-directive\n- [ ] req\n\n"
            "## Implementation Phases\n\n### Phase 1: X\n- [ ] one\n\n"
            "## Notes\n**Profile:** `strong`\n- [ ] not a phase\n"
        )
        phases = parse_implementation_phases(text)
        assert len(phases) == 1
        assert phases[0].profile is None
        assert phases[0].total_tasks == 1

    def test_alphanumeric_phase_label(self) -> None:
        text = "## Implementation Phases\n\n### Phase 3a: Follow-up\n- [ ] one\n"
        phase = parse_implementation_phases(text)[0]
        assert phase.label == "3a"
        assert phase.title == "Follow-up"

    def test_prose_only_phase_is_non_selectable(self) -> None:
        text = "## Implementation Phases\n\n### Phase 1: Intro\nJust prose.\n"
        phase = parse_implementation_phases(text)[0]
        assert phase.total_tasks == 0
        assert phase.has_unchecked_task is False

    def test_multiple_phases_ordered(self) -> None:
        text = (
            "## Implementation Phases\n\n"
            "### Phase 1: A\n- [x] one\n\n"
            "### Phase 2: B\n**Profile:** `strong`\n- [ ] two\n"
        )
        phases = parse_implementation_phases(text)
        assert [p.label for p in phases] == ["1", "2"]
        assert phases[1].profile == "strong"


class TestActivePhaseSelection:
    def test_first_incomplete_phase_selected(self) -> None:
        phases = [
            PlanPhase("1", "A", None, 2, 2),
            PlanPhase("2", "B", "strong", 3, 1),
            PlanPhase("3", "C", None, 1, 0),
        ]
        selected = select_active_phase(phases)
        assert selected is not None
        assert selected.label == "2"

    def test_all_complete_returns_none(self) -> None:
        phases = [PlanPhase("1", "A", None, 1, 1), PlanPhase("2", "B", "max", 1, 1)]
        assert select_active_phase(phases) is None


class TestLifecycleScopeValidation:
    @pytest.mark.parametrize(
        "title",
        [
            "Testing",
            "Quality review",
            "Code Clarity",
            "Comment Hygiene",
            "PR readiness and approval stop",
            "Done",
            "Final summary",
        ],
    )
    def test_rejects_pending_outer_lifecycle_phase(self, title: str) -> None:
        text = (
            "## Implementation Phases\n\n"
            f"### Phase 9: {title}\n**Profile:** `standard`\n- [ ] Run gate\n"
        )
        with pytest.raises(PlanResolutionError, match="outer workflow stage"):
            validate_plan_contract(text)

    def test_rejects_neutral_phase_with_explicit_approval_stop(self) -> None:
        phases = [
            PlanPhase(
                "4",
                "Final checks",
                "standard",
                1,
                0,
                (PlanTask("Wait for explicit user approval", False),),
            )
        ]
        with pytest.raises(PlanResolutionError, match="outer workflow stage"):
            validate_pending_implementation_scope(phases)

    @pytest.mark.parametrize(
        "task",
        [
            "Transition to testing",
            "Run the `pr-readiness` gate",
            "Run Code Clarity against the final diff",
            "Run Comment Hygiene",
            "Signal done",
        ],
    )
    def test_rejects_lifecycle_task_hidden_in_neutral_phase(self, task: str) -> None:
        text = (
            "## Implementation Phases\n\n"
            "### Phase 4: Final checks\n"
            "**Profile:** `standard`\n"
            f"- [ ] {task}\n"
        )
        with pytest.raises(PlanResolutionError, match="outer workflow stage"):
            validate_plan_contract(text)

    def test_allows_test_authoring_as_implementation_work(self) -> None:
        text = (
            "## Implementation Phases\n\n"
            "### Phase 2: Integration test coverage\n"
            "**Profile:** `strong`\n"
            "- [ ] Author unit and integration tests\n"
            "- [ ] Add fixtures for queue failure paths\n"
            "\n## Verification Plan\n- Run the full regression suite\n"
        )
        phases = validate_plan_contract(text)
        assert phases[0].title == "Integration test coverage"

    def test_allows_completed_legacy_lifecycle_phase_to_recover(self) -> None:
        text = (
            "## Implementation Phases\n\n"
            "### Phase 14: PR readiness and approval stop\n"
            "**Profile:** `standard`\n- [x] Run gate\n"
        )
        phases = validate_plan_contract(text)
        assert select_active_phase(phases) is None


class TestResolvePlanPhase:
    def _write_session(self, tmp_path: Path, plan_body: str) -> Path:
        (tmp_path / "plan.md").write_text(plan_body)
        (tmp_path / "_overview.md").write_text("## Plans\n- plan.md - the plan\n")
        return tmp_path

    def test_explicit_profile_selected(self, tmp_path: Path) -> None:
        session = self._write_session(
            tmp_path,
            "## Implementation Phases\n\n### Phase 1: X\n**Profile:** `strong`\n- [ ] one\n",
        )
        result = resolve_plan_phase(session)
        assert result.profile == "strong"
        assert result.source is PlanProfileSource.PLAN_PHASE_EXPLICIT
        assert result.phase_label == "1"
        assert result.all_complete is False

    def test_omitted_profile_falls_back(self, tmp_path: Path) -> None:
        session = self._write_session(
            tmp_path, "## Implementation Phases\n\n### Phase 1: X\n- [ ] one\n"
        )
        result = resolve_plan_phase(session)
        assert result.profile is None
        assert result.source is PlanProfileSource.IMPLEMENTATION_DEFAULT
        assert result.phase_label == "1"

    def test_all_phases_complete_falls_back(self, tmp_path: Path) -> None:
        session = self._write_session(
            tmp_path, "## Implementation Phases\n\n### Phase 1: X\n- [x] one\n"
        )
        result = resolve_plan_phase(session)
        assert result.all_complete is True
        assert result.phase_label is None
        assert result.source is PlanProfileSource.IMPLEMENTATION_DEFAULT

    def test_multi_plan_uses_last(self, tmp_path: Path) -> None:
        (tmp_path / "old.md").write_text(
            "## Implementation Phases\n\n### Phase 1: Old\n- [ ] a\n"
        )
        (tmp_path / "new.md").write_text(
            "## Implementation Phases\n\n### Phase 1: New\n**Profile:** `max`\n- [ ] b\n"
        )
        (tmp_path / "_overview.md").write_text(
            "## Plans\n- old.md - superseded\n- new.md - current\n"
        )
        result = resolve_plan_phase(tmp_path)
        assert result.plan_path == tmp_path / "new.md"
        assert result.profile == "max"

    def test_missing_overview_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(PlanResolutionError, match="session overview not found"):
            resolve_plan_phase(tmp_path)


class TestPlanFormatFixture:
    def test_selects_first_incomplete_phase_with_its_profile(self) -> None:
        """Mirrors the live plan shape: earlier phases checked, the first
        incomplete phase carries an explicit `**Profile:**`."""
        text = (
            "## Implementation Phases\n\n"
            "### Phase 3: Prior\n**Profile:** `standard`\n- [x] done\n\n"
            "### Phase 4: Resolver\n**Profile:** `strong`\n- [ ] build it\n"
        )
        active = select_active_phase(parse_implementation_phases(text))
        assert active is not None
        assert active.label == "4"
        assert active.profile == "strong"
