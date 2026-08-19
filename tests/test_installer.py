"""Tests for worker.installer - install/uninstall primitives and orchestration."""

from pathlib import Path

import pytest

from worker import installer
from worker.installer import (
    ASSET_CLASSES,
    AssetClass,
    InstallMode,
    InstallOutcome,
    UninstallOutcome,
    _enumerate_asset_entries,
    _is_inside_git_repo,
    _is_inside_site_packages,
    _is_samocode_owned,
    _uninstall_asset,
    install,
    install_asset,
    resolve_install_mode,
    resolve_provider_base_dir,
    resolve_target_dir,
    uninstall,
)


# === Fixtures ===


@pytest.fixture
def fake_src_root(tmp_path: Path) -> Path:
    """A fake samocode source tree with one of each asset class plus a template."""
    root = tmp_path / "samocode"
    (root / "skills" / "investigation").mkdir(parents=True)
    (root / "skills" / "investigation" / "SKILL.md").write_text("skill")
    (root / "skills" / "planning").mkdir(parents=True)
    (root / "skills" / "planning" / "SKILL.md").write_text("skill")
    (root / "agents").mkdir()
    (root / "agents" / "quality-agent.md").write_text("agent")
    (root / "agents" / "AGENT_TEMPLATE.md").write_text("template")
    (root / "commands").mkdir()
    (root / "commands" / "samocode-run.md").write_text("command")
    return root.resolve()


# === resolve_provider_base_dir ===


def test_provider_base_dir_claude(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert resolve_provider_base_dir("claude") == tmp_path / ".claude"


def test_provider_base_dir_codex_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    assert resolve_provider_base_dir("codex") == tmp_path / ".codex"


def test_provider_base_dir_codex_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    custom = tmp_path / "custom-codex"
    monkeypatch.setenv("CODEX_HOME", str(custom))
    assert resolve_provider_base_dir("codex") == custom.resolve()


def test_provider_base_dir_unknown_raises():
    with pytest.raises(ValueError):
        resolve_provider_base_dir("gemini")


# === resolve_target_dir ===


def test_target_dir_valid(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    skills = ASSET_CLASSES[0]
    assert resolve_target_dir(skills, "codex") == tmp_path / ".codex" / "skills"


def test_target_dir_provider_not_allowed():
    agents = AssetClass("agents", "agents", is_dir_asset=False, providers=("claude",))
    with pytest.raises(ValueError):
        resolve_target_dir(agents, "codex")


# === Mode detection ===


def test_inside_git_repo(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    child = tmp_path / "skills"
    child.mkdir()
    assert _is_inside_git_repo(child) is True


def test_not_inside_git_repo(tmp_path: Path):
    assert _is_inside_git_repo(tmp_path) is False


def test_inside_site_packages():
    assert _is_inside_site_packages(Path("/x/lib/python3.11/site-packages/samocode"))
    assert not _is_inside_site_packages(Path("/home/dev/samocode"))


def test_resolve_install_mode_site_packages():
    p = Path("/x/lib/python3.11/site-packages/samocode")
    assert resolve_install_mode(p) is InstallMode.COPY


def test_resolve_install_mode_git_checkout(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    assert resolve_install_mode(tmp_path) is InstallMode.SYMLINK


def test_resolve_install_mode_neither(tmp_path: Path):
    assert resolve_install_mode(tmp_path) is InstallMode.COPY


# === install_asset ===


def test_install_asset_creates_symlink(tmp_path: Path, fake_src_root: Path):
    src = fake_src_root / "skills" / "investigation"
    target = tmp_path / "out" / "investigation"
    result = install_asset(src, target, InstallMode.SYMLINK, src_root=fake_src_root)
    assert result.outcome is InstallOutcome.INSTALLED
    assert target.is_symlink()
    assert target.resolve() == src.resolve()


def test_install_asset_refreshes_owned_symlink(tmp_path: Path, fake_src_root: Path):
    src = fake_src_root / "skills" / "investigation"
    target = tmp_path / "out" / "investigation"
    install_asset(src, target, InstallMode.SYMLINK, src_root=fake_src_root)
    result = install_asset(src, target, InstallMode.SYMLINK, src_root=fake_src_root)
    assert result.outcome is InstallOutcome.UPDATED
    assert target.is_symlink()


def test_install_asset_skips_foreign_symlink(tmp_path: Path, fake_src_root: Path):
    """A symlink pointing outside src_root must never be clobbered (Q-002)."""
    foreign = tmp_path / "elsewhere"
    foreign.mkdir()
    target = tmp_path / "out" / "investigation"
    target.parent.mkdir(parents=True)
    target.symlink_to(foreign)

    src = fake_src_root / "skills" / "investigation"
    result = install_asset(src, target, InstallMode.SYMLINK, src_root=fake_src_root)
    assert result.outcome is InstallOutcome.SKIPPED
    assert result.warning is not None
    assert target.resolve() == foreign.resolve()  # untouched


def test_install_asset_skips_real_file(tmp_path: Path, fake_src_root: Path):
    target = tmp_path / "out" / "investigation"
    target.parent.mkdir(parents=True)
    target.write_text("user's own file")
    src = fake_src_root / "skills" / "investigation"
    result = install_asset(src, target, InstallMode.SYMLINK, src_root=fake_src_root)
    assert result.outcome is InstallOutcome.SKIPPED
    assert target.read_text() == "user's own file"


def test_install_asset_copy_dir(tmp_path: Path, fake_src_root: Path):
    src = fake_src_root / "skills" / "investigation"
    target = tmp_path / "out" / "investigation"
    result = install_asset(src, target, InstallMode.COPY, src_root=fake_src_root)
    assert result.outcome is InstallOutcome.INSTALLED
    assert not target.is_symlink()
    assert (target / "SKILL.md").read_text() == "skill"


def test_install_asset_missing_src_raises(tmp_path: Path, fake_src_root: Path):
    with pytest.raises(FileNotFoundError):
        install_asset(
            fake_src_root / "skills" / "nope",
            tmp_path / "out" / "nope",
            InstallMode.SYMLINK,
            src_root=fake_src_root,
        )


# === _is_samocode_owned ===


def test_owned_symlink_into_src_root(tmp_path: Path, fake_src_root: Path):
    src = fake_src_root / "skills" / "investigation"
    target = tmp_path / "link"
    target.symlink_to(src)
    assert _is_samocode_owned(target, fake_src_root) is True


def test_owned_rejects_foreign_symlink(tmp_path: Path, fake_src_root: Path):
    foreign = tmp_path / "elsewhere"
    foreign.mkdir()
    target = tmp_path / "link"
    target.symlink_to(foreign)
    assert _is_samocode_owned(target, fake_src_root) is False


def test_owned_rejects_sibling_prefix_dir(tmp_path: Path, fake_src_root: Path):
    """Sibling tree '<src_root>-backup' must not match by string prefix (Q-001)."""
    sibling = fake_src_root.parent / "samocode-backup"
    (sibling / "skills").mkdir(parents=True)
    asset = sibling / "skills" / "investigation"
    asset.mkdir()
    target = tmp_path / "link"
    target.symlink_to(asset)
    assert _is_samocode_owned(target, fake_src_root) is False


def test_owned_rejects_real_file(tmp_path: Path, fake_src_root: Path):
    target = tmp_path / "realfile"
    target.write_text("x")
    assert _is_samocode_owned(target, fake_src_root) is False


# === _enumerate_asset_entries ===


def test_enumerate_dir_asset(fake_src_root: Path):
    skills = AssetClass("skills", "skills", is_dir_asset=True, providers=("claude",))
    entries = _enumerate_asset_entries(skills, fake_src_root)
    assert [p.name for p in entries] == ["investigation", "planning"]


def test_enumerate_file_asset_excludes_template(fake_src_root: Path):
    agents = AssetClass("agents", "agents", is_dir_asset=False, providers=("claude",))
    entries = _enumerate_asset_entries(agents, fake_src_root)
    names = [p.name for p in entries]
    assert "quality-agent.md" in names
    assert "AGENT_TEMPLATE.md" not in names  # Q-007


def test_enumerate_missing_dir_returns_empty(fake_src_root: Path):
    missing = AssetClass(
        "x", "does-not-exist", is_dir_asset=True, providers=("claude",)
    )
    assert _enumerate_asset_entries(missing, fake_src_root) == []


# === _uninstall_asset ===


def test_uninstall_not_found(tmp_path: Path, fake_src_root: Path):
    result = _uninstall_asset(tmp_path / "nope", fake_src_root)
    assert result.outcome is UninstallOutcome.NOT_FOUND


def test_uninstall_removes_owned_symlink(tmp_path: Path, fake_src_root: Path):
    src = fake_src_root / "skills" / "investigation"
    target = tmp_path / "link"
    target.symlink_to(src)
    result = _uninstall_asset(target, fake_src_root)
    assert result.outcome is UninstallOutcome.REMOVED
    assert not target.exists() and not target.is_symlink()


def test_uninstall_skips_foreign_symlink(tmp_path: Path, fake_src_root: Path):
    foreign = tmp_path / "elsewhere"
    foreign.mkdir()
    target = tmp_path / "link"
    target.symlink_to(foreign)
    result = _uninstall_asset(target, fake_src_root)
    assert result.outcome is UninstallOutcome.SKIPPED
    assert target.is_symlink()  # untouched


def test_uninstall_skips_real_file(tmp_path: Path, fake_src_root: Path):
    target = tmp_path / "realfile"
    target.write_text("x")
    result = _uninstall_asset(target, fake_src_root)
    assert result.outcome is UninstallOutcome.SKIPPED
    assert target.exists()


# === install() / uninstall() orchestration round trip ===


@pytest.fixture
def installed_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_src_root: Path):
    """Point installer at the fake source tree and a temp HOME."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(installer, "resolve_asset_source_dir", lambda: fake_src_root)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    return home


def test_install_roundtrip_symlink(installed_env: Path, fake_src_root: Path):
    results = install(copy=False)  # force symlink
    claude_skills = installed_env / ".claude" / "skills"
    codex_skills = installed_env / ".codex" / "skills"
    claude_agents = installed_env / ".claude" / "agents"
    claude_commands = installed_env / ".claude" / "commands"

    assert (claude_skills / "investigation").is_symlink()
    assert (codex_skills / "planning").is_symlink()
    assert (claude_agents / "quality-agent.md").is_symlink()
    # template excluded
    assert not (claude_agents / "AGENT_TEMPLATE.md").exists()
    assert (claude_commands / "samocode-run.md").is_symlink()
    # all created results are non-skipped
    assert all(r.outcome is not InstallOutcome.SKIPPED for r in results)

    uninstall()
    assert not (claude_skills / "investigation").exists()
    assert not (codex_skills / "planning").exists()
    assert not (claude_agents / "quality-agent.md").exists()


def test_install_copy_mode_not_removed_by_uninstall(installed_env: Path):
    """Copy-mode installs are real files; uninstall must leave them in place."""
    install(copy=True)
    claude_skills = installed_env / ".claude" / "skills"
    assert (claude_skills / "investigation").is_dir()
    assert not (claude_skills / "investigation").is_symlink()

    uninstall()
    # copied dir is not samocode-owned (not a symlink) -> left behind
    assert (claude_skills / "investigation").is_dir()


def test_install_copy_skipped_on_rerun(installed_env: Path):
    install(copy=True)
    results = install(copy=True)
    skill_results = [r for r in results if r.src.name in ("investigation", "planning")]
    assert all(r.outcome is InstallOutcome.SKIPPED for r in skill_results)


# === install() prune + skill/command shadowing ===


def test_install_prunes_deleted_source_asset(installed_env: Path, fake_src_root: Path):
    install(copy=False)
    claude_commands = installed_env / ".claude" / "commands"
    assert (claude_commands / "samocode-run.md").is_symlink()

    (fake_src_root / "commands" / "samocode-run.md").unlink()
    install(copy=False)
    assert not (claude_commands / "samocode-run.md").is_symlink()


def test_install_prune_leaves_foreign_entries(installed_env: Path, fake_src_root: Path):
    claude_commands = installed_env / ".claude" / "commands"
    claude_commands.mkdir(parents=True)
    (claude_commands / "user-note.md").write_text("mine")
    foreign_src = installed_env / "elsewhere.md"
    foreign_src.write_text("foreign")
    (claude_commands / "foreign.md").symlink_to(foreign_src)

    install(copy=False)
    assert (claude_commands / "user-note.md").exists()
    assert (claude_commands / "foreign.md").is_symlink()


def test_install_skips_command_shadowed_by_skill(
    installed_env: Path, fake_src_root: Path
):
    (fake_src_root / "commands" / "investigation.md").write_text("trampoline")
    install(copy=False)
    claude_commands = installed_env / ".claude" / "commands"
    assert not (claude_commands / "investigation.md").exists()
    assert (claude_commands / "samocode-run.md").is_symlink()


def test_reinstall_prunes_newly_shadowed_command(
    installed_env: Path, fake_src_root: Path
):
    """A command installed before the same-named skill existed must disappear."""
    (fake_src_root / "commands" / "newskill.md").write_text("trampoline")
    install(copy=False)
    claude_commands = installed_env / ".claude" / "commands"
    assert (claude_commands / "newskill.md").is_symlink()

    (fake_src_root / "skills" / "newskill").mkdir()
    (fake_src_root / "skills" / "newskill" / "SKILL.md").write_text("skill")
    install(copy=False)
    assert not (claude_commands / "newskill.md").exists()
