"""Copy the bundled /icloudseal skill into host skill directories.

Default hosts match the seal-family installer: Copilot, Claude, Codex,
``.agents``, and Hermes. Whole-directory home symlinks (for example a
dotfiles checkout) are skipped unless ``--force`` is passed, so a public
install does not dirty a linked repo.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import Any

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

DEFAULT_SKILL_PLATFORMS: tuple[str, ...] = (
    "copilot",
    "claude",
    "codex",
    "agents",
    "hermes",
)

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
    """Resolve SKILL.md from a checkout, then from the installed wheel."""
    candidates: list[Path] = []
    if project_root is not None:
        candidates.append(Path(project_root) / "skills" / skill_name)
    env = os.environ.get("ICLOUDSEAL_PROJECT_ROOT")
    if env:
        candidates.append(Path(env) / "skills" / skill_name)
    module_dir = Path(__file__).resolve().parent
    candidates.append(module_dir.parent / "skills" / skill_name)
    candidates.append(module_dir / "data" / "skills" / skill_name)
    for candidate in candidates:
        if (candidate / "SKILL.md").is_file():
            return candidate
    raise SkillInstallError(
        "Packaged skill missing. Expected skills/"
        f"{skill_name}/SKILL.md next to the checkout or in the wheel."
    )


def packaged_helper_source(project_root: Path | None = None) -> Path:
    """Resolve native-approval.swift from a checkout, then from the wheel."""
    candidates: list[Path] = []
    if project_root is not None:
        candidates.append(Path(project_root) / "native-approval.swift")
    env = os.environ.get("ICLOUDSEAL_PROJECT_ROOT")
    if env:
        candidates.append(Path(env) / "native-approval.swift")
    module_dir = Path(__file__).resolve().parent
    candidates.append(module_dir.parent / "native-approval.swift")
    candidates.append(module_dir / "data" / "native-approval.swift")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


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


def _symlink_host_dir(dest: Path, root: Path) -> Path | None:
    """Return the home-relative ancestor of dest that is a symlink, if any."""
    try:
        dest.relative_to(root)
    except ValueError:
        return None
    current = dest.parent
    while current != root and current != current.parent:
        if current.is_symlink():
            return current
        current = current.parent
    return None


def hermes_mcp_attach_command(project_root: Path | None = None) -> dict[str, Any]:
    root = Path(project_root) if project_root is not None else default_project_root()
    local_command = str(root / "mcp-wrapper.sh")
    return {
        "host": "hermes",
        "skillPath": f"~/.hermes/skills/{DEFAULT_SKILL_NAME}/SKILL.md",
        "add": (
            "printf 'Y\\n' | hermes mcp add icloudseal "
            "--command icloudseal-mcp-server"
        ),
        "local": {
            "command": local_command,
            "add": (
                "printf 'Y\\n' | hermes mcp add icloudseal "
                f"--command {json.dumps(local_command)}"
            ),
        },
        "config": {
            "mcp_servers": {
                "icloudseal": {
                    "command": "icloudseal-mcp-server",
                    "args": [],
                }
            }
        },
        "note": (
            "Restart Hermes after adding. Tools register as "
            "mcp_icloudseal_icloud_* / mcp__icloudseal__icloud_*. "
            "From a git checkout, local.command still points at mcp-wrapper.sh."
        ),
    }


def vscode_mcp_snippet(project_root: Path | None = None) -> dict[str, Any]:
    """Installed-package mcp.json plus the git-checkout wrapper path."""
    root = Path(project_root) if project_root is not None else default_project_root()
    local_command = str(root / "mcp-wrapper.sh")
    return {
        "servers": {
            "icloudseal": {
                "type": "stdio",
                "command": "icloudseal-mcp-server",
            }
        },
        "local": {
            "servers": {
                "icloudseal": {
                    "type": "stdio",
                    "command": local_command,
                }
            }
        },
    }


def install_skill(
    *,
    project_root: Path | None = None,
    platforms: Iterable[str] | None = None,
    project: bool = False,
    project_dir: Path | None = None,
    home_dir: Path | None = None,
    skill_name: str = DEFAULT_SKILL_NAME,
    version: str = __version__,
    force: bool = False,
) -> dict[str, Any]:
    src_dir = packaged_skill_dir(project_root, skill_name)
    skill_src = src_dir / "SKILL.md"
    if not skill_src.is_file():
        raise SkillInstallError(f"Packaged skill missing: {skill_src}")
    chosen = list(platforms) if platforms is not None else list(DEFAULT_SKILL_PLATFORMS)
    root = Path(project_dir or Path.cwd()) if project else Path(home_dir or Path.home())
    installed: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    for platform in chosen:
        dest = skill_destination(
            platform=platform,
            project=project,
            project_dir=project_dir,
            home_dir=home_dir,
            skill_name=skill_name,
        )
        if not project and not force:
            linked = _symlink_host_dir(dest, root)
            if linked is not None:
                skipped.append(
                    {
                        "platform": platform,
                        "path": str(dest),
                        "reason": f"destination host dir is a symlink: {linked}",
                    }
                )
                continue
        dest_dir = dest.parent
        _copy_skill_tree(src_dir, dest_dir)
        (dest_dir / VERSION_STAMP).write_text(f"{version}\n", encoding="utf-8")
        installed.append({"platform": platform, "path": str(dest)})
    return {
        "skillName": skill_name,
        "version": version,
        "project": project,
        "installed": installed,
        "skipped": skipped,
        "mcp": hermes_mcp_attach_command(project_root),
    }


def uninstall_skill(
    *,
    platforms: Iterable[str] | None = None,
    project: bool = False,
    project_dir: Path | None = None,
    home_dir: Path | None = None,
    skill_name: str = DEFAULT_SKILL_NAME,
    force: bool = False,
) -> dict[str, Any]:
    chosen = list(platforms) if platforms is not None else list(DEFAULT_SKILL_PLATFORMS)
    removed: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
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
        if not dest_dir.exists() and not dest_dir.is_symlink():
            continue
        if not project and not force:
            linked = _symlink_host_dir(dest, stop)
            if linked is not None:
                skipped.append(
                    {
                        "platform": platform,
                        "path": str(dest),
                        "reason": f"destination host dir is a symlink: {linked}",
                    }
                )
                continue
        if not dest_dir.exists():
            continue
        shutil.rmtree(dest_dir)
        _prune_empty_parents(dest_dir.parent, stop)
        removed.append({"platform": platform, "path": str(dest)})
    return {
        "skillName": skill_name,
        "project": project,
        "removed": removed,
        "skipped": skipped,
    }


def cmd_install_skill(args: Any) -> int:
    try:
        result = install_skill(
            platforms=parse_skill_platforms(getattr(args, "platform", None)),
            project=bool(getattr(args, "project", False)),
            project_dir=Path.cwd(),
            force=bool(getattr(args, "force", False)),
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
            force=bool(getattr(args, "force", False)),
        )
    except SkillInstallError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result, indent=2))
    return 0


def cmd_setup(args: Any) -> int:
    """Install the default /icloudseal skill copies and print MCP attach snippets."""
    try:
        result = install_skill(
            platforms=parse_skill_platforms(getattr(args, "platform", None)),
            project=bool(getattr(args, "project", False)),
            project_dir=Path.cwd(),
            force=bool(getattr(args, "force", False)),
        )
    except SkillInstallError as exc:
        raise SystemExit(str(exc)) from exc
    if bool(getattr(args, "json", False)):
        print(json.dumps(result, indent=2))
        return 0
    installed = ", ".join(row["platform"] for row in result["installed"]) or "(none)"
    skipped = ", ".join(row["platform"] for row in result["skipped"]) or "(none)"
    mcp = result["mcp"]
    print(f"icloudseal-mcp {result['version']}")
    print(f"installed: {installed}")
    print(f"skipped:   {skipped}")
    print()
    print("Hermes MCP (installed package):")
    print(f"  {mcp['add']}")
    print("Hermes MCP (git checkout):")
    print(f"  {mcp['local']['add']}")
    print()
    print("VS Code / Copilot mcp.json (installed package):")
    print(json.dumps(vscode_mcp_snippet()["servers"], indent=2))
    print("VS Code / Copilot mcp.json (git checkout):")
    print(json.dumps(vscode_mcp_snippet()["local"]["servers"], indent=2))
    print()
    print("Next: icloudseal-mcp mail setup --email you@icloud.com")
    print("Mutations stay sealed until Touch ID. This command does not store credentials.")
    return 0


def add_skill_parsers(sub: Any) -> None:
    inst = sub.add_parser(
        "install-skill",
        help=(
            "Copy skills/icloudseal/ to agent skill dirs "
            "(default: copilot,claude,codex,agents,hermes)."
        ),
    )
    inst.add_argument(
        "--platform",
        default=None,
        help="Host(s): default five, a comma list, or all.",
    )
    inst.add_argument(
        "--project",
        action="store_true",
        help="Install under the current project instead of $HOME.",
    )
    inst.add_argument(
        "--force",
        action="store_true",
        help="Write even when the host dir is a symlink (dotfiles checkouts).",
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
    uninst.add_argument(
        "--force",
        action="store_true",
        help="Remove even when the host dir is a symlink (dotfiles checkouts).",
    )
    uninst.set_defaults(func=cmd_uninstall_skill)

    setup = sub.add_parser(
        "setup",
        help="Install /icloudseal on the default hosts and print MCP attach snippets.",
    )
    setup.add_argument(
        "--platform",
        default=None,
        help="Host(s): default five, a comma list, or all.",
    )
    setup.add_argument(
        "--project",
        action="store_true",
        help="Install under the current project instead of $HOME.",
    )
    setup.add_argument(
        "--force",
        action="store_true",
        help="Write even when the host dir is a symlink (dotfiles checkouts).",
    )
    setup.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    setup.set_defaults(func=cmd_setup)
