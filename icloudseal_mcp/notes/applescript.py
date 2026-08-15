"""iCloud Notes access via AppleScript (Notes.app).

Notes has no remote API and its on-disk store is an opaque gzipped protobuf, so
the reliable interface is AppleScript automation of Notes.app. The first run
triggers a macOS Automation permission prompt for the controlling app.
"""

from __future__ import annotations

import html
import re
import subprocess
from dataclasses import dataclass

FIELD = "\x1f"
RECORD = "\x1e"


class NotesError(RuntimeError):
    pass


@dataclass(frozen=True)
class Note:
    id: str
    name: str
    modified: str


def _run(script: str, *args: str) -> str:
    result = subprocess.run(
        ["osascript", "-e", script, "--", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise NotesError(result.stderr.strip() or "osascript failed")
    return result.stdout


def strip_html(body: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", body, flags=re.I)
    text = re.sub(r"</(div|p|h1|h2|h3|li)>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def list_notes() -> list[Note]:
    script = (
        'tell application "Notes"\n'
        "  set output to \"\"\n"
        "  repeat with n in notes\n"
        f'    set output to output & (id of n) & "{FIELD}" & (name of n) & "{FIELD}" '
        f'& (modification date of n as string) & "{RECORD}"\n'
        "  end repeat\n"
        "  return output\n"
        "end tell"
    )
    raw = _run(script)
    notes: list[Note] = []
    for rec in raw.split(RECORD):
        if not rec.strip():
            continue
        parts = rec.split(FIELD)
        if len(parts) >= 3:
            notes.append(Note(parts[0], parts[1], parts[2]))
    return notes


def read_note(note_id: str) -> str:
    script = """on run argv
    set noteID to item 1 of argv
    tell application "Notes" to return body of note id noteID
end run"""
    body = _run(script, note_id)
    return strip_html(body)


def create_note(title: str, body: str) -> None:
    content = f"{title}\n{body}" if body else title
    content_html = html.escape(content).replace("\n", "<br>")
    script = """on run argv
    set noteBody to item 1 of argv
    tell application "Notes"
        tell account "iCloud"
            make new note with properties {body:noteBody}
        end tell
    end tell
end run"""
    _run(script, content_html)


def update_note(note_id: str, *, title: str | None = None, body: str | None = None) -> None:
    if title is None and body is None:
        raise NotesError("Provide at least one of title or body to update.")
    body_html = html.escape(body).replace("\n", "<br>") if body is not None else ""
    script = """on run argv
    set noteID to item 1 of argv
    set newTitle to item 2 of argv
    set newBody to item 3 of argv
    set hasTitle to item 4 of argv
    set hasBody to item 5 of argv
    tell application "Notes"
        if hasTitle is "1" then
            set name of note id noteID to newTitle
        end if
        if hasBody is "1" then
            set body of note id noteID to newBody
        end if
    end tell
end run"""
    _run(
        script,
        note_id,
        title or "",
        body_html,
        "1" if title is not None else "0",
        "1" if body is not None else "0",
    )


def delete_note(note_id: str) -> None:
    script = """on run argv
    set noteID to item 1 of argv
    tell application "Notes" to delete note id noteID
end run"""
    _run(script, note_id)
