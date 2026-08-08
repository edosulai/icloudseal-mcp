"""Read-only access to the local Messages database (iMessage + SMS).

iCloud exposes no messaging API. Messages synced to this Mac live in
``~/Library/Messages/chat.db`` (SQLite). Reading it requires the running
process to have **Full Disk Access** (System Settings → Privacy & Security →
Full Disk Access). We open the DB read-only and immutable so a locked/WAL
database can still be read without disturbing Messages.app.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

CHAT_DB = Path.home() / "Library" / "Messages" / "chat.db"
# Apple Cocoa epoch: 2001-01-01 UTC. Modern macOS stores nanoseconds.
_APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=UTC)


class MessagesAccessError(RuntimeError):
    """Raised when chat.db cannot be opened (usually missing Full Disk Access)."""


@dataclass(frozen=True)
class Chat:
    identifier: str
    display_name: str
    last_iso: str
    count: int


@dataclass(frozen=True)
class Msg:
    date_iso: str
    from_me: bool
    handle: str
    text: str
    service: str


def _apple_to_iso(value: int | None) -> str:
    if not value:
        return ""
    seconds = value / 1_000_000_000 if value > 1_000_000_000_000 else value
    try:
        return (_APPLE_EPOCH + timedelta(seconds=seconds)).astimezone().isoformat()
    except (OverflowError, OSError):
        return ""


def _connect() -> sqlite3.Connection:
    if not CHAT_DB.exists():
        raise MessagesAccessError(f"{CHAT_DB} not found.")
    try:
        con = sqlite3.connect(f"file:{CHAT_DB}?mode=ro&immutable=1", uri=True)
        con.row_factory = sqlite3.Row
        con.execute("SELECT 1 FROM message LIMIT 1")
        return con
    except sqlite3.OperationalError as exc:
        raise MessagesAccessError(
            "Cannot read chat.db. Grant Full Disk Access to your terminal/app: "
            "System Settings → Privacy & Security → Full Disk Access."
        ) from exc


def _attributed_text(blob: bytes | None) -> str:
    """Best-effort extraction of message text from the streamtyped blob.

    Newer macOS leaves ``message.text`` NULL and stores the body in
    ``attributedBody``. This recovers the plain text for the common case.
    """
    if not blob:
        return ""
    marker = blob.find(b"NSString")
    if marker == -1:
        return ""
    chunk = blob[marker + 8 :]
    plus = chunk.find(b"\x2b")
    if plus == -1 or plus + 1 >= len(chunk):
        return ""
    p = plus + 1
    if chunk[p] == 0x81:  # 2-byte little-endian length prefix
        if p + 3 > len(chunk):
            return ""
        length = int.from_bytes(chunk[p + 1 : p + 3], "little")
        start = p + 3
    else:
        length = chunk[p]
        start = p + 1
    return chunk[start : start + length].decode("utf-8", errors="replace")


def _text_of(row: sqlite3.Row) -> str:
    return (row["text"] or "").strip() or _attributed_text(row["attributedBody"])


def list_chats(limit: int = 30) -> list[Chat]:
    with _connect() as con:
        rows = con.execute(
            """
            SELECT c.chat_identifier AS ident,
                   COALESCE(c.display_name, '') AS name,
                   MAX(m.date) AS last,
                   COUNT(*) AS n
            FROM chat c
            JOIN chat_message_join cmj ON cmj.chat_id = c.ROWID
            JOIN message m ON m.ROWID = cmj.message_id
            GROUP BY c.ROWID
            ORDER BY last DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [
        Chat(r["ident"] or "(unknown)", r["name"], _apple_to_iso(r["last"]), r["n"])
        for r in rows
    ]


def chat_messages(selector: str, limit: int = 40) -> list[Msg]:
    like = f"%{selector}%"
    with _connect() as con:
        rows = con.execute(
            """
            SELECT m.date, m.is_from_me, m.text, m.attributedBody,
                   COALESCE(m.service, '') AS service,
                   COALESCE(h.id, '') AS handle
            FROM message m
            JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
            JOIN chat c ON c.ROWID = cmj.chat_id
            LEFT JOIN handle h ON h.ROWID = m.handle_id
            WHERE c.chat_identifier = ? OR c.display_name LIKE ?
            ORDER BY m.date DESC
            LIMIT ?
            """,
            (selector, like, limit),
        ).fetchall()
    out = [
        Msg(_apple_to_iso(r["date"]), bool(r["is_from_me"]), r["handle"], _text_of(r), r["service"])
        for r in rows
    ]
    out.reverse()  # chronological
    return out


def search(query: str, limit: int = 40) -> list[Msg]:
    like = f"%{query}%"
    with _connect() as con:
        rows = con.execute(
            """
            SELECT m.date, m.is_from_me, m.text, m.attributedBody,
                   COALESCE(m.service, '') AS service,
                   COALESCE(h.id, '') AS handle
            FROM message m
            LEFT JOIN handle h ON h.ROWID = m.handle_id
            WHERE m.text LIKE ?
            ORDER BY m.date DESC
            LIMIT ?
            """,
            (like, limit),
        ).fetchall()
    return [
        Msg(_apple_to_iso(r["date"]), bool(r["is_from_me"]), r["handle"], _text_of(r), r["service"])
        for r in rows
    ]
