"""iCloud Photos commands (read-only metadata + best-effort export).

Reads the local Photos.sqlite catalog (needs Full Disk Access). Most originals
live in iCloud under "Optimize Mac Storage", so ``export`` only copies originals
already downloaded to this Mac and reports the rest as not-downloaded.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rich.table import Table

from ..common import console
from . import photosdb
from .photosdb import PhotosAccessError


def _guard(fn):
    try:
        return fn()
    except PhotosAccessError as exc:
        console.print(f"[red]Photos access error:[/red] {exc}")
        return None


def cmd_stats(args: argparse.Namespace) -> int:
    s = _guard(photosdb.stats)
    if s is None:
        return 2
    if args.json:
        print(json.dumps(s, indent=2))
        return 0
    table = Table(title="Photos library")
    table.add_column("Metric")
    table.add_column("Count", justify="right")
    for k in ("total", "photos", "videos", "favorites", "albums"):
        table.add_row(k, str(s[k]))
    console.print(table)
    return 0


def cmd_albums(args: argparse.Namespace) -> int:
    albums = _guard(photosdb.list_albums)
    if albums is None:
        return 2
    if args.json:
        print(json.dumps([a.__dict__ for a in albums], indent=2))
        return 0
    table = Table(title=f"Albums ({len(albums)})")
    table.add_column("Album")
    table.add_column("Items", justify="right")
    for a in albums:
        table.add_row(a.title[:50], str(a.count))
    console.print(table)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    assets = _guard(lambda: photosdb.list_assets(
        album=args.album, limit=args.limit, kind=args.kind, favorites_only=args.favorites,
    ))
    if assets is None:
        return 2
    if args.json:
        print(json.dumps([a.__dict__ for a in assets], indent=2))
        return 0
    title = f"Assets ({len(assets)})" + (f" in {args.album!r}" if args.album else "")
    table = Table(title=title)
    table.add_column("Date", style="cyan")
    table.add_column("Type", style="dim")
    table.add_column("Fav", justify="center")
    table.add_column("Filename")
    for a in assets:
        table.add_row(a.date_iso[:16].replace("T", " "), a.kind, "★" if a.favorite else "",
                      a.filename[:40])
    console.print(table)
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    assets = _guard(lambda: photosdb.list_assets(
        album=args.album, limit=args.limit, kind=args.kind, favorites_only=args.favorites,
    ))
    if assets is None:
        return 2
    dest = Path(args.dest)
    found = [(a, photosdb.find_local_original(a)) for a in assets]
    local = [(a, p) for a, p in found if p]
    remote = [a for a, p in found if not p]

    console.print(
        f"{len(assets)} asset(s) selected: [green]{len(local)} downloaded locally[/green], "
        f"[yellow]{len(remote)} only in iCloud[/yellow] (need Photos.app to download)."
    )
    if not args.apply:
        console.print(f"[yellow]Dry-run.[/yellow] Add --apply to copy the {len(local)} local "
                      f"original(s) to {dest}.")
        return 0
    dest.mkdir(parents=True, exist_ok=True)
    for asset, p in local:
        (dest / f"{asset.uuid}-{asset.filename}").write_bytes(p.read_bytes())
    console.print(f"[green]Exported {len(local)} original(s) to[/green] {dest}")
    if remote:
        console.print(f"[dim]{len(remote)} not downloaded; open them in Photos.app first.[/dim]")
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    sp = sub.add_parser("stats", help="Library totals (photos/videos/favorites/albums).")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_stats)

    sp = sub.add_parser("albums", help="List albums with item counts.")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_albums)

    sp = sub.add_parser("list", help="List assets (newest first).")
    sp.add_argument("--album", help="Limit to an album by exact title")
    sp.add_argument("--kind", choices=["photo", "video"], help="Filter by type")
    sp.add_argument("--favorites", action="store_true", help="Only favorites")
    sp.add_argument("--limit", type=int, default=50)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("export", help="Copy locally-downloaded originals out. Requires --apply.")
    sp.add_argument("dest", help="Destination directory")
    sp.add_argument("--album")
    sp.add_argument("--kind", choices=["photo", "video"])
    sp.add_argument("--favorites", action="store_true")
    sp.add_argument("--limit", type=int, default=200)
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_export)


__all__ = ["register"]
