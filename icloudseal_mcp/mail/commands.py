"""iCloud Mail (IMAP + SMTP) commands.

Read commands gather context; mutating commands go through a dry-run plan and a
gated ``--apply``. Deletes/moves back up every message as ``.eml`` first.
Outbound send uses SMTP and never invents a From address.

``register(subparsers)`` mounts these as subcommands so both the top-level
``icloudseal-mcp mail ...`` CLI and the legacy ``mail-agent ...`` CLI can reuse
the exact same command set.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from .. import auth
from ..common import (
    console,
    human_size,
    parse_age_cutoff,
    parse_since,
    write_json_file,
)
from ..paths import default_plan_path, now_utc_iso
from . import cache, cleanup, jobs
from .imap_client import open_session
from .smtp_client import SMTPError, freeze_send, send_frozen

# ---- helpers -----------------------------------------------------------


def _load_plan(path: Path) -> dict:
    try:
        plan = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Cannot read plan file {path}: {exc}") from exc
    if plan.get("version") != 1:
        raise SystemExit("Unsupported plan version.")
    if plan.get("action") not in {"move", "delete"}:
        raise SystemExit("Plan action must be 'move' or 'delete'.")
    if not isinstance(plan.get("messages"), list):
        raise SystemExit("Plan is missing a messages list.")
    return plan


def _backup_message(imap, *, folder: str, uid: int, backup_root: Path) -> Path:
    msg = imap.fetch_body(uid)
    folder_slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", folder).strip("_") or "folder"
    out = backup_root / f"{folder_slug}-{uid}.eml"
    out.write_bytes(msg.as_bytes())
    return out


def _sync_folder_metadata(*, folder: str, since: str | None = None) -> int:
    creds = auth.load_credentials()
    criteria = f"SINCE {since}" if since else "ALL"

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
            return written


# ---- commands ----------------------------------------------------------


def cmd_setup(args: argparse.Namespace) -> int:
    auth.prompt_and_store(args.email)
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    from rich.table import Table

    creds = auth.load_credentials()
    with open_session(creds.email, creds.password) as imap:
        folders = imap.list_folders()
        rows = []
        for f in folders:
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
    _sync_folder_metadata(folder=args.folder, since=parse_since(args.since))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    from rich.table import Table

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
            human_size(r["size"] or 0),
        )
    console.print(table)
    return 0


def cmd_senders(args: argparse.Namespace) -> int:
    from rich.table import Table

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
            human_size(r["total_bytes"] or 0),
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

    body_text = _plain_body(msg)
    if args.json:
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
    console.print(body_text[: args.max_body_chars])
    if len(body_text) > args.max_body_chars:
        console.print("[dim]... (truncated)[/dim]")
    return 0


def _plain_body(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode(
                        part.get_content_charset() or "utf-8", errors="replace"
                    )
        return ""
    payload = msg.get_payload(decode=True)
    if payload:
        return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
    return ""


def cmd_triage(args: argparse.Namespace) -> int:
    from rich.table import Table

    older_than_iso = parse_age_cutoff(args.older_than)
    with cache.connect() as conn:
        rows = cache.find_messages(
            conn,
            folder=args.folder,
            sender=args.sender,
            sender_like=args.sender_like,
            subject_like=args.subject_like,
            older_than_iso=older_than_iso,
            has_list_unsubscribe=args.has_list_unsubscribe,
            limit=args.limit,
        )

    action = "delete" if args.delete else "move"
    plan = {
        "version": 1,
        "created_at": now_utc_iso(),
        "source": "icloudseal-mcp mail triage",
        "folder": args.folder,
        "action": action,
        "destination": args.move_to if action == "move" else None,
        "filters": {
            "sender": args.sender,
            "sender_like": args.sender_like,
            "subject_like": args.subject_like,
            "older_than": args.older_than,
            "older_than_iso": older_than_iso,
            "has_list_unsubscribe": args.has_list_unsubscribe,
        },
        "messages": [dict(r) for r in rows],
    }

    if args.plan_file:
        write_json_file(Path(args.plan_file), plan)
        console.print(f"[green]Wrote dry-run plan:[/green] {args.plan_file}")

    if args.json:
        print(json.dumps(plan, indent=2, default=str))
        return 0

    title = f"Dry-run triage plan: {action}"
    if action == "move":
        title += f" to {args.move_to}"
    table = Table(title=title)
    table.add_column("UID", justify="right", style="dim")
    table.add_column("Date", style="cyan")
    table.add_column("From")
    table.add_column("Subject")
    for r in rows:
        sender = r["sender_name"] or r["sender_email"]
        table.add_row(
            str(r["uid"]),
            (r["date_iso"] or "")[:10],
            sender[:35],
            (r["subject"] or "")[:70],
        )
    console.print(table)
    console.print(
        f"[yellow]Dry-run only.[/yellow] Review {len(rows)} message(s), then run "
        "`icloudseal-mcp mail apply <plan.json> --apply` to mutate iCloud Mail."
    )
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    plan_path = Path(args.plan_file)
    plan = _load_plan(plan_path)
    messages = plan["messages"]
    if not messages:
        console.print("[yellow]Plan has no messages. Nothing to do.[/yellow]")
        return 0

    folder = plan["folder"]
    action = plan["action"]
    dest = plan.get("destination")
    uids = [int(m["uid"]) for m in messages]

    if not args.apply:
        console.print(
            f"[yellow]Dry-run.[/yellow] Would {action} {len(uids)} message(s) "
            f"from {folder!r}. Add --apply to execute."
        )
        return 0

    if action == "move" and not dest:
        raise SystemExit("Move plan is missing destination.")

    creds = auth.load_credentials()

    with open_session(creds.email, creds.password) as imap:
        imap.select(folder, readonly=False)
        if action == "move":
            imap.move(uids, dest)
        else:
            # "delete" now means move to Trash (Deleted Messages) — no local
            # .eml backup, no permanent expunge. iCloud keeps messages in Trash
            # for ~30 days before auto-purging.
            imap.move(uids, "Deleted Messages")

    with cache.connect() as conn:
        removed = cache.remove_cached_messages(conn, folder=folder, uids=uids)

    if action == "move":
        console.print(
            f"[green]Moved {len(uids)} message(s) to {dest!r}.[/green] "
            f"Cache rows removed: {removed}."
        )
    else:
        console.print(
            f"[green]Moved {len(uids)} message(s) to Trash (Deleted Messages).[/green] "
            f"No local backup created. Cache rows removed: {removed}."
        )
    return 0


def cmd_cleanup_strict(args: argparse.Namespace) -> int:
    from rich.table import Table

    if args.sync:
        _sync_folder_metadata(folder=args.folder, since=None)

    with cache.connect() as conn:
        messages = [dict(r) for r in cleanup.strict_bulk_messages(conn, folder=args.folder)]
        summaries = cleanup.strict_sender_summary(conn, folder=args.folder)

    plan = {
        "version": 1,
        "created_at": now_utc_iso(),
        "source": "icloudseal-mcp mail cleanup strict",
        "folder": args.folder,
        "action": "move",
        "destination": "Deleted Messages",
        "filters": {
            "sender_emails": sorted(cleanup.STRICT_BULK_SENDERS),
            "policy": cleanup.STRICT_POLICY_NOTE,
        },
        "messages": messages,
    }

    plan_path = (
        Path(args.plan_file)
        if args.plan_file
        else default_plan_path("trash-strict-bulk-inbox")
    )
    write_json_file(plan_path, plan)

    table = Table(title=f"Strict cleanup plan for {args.folder}")
    table.add_column("Count", justify="right")
    table.add_column("Latest", style="cyan")
    table.add_column("Sender")
    for summary in summaries:
        sender = summary.sender_name or summary.sender_email
        table.add_row(
            str(summary.count),
            (summary.latest or "")[:10],
            f"{sender} <{summary.sender_email}>"[:74],
        )
    console.print(table)
    console.print(f"[green]Wrote plan:[/green] {plan_path}")

    if not args.apply:
        console.print(
            f"[yellow]Dry-run.[/yellow] Would move {len(messages)} strict bulk message(s) "
            "to Trash (Deleted Messages). Add --apply to execute. No local backup is made "
            "— iCloud Trash retains messages for ~30 days before auto-purge."
        )
        return 0

    apply_args = argparse.Namespace(plan_file=str(plan_path), apply=True)
    return cmd_apply(apply_args)


def cmd_jobs_collect(args: argparse.Namespace) -> int:
    from rich.table import Table

    creds = auth.load_credentials()
    since = parse_since(args.since)
    criteria = f"SINCE {since}" if since else "ALL"
    leads = []
    scanned = 0

    with open_session(creds.email, creds.password) as imap:
        total = imap.select(args.folder, readonly=True)
        console.print(f"[dim]Selected {args.folder!r} (server reports {total} messages)[/dim]")
        uids = imap.search_uids(criteria)
        if args.limit:
            uids = uids[-args.limit :]
        console.print(f"Scanning {len(uids)} message(s) matching {criteria!r}")
        metas = list(imap.fetch_metadata(uids))
        for meta in metas:
            if not jobs.is_job_alert(meta.sender_email, meta.sender_name, meta.subject):
                continue
            scanned += 1
            msg = imap.fetch_body(meta.uid)
            leads.extend(
                lead.to_dict()
                for lead in jobs.extract_job_leads(
                    uid=meta.uid,
                    sender_email=meta.sender_email,
                    sender_name=meta.sender_name,
                    subject=meta.subject,
                    msg=msg,
                )
                if lead.score >= args.min_score
            )

    leads.sort(key=lambda item: item["score"], reverse=True)
    plan = {
        "version": 1,
        "kind": "job-leads",
        "created_at": now_utc_iso(),
        "source": "icloudseal-mcp mail jobs collect",
        "folder": args.folder,
        "filters": {
            "since": args.since,
            "criteria": criteria,
            "limit": args.limit,
            "min_score": args.min_score,
        },
        "messages_scanned": len(uids),
        "job_alert_messages": scanned,
        "leads": leads,
        "approval_gate": {
            "auto_apply": False,
            "note": (
                "Review leads before opening/applying. "
                "This command never submits applications."
            ),
        },
    }

    if args.out:
        write_json_file(Path(args.out), plan)
        console.print(f"[green]Wrote job lead plan:[/green] {args.out}")

    if args.json:
        print(json.dumps(plan, indent=2, default=str))
        return 0

    table = Table(title=f"Job leads from {scanned} job-alert message(s)")
    table.add_column("Score", justify="right")
    table.add_column("Provider")
    table.add_column("Title")
    table.add_column("Company")
    table.add_column("Location")
    for lead in leads[: args.top]:
        table.add_row(
            str(lead["score"]),
            lead["provider"],
            lead["title"][:45],
            lead["company"][:28],
            lead["location"][:24],
        )
    console.print(table)
    console.print("[yellow]Review only.[/yellow] No applications were submitted.")
    return 0


def _parse_uids(raw: str) -> list[int]:
    uids: list[int] = []
    seen: set[int] = set()
    for part in raw.split(","):
        token = part.strip()
        if not token:
            continue
        try:
            uid = int(token)
        except ValueError as exc:
            raise SystemExit(f"Invalid mail UID: {token!r}") from exc
        if uid <= 0 or uid in seen:
            raise SystemExit("Mail UIDs must be positive and unique.")
        seen.add(uid)
        uids.append(uid)
    if not uids:
        raise SystemExit("Provide at least one mail UID.")
    if len(uids) > 500:
        raise SystemExit("Mail mutations are limited to 500 messages.")
    return uids


def _cached_mail_targets(*, folder: str, uids: list[int]) -> list[dict]:
    with cache.connect() as conn:
        rows = {
            int(row["uid"]): dict(row)
            for row in cache.get_messages(conn, folder=folder, uids=uids)
        }
    missing = [uid for uid in uids if uid not in rows]
    if missing:
        raise SystemExit(
            f"UIDs not in the {folder!r} cache: {missing[:20]}. Sync the folder first."
        )
    return [rows[uid] for uid in uids]


def _print_mail_targets(messages: list[dict]) -> None:
    for message in messages:
        subject = (message.get("subject") or "(no subject)")[:60]
        sender = message.get("sender_email") or ""
        console.print(f"  UID {message['uid']}: {subject}  <{sender}>")


def cmd_mark_read(args: argparse.Namespace) -> int:
    return _cmd_flags(args, seen=True)


def cmd_mark_unread(args: argparse.Namespace) -> int:
    return _cmd_flags(args, seen=False)


def _cmd_flags(args: argparse.Namespace, *, seen: bool) -> int:
    uids = _parse_uids(args.uids)
    messages = _cached_mail_targets(folder=args.folder, uids=uids)
    label = "read" if seen else "unread"
    console.rule(f"Mark mail {label}" if args.apply else f"Mark mail {label} (dry-run)")
    console.print(f"Folder: {args.folder}")
    _print_mail_targets(messages)
    if not args.apply:
        console.print(f"[yellow]Dry-run.[/yellow] Add --apply to mark {label}.")
        return 0
    creds = auth.load_credentials()
    with open_session(creds.email, creds.password) as imap:
        imap.select(args.folder, readonly=False)
        if seen:
            imap.add_flags(uids, r"(\Seen)")
        else:
            imap.remove_flags(uids, r"(\Seen)")
        refreshed = list(imap.fetch_metadata(uids))
    with cache.connect() as conn:
        cache.upsert_messages(conn, refreshed)
    console.print(f"[green]Marked {len(uids)} message(s) {label}.[/green]")
    return 0


def cmd_move(args: argparse.Namespace) -> int:
    uids = _parse_uids(args.uids)
    messages = _cached_mail_targets(folder=args.folder, uids=uids)
    dest = args.to.strip()
    if not dest:
        raise SystemExit("Destination folder is required.")
    console.rule("Move mail" if args.apply else "Move mail (dry-run)")
    console.print(f"From: {args.folder}")
    console.print(f"To: {dest}")
    _print_mail_targets(messages)
    if not args.apply:
        console.print("[yellow]Dry-run.[/yellow] Add --apply to move the messages.")
        return 0
    creds = auth.load_credentials()
    with open_session(creds.email, creds.password) as imap:
        imap.select(args.folder, readonly=False)
        imap.move(uids, dest)
    with cache.connect() as conn:
        removed = cache.remove_cached_messages(conn, folder=args.folder, uids=uids)
    console.print(
        f"[green]Moved {len(uids)} message(s) to {dest!r}.[/green] "
        f"Cache rows removed: {removed}."
    )
    return 0


def cmd_trash(args: argparse.Namespace) -> int:
    uids = _parse_uids(args.uids)
    messages = _cached_mail_targets(folder=args.folder, uids=uids)
    dest = "Deleted Messages"
    console.rule("Trash mail" if args.apply else "Trash mail (dry-run)")
    console.print(f"From: {args.folder}")
    console.print(f"To: {dest}")
    _print_mail_targets(messages)
    if not args.apply:
        console.print(
            "[yellow]Dry-run.[/yellow] Add --apply to move the messages to Trash."
        )
        return 0
    creds = auth.load_credentials()
    with open_session(creds.email, creds.password) as imap:
        imap.select(args.folder, readonly=False)
        imap.move(uids, dest)
    with cache.connect() as conn:
        removed = cache.remove_cached_messages(conn, folder=args.folder, uids=uids)
    console.print(
        f"[green]Moved {len(uids)} message(s) to Trash ({dest}).[/green] "
        f"Cache rows removed: {removed}."
    )
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    creds = auth.load_credentials()
    try:
        frozen = freeze_send(
            from_addr=creds.email,
            to=args.to,
            subject=args.subject,
            body=args.body,
            cc=args.cc,
            bcc=args.bcc,
        )
    except SMTPError as exc:
        raise SystemExit(str(exc)) from exc

    console.rule("Send mail (dry-run)" if not args.apply else "Send mail")
    console.print(f"From: {frozen['from']}")
    console.print(f"To: {', '.join(frozen['to'])}")
    console.print(f"Cc: {', '.join(frozen['cc']) or '(none)'}")
    console.print(f"Bcc: {', '.join(frozen['bcc']) or '(none)'}")
    console.print(f"Subject: {frozen['subject']}")
    console.print(f"Body chars: {len(frozen['body'])}")
    console.print(f"Message-ID: {frozen['messageId']}")
    if not args.apply:
        console.print("[yellow]Dry-run.[/yellow] Add --apply to send via SMTP.")
        return 0
    try:
        result = send_frozen(frozen, password=creds.password)
    except SMTPError as exc:
        raise SystemExit(str(exc)) from exc
    console.print(
        f"[green]Sent[/green] {result['subject']!r} to {result['recipients']} recipient(s)."
    )
    return 0


# ---- argparse registration ---------------------------------------------


def register(sub: argparse._SubParsersAction) -> None:
    """Mount the mail commands onto a subparsers object."""
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

    sp = sub.add_parser("triage", help="Build a dry-run move/delete plan from cached metadata.")
    sp.add_argument("--folder", default="INBOX")
    sp.add_argument("--sender", help="Exact sender email match")
    sp.add_argument("--sender-like", help="Substring match against sender name or email")
    sp.add_argument("--subject-like", help="Substring match against subject")
    sp.add_argument("--older-than", help="e.g. '30d', '24h', or '2025-01-01'")
    sp.add_argument("--has-list-unsubscribe", action="store_true")
    sp.add_argument("--limit", type=int, default=100)
    action = sp.add_mutually_exclusive_group(required=True)
    action.add_argument("--move-to", help="Destination folder for matched messages")
    action.add_argument("--delete", action="store_true", help="Delete matched messages")
    sp.add_argument("--plan-file", help="Write the dry-run plan to this JSON file")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_triage)

    sp = sub.add_parser("apply", help="Apply a reviewed triage plan. Requires --apply.")
    sp.add_argument("plan_file", help="JSON plan produced by `triage`")
    sp.add_argument("--apply", action="store_true", help="Actually mutate iCloud Mail")
    sp.set_defaults(func=cmd_apply)

    sp = sub.add_parser("cleanup", help="Automated cleanup workflows.")
    cleanup_sub = sp.add_subparsers(dest="cleanup_cmd", required=True)
    strict = cleanup_sub.add_parser(
        "strict",
        help="Sync all INBOX metadata and move known bulk senders to Trash.",
    )
    strict.add_argument("--folder", default="INBOX")
    strict.add_argument(
        "--no-sync",
        dest="sync",
        action="store_false",
        help="Use the existing cache instead of syncing all folder metadata first.",
    )
    strict.add_argument("--plan-file", help="Write the generated plan to this JSON file")
    strict.add_argument(
        "--apply",
        action="store_true",
        help="Actually move matched mail to Trash (Deleted Messages). No local backup.",
    )
    strict.set_defaults(func=cmd_cleanup_strict, sync=True)

    sp = sub.add_parser("jobs", help="Collect and score job-alert leads.")
    job_sub = sp.add_subparsers(dest="jobs_cmd", required=True)
    collect = job_sub.add_parser("collect", help="Extract scored job leads from email alerts.")
    collect.add_argument("--folder", default="INBOX")
    collect.add_argument("--since", default="7d", help="e.g. '7d', '24h', or '2025-01-01'")
    collect.add_argument("--limit", type=int, default=50, help="Scan newest N matching messages")
    collect.add_argument("--min-score", type=int, default=0)
    collect.add_argument("--top", type=int, default=20, help="Rows to show in the table")
    collect.add_argument("--out", help="Write review plan JSON")
    collect.add_argument("--json", action="store_true")
    collect.set_defaults(func=cmd_jobs_collect)

    sp = sub.add_parser("send", help="Send a message via iCloud SMTP. Requires --apply.")
    sp.add_argument("--to", required=True, help="Comma-separated To recipients")
    sp.add_argument("--subject", required=True)
    sp.add_argument("--body", required=True)
    sp.add_argument("--cc", help="Comma-separated Cc recipients")
    sp.add_argument("--bcc", help="Comma-separated Bcc recipients")
    sp.add_argument("--apply", action="store_true", help="Actually send the message")
    sp.set_defaults(func=cmd_send)

    sp = sub.add_parser("mark-read", help="Mark cached messages as read. Requires --apply.")
    sp.add_argument("--uids", required=True, help="Comma-separated IMAP UIDs")
    sp.add_argument("--folder", default="INBOX")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_mark_read)

    sp = sub.add_parser(
        "mark-unread", help="Mark cached messages as unread. Requires --apply."
    )
    sp.add_argument("--uids", required=True, help="Comma-separated IMAP UIDs")
    sp.add_argument("--folder", default="INBOX")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_mark_unread)

    sp = sub.add_parser("move", help="Move cached messages to another folder. Requires --apply.")
    sp.add_argument("--uids", required=True, help="Comma-separated IMAP UIDs")
    sp.add_argument("--to", required=True, help="Destination folder")
    sp.add_argument("--folder", default="INBOX")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_move)

    sp = sub.add_parser(
        "trash",
        help="Move cached messages to Deleted Messages. Requires --apply.",
    )
    sp.add_argument("--uids", required=True, help="Comma-separated IMAP UIDs")
    sp.add_argument("--folder", default="INBOX")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_trash)


__all__ = ["register"]
