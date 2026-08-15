"""Entry points for icloudseal-mcp.

Two console scripts share one command set:

* ``icloudseal-mcp <domain> <action>`` — the multi-domain CLI
  (``mail``, ``contacts``, and more as domains are added).
* ``mail-agent <action>`` — legacy alias that maps straight to the mail domain
  so existing muscle memory / scripts keep working.
"""

from __future__ import annotations

import argparse
import sys

from . import auth
from .calendar import commands as calendar_commands
from .common import console
from .contacts import commands as contacts_commands
from .drive import commands as drive_commands
from .mail import commands as mail_commands
from .messages import commands as messages_commands
from .notes import commands as notes_commands
from .photos import commands as photos_commands

DOMAINS = {
    "mail": ("iCloud Mail (IMAP + SMTP): triage, cleanup, gated send.", mail_commands.register),
    "contacts": (
        "iCloud Contacts (CardDAV): list, search, gated CRUD.",
        contacts_commands.register,
    ),
    "calendar": (
        "iCloud Calendar + Reminders (CalDAV): list, gated add/update/rm/done.",
        calendar_commands.register,
    ),
    "messages": (
        "iMessage/SMS: read chat.db (needs Full Disk Access), gated send.",
        messages_commands.register,
    ),
    "notes": (
        "iCloud Notes (AppleScript): list/read, gated create/delete.",
        notes_commands.register,
    ),
    "drive": (
        "iCloud Drive (filesystem): ls/tree/find/read, gated put/rm.",
        drive_commands.register,
    ),
    "photos": (
        "iCloud Photos: read-only stats/albums/list + best-effort export.",
        photos_commands.register,
    ),
}


def build_icloud_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="icloudseal-mcp",
        description="Agent-driven CRUD layer for iCloud services (mail, contacts, ...).",
    )
    domain_sub = p.add_subparsers(dest="domain", required=True)
    for name, (help_text, register) in DOMAINS.items():
        dp = domain_sub.add_parser(name, help=help_text)
        action_sub = dp.add_subparsers(dest="cmd", required=True)
        register(action_sub)
    return p


def build_mail_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mail-agent",
        description="Legacy alias for `icloudseal-mcp mail`. CRUD layer for iCloud Mail.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    mail_commands.register(sub)
    return p


def _dispatch(parser: argparse.ArgumentParser, argv: list[str] | None) -> int:
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except auth.AuthError as e:
        console.print(f"[red]Auth error:[/red] {e}")
        return 2
    except KeyboardInterrupt:
        console.print("[yellow]Interrupted.[/yellow]")
        return 130


def main(argv: list[str] | None = None) -> int:
    return _dispatch(build_icloud_parser(), argv)


def mail_main(argv: list[str] | None = None) -> int:
    return _dispatch(build_mail_parser(), argv)


if __name__ == "__main__":
    sys.exit(main())
