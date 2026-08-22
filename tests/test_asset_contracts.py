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
    stale = "Phase: implementation\n   - `Blocked: no`\n   - `Last Action: Plan approved"
    assert stale not in content


def test_planning_agent_references_approve_cli() -> None:
    content = (ROOT / "agents" / "planning-agent.md").read_text()
    assert "samocode approve" in content


def test_architecture_source_map_covers_new_modules() -> None:
    content = (ROOT / "ARCHITECTURE.md").read_text()
    assert "workflow_event.py" in content
    assert "workflow_state.py" in content
    assert "approval.py" in content


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
