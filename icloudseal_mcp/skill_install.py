"""Copy the bundled /icloudseal skill into host skill directories.

Default host is Copilot only. Other platforms are opt-in via ``--platform``.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Iterable

from . import __version__

DEFAULT_SKILL_NAME = "icloudseal"
VERSION_STAMP = ".icloudseal_version"

SKILL_PLATFORMS: dict[str, tuple[str, ...]] = {
    "copilot": (".copilot", "skills"),
    "claude": (".claude", "skills"),
    "codex": (".codex", "skills"),
    "agents": (".agents", "skills"),
    "cursor": (".cursor", "skills"),
    "gemini": (".gemini", "skills"),
    "opencode": (".config", "opencode", "skills"),
    "kilo": (".config", "kilo", "skills"),
    "aider": (".aider", "skills"),
    "claw": (".openclaw", "skills"),
    "droid": (".factory", "skills"),
    "trae": (".trae", "skills"),
    "trae-cn": (".trae-cn", "skills"),
    "hermes": (".hermes", "skills"),
    "kiro": (".kiro", "skills"),
    "pi": (".pi", "agent", "skills"),
    "codebuddy": (".codebuddy", "skills"),
    "antigravity": (".gemini", "config", "skills"),
    "windows": (".claude", "skills"),
    "kimi": (".kimi", "skills"),
    "amp": (".config", "agents", "skills"),
    "devin": (".config", "devin", "skills"),
}

# Default MUST stay a single host. Multi-host home dirs on this Mac are
# whole-directory symlinks into DOTFILE and would double-load + dirty that repo.
DEFAULT_SKILL_PLATFORMS: tuple[str, ...] = ("copilot",)

PROJECT_SKILL_ROOTS: dict[str, tuple[str, ...]] = {
    "copilot": (".copilot", "skills"),
    "claude": (".claude", "skills"),
    "codex": (".codex", "skills"),
    "agents": (".agents", "skills"),
    "cursor": (".cursor", "skills"),
    "gemini": (".gemini", "skills"),
    "opencode": (".opencode", "skills"),
    "kilo": (".kilo", "skills"),
    "aider": (".aider", "skills"),
    "claw": (".openclaw", "skills"),
    "droid": (".factory", "skills"),
    "trae": (".trae", "skills"),
    "trae-cn": (".trae-cn", "skills"),
    "hermes": (".hermes", "skills"),
    "kiro": (".kiro", "skills"),
    "pi": (".pi", "agent", "skills"),
    "codebuddy": (".codebuddy", "skills"),
    "antigravity": (".agents", "skills"),
    "windows": (".claude", "skills"),
    "kimi": (".kimi", "skills"),
    "amp": (".agents", "skills"),
    "devin": (".devin", "skills"),
}


class SkillInstallError(ValueError):
    """Unknown platform or missing packaged skill."""


def default_project_root() -> Path:
    env = os.environ.get("ICLOUDSEAL_PROJECT_ROOT")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent


def packaged_skill_dir(
    project_root: Path | None = None,
    skill_name: str = DEFAULT_SKILL_NAME,
) -> Path:
    root = project_root if project_root is not None else default_project_root()
    return Path(root) / "skills" / skill_name


def skill_destination(
    *,
    platform: str,
    project: bool = False,
    project_dir: Path | None = None,
    home_dir: Path | None = None,
    skill_name: str = DEFAULT_SKILL_NAME,
) -> Path:
    parts = PROJECT_SKILL_ROOTS.get(platform) if project else SKILL_PLATFORMS.get(platform)
    if not parts:
        raise SkillInstallError(f"Unknown skill platform: {platform}")
    root = Path(project_dir or Path.cwd()) if project else Path(home_dir or Path.home())
    return root.joinpath(*parts, skill_name, "SKILL.md")


def parse_skill_platforms(
    raw: str | None,
    *,
    default_platforms: Iterable[str] = DEFAULT_SKILL_PLATFORMS,
) -> list[str]:
    if raw is None or raw == "":
        return list(default_platforms)
    requested = [part.strip() for part in str(raw).split(",") if part.strip()]
    if "all" in requested:
        return list(SKILL_PLATFORMS)
    unknown = [name for name in requested if name not in SKILL_PLATFORMS]
    if unknown:
        raise SkillInstallError(f"Unknown skill platform: {', '.join(unknown)}")
    return list(dict.fromkeys(requested))


def _copy_skill_tree(src_dir: Path, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_dir / "SKILL.md", dest_dir / "SKILL.md")
    refs_src = src_dir / "references"
    refs_dest = dest_dir / "references"
    if refs_dest.exists():
        shutil.rmtree(refs_dest)
    if not refs_src.is_dir():
        return
    refs_dest.mkdir(parents=True, exist_ok=True)
    for entry in refs_src.iterdir():
        if entry.is_file():
            shutil.copy2(entry, refs_dest / entry.name)


def _prune_empty_parents(start_dir: Path, stop_dir: Path) -> None:
    current = start_dir
    for _ in range(4):
        if current == stop_dir or not current.exists():
            break
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def install_skill(
    *,
    project_root: Path | None = None,
    platforms: Iterable[str] | None = None,
    project: bool = False,
    project_dir: Path | None = None,
    home_dir: Path | None = None,
    skill_name: str = DEFAULT_SKILL_NAME,
    version: str = __version__,
) -> dict[str, Any]:
    src_dir = packaged_skill_dir(project_root, skill_name)
    skill_src = src_dir / "SKILL.md"
    if not skill_src.is_file():
        raise SkillInstallError(f"Packaged skill missing: {skill_src}")
    chosen = list(platforms) if platforms is not None else list(DEFAULT_SKILL_PLATFORMS)
    installed: list[dict[str, str]] = []
    for platform in chosen:
        dest = skill_destination(
            platform=platform,
            project=project,
            project_dir=project_dir,
            home_dir=home_dir,
            skill_name=skill_name,
        )
        dest_dir = dest.parent
        _copy_skill_tree(src_dir, dest_dir)
        (dest_dir / VERSION_STAMP).write_text(f"{version}\n", encoding="utf-8")
        installed.append({"platform": platform, "path": str(dest)})
    return {
        "skillName": skill_name,
        "version": version,
        "project": project,
        "installed": installed,
    }


def uninstall_skill(
    *,
    platforms: Iterable[str] | None = None,
    project: bool = False,
    project_dir: Path | None = None,
    home_dir: Path | None = None,
    skill_name: str = DEFAULT_SKILL_NAME,
) -> dict[str, Any]:
    chosen = list(platforms) if platforms is not None else list(DEFAULT_SKILL_PLATFORMS)
    removed: list[dict[str, str]] = []
    stop = Path(project_dir or Path.cwd()) if project else Path(home_dir or Path.home())
    for platform in chosen:
        dest = skill_destination(
            platform=platform,
            project=project,
            project_dir=project_dir,
            home_dir=home_dir,
            skill_name=skill_name,
        )
        dest_dir = dest.parent
        if not dest_dir.exists():
            continue
        shutil.rmtree(dest_dir)
        _prune_empty_parents(dest_dir.parent, stop)
        removed.append({"platform": platform, "path": str(dest)})
    return {"skillName": skill_name, "project": project, "removed": removed}


def cmd_install_skill(args: Any) -> int:
    try:
        result = install_skill(
            platforms=parse_skill_platforms(getattr(args, "platform", None)),
            project=bool(getattr(args, "project", False)),
            project_dir=Path.cwd(),
        )
    except SkillInstallError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result, indent=2))
    return 0


def cmd_uninstall_skill(args: Any) -> int:
    try:
        result = uninstall_skill(
            platforms=parse_skill_platforms(getattr(args, "platform", None)),
            project=bool(getattr(args, "project", False)),
            project_dir=Path.cwd(),
        )
    except SkillInstallError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result, indent=2))
    return 0


def add_skill_parsers(sub: Any) -> None:
    inst = sub.add_parser(
        "install-skill",
        help="Copy skills/icloudseal/ to agent skill dirs (default: copilot).",
    )
    inst.add_argument(
        "--platform",
        default=None,
        help="Host(s): copilot (default), a comma list, or all.",
    )
    inst.add_argument(
        "--project",
        action="store_true",
        help="Install under the current project instead of $HOME.",
    )
    inst.set_defaults(func=cmd_install_skill)

    uninst = sub.add_parser(
        "uninstall-skill",
        help="Remove only the icloudseal skill copy.",
    )
    uninst.add_argument("--platform", default=None, help="Host(s) to remove from.")
    uninst.add_argument(
        "--project",
        action="store_true",
        help="Remove a project-local copy instead of $HOME.",
    )
    uninst.set_defaults(func=cmd_uninstall_skill)
