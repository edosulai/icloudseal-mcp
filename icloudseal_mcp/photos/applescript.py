"""Sealed Photos.app mutations via AppleScript.

Reads stay on Photos.sqlite. Favorite/album changes go through argv-only
scripts. Import/upload is not implemented: PhotoKit/TCC plus offloaded
originals make a sealed write path unreliable.
"""

from __future__ import annotations

import subprocess

MAX_NAME_LEN = 200


class PhotosScriptError(RuntimeError):
    pass


def _run(script: str, *args: str) -> str:
    result = subprocess.run(
        ["osascript", "-e", script, "--", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise PhotosScriptError(result.stderr.strip() or "osascript failed")
    return result.stdout


def _clean_name(value: str, field: str) -> str:
    name = (value or "").strip()
    if not name:
        raise PhotosScriptError(f"{field} is required.")
    if any(ch in name for ch in "\r\n\x00"):
        raise PhotosScriptError(f"{field} must not contain control characters.")
    if len(name) > MAX_NAME_LEN:
        raise PhotosScriptError(f"{field} is limited to {MAX_NAME_LEN} characters.")
    return name


def set_favorite(filename: str, *, favorite: bool) -> None:
    """Toggle favorite on the first media item whose filename matches."""
    name = _clean_name(filename, "filename")
    script = """on run argv
    set theName to item 1 of argv
    set flag to item 2 of argv
    tell application "Photos"
        set theItems to (every media item whose filename is theName)
        if (count of theItems) is 0 then error "No Photos item with that filename"
        set favorite of item 1 of theItems to (flag is "1")
    end tell
end run"""
    _run(script, name, "1" if favorite else "0")


def add_to_album(filename: str, album: str) -> None:
    """Add the first matching media item to an existing album by title."""
    name = _clean_name(filename, "filename")
    album_name = _clean_name(album, "album")
    script = """on run argv
    set theName to item 1 of argv
    set albumName to item 2 of argv
    tell application "Photos"
        set theItems to (every media item whose filename is theName)
        if (count of theItems) is 0 then error "No Photos item with that filename"
        set theAlbums to (every album whose name is albumName)
        if (count of theAlbums) is 0 then error "No Photos album with that name"
        add {item 1 of theItems} to item 1 of theAlbums
    end tell
end run"""
    _run(script, name, album_name)


def create_album(album: str) -> None:
    """Create an empty Photos album by title. Does not import photos."""
    album_name = _clean_name(album, "album")
    script = """on run argv
    set albumName to item 1 of argv
    tell application "Photos"
        set theAlbums to (every album whose name is albumName)
        if (count of theAlbums) is greater than 0 then error "Photos album already exists"
        make new album named albumName
    end tell
end run"""
    _run(script, album_name)


def remove_from_album(filename: str, album: str) -> None:
    """Remove the first matching media item from a named album. Does not delete the asset."""
    name = _clean_name(filename, "filename")
    album_name = _clean_name(album, "album")
    script = """on run argv
    set theName to item 1 of argv
    set albumName to item 2 of argv
    tell application "Photos"
        set theAlbums to (every album whose name is albumName)
        if (count of theAlbums) is 0 then error "No Photos album with that name"
        set theAlbum to item 1 of theAlbums
        set theItems to (every media item of theAlbum whose filename is theName)
        if (count of theItems) is 0 then error "No Photos item with that filename in the album"
        remove {item 1 of theItems} from theAlbum
    end tell
end run"""
    _run(script, name, album_name)


def delete_album(album: str) -> None:
    """Delete an existing Photos album by title. Photos stay in the library."""
    album_name = _clean_name(album, "album")
    script = """on run argv
    set albumName to item 1 of argv
    tell application "Photos"
        set theAlbums to (every album whose name is albumName)
        if (count of theAlbums) is 0 then error "No Photos album with that name"
        delete item 1 of theAlbums
    end tell
end run"""
    _run(script, album_name)
