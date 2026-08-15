"""Music.app access via AppleScript.

Reads never launch Music. Playback commands are constant scripts with no
user interpolation. Search / play-by-name is intentionally not exposed.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Any

FIELD = "\x1f"

ALLOWED_PLAYBACK = frozenset({"playpause", "next", "previous"})

PLAYBACK_SCRIPTS = {
    "playpause": 'tell application "Music" to playpause',
    "next": 'tell application "Music" to next track',
    "previous": 'tell application "Music" to previous track',
}


class MusicError(RuntimeError):
    pass


@dataclass(frozen=True)
class NowPlaying:
    state: str
    name: str | None = None
    artist: str | None = None
    album: str | None = None
    duration_sec: float | None = None
    position_sec: float | None = None
    persistent_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"state": self.state}
        if self.state == "stopped":
            return payload
        if self.name is not None:
            payload["name"] = self.name
        if self.artist is not None:
            payload["artist"] = self.artist
        if self.album is not None:
            payload["album"] = self.album
        if self.duration_sec is not None:
            payload["duration_sec"] = self.duration_sec
        if self.position_sec is not None:
            payload["position_sec"] = self.position_sec
        if self.persistent_id is not None:
            payload["persistent_id"] = self.persistent_id
        return payload


def _run(script: str, *args: str) -> str:
    result = subprocess.run(
        ["osascript", "-e", script, "--", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise MusicError(result.stderr.strip() or "osascript failed")
    return result.stdout


def music_is_running() -> bool:
    script = (
        'tell application "System Events" to '
        '(name of processes) contains "Music"'
    )
    return _run(script).strip().lower() == "true"


def _optional(value: str) -> str | None:
    text = value.strip()
    if not text or text.lower() == "missing value":
        return None
    return text


def _optional_float(value: str) -> float | None:
    text = _optional(value)
    if text is None:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def now_playing() -> NowPlaying:
    """Return current playback. Does not launch Music.app."""
    if not music_is_running():
        return NowPlaying(state="stopped")
    script = (
        'tell application "Music"\n'
        "  set theState to player state as string\n"
        '  if theState is "stopped" then return theState\n'
        "  set theName to name of current track\n"
        "  set theArtist to artist of current track\n"
        "  set theAlbum to album of current track\n"
        "  set theDuration to duration of current track\n"
        "  set thePosition to player position\n"
        "  set theId to persistent ID of current track\n"
        f'  return theState & "{FIELD}" & theName & "{FIELD}" & theArtist '
        f'& "{FIELD}" & theAlbum & "{FIELD}" & theDuration & "{FIELD}" '
        f'& thePosition & "{FIELD}" & theId\n'
        "end tell"
    )
    raw = _run(script).strip()
    if not raw or raw == "stopped":
        return NowPlaying(state="stopped")
    parts = raw.split(FIELD)
    state = (parts[0] if parts else "stopped").strip().lower() or "stopped"
    if state == "stopped":
        return NowPlaying(state="stopped")
    name = _optional(parts[1]) if len(parts) > 1 else None
    artist = _optional(parts[2]) if len(parts) > 2 else None
    album = _optional(parts[3]) if len(parts) > 3 else None
    duration = _optional_float(parts[4]) if len(parts) > 4 else None
    position = _optional_float(parts[5]) if len(parts) > 5 else None
    persistent_id = _optional(parts[6]) if len(parts) > 6 else None
    return NowPlaying(
        state=state,
        name=name,
        artist=artist,
        album=album,
        duration_sec=duration,
        position_sec=position,
        persistent_id=persistent_id,
    )


def playback(action: str) -> None:
    if action not in ALLOWED_PLAYBACK:
        raise MusicError("playback action must be playpause, next, or previous.")
    _run(PLAYBACK_SCRIPTS[action])
