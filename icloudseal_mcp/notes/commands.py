"""iCloud Notes commands (AppleScript-backed).

Read commands list/search/read notes. Mutating commands (create/update/delete)
preview then require ``--apply``; update/delete back up the note body first.
"""

from __future__ import annotations

import argparse
import json

from rich.table import Table

from ..common import console
from ..paths import BACKUP_DIR, timestamp_slug
from . import applescript
from .applescript import Note, NotesError


def _guard(fn):
    try:
        return fn()
    except NotesError as exc:
        console.print(f"[red]Notes error:[/red] {exc}")
        console.print("[dim]Grant Automation access to control Notes.app if prompted.[/dim]")
        return None


def _match(notes: list[Note], query: str) -> list[Note]:
    q = query.lower()
    return [n for n in notes if q in n.name.lower() or n.id == query]


def cmd_list(args: argparse.Namespace) -> int:
    notes = _guard(applescript.list_notes)
    if notes is None:
        return 2
    if args.limit:
        notes = notes[: args.limit]
    if args.json:
        print(json.dumps([n.__dict__ for n in notes], indent=2))
        return 0
    table = Table(title=f"iCloud Notes ({len(notes)})")
    table.add_column("Name")
    table.add_column("Modified", style="dim")
    for n in notes:
        table.add_row(n.name[:50], n.modified[:24])
    console.print(table)
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    notes = _guard(applescript.list_notes)
    if notes is None:
        return 2
    hits = _match(notes, args.query)
    if args.json:
        print(json.dumps([n.__dict__ for n in hits], indent=2))
        return 0
    table = Table(title=f"Notes matching {args.query!r} ({len(hits)})")
    table.add_column("Name")
    table.add_column("Modified", style="dim")
    for n in hits:
        table.add_row(n.name[:50], n.modified[:24])
    console.print(table)
    return 0


def _resolve_one(query: str) -> Note | None:
    notes = applescript.list_notes()
    hits = _match(notes, query)
    if not hits:
        console.print(f"[yellow]No note matches {query!r}.[/yellow]")
        return None
    if len(hits) > 1:
        console.print(f"[yellow]{len(hits)} notes match {query!r}; be more specific.[/yellow]")
        return None
    return hits[0]


def cmd_read(args: argparse.Namespace) -> int:
    note = _guard(lambda: _resolve_one(args.query))
    if not note:
        return 2 if note is None else 0
    body = applescript.read_note(note.id)
    console.rule(note.name)
    console.print(body)
    return 0


def cmd_accounts(args: argparse.Namespace) -> int:
    accounts = _guard(applescript.list_accounts)
    if accounts is None:
        return 2
    if args.json:
        print(json.dumps(accounts, indent=2))
        return 0
    table = Table(title=f"Notes accounts ({len(accounts)})")
    table.add_column("Account")
    for name in accounts:
        table.add_row(name)
    console.print(table)
    return 0


def cmd_folders(args: argparse.Namespace) -> int:
    folders = _guard(applescript.list_folders)
    if folders is None:
        return 2
    if args.json:
        print(json.dumps([folder.__dict__ for folder in folders], indent=2))
        return 0
    table = Table(title=f"Notes folders ({len(folders)})")
    table.add_column("Account")
    table.add_column("Folder")
    for folder in folders:
        table.add_row(folder.account, folder.name)
    console.print(table)
    return 0


def cmd_create(args: argparse.Namespace) -> int:
    console.rule("New note (dry-run)")
    console.print(f"[bold]{args.title}[/bold]")
    console.print(f"Account: {args.account or applescript.DEFAULT_ACCOUNT}")
    if args.folder:
        console.print(f"Folder: {args.folder}")
    if args.body:
        console.print(args.body)
    if not args.apply:
        console.print("[yellow]Dry-run.[/yellow] Add --apply to create.")
        return 0
    if _guard(
        lambda: applescript.create_note(
            args.title,
            args.body or "",
            folder=args.folder,
            account=args.account,
        )
    ) is None:
        return 2
    console.print(f"[green]Created note[/green] {args.title}")
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    note = _guard(lambda: _resolve_one(args.query))
    if not note:
        return 2 if note is None else 0
    console.print(f"Would delete note: [bold]{note.name}[/bold]")
    if not args.apply:
        console.print("[yellow]Dry-run.[/yellow] Add --apply to delete (body backed up first).")
        return 0
    body = applescript.read_note(note.id)
    root = BACKUP_DIR / f"notes-delete-{timestamp_slug()}"
    root.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() else "_" for c in note.name)[:60] or "note"
    (root / f"{safe}.txt").write_text(f"{note.name}\n\n{body}")
    applescript.delete_note(note.id)
    console.print(f"[green]Deleted note[/green] {note.name}. Backup: {root}")
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    if args.title is None and args.body is None:
        raise SystemExit("Provide at least one of --title or --body.")
    note = _guard(lambda: _resolve_one(args.query))
    if not note:
        return 2 if note is None else 0
    console.rule("Update note" if args.apply else "Update note (dry-run)")
    console.print(f"Current: [bold]{note.name}[/bold]")
    if args.title is not None:
        console.print(f"New title: {args.title}")
    if args.body is not None:
        console.print(f"New body chars: {len(args.body)}")
    if not args.apply:
        console.print("[yellow]Dry-run.[/yellow] Add --apply to update (body backed up first).")
        return 0
    body = applescript.read_note(note.id)
    root = BACKUP_DIR / f"notes-update-{timestamp_slug()}"
    root.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() else "_" for c in note.name)[:60] or "note"
    (root / f"{safe}.txt").write_text(f"{note.name}\n\n{body}")
    if _guard(lambda: applescript.update_note(note.id, title=args.title, body=args.body)) is None:
        return 2
    console.print(f"[green]Updated note[/green] {note.name}. Backup: {root}")
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    sp = sub.add_parser("list", help="List notes.")
    sp.add_argument("--limit", type=int, default=0)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("search", help="Search notes by title.")
    sp.add_argument("query")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("read", help="Print a note's body.")
    sp.add_argument("query", help="Note title fragment or id")
    sp.set_defaults(func=cmd_read)

    sp = sub.add_parser("accounts", help="List Notes.app accounts.")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_accounts)

    sp = sub.add_parser("folders", help="List Notes.app folders.")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_folders)

    sp = sub.add_parser("create", help="Create a note. Requires --apply.")
    sp.add_argument("--title", required=True)
    sp.add_argument("--body")
    sp.add_argument("--account", help="Notes.app account name (default: iCloud).")
    sp.add_argument("--folder", help="Folder name inside the chosen account.")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_create)

    sp = sub.add_parser("delete", help="Delete a note. Requires --apply.")
    sp.add_argument("query", help="Note title fragment or id")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_delete)

    sp = sub.add_parser("update", help="Update a note title and/or body. Requires --apply.")
    sp.add_argument("query", help="Note title fragment or id")
    sp.add_argument("--title")
    sp.add_argument("--body")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_update)


__all__ = ["register"]
