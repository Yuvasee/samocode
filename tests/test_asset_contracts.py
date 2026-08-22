from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


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
