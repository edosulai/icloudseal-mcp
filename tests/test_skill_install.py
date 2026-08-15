"""Skill installer isolations (temp HOME only; never write the live home)."""

from __future__ import annotations

from pathlib import Path

import pytest

from icloudseal_mcp.skill_install import (
    DEFAULT_SKILL_PLATFORMS,
    SKILL_PLATFORMS,
    SkillInstallError,
    install_skill,
    parse_skill_platforms,
    skill_destination,
    uninstall_skill,
)


def test_parse_skill_platforms_defaults_all_and_rejects_unknown() -> None:
    assert parse_skill_platforms(None) == ["copilot"]
    assert parse_skill_platforms("") == ["copilot"]
    assert DEFAULT_SKILL_PLATFORMS == ("copilot",)
    assert "copilot" in parse_skill_platforms("all")
    assert "claude" in parse_skill_platforms("all")
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
    assert [row["platform"] for row in defaulted["installed"]] == ["copilot"]
    assert not (home / ".claude").exists()

    installed = install_skill(
        project_root=root,
        platforms=["copilot", "claude"],
        home_dir=home,
        version="test",
    )
    assert len(installed["installed"]) == 2
    dest = skill_destination(platform="copilot", home_dir=home)
    body = dest.read_text(encoding="utf-8")
    assert body.startswith("---\nname: icloudseal\n")
    assert "icloud_request_local_approval" in body
    tools = (dest.parent / "references" / "tools.md").read_text(encoding="utf-8")
    assert "icloud_doctor" in tools
    assert "icloud_prepare_mail_send" in tools
    assert (dest.parent / ".icloudseal_version").read_text(encoding="utf-8").strip() == "test"
    assert not (home / ".codex").exists()

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

    removed = uninstall_skill(platforms=["copilot", "claude"], home_dir=home)
    assert len(removed["removed"]) == 2
    assert not dest.exists()
    leftover = uninstall_skill(platforms=["copilot"], home_dir=home)
    assert leftover["removed"] == []
