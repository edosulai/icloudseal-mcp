"""CLI entry point for mail-agent.

All Phase 1 commands are read-only. Designed to be invoked by Cascade
during chat sessions to gather context, then by the user once a plan is
approved.

Output: Rich tables for human-readable; pass --json for machine-readable
output that the agent can paste back into context.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone

from rich.console import Console
from rich.table import Table

from . import auth, cache
from .imap_client import open_session

console = Console()


# ---- helpers -----------------------------------------------------------


def _parse_since(value: str | None) -> str | None:
    """Convert "7d" / "24h" / "2025-01-01" to IMAP date format (DD-Mon-YYYY)."""
    if not value:
        return None
    m = re.fullmatch(r"(\d+)([dh])", value)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        delta = timedelta(days=n) if unit == "d" else timedelta(hours=n)
        dt = datetime.now(timezone.utc) - delta
    else:
        try:
            dt = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise SystemExit(f"Invalid --since value: {value!r}") from exc
    # IMAP wants "DD-Mon-YYYY" e.g. "01-Jan-2025"
    return dt.strftime("%d-%b-%Y")


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}B"
        n /= 1024
    return f"{n:.1f}TB"


# ---- commands ----------------------------------------------------------


def cmd_setup(args: argparse.Namespace) -> int:
    auth.prompt_and_store(args.email)
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    creds = auth.load_credentials()
    with open_session(creds.email, creds.password) as imap:
        folders = imap.list_folders()
        rows = []
        for f in folders:
            # Skip non-selectable parent folders
            if r"\Noselect" in f.flags:
                continue
            try:
                count = imap.folder_count(f.name)
            except Exception:  # noqa: BLE001
                count = -1
            rows.append((f.name, count, ", ".join(f.flags)))

    if args.json:
        print(json.dumps(
            [{"folder": n, "count": c, "flags": fl} for n, c, fl in rows],
            indent=2,
        ))
        return 0

    table = Table(title=f"iCloud Mail folders for {creds.email}")
    table.add_column("Folder")
    table.add_column("Messages", justify="right")
    table.add_column("Flags", style="dim")
    for name, count, flags in rows:
        table.add_row(name, str(count) if count >= 0 else "?", flags)
    console.print(table)
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    creds = auth.load_credentials()
    folder = args.folder
    since = _parse_since(args.since)
    criteria = f'SINCE {since}' if since else "ALL"

    with open_session(creds.email, creds.password) as imap:
        total = imap.select(folder, readonly=True)
        console.print(f"[dim]Selected {folder!r} (server reports {total} messages)[/dim]")
        uids = imap.search_uids(criteria)
        console.print(f"Found {len(uids)} message(s) matching {criteria!r}")
        if not uids:
            return 0

        with cache.connect() as conn:
            written = 0
            buffer: list = []
            for meta in imap.fetch_metadata(uids):
                buffer.append(meta)
                if len(buffer) >= 100:
                    written += cache.upsert_messages(conn, buffer)
                    buffer.clear()
                    console.print(f"[dim]  ...cached {written}[/dim]")
            if buffer:
                written += cache.upsert_messages(conn, buffer)
            cache.update_sync_state(conn, folder, max(uids), total)
            console.print(f"[green]Synced {written} message(s) into local cache.[/green]")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    with cache.connect() as conn:
        rows = cache.list_messages(conn, args.folder, limit=args.limit)

    if args.json:
        print(json.dumps([dict(r) for r in rows], indent=2, default=str))
        return 0

    table = Table(title=f"{args.folder} (latest {len(rows)})")
    table.add_column("UID", justify="right", style="dim")
    table.add_column("Date", style="cyan")
    table.add_column("From")
    table.add_column("Subject")
    table.add_column("Size", justify="right", style="dim")
    for r in rows:
        date_short = (r["date_iso"] or "")[:16].replace("T", " ")
        sender = r["sender_name"] or r["sender_email"]
        table.add_row(
            str(r["uid"]),
            date_short,
            sender[:30],
            (r["subject"] or "")[:60],
            _human_size(r["size"] or 0),
        )
    console.print(table)
    return 0


def cmd_senders(args: argparse.Namespace) -> int:
    with cache.connect() as conn:
        rows = cache.top_senders(conn, args.folder, limit=args.top)

    if args.json:
        print(json.dumps([dict(r) for r in rows], indent=2, default=str))
        return 0

    title = "Top senders" + (f" in {args.folder}" if args.folder else " (all folders)")
    table = Table(title=title)
    table.add_column("#", justify="right", style="dim")
    table.add_column("Count", justify="right")
    table.add_column("Total size", justify="right", style="dim")
    table.add_column("Sender")
    table.add_column("Latest", style="cyan")
    for i, r in enumerate(rows, 1):
        sender = r["sender_name"] or r["sender_email"]
        table.add_row(
            str(i),
            str(r["n"]),
            _human_size(r["total_bytes"] or 0),
            f"{sender} <{r['sender_email']}>"[:60],
            (r["latest"] or "")[:10],
        )
    console.print(table)
    return 0


def cmd_peek(args: argparse.Namespace) -> int:
    creds = auth.load_credentials()
    with open_session(creds.email, creds.password) as imap:
        imap.select(args.folder, readonly=True)
        msg = imap.fetch_body(args.uid)

    if args.json:
        # Extract plain-text body if present
        body_text = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        body_text = payload.decode(
                            part.get_content_charset() or "utf-8",
                            errors="replace",
                        )
                        break
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                body_text = payload.decode(
                    msg.get_content_charset() or "utf-8", errors="replace"
                )
        out = {
            "from": msg.get("From"),
            "to": msg.get("To"),
            "subject": msg.get("Subject"),
            "date": msg.get("Date"),
            "body": body_text[: args.max_body_chars],
            "truncated": len(body_text) > args.max_body_chars,
        }
        print(json.dumps(out, indent=2, default=str))
        return 0

    console.rule(f"UID {args.uid} in {args.folder}")
    console.print(f"[bold]From:[/bold]    {msg.get('From')}")
    console.print(f"[bold]To:[/bold]      {msg.get('To')}")
    console.print(f"[bold]Date:[/bold]    {msg.get('Date')}")
    console.print(f"[bold]Subject:[/bold] {msg.get('Subject')}")
    console.rule()
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    text = payload.decode(
                        part.get_content_charset() or "utf-8", errors="replace"
                    )
                    console.print(text[: args.max_body_chars])
                    if len(text) > args.max_body_chars:
                        console.print("[dim]... (truncated)[/dim]")
                    break
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            text = payload.decode(
                msg.get_content_charset() or "utf-8", errors="replace"
            )
            console.print(text[: args.max_body_chars])
    return 0


# ---- argparse setup ----------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mail-agent",
        description="CRUD layer for iCloud Mail (Phase 1: read-only).",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("setup", help="Store iCloud app-specific password in Keychain.")
    sp.add_argument("--email", required=True, help="Your iCloud email address")
    sp.set_defaults(func=cmd_setup)

    sp = sub.add_parser("stats", help="List folders + message counts (live IMAP).")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_stats)

    sp = sub.add_parser("sync", help="Pull metadata from a folder into local cache.")
    sp.add_argument("--folder", default="INBOX")
    sp.add_argument("--since", help="e.g. '7d', '24h', or '2025-01-01'")
    sp.set_defaults(func=cmd_sync)

    sp = sub.add_parser("list", help="List cached messages from local DB.")
    sp.add_argument("--folder", default="INBOX")
    sp.add_argument("--limit", type=int, default=50)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("senders", help="Top senders by message count.")
    sp.add_argument("--folder", default=None, help="Limit to a folder (default: all cached)")
    sp.add_argument("--top", type=int, default=30)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_senders)

    sp = sub.add_parser("peek", help="Fetch and show the body of one message.")
    sp.add_argument("uid", type=int)
    sp.add_argument("--folder", default="INBOX")
    sp.add_argument("--max-body-chars", type=int, default=4000)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_peek)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except auth.AuthError as e:
        console.print(f"[red]Auth error:[/red] {e}")
        return 2
    except KeyboardInterrupt:
        console.print("[yellow]Interrupted.[/yellow]")
        return 130


if __name__ == "__main__":
    sys.exit(main())
