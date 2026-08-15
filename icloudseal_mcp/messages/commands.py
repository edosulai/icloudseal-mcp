"""iCloud Messages (iMessage/SMS) commands.

Reads are sourced from the local ``chat.db`` (needs Full Disk Access). Sending
has no API; it is driven through Messages.app via AppleScript and is gated
behind ``--apply``. SMS (green-bubble) sending only works if this Mac has Text
Message Forwarding enabled with an iPhone.
"""

from __future__ import annotations

import argparse
import json
import subprocess

from rich.table import Table

from ..common import console
from . import chatdb
from .chatdb import MessagesAccessError


def _guard(fn):
    try:
        return fn()
    except MessagesAccessError as exc:
        console.print(f"[red]Messages access error:[/red] {exc}")
        return None


def cmd_chats(args: argparse.Namespace) -> int:
    chats = _guard(lambda: chatdb.list_chats(limit=args.limit))
    if chats is None:
        return 2
    if args.json:
        print(json.dumps([c.__dict__ for c in chats], indent=2))
        return 0
    table = Table(title=f"Recent conversations ({len(chats)})")
    table.add_column("Last", style="cyan")
    table.add_column("Chat")
    table.add_column("Name")
    table.add_column("Msgs", justify="right", style="dim")
    for c in chats:
        table.add_row(c.last_iso[:16].replace("T", " "), c.identifier[:30], c.display_name[:24],
                      str(c.count))
    console.print(table)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    msgs = _guard(lambda: chatdb.chat_messages(args.chat, limit=args.limit))
    if msgs is None:
        return 2
    if args.json:
        print(json.dumps([m.__dict__ for m in msgs], indent=2))
        return 0
    console.rule(f"Messages with {args.chat} ({len(msgs)})")
    for m in msgs:
        who = "[green]me[/green]" if m.from_me else f"[cyan]{m.handle or 'them'}[/cyan]"
        ts = m.date_iso[:16].replace("T", " ")
        console.print(f"[dim]{ts}[/dim] {who}: {m.text or '[no text/attachment]'}")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    msgs = _guard(lambda: chatdb.search(args.query, limit=args.limit))
    if msgs is None:
        return 2
    if args.json:
        print(json.dumps([m.__dict__ for m in msgs], indent=2))
        return 0
    table = Table(title=f"Messages matching {args.query!r} ({len(msgs)})")
    table.add_column("Date", style="cyan")
    table.add_column("From")
    table.add_column("Text")
    for m in msgs:
        who = "me" if m.from_me else (m.handle or "them")
        table.add_row(m.date_iso[:16].replace("T", " "), who, (m.text or "")[:60])
    console.print(table)
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    msgs = _guard(lambda: chatdb.chat_messages(args.chat, limit=args.limit))
    if msgs is None:
        return 2
    from pathlib import Path

    out = Path(args.path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([m.__dict__ for m in msgs], indent=2) + "\n")
    console.print(f"[green]Exported {len(msgs)} message(s) to[/green] {out}")
    return 0


def _applescript_send(
    to: str,
    text: str,
    *,
    service: str,
    attachment: str | None = None,
) -> None:
    if attachment:
        script = """on run argv
    set recipient to item 1 of argv
    set messageText to item 2 of argv
    set requestedService to item 3 of argv
    set filePath to item 4 of argv
    tell application "Messages"
        if requestedService is "imessage" then
            set targetService to 1st account whose service type = iMessage
        else
            set targetService to 1st account whose service type = SMS
        end if
        set targetBuddy to participant recipient of targetService
        if messageText is not "" then send messageText to targetBuddy
        send (POSIX file filePath) to targetBuddy
    end tell
end run"""
        args = [to, text, service, attachment]
    else:
        script = """on run argv
    set recipient to item 1 of argv
    set messageText to item 2 of argv
    set requestedService to item 3 of argv
    tell application "Messages"
        if requestedService is "imessage" then
            set targetService to 1st account whose service type = iMessage
        else
            set targetService to 1st account whose service type = SMS
        end if
        set targetBuddy to participant recipient of targetService
        send messageText to targetBuddy
    end tell
end run"""
        args = [to, text, service]
    result = subprocess.run(
        ["osascript", "-e", script, "--", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "osascript failed")


def cmd_send(args: argparse.Namespace) -> int:
    attach = getattr(args, "attachment", None)
    extra = f"\n  file: {attach}" if attach else ""
    console.print(
        f"Would send via [bold]{args.service}[/bold] to [bold]{args.to}[/bold]:\n"
        f"  {args.text}{extra}"
    )
    if not args.apply:
        console.print("[yellow]Dry-run.[/yellow] Add --apply to actually send.")
        return 0
    try:
        _applescript_send(args.to, args.text, service=args.service, attachment=attach)
    except RuntimeError as exc:
        console.print(f"[red]Send failed:[/red] {exc}")
        return 2
    console.print(f"[green]Sent to {args.to}.[/green]")
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    sp = sub.add_parser("chats", help="List recent conversations (reads chat.db).")
    sp.add_argument("--limit", type=int, default=30)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_chats)

    sp = sub.add_parser("list", help="Show messages in a conversation.")
    sp.add_argument("chat", help="Chat identifier (phone/email) or display name fragment")
    sp.add_argument("--limit", type=int, default=40)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("search", help="Search message text.")
    sp.add_argument("query")
    sp.add_argument("--limit", type=int, default=40)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("export", help="Export a conversation to JSON.")
    sp.add_argument("chat")
    sp.add_argument("path")
    sp.add_argument("--limit", type=int, default=1000)
    sp.set_defaults(func=cmd_export)

    sp = sub.add_parser("send", help="Send a message via Messages.app. Requires --apply.")
    sp.add_argument("--to", required=True, help="Phone number or email")
    sp.add_argument("--text", required=True)
    sp.add_argument("--file", dest="attachment", help="Optional local file to attach")
    sp.add_argument("--service", choices=["imessage", "sms"], default="imessage")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_send)


__all__ = ["register"]
