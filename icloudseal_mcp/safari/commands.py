"""Safari commands (AppleScript-backed).

Reads list open tabs. Opening a URL is dry-run unless ``--apply``.
"""

from __future__ import annotations

import argparse
import json

from rich.table import Table

from ..common import console
from . import applescript, store
from .applescript import SafariError, SafariTab
from .store import SafariStoreError


def _guard(fn):
    try:
        return fn()
    except SafariError as exc:
        console.print(f"[red]Safari error:[/red] {exc}")
        console.print("[dim]Grant Automation access to control Safari if prompted.[/dim]")
        return None


def _tab_row(tab: SafariTab) -> dict[str, object]:
    return {
        "window_index": tab.window_index,
        "tab_index": tab.tab_index,
        "name": tab.name,
        "url": tab.url,
        "is_current": tab.is_current,
    }


def cmd_tabs(args: argparse.Namespace) -> int:
    tabs = _guard(applescript.list_tabs)
    if tabs is None:
        return 2
    if args.json:
        print(json.dumps([_tab_row(tab) for tab in tabs], indent=2))
        return 0
    if not tabs:
        console.print("[dim]Safari is not running or has no open tabs.[/dim]")
        return 0
    table = Table(title=f"Safari tabs ({len(tabs)})")
    table.add_column("Win", justify="right")
    table.add_column("Tab", justify="right")
    table.add_column("Current", justify="center")
    table.add_column("Name")
    table.add_column("URL", style="dim")
    for tab in tabs:
        table.add_row(
            str(tab.window_index),
            str(tab.tab_index),
            "*" if tab.is_current else "",
            tab.name[:50],
            tab.url[:80],
        )
    console.print(table)
    return 0


def cmd_current(args: argparse.Namespace) -> int:
    tabs = _guard(applescript.list_tabs)
    if tabs is None:
        return 2
    tab = next((item for item in tabs if item.is_current), None)
    if tab is None:
        if args.json:
            print(json.dumps({"running": False}, indent=2))
            return 0
        console.print("[dim]Safari is not running or has no current tab.[/dim]")
        return 0
    payload = _tab_row(tab)
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0
    console.rule(tab.name)
    console.print(tab.url)
    console.print(f"[dim]window {tab.window_index} tab {tab.tab_index}[/dim]")
    return 0


def cmd_open(args: argparse.Namespace) -> int:
    target = "new_window" if args.window else "new_tab"
    try:
        url = applescript.validate_url(args.url)
    except SafariError as exc:
        console.print(f"[red]Safari error:[/red] {exc}")
        return 2
    console.rule("Open URL" if args.apply else "Open URL (dry-run)")
    console.print(f"URL: {url}")
    console.print(f"Target: {target}")
    if not args.apply:
        console.print("[yellow]Dry-run.[/yellow] Add --apply to open in Safari.")
        return 0
    try:
        applescript.open_url(url, target=target)
    except SafariError as exc:
        console.print(f"[red]Safari error:[/red] {exc}")
        console.print("[dim]Grant Automation access to control Safari if prompted.[/dim]")
        return 2
    console.print(f"[green]Opened[/green] {url}")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    try:
        url = applescript.search_url(args.query)
    except SafariError as exc:
        console.print(f"[red]Safari error:[/red] {exc}")
        return 2
    target = "new_window" if args.window else "new_tab"
    console.rule("Safari search" if args.apply else "Safari search (dry-run)")
    console.print(f"Query: {args.query}")
    console.print(f"URL: {url}")
    console.print(f"Target: {target}")
    if not args.apply:
        console.print("[yellow]Dry-run.[/yellow] Add --apply to open the search in Safari.")
        return 0
    try:
        applescript.open_url(url, target=target)
    except SafariError as exc:
        console.print(f"[red]Safari error:[/red] {exc}")
        return 2
    console.print(f"[green]Opened[/green] {url}")
    return 0


def cmd_close(args: argparse.Namespace) -> int:
    tabs = _guard(applescript.list_tabs)
    if tabs is None:
        return 2
    match = next(
        (
            tab
            for tab in tabs
            if tab.window_index == args.window and tab.tab_index == args.tab
        ),
        None,
    )
    if match is None:
        console.print(f"[red]No Safari tab at window {args.window} tab {args.tab}.[/red]")
        return 2
    console.rule("Close Safari tab" if args.apply else "Close Safari tab (dry-run)")
    console.print(f"Window: {match.window_index} Tab: {match.tab_index}")
    console.print(f"Name: {match.name or '(untitled)'}")
    console.print(f"URL: {match.url}")
    if not args.apply:
        console.print("[yellow]Dry-run.[/yellow] Add --apply to close the tab.")
        return 0
    try:
        applescript.close_tab(
            window_index=match.window_index,
            tab_index=match.tab_index,
            name=match.name,
            url=match.url,
        )
    except SafariError as exc:
        console.print(f"[red]Safari error:[/red] {exc}")
        return 2
    console.print("[green]Closed tab.[/green]")
    return 0


def cmd_source(args: argparse.Namespace) -> int:
    tabs = _guard(applescript.list_tabs)
    if tabs is None:
        return 2
    if args.window is None or args.tab is None:
        match = next((tab for tab in tabs if tab.is_current), None)
        if match is None:
            console.print("[dim]Safari is not running or has no current tab.[/dim]")
            return 2
    else:
        match = next(
            (
                tab
                for tab in tabs
                if tab.window_index == args.window and tab.tab_index == args.tab
            ),
            None,
        )
        if match is None:
            console.print(f"[red]No Safari tab at window {args.window} tab {args.tab}.[/red]")
            return 2
    try:
        text = applescript.page_text(
            window_index=match.window_index,
            tab_index=match.tab_index,
            name=match.name,
            url=match.url,
        )
    except SafariError as exc:
        console.print(f"[red]Safari error:[/red] {exc}")
        return 2
    if args.json:
        print(
            json.dumps(
                {
                    "window_index": match.window_index,
                    "tab_index": match.tab_index,
                    "name": match.name,
                    "url": match.url,
                    "text": text,
                },
                indent=2,
            )
        )
        return 0
    console.rule(match.name or match.url)
    console.print(text)
    return 0


def cmd_bookmarks(args: argparse.Namespace) -> int:
    reading_list = True if args.reading_list else None
    try:
        items = store.list_bookmarks(reading_list=reading_list, limit=args.limit)
    except SafariStoreError as exc:
        console.print(f"[red]Safari store:[/red] {exc}")
        return 2
    if args.json:
        print(json.dumps([item.to_dict() for item in items], indent=2))
        return 0
    if not items:
        console.print("[dim]No Safari bookmarks found.[/dim]")
        return 0
    table = Table(title=f"Safari bookmarks ({len(items)})")
    table.add_column("Folder")
    table.add_column("Title")
    table.add_column("URL", style="dim")
    for item in items:
        table.add_row(item.folder[:40], item.title[:50], item.url[:80])
    console.print(table)
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    try:
        items = store.list_history(limit=args.limit)
    except SafariStoreError as exc:
        console.print(f"[red]Safari store:[/red] {exc}")
        return 2
    if args.json:
        print(json.dumps([item.to_dict() for item in items], indent=2))
        return 0
    if not items:
        console.print("[dim]No Safari history found.[/dim]")
        return 0
    table = Table(title=f"Safari history ({len(items)})")
    table.add_column("Visited")
    table.add_column("Title")
    table.add_column("URL", style="dim")
    for item in items:
        table.add_row(item.visited_at[:19], item.title[:50], item.url[:80])
    console.print(table)
    return 0


def cmd_bookmark_add(args: argparse.Namespace) -> int:
    try:
        title = applescript.validate_bookmark_title(args.title)
        url = applescript.validate_url(args.url)
    except SafariError as exc:
        console.print(f"[red]Safari error:[/red] {exc}")
        return 2
    console.rule("Add Safari bookmark" if args.apply else "Add Safari bookmark (dry-run)")
    console.print(f"Title: {title}")
    console.print(f"URL: {url}")
    if not args.apply:
        console.print("[yellow]Dry-run.[/yellow] Add --apply to add the bookmark.")
        return 0
    try:
        applescript.add_bookmark(title, url)
    except SafariError as exc:
        console.print(f"[red]Safari error:[/red] {exc}")
        return 2
    console.print(f"[green]Added bookmark[/green] {title}")
    return 0


def cmd_reading_list_add(args: argparse.Namespace) -> int:
    try:
        title = applescript.validate_bookmark_title(args.title)
        url = applescript.validate_url(args.url)
    except SafariError as exc:
        console.print(f"[red]Safari error:[/red] {exc}")
        return 2
    console.rule("Add Safari Reading List" if args.apply else "Add Safari Reading List (dry-run)")
    console.print(f"Title: {title}")
    console.print(f"URL: {url}")
    if not args.apply:
        console.print("[yellow]Dry-run.[/yellow] Add --apply to add the Reading List item.")
        return 0
    try:
        applescript.add_reading_list(title, url)
    except SafariError as exc:
        console.print(f"[red]Safari error:[/red] {exc}")
        return 2
    console.print(f"[green]Added Reading List item[/green] {title}")
    return 0


def cmd_reading_list_rm(args: argparse.Namespace) -> int:
    try:
        title = applescript.validate_bookmark_title(args.title)
        url = applescript.validate_url(args.url)
    except SafariError as exc:
        console.print(f"[red]Safari error:[/red] {exc}")
        return 2
    console.rule(
        "Remove Safari Reading List" if args.apply else "Remove Safari Reading List (dry-run)"
    )
    console.print(f"Title: {title}")
    console.print(f"URL: {url}")
    if not args.apply:
        console.print("[yellow]Dry-run.[/yellow] Add --apply to remove the Reading List item.")
        return 0
    try:
        applescript.remove_reading_list(title, url)
    except SafariError as exc:
        console.print(f"[red]Safari error:[/red] {exc}")
        return 2
    console.print(f"[green]Removed Reading List item[/green] {title}")
    return 0


def cmd_bookmark_rm(args: argparse.Namespace) -> int:
    try:
        title = applescript.validate_bookmark_title(args.title)
        url = applescript.validate_url(args.url)
    except SafariError as exc:
        console.print(f"[red]Safari error:[/red] {exc}")
        return 2
    console.rule("Remove Safari bookmark" if args.apply else "Remove Safari bookmark (dry-run)")
    console.print(f"Title: {title}")
    console.print(f"URL: {url}")
    if not args.apply:
        console.print("[yellow]Dry-run.[/yellow] Add --apply to remove the bookmark.")
        return 0
    try:
        applescript.remove_bookmark(title, url)
    except SafariError as exc:
        console.print(f"[red]Safari error:[/red] {exc}")
        return 2
    console.print(f"[green]Removed bookmark[/green] {title}")
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    tabs = _guard(applescript.list_tabs)
    if tabs is None:
        return 2
    if args.window is None or args.tab is None:
        match = next((tab for tab in tabs if tab.is_current), None)
        if match is None:
            console.print("[dim]Safari is not running or has no current tab.[/dim]")
            return 2
    else:
        match = next(
            (
                tab
                for tab in tabs
                if tab.window_index == args.window and tab.tab_index == args.tab
            ),
            None,
        )
        if match is None:
            console.print(f"[red]No Safari tab at window {args.window} tab {args.tab}.[/red]")
            return 2
    try:
        text = applescript.page_extract(
            window_index=match.window_index,
            tab_index=match.tab_index,
            name=match.name,
            url=match.url,
            extract=args.extract,
        )
    except SafariError as exc:
        console.print(f"[red]Safari error:[/red] {exc}")
        return 2
    if args.json:
        print(
            json.dumps(
                {
                    "window_index": match.window_index,
                    "tab_index": match.tab_index,
                    "name": match.name,
                    "url": match.url,
                    "extract": args.extract,
                    "text": text,
                },
                indent=2,
            )
        )
        return 0
    console.rule(match.name or match.url)
    console.print(text)
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    sp = sub.add_parser("tabs", help="List open Safari tabs (does not launch Safari).")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_tabs)

    sp = sub.add_parser("current", help="Show the current Safari tab.")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_current)

    sp = sub.add_parser("open", help="Open an http(s) URL in Safari. Requires --apply.")
    sp.add_argument("--url", required=True)
    sp.add_argument("--window", action="store_true", help="Open in a new window.")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_open)

    sp = sub.add_parser("search", help="Open a web search in Safari. Requires --apply.")
    sp.add_argument("--query", required=True)
    sp.add_argument("--window", action="store_true", help="Open in a new window.")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("close", help="Close one Safari tab. Requires --apply.")
    sp.add_argument("--window", type=int, required=True, dest="window")
    sp.add_argument("--tab", type=int, required=True)
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_close)

    sp = sub.add_parser("source", help="Print size-capped text of one Safari tab.")
    sp.add_argument("--window", type=int)
    sp.add_argument("--tab", type=int)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_source)

    sp = sub.add_parser(
        "extract",
        help="Print allowlisted title+innerText of one Safari tab.",
    )
    sp.add_argument("--window", type=int)
    sp.add_argument("--tab", type=int)
    sp.add_argument(
        "--extract",
        default="title_text",
        choices=sorted(applescript.ALLOWED_EXTRACTS),
        help="Allowlisted extract (title_text only).",
    )
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_extract)

    sp = sub.add_parser("bookmarks", help="List Safari bookmarks (needs Full Disk Access).")
    sp.add_argument("--reading-list", action="store_true")
    sp.add_argument("--limit", type=int, default=200)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_bookmarks)

    sp = sub.add_parser("history", help="List recent Safari history (read-only, needs FDA).")
    sp.add_argument("--limit", type=int, default=50)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_history)

    sp = sub.add_parser("bookmark-add", help="Add a bookmarks-bar item. Requires --apply.")
    sp.add_argument("--title", required=True)
    sp.add_argument("--url", required=True)
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_bookmark_add)

    sp = sub.add_parser("bookmark-rm", help="Remove a bookmarks-bar item. Requires --apply.")
    sp.add_argument("--title", required=True)
    sp.add_argument("--url", required=True)
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_bookmark_rm)

    sp = sub.add_parser("reading-list-add", help="Add a Reading List item. Requires --apply.")
    sp.add_argument("--title", required=True)
    sp.add_argument("--url", required=True)
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_reading_list_add)

    sp = sub.add_parser("reading-list-rm", help="Remove a Reading List item. Requires --apply.")
    sp.add_argument("--title", required=True)
    sp.add_argument("--url", required=True)
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_reading_list_rm)


__all__ = ["register"]
