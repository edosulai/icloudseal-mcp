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


def _run(script: str) -> str:
    result = subprocess.run(
        ["osascript", "-e", script], capture_output=True, text=True, check=False
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
    esc = note_id.replace('"', '\\"')
    body = _run(f'tell application "Notes" to return body of note id "{esc}"')
    return strip_html(body)


def create_note(title: str, body: str) -> None:
    content = f"{title}\n{body}" if body else title
    esc = content.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "<br>")
    script = (
        'tell application "Notes"\n'
        '  tell account "iCloud"\n'
        f'    make new note with properties {{body:"{esc}"}}\n'
        "  end tell\n"
        "end tell"
    )
    _run(script)


def delete_note(note_id: str) -> None:
    esc = note_id.replace('"', '\\"')
    _run(f'tell application "Notes" to delete note id "{esc}"')
