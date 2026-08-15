"""iCloud Calendar + Reminders (CalDAV) commands.

Read commands hit iCloud live. Mutating commands preview then require
``--apply``; deletes/updates back up the raw ``.ics`` first.
"""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

from rich.table import Table

from ..common import console
from ..paths import BACKUP_DIR, timestamp_slug
from . import caldav
from .caldav import CalendarSession, CalItem


def _backup(items: list[CalItem], label: str) -> Path:
    root = BACKUP_DIR / f"calendar-{label}-{timestamp_slug()}"
    root.mkdir(parents=True, exist_ok=True)
    for it in items:
        if it.raw:
            (root / f"{it.uid}.ics").write_text(it.raw)
    return root


def _resolve_one(items: list[CalItem], query: str) -> CalItem:
    by_uid = [i for i in items if i.uid == query]
    if by_uid:
        return by_uid[0]
    matched = [i for i in items if caldav.matches(i, query)]
    if not matched:
        raise SystemExit(f"No item matches {query!r}.")
    if len(matched) > 1:
        raise SystemExit(f"{len(matched)} items match {query!r}; be more specific.")
    return matched[0]


# ---- read --------------------------------------------------------------


def cmd_calendars(args: argparse.Namespace) -> int:
    session = CalendarSession.connect()
    cols = session.collections()
    if args.json:
        print(json.dumps(
            [{"name": c.name, "url": c.url, "components": sorted(c.components)} for c in cols],
            indent=2,
        ))
        return 0
    table = Table(title=f"iCloud calendars & reminder lists ({len(cols)})")
    table.add_column("Name")
    table.add_column("Type")
    for c in cols:
        kind = "events" if c.is_events else ("reminders" if c.is_reminders else "?")
        table.add_row(c.name, kind)
    console.print(table)
    return 0


def cmd_events(args: argparse.Namespace) -> int:
    session = CalendarSession.connect()
    events = session.list_events(days=args.days)
    if args.json:
        print(json.dumps([e.to_dict() for e in events], indent=2))
        return 0
    table = Table(title=f"Upcoming events (next {args.days}d, {len(events)})")
    table.add_column("Start", style="cyan")
    table.add_column("End", style="dim")
    table.add_column("Summary")
    table.add_column("Location", style="dim")
    for e in events:
        table.add_row(e.start, e.end, e.summary[:40], e.location[:24])
    console.print(table)
    return 0


def cmd_timezones(args: argparse.Namespace) -> int:
    try:
        zones = caldav.list_timezones(query=args.query, limit=args.limit)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if args.json:
        print(json.dumps({"query": args.query, "count": len(zones), "timezones": zones}, indent=2))
        return 0
    table = Table(title=f"Timezones ({len(zones)})")
    table.add_column("TZID")
    for zone in zones:
        table.add_row(zone)
    console.print(table)
    return 0


def cmd_reminders(args: argparse.Namespace) -> int:
    session = CalendarSession.connect()
    rem = session.list_reminders(include_completed=args.all)
    if args.json:
        print(json.dumps([r.to_dict() for r in rem], indent=2))
        return 0
    table = Table(title=f"Reminders ({'all' if args.all else 'open'}, {len(rem)})")
    table.add_column("Due", style="cyan")
    table.add_column("Status", style="dim")
    table.add_column("Summary")
    for r in rem:
        table.add_row(r.start or "-", r.status or "NEEDS-ACTION", r.summary[:50])
    console.print(table)
    return 0


# ---- write -------------------------------------------------------------


def cmd_event_add(args: argparse.Namespace) -> int:
    uid = str(uuid.uuid4()).upper()
    try:
        ics = caldav.build_event(
            uid=uid, summary=args.title, start=args.start, end=args.end,
            location=args.location or "", all_day=args.all_day,
            timezone=args.timezone, attendees=args.attendees,
            rrule=args.rrule, alarm=args.alarm, partstat=args.partstat,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    console.rule("New event (dry-run)")
    console.print(ics)
    if not args.apply:
        console.print("[yellow]Dry-run.[/yellow] Add --apply to create.")
        return 0
    session = CalendarSession.connect()
    col = session.find_collection(args.calendar, events=True)
    href = session.put_item(col.url, uid, ics)
    console.print(f"[green]Created event[/green] {args.title} in {col.name} -> {href}")
    return 0


def cmd_event_rm(args: argparse.Namespace) -> int:
    session = CalendarSession.connect()
    target = _resolve_one(session.list_events(days=args.days), args.query)
    console.print(f"Would delete event: [bold]{target.summary}[/bold] ({target.start})")
    if not args.apply:
        console.print("[yellow]Dry-run.[/yellow] Add --apply to delete (.ics backed up first).")
        return 0
    backup = _backup([target], "event-delete")
    session.delete(target)
    console.print(f"[green]Deleted event[/green] {target.summary}. Backup: {backup}")
    return 0


def cmd_event_update(args: argparse.Namespace) -> int:
    if all(
        value is None
        for value in (
            args.title,
            args.start,
            args.end,
            args.location,
            args.attendees,
            args.timezone,
            args.rrule,
            args.alarm,
            args.partstat,
        )
    ):
        raise SystemExit(
            "Provide at least one of --title, --start, --end, --location, "
            "--attendees, --timezone, --rrule, --alarm, --partstat."
        )
    session = CalendarSession.connect()
    target = _resolve_one(session.list_events(days=args.days), args.query)
    try:
        ics = caldav.update_event(
            target.raw,
            summary=args.title,
            start=args.start,
            end=args.end,
            location=args.location,
            all_day=args.all_day or None,
            timezone=args.timezone,
            attendees=args.attendees,
            rrule=args.rrule,
            alarm=args.alarm,
            partstat=args.partstat,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    console.print(
        f"Would update event: [bold]{target.summary}[/bold] ({target.start})"
    )
    console.rule("Updated iCalendar (preview)")
    console.print(ics)
    if not args.apply:
        console.print("[yellow]Dry-run.[/yellow] Add --apply to update (.ics backed up first).")
        return 0
    backup = _backup([target], "event-update")
    session.update(target, ics)
    console.print(f"[green]Updated event[/green] {target.uid}. Backup: {backup}")
    return 0


def cmd_reminder_add(args: argparse.Namespace) -> int:
    uid = str(uuid.uuid4()).upper()
    try:
        ics = caldav.build_reminder(
            uid=uid, summary=args.title, due=args.due, priority=args.priority,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    console.rule("New reminder (dry-run)")
    console.print(ics)
    if not args.apply:
        console.print("[yellow]Dry-run.[/yellow] Add --apply to create.")
        return 0
    session = CalendarSession.connect()
    col = session.find_collection(args.list, events=False)
    href = session.put_item(col.url, uid, ics)
    console.print(f"[green]Created reminder[/green] {args.title} in {col.name} -> {href}")
    return 0


def cmd_reminder_done(args: argparse.Namespace) -> int:
    session = CalendarSession.connect()
    target = _resolve_one(session.list_reminders(include_completed=True), args.query)
    ics = caldav.build_reminder(uid=target.uid, summary=target.summary, due=target.start or None,
                                completed=True)
    console.print(f"Would mark complete: [bold]{target.summary}[/bold]")
    if not args.apply:
        console.print("[yellow]Dry-run.[/yellow] Add --apply to complete it.")
        return 0
    backup = _backup([target], "reminder-done")
    session.update(target, ics)
    console.print(f"[green]Completed reminder[/green] {target.summary}. Backup: {backup}")
    return 0


def cmd_reminder_rm(args: argparse.Namespace) -> int:
    session = CalendarSession.connect()
    target = _resolve_one(session.list_reminders(include_completed=True), args.query)
    console.print(f"Would delete reminder: [bold]{target.summary}[/bold]")
    if not args.apply:
        console.print("[yellow]Dry-run.[/yellow] Add --apply to delete (.ics backed up first).")
        return 0
    backup = _backup([target], "reminder-delete")
    session.delete(target)
    console.print(f"[green]Deleted reminder[/green] {target.summary}. Backup: {backup}")
    return 0


def cmd_reminder_update(args: argparse.Namespace) -> int:
    if args.title is None and args.due is None and args.priority is None:
        raise SystemExit("Provide at least one of --title, --due, or --priority.")
    session = CalendarSession.connect()
    target = _resolve_one(session.list_reminders(include_completed=True), args.query)
    try:
        ics = caldav.update_reminder(
            target.raw,
            summary=args.title,
            due=args.due,
            priority=args.priority,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    console.print(f"Would update reminder: [bold]{target.summary}[/bold]")
    console.rule("Updated iCalendar (preview)")
    console.print(ics)
    if not args.apply:
        console.print("[yellow]Dry-run.[/yellow] Add --apply to update (.ics backed up first).")
        return 0
    backup = _backup([target], "reminder-update")
    session.update(target, ics)
    console.print(f"[green]Updated reminder[/green] {target.uid}. Backup: {backup}")
    return 0


# ---- registration ------------------------------------------------------


def register(sub: argparse._SubParsersAction) -> None:
    sp = sub.add_parser("calendars", help="List calendars and reminder lists.")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_calendars)

    sp = sub.add_parser("events", help="List upcoming events.")
    sp.add_argument("--days", type=int, default=30)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_events)

    sp = sub.add_parser("reminders", help="List reminders (open by default).")
    sp.add_argument("--all", action="store_true", help="Include completed")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_reminders)

    sp = sub.add_parser("timezones", help="List IANA-like timezone names for event create/update.")
    sp.add_argument("--query", help="Substring filter, e.g. Asia or America/Los")
    sp.add_argument("--limit", type=int, default=50)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_timezones)

    sp = sub.add_parser("event-add", help="Create an event. Requires --apply.")
    sp.add_argument("--title", required=True)
    sp.add_argument("--start", required=True, help="'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM'")
    sp.add_argument("--end", help="Same format as --start")
    sp.add_argument("--location")
    sp.add_argument("--timezone", help="IANA timezone for timed events, e.g. America/Los_Angeles")
    sp.add_argument("--attendees", help="Comma-separated attendee emails")
    sp.add_argument("--rrule", help="Validated RRULE, e.g. FREQ=WEEKLY;COUNT=8")
    sp.add_argument("--alarm", help="DISPLAY alarm duration, e.g. -PT15M")
    sp.add_argument(
        "--partstat",
        help="ATTENDEE PARTSTAT: NEEDS-ACTION, ACCEPTED, DECLINED, TENTATIVE, DELEGATED",
    )
    sp.add_argument("--calendar", help="Target calendar name (default: first)")
    sp.add_argument("--all-day", action="store_true")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_event_add)

    sp = sub.add_parser("event-rm", help="Delete an event by UID or unique match.")
    sp.add_argument("query")
    sp.add_argument("--days", type=int, default=365, help="Search window")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_event_rm)

    sp = sub.add_parser(
        "event-update",
        help="Update an event by UID or unique match. Requires --apply.",
    )
    sp.add_argument("query")
    sp.add_argument("--title")
    sp.add_argument("--start", help="'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM'")
    sp.add_argument("--end", help="Same format as --start")
    sp.add_argument("--location")
    sp.add_argument("--timezone", help="IANA timezone for timed events")
    sp.add_argument("--attendees", help="Comma-separated attendee emails")
    sp.add_argument("--rrule", help="Replace RRULE; empty string clears it")
    sp.add_argument("--alarm", help="Replace DISPLAY alarm; empty string clears it")
    sp.add_argument(
        "--partstat",
        help="Replace ATTENDEE PARTSTAT (requires --attendees)",
    )
    sp.add_argument("--all-day", action="store_true")
    sp.add_argument("--days", type=int, default=365, help="Search window")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_event_update)

    sp = sub.add_parser("reminder-add", help="Create a reminder. Requires --apply.")
    sp.add_argument("--title", required=True)
    sp.add_argument("--due", help="'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM'")
    sp.add_argument("--priority", type=int, help="PRIORITY 1-9 (1 is high)")
    sp.add_argument("--list", help="Target reminder list name (default: first)")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_reminder_add)

    sp = sub.add_parser("reminder-done", help="Mark a reminder complete. Requires --apply.")
    sp.add_argument("query")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_reminder_done)

    sp = sub.add_parser("reminder-rm", help="Delete a reminder. Requires --apply.")
    sp.add_argument("query")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_reminder_rm)

    sp = sub.add_parser(
        "reminder-update",
        help="Update a reminder by UID or unique match. Requires --apply.",
    )
    sp.add_argument("query")
    sp.add_argument("--title")
    sp.add_argument("--due", help="'YYYY-MM-DD', 'YYYY-MM-DD HH:MM', or '' to clear")
    sp.add_argument(
        "--priority",
        help="PRIORITY 1-9, or empty string to clear",
    )
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_reminder_update)


__all__ = ["register"]
