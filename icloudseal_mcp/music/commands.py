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


def cmd_search(args: argparse.Namespace) -> int:
    tracks = _guard(lambda: applescript.search_tracks(args.query, limit=args.limit))
    if tracks is None:
        return 2
    if args.json:
        print(json.dumps({"query": args.query, "count": len(tracks), "tracks": tracks}, indent=2))
        return 0
    if not tracks:
        console.print("[dim]No matching tracks.[/dim]")
        return 0
    for track in tracks:
        artist = track.get("artist") or ""
        album = track.get("album") or ""
        extras = " — ".join(part for part in (artist, album) if part)
        label = track.get("name") or "(untitled)"
        console.print(f"{label}{(' — ' + extras) if extras else ''}")
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


def cmd_volume(args: argparse.Namespace) -> int:
    console.rule("Music volume" if args.apply else "Music volume (dry-run)")
    console.print(f"Level: {args.level}")
    if not args.apply:
        console.print("[yellow]Dry-run.[/yellow] Add --apply to set the volume.")
        return 0
    if _guard(lambda: applescript.set_volume(args.level)) is None:
        return 2
    console.print(f"[green]Volume[/green] {args.level}")
    return 0


def cmd_shuffle(args: argparse.Namespace) -> int:
    console.rule("Music shuffle" if args.apply else "Music shuffle (dry-run)")
    console.print(f"Mode: {args.mode}")
    if not args.apply:
        console.print("[yellow]Dry-run.[/yellow] Add --apply to set shuffle.")
        return 0
    if _guard(lambda: applescript.set_shuffle(args.mode)) is None:
        return 2
    console.print(f"[green]Shuffle[/green] {args.mode}")
    return 0


def cmd_repeat(args: argparse.Namespace) -> int:
    console.rule("Music repeat" if args.apply else "Music repeat (dry-run)")
    console.print(f"Mode: {args.mode}")
    if not args.apply:
        console.print("[yellow]Dry-run.[/yellow] Add --apply to set repeat.")
        return 0
    if _guard(lambda: applescript.set_repeat(args.mode)) is None:
        return 2
    console.print(f"[green]Repeat[/green] {args.mode}")
    return 0


def cmd_play(args: argparse.Namespace) -> int:
    console.rule("Music play" if args.apply else "Music play (dry-run)")
    console.print(f"Query: {args.query}")
    if not args.apply:
        console.print("[yellow]Dry-run.[/yellow] Add --apply to play the first match.")
        return 0
    if _guard(lambda: applescript.play_by_name(args.query)) is None:
        return 2
    console.print(f"[green]Playing[/green] {args.query}")
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    sp = sub.add_parser("now", help="Show now-playing (does not launch Music).")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_now)

    sp = sub.add_parser(
        "search",
        help="Search Music.app library and print names (does not play).",
    )
    sp.add_argument("--query", required=True)
    sp.add_argument("--limit", type=int, default=applescript.MAX_SEARCH_RESULTS)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("playpause", help="Toggle play/pause. Requires --apply.")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_playpause)

    sp = sub.add_parser("next", help="Skip to the next track. Requires --apply.")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_next)

    sp = sub.add_parser("previous", help="Skip to the previous track. Requires --apply.")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_previous)

    sp = sub.add_parser("volume", help="Set Music.app volume 0-100. Requires --apply.")
    sp.add_argument("--level", type=int, required=True)
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_volume)

    sp = sub.add_parser("shuffle", help="Set Music.app shuffle mode. Requires --apply.")
    sp.add_argument("--mode", required=True, choices=sorted(applescript.ALLOWED_SHUFFLE))
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_shuffle)

    sp = sub.add_parser("repeat", help="Set Music.app repeat mode. Requires --apply.")
    sp.add_argument("--mode", required=True, choices=sorted(applescript.ALLOWED_REPEAT))
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_repeat)

    sp = sub.add_parser("play", help="Play the first Music.app track matching a query. Requires --apply.")
    sp.add_argument("--query", required=True)
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_play)


__all__ = ["register"]
