"""Ops commands. Generate a mail-cleanup LaunchAgent; never load it."""

from __future__ import annotations

import argparse

from ..common import console
from ..mcp import services


def cmd_cleanup_agent(args: argparse.Namespace) -> int:
    try:
        frozen = services.prepare_ops_cleanup_agent(interval=args.interval)
    except services.ServiceError as exc:
        console.print(f"[red]Ops error:[/red] {exc}")
        return 2
    console.rule("Mail cleanup LaunchAgent" if args.apply else "Mail cleanup LaunchAgent (dry-run)")
    console.print(f"Dest: {frozen['destination']}")
    console.print(f"Interval: {frozen['interval']}s")
    console.print(f"Label: {frozen['label']}")
    if not args.apply:
        console.print("[yellow]Dry-run.[/yellow] Add --apply to write the plist.")
        return 0
    try:
        result = services.exec_ops_cleanup_agent(frozen)
    except services.ServiceError as exc:
        console.print(f"[red]Ops error:[/red] {exc}")
        return 2
    console.print(f"[green]Wrote[/green] {result['path']}")
    console.print("[dim]launchctl load was not run.[/dim]")
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    sp = sub.add_parser(
        "cleanup-agent",
        help="Write a mail-cleanup LaunchAgent plist. Does not launchctl load.",
    )
    sp.add_argument(
        "--interval",
        type=int,
        default=86_400,
        help="StartInterval seconds (minimum 3600).",
    )
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_cleanup_agent)


__all__ = ["register"]
