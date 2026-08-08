"""iCloud Contacts (CardDAV) commands.

Read commands (``list``/``search``/``export``) hit iCloud live. Mutating
commands (``create``/``update``/``delete``) print a dry-run preview and require
``--apply`` to execute; deletes and updates back up the raw vCard first.
"""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

from rich.table import Table

from ..common import console
from ..paths import BACKUP_DIR, timestamp_slug
from . import carddav
from .carddav import Contact, ContactsSession

# ---- helpers -----------------------------------------------------------


def _print_table(contacts: list[Contact], title: str) -> None:
    table = Table(title=title)
    table.add_column("#", justify="right", style="dim")
    table.add_column("Name")
    table.add_column("Emails")
    table.add_column("Phones")
    table.add_column("Org", style="dim")
    for i, c in enumerate(contacts, 1):
        table.add_row(
            str(i),
            c.full_name[:32],
            ", ".join(c.emails)[:34],
            ", ".join(c.phones)[:24],
            c.org[:20],
        )
    console.print(table)


def _backup_vcards(contacts: list[Contact], label: str) -> Path:
    root = BACKUP_DIR / f"contacts-{label}-{timestamp_slug()}"
    root.mkdir(parents=True, exist_ok=True)
    for c in contacts:
        raw = c.raw or carddav.build_vcard(
            uid=c.uid,
            full_name=c.full_name,
            first=c.first,
            last=c.last,
            org=c.org,
            emails=c.emails,
            phones=c.phones,
        )
        (root / f"{c.uid}.vcf").write_text(raw)
    return root


# ---- read commands -----------------------------------------------------


def cmd_list(args: argparse.Namespace) -> int:
    session = ContactsSession.connect()
    contacts = session.list_contacts()
    if args.limit:
        contacts = contacts[: args.limit]
    if args.json:
        print(json.dumps([c.to_dict() for c in contacts], indent=2, default=str))
        return 0
    _print_table(contacts, f"iCloud Contacts ({len(contacts)})")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    session = ContactsSession.connect()
    contacts = [c for c in session.list_contacts() if carddav.matches(c, args.query)]
    if args.json:
        print(json.dumps([c.to_dict() for c in contacts], indent=2, default=str))
        return 0
    _print_table(contacts, f"Contacts matching {args.query!r} ({len(contacts)})")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    session = ContactsSession.connect()
    contacts = session.list_contacts()
    out = Path(args.path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix.lower() == ".json":
        out.write_text(json.dumps([c.to_dict() for c in contacts], indent=2, default=str) + "\n")
    else:
        out.write_text("".join(c.raw for c in contacts if c.raw))
    console.print(f"[green]Exported {len(contacts)} contact(s) to[/green] {out}")
    return 0


# ---- write commands ----------------------------------------------------


def cmd_create(args: argparse.Namespace) -> int:
    full_name = args.name or " ".join(p for p in (args.first, args.last) if p)
    if not full_name:
        raise SystemExit("Provide --name (or --first/--last).")
    contact = Contact(
        uid=str(uuid.uuid4()).upper(),
        href=None,
        etag=None,
        full_name=full_name,
        first=args.first or "",
        last=args.last or "",
        org=args.org or "",
        emails=args.email or [],
        phones=args.phone or [],
    )
    preview = carddav.build_vcard(
        uid=contact.uid,
        full_name=contact.full_name,
        first=contact.first,
        last=contact.last,
        org=contact.org,
        emails=contact.emails,
        phones=contact.phones,
    )
    console.rule("New contact (dry-run)")
    console.print(preview)
    if not args.apply:
        console.print("[yellow]Dry-run.[/yellow] Add --apply to create this contact.")
        return 0
    session = ContactsSession.connect()
    href = session.create(contact)
    console.print(f"[green]Created contact[/green] {contact.full_name} -> {href}")
    return 0


def _resolve_one(session: ContactsSession, selector: str) -> Contact:
    contacts = session.list_contacts()
    by_uid = [c for c in contacts if c.uid == selector]
    if by_uid:
        return by_uid[0]
    matched = [c for c in contacts if carddav.matches(c, selector)]
    if not matched:
        raise SystemExit(f"No contact matches {selector!r}.")
    if len(matched) > 1:
        _print_table(matched, "Ambiguous selector — refine it")
        raise SystemExit(f"{len(matched)} contacts match {selector!r}; be more specific.")
    return matched[0]


def cmd_update(args: argparse.Namespace) -> int:
    session = ContactsSession.connect()
    contact = _resolve_one(session, args.selector)

    if args.name:
        contact.full_name = args.name
    if args.first is not None:
        contact.first = args.first
    if args.last is not None:
        contact.last = args.last
    if args.org is not None:
        contact.org = args.org
    if args.add_email:
        contact.emails.extend(e for e in args.add_email if e not in contact.emails)
    if args.add_phone:
        contact.phones.extend(p for p in args.add_phone if p not in contact.phones)
    if args.set_email is not None:
        contact.emails = args.set_email
    if args.set_phone is not None:
        contact.phones = args.set_phone

    preview = carddav.build_vcard(
        uid=contact.uid,
        full_name=contact.full_name,
        first=contact.first,
        last=contact.last,
        org=contact.org,
        emails=contact.emails,
        phones=contact.phones,
    )
    console.rule(f"Update {contact.full_name} (dry-run)")
    console.print(preview)
    if not args.apply:
        console.print("[yellow]Dry-run.[/yellow] Add --apply to push this update.")
        return 0
    backup = _backup_vcards([contact], "update")
    session.update(contact)
    console.print(f"[green]Updated[/green] {contact.full_name}. Backup: {backup}")
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    session = ContactsSession.connect()
    contacts = session.list_contacts()
    targets = [c for c in contacts if carddav.matches(c, args.query)]
    if not targets:
        console.print(f"[yellow]No contacts match {args.query!r}.[/yellow]")
        return 0

    _print_table(targets, f"Would delete {len(targets)} contact(s) matching {args.query!r}")
    if not args.apply:
        console.print("[yellow]Dry-run.[/yellow] Add --apply to delete (vCards backed up first).")
        return 0
    backup = _backup_vcards(targets, "delete")
    for i, c in enumerate(targets, 1):
        session.delete(c)
        console.print(f"[dim][{i}/{len(targets)}] deleted {c.full_name}[/dim]")
    console.print(f"[green]Deleted {len(targets)} contact(s).[/green] Backup: {backup}")
    return 0


# ---- registration ------------------------------------------------------


def register(sub: argparse._SubParsersAction) -> None:
    sp = sub.add_parser("list", help="List all iCloud contacts (live CardDAV).")
    sp.add_argument("--limit", type=int, default=0, help="Show only the first N")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("search", help="Search contacts by name/email/phone/org.")
    sp.add_argument("query")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("export", help="Export all contacts to .json or .vcf.")
    sp.add_argument("path", help="Output file (.json or .vcf)")
    sp.set_defaults(func=cmd_export)

    sp = sub.add_parser("create", help="Create a contact. Requires --apply to execute.")
    sp.add_argument("--name", help="Full display name")
    sp.add_argument("--first", help="Given name")
    sp.add_argument("--last", help="Family name")
    sp.add_argument("--org", help="Organization")
    sp.add_argument("--email", action="append", help="Email (repeatable)")
    sp.add_argument("--phone", action="append", help="Phone (repeatable)")
    sp.add_argument("--apply", action="store_true", help="Actually create in iCloud")
    sp.set_defaults(func=cmd_create)

    sp = sub.add_parser("update", help="Update a contact by UID or search match.")
    sp.add_argument("selector", help="Contact UID or unique search term")
    sp.add_argument("--name")
    sp.add_argument("--first")
    sp.add_argument("--last")
    sp.add_argument("--org")
    sp.add_argument("--add-email", action="append", help="Append an email (repeatable)")
    sp.add_argument("--add-phone", action="append", help="Append a phone (repeatable)")
    sp.add_argument("--set-email", action="append", help="Replace all emails (repeatable)")
    sp.add_argument("--set-phone", action="append", help="Replace all phones (repeatable)")
    sp.add_argument("--apply", action="store_true", help="Actually push the update")
    sp.set_defaults(func=cmd_update)

    sp = sub.add_parser("delete", help="Delete contacts matching a query. Requires --apply.")
    sp.add_argument("query", help="Search term selecting contacts to delete")
    sp.add_argument("--apply", action="store_true", help="Actually delete (after .vcf backup)")
    sp.set_defaults(func=cmd_delete)


__all__ = ["register"]
