"""Music.app access via AppleScript.

Reads never launch Music. Playback commands are constant scripts with no
user interpolation. Search / play-by-name is a separate argv-only helper.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Any

FIELD = "\x1f"

ALLOWED_PLAYBACK = frozenset({"playpause", "next", "previous"})
ALLOWED_SHUFFLE = frozenset({"off", "songs", "albums", "groupings"})
ALLOWED_REPEAT = frozenset({"off", "one", "all"})
MAX_SEARCH_LEN = 200

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


def set_volume(level: object) -> None:
    if isinstance(level, bool) or not isinstance(level, int) or level < 0 or level > 100:
        raise MusicError("volume must be an integer 0-100.")
    script = """on run argv
    set theLevel to item 1 of argv as integer
    tell application "Music" to set sound volume to theLevel
end run"""
    _run(script, str(level))


def set_shuffle(mode: str) -> None:
    flag = (mode or "").strip().lower()
    if flag not in ALLOWED_SHUFFLE:
        raise MusicError("shuffle must be off, songs, albums, or groupings.")
    script = """on run argv
    set theMode to item 1 of argv
    tell application "Music"
        if theMode is "off" then
            set shuffle enabled to false
        else
            set shuffle enabled to true
            set shuffle mode to songs
            if theMode is "albums" then set shuffle mode to albums
            if theMode is "groupings" then set shuffle mode to groupings
        end if
    end tell
end run"""
    _run(script, flag)


def set_repeat(mode: str) -> None:
    flag = (mode or "").strip().lower()
    if flag not in ALLOWED_REPEAT:
        raise MusicError("repeat must be off, one, or all.")
    script = """on run argv
    set theMode to item 1 of argv
    tell application "Music"
        if theMode is "off" then
            set song repeat to off
        else if theMode is "one" then
            set song repeat to one
        else
            set song repeat to all
        end if
    end tell
end run"""
    _run(script, flag)


def play_by_name(query: str) -> None:
    candidate = (query or "").strip()
    if not candidate:
        raise MusicError("Search query is required.")
    if any(ch in candidate for ch in "\r\n\x00"):
        raise MusicError("Search query must not contain control characters.")
    if len(candidate) > MAX_SEARCH_LEN:
        raise MusicError(f"Search query is limited to {MAX_SEARCH_LEN} characters.")
    script = """on run argv
    set theQuery to item 1 of argv
    tell application "Music"
        set theResults to search playlist 1 for theQuery
        if (count of theResults) is 0 then error "No matching track"
        play item 1 of theResults
    end tell
end run"""
    _run(script, candidate)


RECORD = "\x1e"
MAX_SEARCH_RESULTS = 20
MAX_PLAYLISTS = 100


def search_tracks(query: str, *, limit: int = MAX_SEARCH_RESULTS) -> list[dict[str, Any]]:
    """Search Music.app library and return names only. Never plays a track."""
    candidate = (query or "").strip()
    if not candidate:
        raise MusicError("Search query is required.")
    if any(ch in candidate for ch in "\r\n\x00"):
        raise MusicError("Search query must not contain control characters.")
    if len(candidate) > MAX_SEARCH_LEN:
        raise MusicError(f"Search query is limited to {MAX_SEARCH_LEN} characters.")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_SEARCH_RESULTS:
        raise MusicError(f"limit must be an integer from 1 to {MAX_SEARCH_RESULTS}.")
    if not music_is_running():
        raise MusicError("Music is not running.")
    script = """on run argv
    set theQuery to item 1 of argv
    set theLimit to item 2 of argv as integer
    tell application "Music"
        set theResults to search playlist 1 for theQuery
        set output to ""
        set n to count of theResults
        if n is 0 then return output
        if n > theLimit then set n to theLimit
        repeat with i from 1 to n
            set theTrack to item i of theResults
            set output to output & (name of theTrack) & "\x1f" & (artist of theTrack) & "\x1f" & (album of theTrack) & "\x1e"
        end repeat
        return output
    end tell
end run"""
    raw = _run(script, candidate, str(limit))
    rows: list[dict[str, Any]] = []
    for rec in raw.split(RECORD):
        if not rec.strip():
            continue
        parts = rec.split(FIELD)
        rows.append(
            {
                "name": _optional(parts[0]) if parts else None,
                "artist": _optional(parts[1]) if len(parts) > 1 else None,
                "album": _optional(parts[2]) if len(parts) > 2 else None,
            }
        )
    return rows


def list_playlists(*, limit: int = 50) -> list[str]:
    """Return user-facing Music playlist names. Does not start playback."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PLAYLISTS:
        raise MusicError(f"limit must be an integer from 1 to {MAX_PLAYLISTS}.")
    if not music_is_running():
        raise MusicError("Music is not running.")
    script = """on run argv
    set theLimit to item 1 of argv as integer
    tell application "Music"
        set theLists to user playlists
        set output to ""
        set n to count of theLists
        if n is 0 then return output
        if n > theLimit then set n to theLimit
        repeat with i from 1 to n
            set output to output & (name of item i of theLists) & "\x1e"
        end repeat
        return output
    end tell
end run"""
    raw = _run(script, str(limit))
    return [name for name in (part.strip() for part in raw.split(RECORD)) if name]


def play_playlist(name: str) -> None:
    """Play one Music playlist by exact name. Name goes through argv only."""
    candidate = (name or "").strip()
    if not candidate:
        raise MusicError("playlist name is required.")
    if any(ch in candidate for ch in "\r\n\x00"):
        raise MusicError("playlist name must not contain control characters.")
    if len(candidate) > MAX_SEARCH_LEN:
        raise MusicError(f"playlist name is limited to {MAX_SEARCH_LEN} characters.")
    script = """on run argv
    set theName to item 1 of argv
    tell application "Music"
        set theLists to (every user playlist whose name is theName)
        if (count of theLists) is 0 then error "No Music playlist with that name"
        play item 1 of theLists
    end tell
end run"""
    _run(script, candidate)
