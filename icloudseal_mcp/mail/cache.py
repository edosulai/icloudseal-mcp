"""Local SQLite cache for email metadata.

Why cache: iCloud IMAP fetch is slow for folders with thousands of messages.
We sync metadata once, then the agent operates on the local DB for triage
decisions. Bodies are still fetched on-demand (not cached) — they're large
and privacy-sensitive.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from ..paths import APP_DIR, DB_PATH, ensure_app_dir
from .imap_client import MessageMeta

# Backward-compatible alias; canonical location lives in icloudseal_mcp.paths.
DB_DIR = APP_DIR

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    folder         TEXT NOT NULL,
    uid            INTEGER NOT NULL,
    sender_email   TEXT NOT NULL,
    sender_name    TEXT NOT NULL,
    subject        TEXT NOT NULL,
    date_iso       TEXT NOT NULL,
    size           INTEGER NOT NULL,
    flags          TEXT NOT NULL,
    list_unsub     TEXT,
    fetched_at     TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (folder, uid)
);

CREATE INDEX IF NOT EXISTS idx_messages_sender ON messages(sender_email);
CREATE INDEX IF NOT EXISTS idx_messages_date   ON messages(date_iso);
CREATE INDEX IF NOT EXISTS idx_messages_folder ON messages(folder);

CREATE TABLE IF NOT EXISTS sync_state (
    folder         TEXT PRIMARY KEY,
    last_sync_iso  TEXT NOT NULL,
    last_uid       INTEGER NOT NULL DEFAULT 0,
    message_count  INTEGER NOT NULL DEFAULT 0
);
"""


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    ensure_app_dir()
    conn = sqlite3.connect(DB_PATH)
    DB_PATH.chmod(0o600)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert_messages(conn: sqlite3.Connection, metas: Iterable[MessageMeta]) -> int:
    rows = [
        (
            m.folder,
            m.uid,
            m.sender_email,
            m.sender_name,
            m.subject,
            m.date_iso,
            m.size,
            ",".join(m.flags),
            m.list_unsubscribe,
        )
        for m in metas
    ]
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT INTO messages
            (folder, uid, sender_email, sender_name, subject, date_iso, size, flags, list_unsub)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(folder, uid) DO UPDATE SET
            sender_email = excluded.sender_email,
            sender_name  = excluded.sender_name,
            subject      = excluded.subject,
            date_iso     = excluded.date_iso,
            size         = excluded.size,
            flags        = excluded.flags,
            list_unsub   = excluded.list_unsub,
            fetched_at   = datetime('now')
        """,
        rows,
    )
    return len(rows)


def update_sync_state(
    conn: sqlite3.Connection, folder: str, last_uid: int, message_count: int
) -> None:
    conn.execute(
        """
        INSERT INTO sync_state (folder, last_sync_iso, last_uid, message_count)
        VALUES (?, datetime('now'), ?, ?)
        ON CONFLICT(folder) DO UPDATE SET
            last_sync_iso = excluded.last_sync_iso,
            last_uid      = excluded.last_uid,
            message_count = excluded.message_count
        """,
        (folder, last_uid, message_count),
    )


def get_last_uid(conn: sqlite3.Connection, folder: str) -> int:
    row = conn.execute(
        "SELECT last_uid FROM sync_state WHERE folder = ?", (folder,)
    ).fetchone()
    return int(row["last_uid"]) if row else 0


def list_messages(
    conn: sqlite3.Connection,
    folder: str,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT uid, sender_email, sender_name, subject, date_iso, size, flags, list_unsub
            FROM messages
            WHERE folder = ?
            ORDER BY date_iso DESC
            LIMIT ? OFFSET ?
            """,
            (folder, limit, offset),
        )
    )


def find_messages(
    conn: sqlite3.Connection,
    *,
    folder: str,
    sender: str | None = None,
    sender_like: str | None = None,
    subject_like: str | None = None,
    older_than_iso: str | None = None,
    has_list_unsubscribe: bool = False,
    limit: int = 100,
) -> list[sqlite3.Row]:
    """Find cached messages for a triage plan.

    Matching stays intentionally simple and SQL-parameterized. More nuanced
    classification belongs in the chat layer after the agent reads candidates.
    """
    clauses = ["folder = ?"]
    params: list[object] = [folder]

    if sender:
        clauses.append("sender_email = ?")
        params.append(sender.lower())
    if sender_like:
        clauses.append("(sender_email LIKE ? OR sender_name LIKE ?)")
        pattern = f"%{sender_like}%"
        params.extend([pattern, pattern])
    if subject_like:
        clauses.append("subject LIKE ?")
        params.append(f"%{subject_like}%")
    if older_than_iso:
        clauses.append("date_iso < ?")
        params.append(older_than_iso)
    if has_list_unsubscribe:
        clauses.append("list_unsub IS NOT NULL AND list_unsub != ''")

    params.append(limit)
    return list(
        conn.execute(
            f"""
            SELECT uid, folder, sender_email, sender_name, subject, date_iso, size, flags,
                   list_unsub
            FROM messages
            WHERE {" AND ".join(clauses)}
            ORDER BY date_iso DESC
            LIMIT ?
            """,
            params,
        )
    )


def remove_cached_messages(
    conn: sqlite3.Connection, *, folder: str, uids: Iterable[int]
) -> int:
    rows = [(folder, uid) for uid in uids]
    if not rows:
        return 0
    conn.executemany("DELETE FROM messages WHERE folder = ? AND uid = ?", rows)
    return len(rows)


def now_utc_iso() -> str:
    return datetime.now(UTC).isoformat()


def top_senders(
    conn: sqlite3.Connection, folder: str | None = None, limit: int = 30
) -> list[sqlite3.Row]:
    if folder:
        return list(
            conn.execute(
                """
                SELECT sender_email, sender_name, COUNT(*) AS n,
                       SUM(size) AS total_bytes,
                       MAX(date_iso) AS latest
                FROM messages
                WHERE folder = ?
                GROUP BY sender_email
                ORDER BY n DESC
                LIMIT ?
                """,
                (folder, limit),
            )
        )
    return list(
        conn.execute(
            """
            SELECT sender_email, sender_name, COUNT(*) AS n,
                   SUM(size) AS total_bytes,
                   MAX(date_iso) AS latest
            FROM messages
            GROUP BY sender_email
            ORDER BY n DESC
            LIMIT ?
            """,
            (limit,),
        )
    )


def folder_stats(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT folder,
                   COUNT(*) AS cached,
                   MIN(date_iso) AS earliest,
                   MAX(date_iso) AS latest
            FROM messages
            GROUP BY folder
            ORDER BY cached DESC
            """
        )
    )
