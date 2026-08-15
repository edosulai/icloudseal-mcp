"""iCloud Drive commands (filesystem-backed).

iCloud Drive is mounted at
``~/Library/Mobile Documents/com~apple~CloudDocs``. Reads are plain filesystem
ops. Writes (``put``/``mkdir``/``rm``) are gated behind ``--apply``; ``rm``
moves items to the macOS Trash (never a permanent delete).
"""

from __future__ import annotations

import argparse
import fnmatch
import shutil
import subprocess
from pathlib import Path

from rich.table import Table

from ..common import console, human_size

DRIVE_ROOT = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
MAX_READ_BYTES = 200_000


def _resolve(path: str | None) -> Path:
    if not path:
        return DRIVE_ROOT
    p = Path(path)
    candidate = p if p.is_absolute() else DRIVE_ROOT / p
    resolved = candidate.expanduser().resolve()
    root = DRIVE_ROOT.resolve()
    if root not in resolved.parents and resolved != root:
        raise SystemExit(f"Path {resolved} is outside iCloud Drive.")
    return resolved


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(DRIVE_ROOT.resolve()))
    except ValueError:
        return str(p)


def cmd_ls(args: argparse.Namespace) -> int:
    base = _resolve(args.path)
    if not base.exists():
        raise SystemExit(f"Not found: {base}")
    if base.is_file():
        console.print(_rel(base))
        return 0
    entries = sorted(base.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    table = Table(title=f"iCloud Drive: {_rel(base) or '/'} ({len(entries)})")
    table.add_column("Type", style="dim")
    table.add_column("Name")
    table.add_column("Size", justify="right", style="dim")
    for e in entries:
        kind = "dir" if e.is_dir() else "file"
        size = "" if e.is_dir() else human_size(e.stat().st_size)
        # .icloud placeholders mark not-yet-downloaded files
        if e.name.startswith(".") and e.name.endswith(".icloud"):
            name = e.name[1:-7] + " ☁"
        else:
            name = e.name
        table.add_row(kind, name, size)
    console.print(table)
    return 0


def cmd_tree(args: argparse.Namespace) -> int:
    base = _resolve(args.path)

    def walk(d: Path, prefix: str, depth: int) -> None:
        if depth > args.depth:
            return
        kids = sorted(d.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        for i, k in enumerate(kids):
            last = i == len(kids) - 1
            console.print(f"{prefix}{'└── ' if last else '├── '}{k.name}")
            if k.is_dir():
                walk(k, prefix + ("    " if last else "│   "), depth + 1)

    console.print(_rel(base) or "/")
    walk(base, "", 1)
    return 0


def cmd_find(args: argparse.Namespace) -> int:
    base = _resolve(args.path)
    hits = [p for p in base.rglob("*") if fnmatch.fnmatch(p.name, args.pattern)]
    table = Table(title=f"Matches for {args.pattern!r} under {_rel(base) or '/'} ({len(hits)})")
    table.add_column("Path")
    table.add_column("Size", justify="right", style="dim")
    for p in hits[: args.limit]:
        table.add_row(_rel(p), "" if p.is_dir() else human_size(p.stat().st_size))
    console.print(table)
    if len(hits) > args.limit:
        console.print(f"[dim]... {len(hits) - args.limit} more (raise --limit)[/dim]")
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    p = _resolve(args.path)
    if not p.is_file():
        raise SystemExit(f"Not a file: {p}")
    data = p.read_bytes()[:MAX_READ_BYTES]
    console.rule(_rel(p))
    console.print(data.decode("utf-8", errors="replace"))
    if p.stat().st_size > MAX_READ_BYTES:
        console.print(f"[dim]... truncated at {MAX_READ_BYTES} bytes[/dim]")
    return 0


def cmd_mkdir(args: argparse.Namespace) -> int:
    dest = _resolve(args.path)
    if dest == DRIVE_ROOT.resolve():
        raise SystemExit("Refusing to mkdir the iCloud Drive root.")
    if dest.exists():
        raise SystemExit(f"Already exists: {_rel(dest)}")
    console.print(f"Would create directory [bold]{_rel(dest)}[/bold]")
    if not args.apply:
        console.print("[yellow]Dry-run.[/yellow] Add --apply to create the directory.")
        return 0
    dest.mkdir(parents=True, exist_ok=False)
    console.print(f"[green]Created[/green] {_rel(dest)}")
    return 0


def cmd_put(args: argparse.Namespace) -> int:
    src = Path(args.local).expanduser().resolve()
    if not src.is_file():
        raise SystemExit(f"Local file not found: {src}")
    dest = _resolve(args.dest)
    if dest.is_dir():
        dest = dest / src.name
    console.print(f"Would copy [bold]{src}[/bold] -> [bold]{_rel(dest)}[/bold]")
    if not args.apply:
        console.print("[yellow]Dry-run.[/yellow] Add --apply to copy.")
        return 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(src.read_bytes())
    console.print(f"[green]Copied to[/green] {_rel(dest)}")
    return 0


def cmd_rm(args: argparse.Namespace) -> int:
    p = _resolve(args.path)
    if not p.exists():
        raise SystemExit(f"Not found: {p}")
    console.print(f"Would move to Trash: [bold]{_rel(p)}[/bold]")
    if not args.apply:
        console.print("[yellow]Dry-run.[/yellow] Add --apply to move it to Trash.")
        return 0
    script = """on run argv
    set targetPath to item 1 of argv
    tell application "Finder" to delete (POSIX file targetPath as alias)
end run"""
    result = subprocess.run(
        ["osascript", "-e", script, "--", str(p)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        console.print(f"[red]Trash failed:[/red] {result.stderr.strip()}")
        return 2
    console.print(f"[green]Moved to Trash:[/green] {_rel(p)}")
    return 0


def _transfer_preview(op: str, src: Path, dest: Path) -> None:
    console.print(f"Would {op} [bold]{_rel(src)}[/bold] -> [bold]{_rel(dest)}[/bold]")


def cmd_rename(args: argparse.Namespace) -> int:
    src = _resolve(args.src)
    if not src.exists():
        raise SystemExit(f"Not found: {src}")
    if src == DRIVE_ROOT.resolve():
        raise SystemExit("Refusing to rename the iCloud Drive root.")
    name = (args.dest or "").strip()
    if not name or "/" in name or name in {".", ".."}:
        raise SystemExit("rename dest must be a single file or folder name.")
    dest = (src.parent / name).resolve()
    if dest.parent != src.parent.resolve():
        raise SystemExit("rename dest must stay in the same directory; use move instead.")
    if dest.exists() and dest.is_dir():
        raise SystemExit("Refusing to overwrite a directory destination.")
    if dest.exists() and not args.overwrite:
        raise SystemExit(f"Already exists: {_rel(dest)} (pass --overwrite).")
    _transfer_preview("rename", src, dest)
    if not args.apply:
        console.print("[yellow]Dry-run.[/yellow] Add --apply to rename.")
        return 0
    if dest.exists():
        dest.unlink()
    src.replace(dest)
    console.print(f"[green]Renamed[/green] {_rel(src)} -> {_rel(dest)}")
    return 0


def cmd_move(args: argparse.Namespace) -> int:
    src = _resolve(args.src)
    if not src.exists():
        raise SystemExit(f"Not found: {src}")
    if src == DRIVE_ROOT.resolve():
        raise SystemExit("Refusing to move the iCloud Drive root.")
    dest = _resolve(args.dest)
    if dest.is_dir() and dest != src:
        dest = dest / src.name
    if dest == DRIVE_ROOT.resolve():
        raise SystemExit("Destination must be inside iCloud Drive, not the root.")
    if dest.exists() and dest.is_dir():
        raise SystemExit("Refusing to overwrite a directory destination.")
    if dest.exists() and not args.overwrite:
        raise SystemExit(f"Already exists: {_rel(dest)} (pass --overwrite).")
    _transfer_preview("move", src, dest)
    if not args.apply:
        console.print("[yellow]Dry-run.[/yellow] Add --apply to move.")
        return 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    src.replace(dest)
    console.print(f"[green]Moved[/green] {_rel(src)} -> {_rel(dest)}")
    return 0


def cmd_copy(args: argparse.Namespace) -> int:
    src = _resolve(args.src)
    if not src.exists():
        raise SystemExit(f"Not found: {src}")
    if src == DRIVE_ROOT.resolve():
        raise SystemExit("Refusing to copy the iCloud Drive root.")
    dest = _resolve(args.dest)
    if dest.is_dir() and dest != src:
        dest = dest / src.name
    if dest == DRIVE_ROOT.resolve():
        raise SystemExit("Destination must be inside iCloud Drive, not the root.")
    if dest.exists() and dest.is_dir():
        raise SystemExit("Refusing to overwrite a directory destination.")
    if dest.exists() and not args.overwrite:
        raise SystemExit(f"Already exists: {_rel(dest)} (pass --overwrite).")
    _transfer_preview("copy", src, dest)
    if not args.apply:
        console.print("[yellow]Dry-run.[/yellow] Add --apply to copy.")
        return 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        if dest.exists():
            raise SystemExit("Refusing to overwrite a destination while copying a directory.")
        shutil.copytree(src, dest, symlinks=False)
    else:
        shutil.copy2(src, dest)
    console.print(f"[green]Copied[/green] {_rel(src)} -> {_rel(dest)}")
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    sp = sub.add_parser("ls", help="List a directory.")
    sp.add_argument("path", nargs="?", help="Path relative to iCloud Drive root")
    sp.set_defaults(func=cmd_ls)

    sp = sub.add_parser("tree", help="Print a directory tree.")
    sp.add_argument("path", nargs="?")
    sp.add_argument("--depth", type=int, default=2)
    sp.set_defaults(func=cmd_tree)

    sp = sub.add_parser("find", help="Find files by glob pattern.")
    sp.add_argument("pattern")
    sp.add_argument("path", nargs="?")
    sp.add_argument("--limit", type=int, default=100)
    sp.set_defaults(func=cmd_find)

    sp = sub.add_parser("read", help="Print a text file.")
    sp.add_argument("path")
    sp.set_defaults(func=cmd_read)

    sp = sub.add_parser("mkdir", help="Create a directory in iCloud Drive. Requires --apply.")
    sp.add_argument("path", help="Directory path relative to iCloud Drive root")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_mkdir)

    sp = sub.add_parser("put", help="Copy a local file into iCloud Drive. Requires --apply.")
    sp.add_argument("local")
    sp.add_argument("dest", help="Destination path/dir in iCloud Drive")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_put)

    sp = sub.add_parser("rm", help="Move a file/dir to Trash. Requires --apply.")
    sp.add_argument("path")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_rm)

    sp = sub.add_parser("rename", help="Rename a file/dir in place. Requires --apply.")
    sp.add_argument("src")
    sp.add_argument("dest", help="New basename only (no slashes)")
    sp.add_argument("--overwrite", action="store_true")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_rename)

    sp = sub.add_parser("move", help="Move a file/dir inside iCloud Drive. Requires --apply.")
    sp.add_argument("src")
    sp.add_argument("dest")
    sp.add_argument("--overwrite", action="store_true")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_move)

    sp = sub.add_parser("copy", help="Copy a file/dir inside iCloud Drive. Requires --apply.")
    sp.add_argument("src")
    sp.add_argument("dest")
    sp.add_argument("--overwrite", action="store_true")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_copy)


__all__ = ["register"]
