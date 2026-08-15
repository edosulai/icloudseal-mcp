"""Maps commands. Search is local URL construction; open is dry-run unless --apply."""

from __future__ import annotations

import argparse
import json

from ..common import console
from . import urls
from .urls import MapsError


def _print_built(payload: dict) -> None:
    console.print(payload["url"])
    console.print(f"[dim]{payload['mode']} · {payload['host']}[/dim]")


def cmd_search(args: argparse.Namespace) -> int:
    try:
        payload = urls.build_search_url(
            args.query,
            latitude=args.lat,
            longitude=args.lon,
            zoom=args.zoom,
            map_type=args.type,
        )
    except MapsError as exc:
        console.print(f"[red]Maps error:[/red] {exc}")
        return 2
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0
    _print_built(payload)
    return 0


def cmd_open(args: argparse.Namespace) -> int:
    has_query = bool(args.query)
    has_dest = bool(args.daddr)
    if has_query == has_dest:
        console.print("[red]Provide either --query or --daddr, not both.[/red]")
        return 2
    try:
        if has_query:
            payload = urls.build_search_url(
                args.query,
                latitude=args.lat,
                longitude=args.lon,
                zoom=args.zoom,
                map_type=args.type,
            )
        else:
            if args.lat is not None or args.lon is not None:
                console.print("[red]--lat/--lon apply only to --query search.[/red]")
                return 2
            payload = urls.build_directions_url(
                daddr=args.daddr,
                saddr=args.saddr,
                dirflg=args.dirflg,
                zoom=args.zoom,
                map_type=args.type,
            )
    except MapsError as exc:
        console.print(f"[red]Maps error:[/red] {exc}")
        return 2
    console.rule("Open Maps" if args.apply else "Open Maps (dry-run)")
    _print_built(payload)
    if not args.apply:
        console.print("[yellow]Dry-run.[/yellow] Add --apply to open in Maps.app.")
        return 0
    try:
        urls.open_maps_url(payload["url"])
    except MapsError as exc:
        console.print(f"[red]Maps error:[/red] {exc}")
        return 2
    console.print(f"[green]Opened[/green] {payload['url']}")
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    sp = sub.add_parser(
        "search",
        help="Build a maps.apple.com search URL (does not open Maps.app).",
    )
    sp.add_argument("--query", required=True)
    sp.add_argument("--lat", type=float)
    sp.add_argument("--lon", type=float)
    sp.add_argument("--zoom", type=int)
    sp.add_argument("--type", choices=sorted(urls.ALLOWED_MAP_TYPES))
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser(
        "open",
        help="Open a frozen maps.apple.com URL. Requires --apply.",
    )
    sp.add_argument("--query", help="Search query.")
    sp.add_argument("--lat", type=float, help="Optional pin latitude (search only).")
    sp.add_argument("--lon", type=float, help="Optional pin longitude (search only).")
    sp.add_argument("--saddr", help="Directions start address.")
    sp.add_argument("--daddr", help="Directions destination.")
    sp.add_argument("--dirflg", choices=sorted(urls.ALLOWED_DIRFLG))
    sp.add_argument("--zoom", type=int)
    sp.add_argument("--type", choices=sorted(urls.ALLOWED_MAP_TYPES))
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_open)


__all__ = ["register"]
