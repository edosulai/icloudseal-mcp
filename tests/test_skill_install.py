"""Skill installer isolations (temp HOME only; never write the live home)."""

from __future__ import annotations

from pathlib import Path

import pytest

from icloudseal_mcp.skill_install import (
    DEFAULT_SKILL_PLATFORMS,
    SKILL_PLATFORMS,
    SkillInstallError,
    hermes_mcp_attach_command,
    install_skill,
    packaged_helper_source,
    packaged_skill_dir,
    parse_skill_platforms,
    skill_destination,
    uninstall_skill,
    vscode_mcp_snippet,
)


def test_parse_skill_platforms_defaults_five_and_rejects_unknown() -> None:
    assert parse_skill_platforms(None) == [
        "copilot",
        "claude",
        "codex",
        "agents",
        "hermes",
    ]
    assert parse_skill_platforms("") == list(DEFAULT_SKILL_PLATFORMS)
    assert DEFAULT_SKILL_PLATFORMS == (
        "copilot",
        "claude",
        "codex",
        "agents",
        "hermes",
    )
    assert "copilot" in parse_skill_platforms("all")
    assert "claude" in parse_skill_platforms("all")
    assert "hermes" in parse_skill_platforms("all")
    assert len(parse_skill_platforms("all")) == len(SKILL_PLATFORMS)
    assert parse_skill_platforms("copilot,claude") == ["copilot", "claude"]
    with pytest.raises(SkillInstallError, match="Unknown skill platform"):
        parse_skill_platforms("not-a-host")


def test_install_skill_copies_into_isolated_home(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    root = Path(__file__).resolve().parent.parent

    defaulted = install_skill(
        project_root=root,
        home_dir=home,
        version="test",
    )
    assert [row["platform"] for row in defaulted["installed"]] == [
        "copilot",
        "claude",
        "codex",
        "agents",
        "hermes",
    ]
    assert defaulted["skipped"] == []
    assert not (home / ".cursor").exists()
    assert "icloudseal-mcp-server" in defaulted["mcp"]["add"]

    dest = skill_destination(platform="copilot", home_dir=home)
    body = dest.read_text(encoding="utf-8")
    assert body.startswith("---\nname: icloudseal\n")
    assert "icloud_request_local_approval" in body
    tools = (dest.parent / "references" / "tools.md").read_text(encoding="utf-8")
    assert "icloud_doctor" in tools
    assert "icloud_prepare_mail_send" in tools
    assert (dest.parent / ".icloudseal_version").read_text(encoding="utf-8").strip() == "test"
    assert (home / ".hermes" / "skills" / "icloudseal" / "SKILL.md").is_file()

    project_result = install_skill(
        project_root=root,
        platforms=["codex"],
        project=True,
        project_dir=project,
        version="test",
    )
    project_dest = Path(project_result["installed"][0]["path"])
    assert project_dest == project / ".codex" / "skills" / "icloudseal" / "SKILL.md"
    assert project_dest.is_file()

    removed = uninstall_skill(
        platforms=["copilot", "claude", "codex", "agents", "hermes"],
        home_dir=home,
    )
    assert len(removed["removed"]) == 5
    assert not dest.exists()
    leftover = uninstall_skill(platforms=["copilot"], home_dir=home)
    assert leftover["removed"] == []


def test_install_skill_skips_symlink_host_dirs_unless_forced(tmp_path: Path) -> None:
    home = tmp_path / "home"
    real = tmp_path / "dotfiles" / "home" / ".claude"
    real.mkdir(parents=True)
    home.mkdir()
    (home / ".claude").symlink_to(real)
    root = Path(__file__).resolve().parent.parent

    skipped = install_skill(
        project_root=root,
        platforms=["claude", "hermes"],
        home_dir=home,
        version="test",
    )
    assert [row["platform"] for row in skipped["installed"]] == ["hermes"]
    assert [row["platform"] for row in skipped["skipped"]] == ["claude"]
    assert "symlink" in skipped["skipped"][0]["reason"]
    assert not (real / "skills" / "icloudseal").exists()

    forced = install_skill(
        project_root=root,
        platforms=["claude"],
        home_dir=home,
        version="test",
        force=True,
    )
    assert [row["platform"] for row in forced["installed"]] == ["claude"]
    assert (real / "skills" / "icloudseal" / "SKILL.md").is_file()

    skipped_rm = uninstall_skill(
        platforms=["claude", "hermes"],
        home_dir=home,
    )
    assert [row["platform"] for row in skipped_rm["removed"]] == ["hermes"]
    assert [row["platform"] for row in skipped_rm["skipped"]] == ["claude"]
    assert (real / "skills" / "icloudseal" / "SKILL.md").is_file()

    forced_rm = uninstall_skill(
        platforms=["claude"],
        home_dir=home,
        force=True,
    )
    assert [row["platform"] for row in forced_rm["removed"]] == ["claude"]
    assert not (real / "skills" / "icloudseal").exists()


def test_packaged_skill_falls_back_to_wheel_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ICLOUDSEAL_PROJECT_ROOT", raising=False)
    import icloudseal_mcp.skill_install as skill_install

    site = tmp_path / "site-packages" / "icloudseal_mcp"
    wheel_skill = site / "data" / "skills" / "icloudseal"
    wheel_skill.mkdir(parents=True)
    (wheel_skill / "SKILL.md").write_text("wheel-skill\n", encoding="utf-8")
    (site / "data" / "native-approval.swift").write_text("wheel-helper\n", encoding="utf-8")
    (site / "skill_install.py").write_text("# fake module path\n", encoding="utf-8")
    monkeypatch.setattr(skill_install, "__file__", str(site / "skill_install.py"))

    missing = tmp_path / "empty-checkout"
    missing.mkdir()
    src = packaged_skill_dir(missing)
    assert src == wheel_skill
    assert (src / "SKILL.md").read_text(encoding="utf-8") == "wheel-skill\n"
    helper = packaged_helper_source(missing)
    assert helper == site / "data" / "native-approval.swift"
    assert helper.read_text(encoding="utf-8") == "wheel-helper\n"


def test_hermes_mcp_attach_command_is_python_not_npx() -> None:
    snippet = hermes_mcp_attach_command(Path("/tmp/icloudseal-mcp"))
    assert "npx" not in snippet["add"]
    assert snippet["config"]["mcp_servers"]["icloudseal"]["command"] == "icloudseal-mcp-server"
    assert snippet["local"]["command"].endswith("mcp-wrapper.sh")
    vscode = vscode_mcp_snippet(Path("/tmp/icloudseal-mcp"))
    assert vscode["servers"]["icloudseal"]["command"] == "icloudseal-mcp-server"
    assert vscode["local"]["servers"]["icloudseal"]["command"].endswith(
        "mcp-wrapper.sh"
    )
