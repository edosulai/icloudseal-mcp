"""Read-only Safari bookmarks, Reading List, and history.

Bookmarks.plist and History.db live under ~/Library/Safari and need Full Disk
Access. Mutations stay in applescript.py (argv-only). History is never mutated.
"""

from __future__ import annotations

import plistlib
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SAFARI_DIR = Path.home() / "Library" / "Safari"
BOOKMARKS_PLIST = SAFARI_DIR / "Bookmarks.plist"
HISTORY_DB = SAFARI_DIR / "History.db"
_APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=UTC)

READING_LIST_TITLE = "com.apple.ReadingList"
MAX_HISTORY = 200
MAX_BOOKMARKS = 500


class SafariStoreError(RuntimeError):
    """Raised when Safari on-disk stores cannot be read (usually missing FDA)."""


@dataclass(frozen=True)
class SafariBookmark:
    title: str
    url: str
    folder: str
    reading_list: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "folder": self.folder,
            "readingList": self.reading_list,
        }


@dataclass(frozen=True)
class SafariHistoryItem:
    title: str
    url: str
    visited_at: str

    def to_dict(self) -> dict[str, Any]:
        return {"title": self.title, "url": self.url, "visitedAt": self.visited_at}


def _cocoa_to_iso(value: float | None) -> str:
    if not value:
        return ""
    try:
        return (_APPLE_EPOCH + timedelta(seconds=value)).astimezone().isoformat()
    except (OverflowError, OSError):
        return ""


def _walk_bookmarks(
    node: Any,
    *,
    folder: str,
    reading_list: bool,
    out: list[SafariBookmark],
    limit: int,
) -> None:
    if len(out) >= limit or not isinstance(node, dict):
        return
    kind = str(node.get("WebBookmarkType") or "")
    title = str(node.get("Title") or node.get("URIDictionary", {}).get("title") or "")
    url = str(node.get("URLString") or "")
    if kind == "WebBookmarkTypeLeaf" and url:
        out.append(
            SafariBookmark(
                title=title or url,
                url=url,
                folder=folder,
                reading_list=reading_list,
            )
        )
        return
    children = node.get("Children") or []
    if not isinstance(children, list):
        return
    next_folder = title or folder
    next_reading = reading_list or title == READING_LIST_TITLE
    display_folder = "Reading List" if next_reading else (next_folder or folder)
    for child in children:
        _walk_bookmarks(
            child,
            folder=display_folder,
            reading_list=next_reading,
            out=out,
            limit=limit,
        )


def list_bookmarks(*, reading_list: bool | None = None, limit: int = 200) -> list[SafariBookmark]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_BOOKMARKS:
        raise SafariStoreError(f"limit must be an integer from 1 to {MAX_BOOKMARKS}.")
    if not BOOKMARKS_PLIST.exists():
        raise SafariStoreError(f"{BOOKMARKS_PLIST} not found.")
    try:
        with BOOKMARKS_PLIST.open("rb") as handle:
            root = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException) as exc:
        raise SafariStoreError(
            "Cannot read Bookmarks.plist. Grant Full Disk Access to your terminal/app: "
            "System Settings → Privacy & Security → Full Disk Access."
        ) from exc
    items: list[SafariBookmark] = []
    _walk_bookmarks(root, folder="Bookmarks", reading_list=False, out=items, limit=MAX_BOOKMARKS)
    if reading_list is True:
        items = [item for item in items if item.reading_list]
    elif reading_list is False:
        items = [item for item in items if not item.reading_list]
    return items[:limit]


def list_history(*, limit: int = 50) -> list[SafariHistoryItem]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_HISTORY:
        raise SafariStoreError(f"limit must be an integer from 1 to {MAX_HISTORY}.")
    if not HISTORY_DB.exists():
        raise SafariStoreError(f"{HISTORY_DB} not found.")
    con: sqlite3.Connection | None = None
    try:
        con = sqlite3.connect(f"file:{HISTORY_DB}?mode=ro&immutable=1", uri=True)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """
            SELECT hu.url AS url, hi.title AS title, hi.visit_time AS visit_time
            FROM history_visits hi
            JOIN history_items hu ON hi.history_item = hu.id
            WHERE hu.url IS NOT NULL AND hu.url != ''
            ORDER BY hi.visit_time DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        raise SafariStoreError(
            "Cannot read History.db. Grant Full Disk Access to your terminal/app: "
            "System Settings → Privacy & Security → Full Disk Access."
        ) from exc
    finally:
        if con is not None:
            con.close()
    return [
        SafariHistoryItem(
            title=str(row["title"] or row["url"] or ""),
            url=str(row["url"] or ""),
            visited_at=_cocoa_to_iso(row["visit_time"]),
        )
        for row in rows
    ]


__all__ = [
    "BOOKMARKS_PLIST",
    "HISTORY_DB",
    "SafariBookmark",
    "SafariHistoryItem",
    "SafariStoreError",
    "list_bookmarks",
    "list_history",
]
