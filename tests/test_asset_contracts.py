import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANONICAL_PROFILES = {"light", "standard", "strong", "max"}
PROFILE_LINE = re.compile(r"\*\*Profile:\*\* `([^`]+)`")


def test_all_phase_agents_inherit_routed_model() -> None:
    for path in sorted((ROOT / "agents").glob("*.md")):
        content = path.read_text()
        model_lines = [
            line for line in content.splitlines() if line.startswith("model:")
        ]
        assert model_lines == ["model: inherit"], path.name


def test_implementation_proposals_inherit_model_and_effort() -> None:
    content = (ROOT / "skills" / "implementation" / "SKILL.md").read_text()
    normalized = " ".join(content.split())
    assert "model: sonnet" not in content
    assert content.count("model: inherit") >= 3
    assert "no effort override" in normalized
    assert "Execution Routing Contract" in content


def test_quality_subagents_inherit_model_and_effort() -> None:
    content = (ROOT / "skills" / "quality" / "SKILL.md").read_text()
    normalized = " ".join(content.split())
    assert "model: haiku" not in content
    assert "model: inherit" in content
    assert "no effort override" in normalized


def test_quality_pipeline_runs_clarity_before_final_comment_hygiene() -> None:
    agent = (ROOT / "agents" / "quality-agent.md").read_text()
    headings = [
        "Step 1 — Cleanup",
        "Step 2 — Multi-review",
        "Step 3 — Triage + fix",
        "Step 4 — Verify fixes",
        "Step 5 — Clarity review",
        "Step 6 — Clarity triage + fix",
        "Step 7 — Clarity verify",
        "Step 8 — Final Comment Hygiene",
    ]
    positions = [agent.index(heading) for heading in headings]

    assert positions == sorted(positions)
    assert "skills: quality, implementation, code-clarity, comment-hygiene" in agent

    ordinary_triage = agent[positions[2] : positions[3]]
    ordinary_verify = agent[positions[3] : positions[4]]
    clarity_pipeline = agent[positions[4] : positions[7]]
    hygiene = agent[positions[7] :]

    assert "Quality Step: clarity-review" in ordinary_triage
    assert "Quality Step: clarity-review" in ordinary_verify
    assert "MUST use `code-clarity`" in clarity_pipeline
    assert "review-only" in clarity_pipeline
    assert "implementation" in clarity_pipeline
    assert "MUST use `comment-hygiene`" in hygiene
    assert "final operation allowed to mutate" in hygiene
    assert "executable-code safety check" in hygiene

    testing_signal = '{"status": "continue", "phase": "testing"}'
    readiness_signal = '{"status": "continue", "phase": "pr-readiness"}'
    assert testing_signal not in agent[: positions[7]]
    assert testing_signal in hygiene
    assert readiness_signal not in agent


def test_post_quality_pipeline_preserves_final_hygiene_boundary() -> None:
    testing_agent = (ROOT / "agents" / "testing-agent.md").read_text()
    testing_skill = (ROOT / "skills" / "testing" / "SKILL.md").read_text()
    readiness_agent = (ROOT / "agents" / "pr-readiness-agent.md").read_text()
    readiness_skill = (ROOT / "skills" / "pr-readiness" / "SKILL.md").read_text()

    for content in (testing_agent, testing_skill):
        normalized = " ".join(content.split())
        assert "git status --porcelain" in content
        assert "post-quality" in content.lower()
        assert "HEAD" in normalized and "status" in normalized
        assert "quality" in normalized
        assert "Result: PASS | FAIL" in content
        assert "[TIMESTAMP_FILE]-test-report.md" in content

    # Mutation is a guard violation, never a route back to quality.
    for content in (testing_agent, testing_skill):
        assert "workflow_error" in content
        assert "re-enter quality" not in content
    assert "return the workflow to quality" not in " ".join(testing_skill.split())

    for content in (readiness_agent, readiness_skill):
        normalized = " ".join(content.split())
        assert "Code Clarity" in content
        assert "Comment Hygiene" in content
        assert "Reviewed HEAD" in content
        assert "Input HEAD" in content
        assert "Output HEAD" in content
        assert "Tested HEAD" in content
        assert "Disposition: settled" in content
        assert "Reviewed HEAD" in normalized and "Input HEAD" in normalized
        assert "Output HEAD" in normalized and "Tested HEAD" in normalized

    readiness_agent_normalized = " ".join(readiness_agent.split())
    readiness_skill_normalized = " ".join(readiness_skill.split())
    assert (
        "settled Code Clarity report's `Reviewed HEAD` equals Comment Hygiene "
        "`Input HEAD`" in readiness_agent_normalized
    )
    assert (
        "Comment Hygiene `Output HEAD` equals the regression test's `Tested HEAD` "
        "and the current project `HEAD`" in readiness_agent_normalized
    )
    assert (
        "settled Code Clarity report's `Reviewed HEAD` to equal Comment Hygiene "
        "`Input HEAD`" in readiness_skill_normalized
    )
    assert (
        "Comment Hygiene `Output HEAD` to equal both the regression report's "
        "`Tested HEAD` and current project `HEAD`" in readiness_skill_normalized
    )
    assert "provenance is never `NOT APPLICABLE`" in readiness_agent_normalized
    assert '{"status": "continue", "phase": "quality"}' in readiness_agent


def test_planning_assets_keep_outer_lifecycle_outside_implementation() -> None:
    planning_agent = (ROOT / "agents" / "planning-agent.md").read_text()
    planning_skill = (ROOT / "skills" / "planning" / "SKILL.md").read_text()

    for content in (planning_agent, planning_skill):
        assert "## Implementation Phases" in content
        assert "## Verification Plan" in content
        normalized = " ".join(content.split())
        assert "lifecycle" in normalized.lower()
        assert "orchestrator" in normalized.lower()
        assert "Code Clarity" in normalized
        assert "Comment Hygiene" in normalized
        assert "PR Readiness" in normalized


def test_workflow_documents_final_polish_order() -> None:
    workflow = (ROOT / "workflow.md").read_text()
    quality_description = next(
        line for line in workflow.splitlines() if line.startswith("- **quality**:")
    )

    clarity = quality_description.index("Code Clarity")
    hygiene = quality_description.index("Comment Hygiene")
    assert clarity < hygiene
    assert "final working-tree mutation" in quality_description
    assert "`code-clarity`" in workflow


def test_requirements_lookup_subagents_inherit_model_and_effort() -> None:
    content = (ROOT / "skills" / "task-definition" / "SKILL.md").read_text()
    normalized = " ".join(content.split())
    assert "model: inherit" in content
    assert "no effort override" in normalized


def test_run_skill_uses_public_cli_and_documents_routing() -> None:
    content = (ROOT / "skills" / "samocode-run" / "SKILL.md").read_text()
    assert "python main.py" not in content
    assert "samocode run" in content
    assert "--provider" in content
    assert "config.toml" in content
    assert "loads and validates the global config once" in content


def test_workflow_treats_execution_routing_as_authoritative() -> None:
    content = (ROOT / "workflow.md").read_text()
    assert "Execution Routing" in content
    assert "Do not change provider, profile, model, or effort" in content
    assert "model: inherit" in content


def test_second_opinion_skills_use_the_consulted_cli_configuration() -> None:
    claude = (ROOT / "skills" / "claude" / "SKILL.md").read_text()
    codex = (ROOT / "skills" / "codex" / "SKILL.md").read_text()
    assert "--model opus" not in claude
    assert "GPT-5.2" not in codex
    assert "own configured/default model" in claude
    assert "own configured/default model" in codex


def test_planning_templates_assign_a_canonical_profile_to_every_phase() -> None:
    for path in (
        ROOT / "skills" / "planning" / "SKILL.md",
        ROOT / "agents" / "planning-agent.md",
    ):
        lines = path.read_text().splitlines()
        phase_indexes = [
            index
            for index, line in enumerate(lines)
            if re.match(r"^\s*### Phase \d+:", line)
        ]
        assert phase_indexes, path

        for offset, phase_index in enumerate(phase_indexes):
            first_line = lines[phase_index + 1].strip()
            match = PROFILE_LINE.fullmatch(first_line)
            assert match, f"{path.name}:{phase_index + 2}: {first_line!r}"
            assert match.group(1) in CANONICAL_PROFILES

            next_index = (
                phase_indexes[offset + 1]
                if offset + 1 < len(phase_indexes)
                else len(lines)
            )
            profile_lines = [
                line.strip()
                for line in lines[phase_index + 1 : next_index]
                if line.strip().startswith("**Profile:**")
            ]
            assert profile_lines == [first_line], path


def test_run_skill_uses_approve_cli_for_plan_approval() -> None:
    content = (ROOT / "skills" / "samocode-run" / "SKILL.md").read_text()
    assert "samocode approve" in content
    assert "--config" in content
    assert "--session" in content
    stale = (
        "Phase: implementation\n   - `Blocked: no`\n   - `Last Action: Plan approved"
    )
    assert stale not in content


def test_run_skill_forbids_manual_lifecycle_repair_and_uses_recovery_cli() -> None:
    content = (ROOT / "skills" / "samocode-run" / "SKILL.md").read_text()
    normalized = " ".join(content.split())

    assert "samocode recover final-polish" in content
    assert "--check" in content and "--apply" in content
    assert "explicit user approval" in content
    assert "Edit `_overview.md`, `_signal.json`, or `_signal_history.jsonl`" in content
    assert "Update `_overview.md`:" not in content
    assert "Phase: investigation`" not in normalized


def test_planning_agent_references_approve_cli() -> None:
    content = (ROOT / "agents" / "planning-agent.md").read_text()
    assert "samocode approve" in content


def test_architecture_source_map_covers_new_modules() -> None:
    content = (ROOT / "ARCHITECTURE.md").read_text()
    assert "workflow_event.py" in content
    assert "workflow_state.py" in content
    assert "approval.py" in content
    assert "lifecycle.py" in content
    assert "process_lease.py" in content
    assert "recovery.py" in content


def test_readme_documents_approve_cli() -> None:
    content = (ROOT / "README.md").read_text()
    assert "samocode approve" in content
    assert "plan_approval" in content


def test_workflow_documents_plan_approval_gate() -> None:
    content = (ROOT / "workflow.md").read_text()
    assert "plan_approval" in content
    assert "samocode approve" in content


def test_planning_contract_keeps_profile_selection_semantic() -> None:
    skill = (ROOT / "skills" / "planning" / "SKILL.md").read_text()
    agent = (ROOT / "agents" / "planning-agent.md").read_text()

    for content in (skill, agent):
        for profile in CANONICAL_PROFILES:
            assert f"| `{profile}` |" in content
        assert "model catalogs" in content
        assert "effort levels" in content
        assert "prices" in content
        assert "runtime" in content.lower()
        assert "legacy" in content.lower()

    stale_guidance = (
        "omit it so routine phases inherit",
        "when unsure, omit it",
        "model profile per phase (optional)",
    )
    normalized = " ".join(f"{skill}\n{agent}".lower().split())
    for phrase in stale_guidance:
        assert phrase not in normalized


def test_documentation_authoring_is_pinned_to_max_in_both_planning_assets() -> None:
    skill = (ROOT / "skills" / "planning" / "SKILL.md").read_text()
    agent = (ROOT / "agents" / "planning-agent.md").read_text()

    rule = "**Documentation authoring is `max`-only.**"
    for content in (skill, agent):
        assert rule in content
        normalized = " ".join(content.split())
        assert "must be split into its own phase" in normalized
        assert "overrides the general" in normalized
        assert "does not apply to reading documentation" in normalized
        assert "source comments and docstrings" in normalized
        assert "worker/plan_resolver.py" in content

    light_row = next(
        line for line in skill.splitlines() if line.strip().startswith("| `light`")
    )
    assert "documentation" not in light_row.lower()


def test_interactive_planning_skill_writes_the_approval_handoff_state() -> None:
    content = (ROOT / "skills" / "planning" / "SKILL.md").read_text()
    assert "Phase: planning" in content
    assert "Blocked: waiting_human" in content
    assert '"for": "plan_approval"' in content
    assert "no\n       markdown link" in content


def test_interactive_task_skill_hands_off_to_planning() -> None:
    content = (ROOT / "skills" / "task-definition" / "SKILL.md").read_text()
    assert "Phase: planning" in content
    assert "task-defined" in content


def test_session_skill_documents_the_closed_phase_enum() -> None:
    content = (ROOT / "skills" / "session-management" / "SKILL.md").read_text()
    assert (
        "init | investigation | requirements | planning | implementation | testing | quality | pr-readiness | done"
        in content
    )


def test_run_skill_stops_on_unknown_phase_without_editing() -> None:
    content = (ROOT / "skills" / "samocode-run" / "SKILL.md").read_text()
    assert "If `Phase` is not one of" in content


def test_testing_assets_document_environment_escalation_and_guard() -> None:
    agent = (ROOT / "agents" / "testing-agent.md").read_text()
    skill = (ROOT / "skills" / "testing" / "SKILL.md").read_text()
    workflow = (ROOT / "workflow.md").read_text()

    assert '"needs": "environment"' in agent
    assert "## Escalated Testing Attempt" in agent

    for content in (agent, skill):
        assert "environment" in content
        assert "workflow_error" in content
        assert "git checkout -- " in content
        assert "reproducible command" in content.lower()

    # The old mutation-as-route clause is gone everywhere.
    assert "re-enter quality" not in agent
    assert "return the workflow to quality" not in skill

    needs_line = next(
        line for line in workflow.splitlines() if line.startswith("**`needs` values**")
    )
    assert "`environment`" in needs_line

    assert "**Escalation**" in workflow
    normalized_workflow = " ".join(workflow.split())
    assert "next semantic profile" in normalized_workflow
    assert "one attempt per phase entry" in normalized_workflow


def test_generic_assets_contain_no_repository_specific_identifiers() -> None:
    forbidden = ("avonai", "avon-ai", "haver", "conversations-monitoring")
    text_suffixes = {".py", ".md", ".toml", ".json", ".txt"}
    scanned: list[Path] = [ROOT / "workflow.md"]
    for base in ("worker", "agents", "skills", "docs"):
        scanned.extend(
            path
            for path in (ROOT / base).rglob("*")
            if path.is_file() and path.suffix in text_suffixes
        )

    for path in scanned:
        lowered = path.read_text().lower()
        for token in forbidden:
            assert token not in lowered, f"{path}: contains forbidden token {token!r}"


def test_pr_readiness_assets_run_final_polish_check() -> None:
    """pr-readiness owns the full gate; its assets must reference the command."""
    pr_agent = (ROOT / "agents" / "pr-readiness-agent.md").read_text()
    pr_skill = (ROOT / "skills" / "pr-readiness" / "SKILL.md").read_text()
    run_skill = (ROOT / "skills" / "samocode-run" / "SKILL.md").read_text()

    cmd = "samocode check final-polish"
    for content, label in [
        (pr_agent, "agents/pr-readiness-agent.md"),
        (pr_skill, "skills/pr-readiness/SKILL.md"),
        (run_skill, "skills/samocode-run/SKILL.md"),
    ]:
        assert cmd in content, f"{label} must reference {cmd!r}"

    # PR-readiness must explicitly forbid signaling done on non-zero
    pr_agent_lower = " ".join(pr_agent.lower().split())
    assert "never signal `done` on non-zero" in pr_agent_lower


def test_workflow_testing_run_label_matches_injected_vocabulary() -> None:
    """The worker injects `1st`/`2nd`; workflow.md must not document the stale forms."""
    workflow = (ROOT / "workflow.md").read_text()

    assert "1st (post-implementation)" in workflow
    assert "2nd (post-quality)" in workflow
    assert "first (post-implementation)" not in workflow
    assert "second (post-quality)" not in workflow


def test_workflow_does_not_run_final_polish_before_transition_lands() -> None:
    """The gate requires `testing -> pr-readiness` to be the latest accepted transition,
    so workflow.md must not instruct running it before that transition lands."""
    workflow = (ROOT / "workflow.md").read_text()
    normalized = " ".join(workflow.split())

    assert "before signaling into pr-readiness" not in normalized
    assert "pr-readiness owns it" in normalized
    assert "latest\naccepted transition" in workflow or (
        "latest accepted transition" in normalized
    )


def test_quality_assets_defer_gate_to_pr_readiness() -> None:
    """The full gate cannot pass mid-quality; quality assets must instruct NOT to run
    it (deferring to pr-readiness), never present it as an executable quality step."""
    quality_agent = (ROOT / "agents" / "quality-agent.md").read_text()
    quality_skill = (ROOT / "skills" / "quality" / "SKILL.md").read_text()

    cmd = "samocode check final-polish"
    for content, label in [
        (quality_agent, "agents/quality-agent.md"),
        (quality_skill, "skills/quality/SKILL.md"),
    ]:
        lowered = " ".join(content.lower().split())
        if cmd in content:
            assert f"do not run `{cmd}`" in lowered, (
                f"{label} may only mention {cmd!r} as deferred-to-pr-readiness guidance"
            )


def test_cli_entry_point_lives_inside_the_package() -> None:
    assert 'samocode = "worker.cli:main"' in (ROOT / "pyproject.toml").read_text()
    assert "from worker.cli import main" in (ROOT / "main.py").read_text()
