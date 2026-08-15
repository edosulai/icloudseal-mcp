"""Shortcuts.app commands (CLI-backed).

Reads list installed shortcuts. Running one is dry-run unless ``--apply``.
"""

from __future__ import annotations

import argparse
import json

from rich.table import Table

from ..common import console
from . import runner
from .runner import ShortcutsError


def _guard(fn):
    try:
        return fn()
    except ShortcutsError as exc:
        console.print(f"[red]Shortcuts error:[/red] {exc}")
        return None


def cmd_list(args: argparse.Namespace) -> int:
    names = _guard(lambda: runner.list_shortcuts(limit=args.limit))
    if names is None:
        return 2
    if args.json:
        print(json.dumps({"count": len(names), "shortcuts": names}, indent=2))
        return 0
    if not names:
        console.print("[dim]No Shortcuts installed.[/dim]")
        return 0
    table = Table(title=f"Shortcuts ({len(names)})")
    table.add_column("Name")
    for name in names:
        table.add_row(name)
    console.print(table)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    try:
        name = runner.require_named(args.name)
    except ShortcutsError as exc:
        console.print(f"[red]Shortcuts error:[/red] {exc}")
        return 2
    try:
        input_text = runner.validate_input(args.input) if args.input is not None else None
    except ShortcutsError as exc:
        console.print(f"[red]Shortcuts error:[/red] {exc}")
        return 2
    console.rule("Shortcuts run" if args.apply else "Shortcuts run (dry-run)")
    console.print(f"Name: {name}")
    if input_text is not None:
        console.print(f"Input: {input_text}")
    if not args.apply:
        console.print("[yellow]Dry-run.[/yellow] Add --apply to run this shortcut.")
        return 0
    if _guard(lambda: runner.run_shortcut(name, input_text=input_text)) is None:
        return 2
    console.print(f"[green]Ran[/green] {name}")
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    sp = sub.add_parser("list", help="List installed Shortcuts (does not run any).")
    sp.add_argument("--limit", type=int, default=100)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("run", help="Run one Shortcut by exact name. Requires --apply.")
    sp.add_argument("name")
    sp.add_argument("--input", help="Optional frozen text input (no files or stdin blobs).")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_run)


__all__ = ["register"]
