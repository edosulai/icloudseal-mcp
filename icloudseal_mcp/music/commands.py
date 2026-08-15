"""Music.app commands (AppleScript-backed).

Reads report now-playing. Playback is dry-run unless ``--apply``.
"""

from __future__ import annotations

import argparse
import json

from ..common import console
from . import applescript
from .applescript import MusicError, NowPlaying


def _guard(fn):
    try:
        return fn()
    except MusicError as exc:
        console.print(f"[red]Music error:[/red] {exc}")
        console.print("[dim]Grant Automation access to control Music.app if prompted.[/dim]")
        return None


def _print_now(track: NowPlaying) -> None:
    if track.state == "stopped":
        console.print("[dim]Music is not running or playback is stopped.[/dim]")
        return
    console.rule(track.name or "(untitled)")
    if track.artist:
        console.print(track.artist)
    if track.album:
        console.print(f"[dim]{track.album}[/dim]")
    console.print(f"state: {track.state}")
    if track.duration_sec is not None:
        console.print(f"duration: {track.duration_sec:.0f}s")
    if track.position_sec is not None:
        console.print(f"position: {track.position_sec:.0f}s")


def cmd_now(args: argparse.Namespace) -> int:
    track = _guard(applescript.now_playing)
    if track is None:
        return 2
    if args.json:
        print(json.dumps(track.to_dict(), indent=2))
        return 0
    _print_now(track)
    return 0


def _cmd_playback(args: argparse.Namespace, action: str) -> int:
    track = _guard(applescript.now_playing)
    if track is None:
        return 2
    console.rule(f"Music {action}" if args.apply else f"Music {action} (dry-run)")
    _print_now(track)
    if not args.apply:
        console.print(f"[yellow]Dry-run.[/yellow] Add --apply to {action}.")
        return 0
    try:
        applescript.playback(action)
    except MusicError as exc:
        console.print(f"[red]Music error:[/red] {exc}")
        console.print("[dim]Grant Automation access to control Music.app if prompted.[/dim]")
        return 2
    console.print(f"[green]{action}[/green]")
    return 0


def cmd_playpause(args: argparse.Namespace) -> int:
    return _cmd_playback(args, "playpause")


def cmd_next(args: argparse.Namespace) -> int:
    return _cmd_playback(args, "next")


def cmd_previous(args: argparse.Namespace) -> int:
    return _cmd_playback(args, "previous")


def register(sub: argparse._SubParsersAction) -> None:
    sp = sub.add_parser("now", help="Show now-playing (does not launch Music).")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_now)

    sp = sub.add_parser("playpause", help="Toggle play/pause. Requires --apply.")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_playpause)

    sp = sub.add_parser("next", help="Skip to the next track. Requires --apply.")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_next)

    sp = sub.add_parser("previous", help="Skip to the previous track. Requires --apply.")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_previous)


__all__ = ["register"]
