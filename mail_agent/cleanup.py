"""Strict mailbox cleanup policies.

This module keeps bulk-mail classification explicit and reviewable. It avoids
semantic guessing for destructive cleanup; only known noisy sender addresses are
included in the strict policy.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

STRICT_BULK_SENDERS = frozenset(
    {
        "newsletters-noreply@linkedin.com",
        "noreply@e.jobstreet.com",
        "jobalerts-noreply@linkedin.com",
        "donotreply@jobalert.indeed.com",
        "alert@indeed.com",
        "newsletter@techinasia.com",
        "noreply@medium.com",
        "newsletters@medium.com",
        "recommendations@discover.pinterest.com",
        "recommendations@explore.pinterest.com",
        "recommendations@inspire.pinterest.com",
        "pinterest-recommendations@ideas.pinterest.com",
        "hello@promotion.indodana.id",
        "learn@itr.mail.codecademy.com",
        "enews@email.erafone.com",
        "support@team.brain.fm",
        "hello@news.railway.app",
        "permatahighlights@permatabank.co.id",
        "hello@mail.promova.com",
    }
)


STRICT_POLICY_NOTE = (
    "strict bulk/promo/job-alert senders only; excludes account/security, "
    "receipts, GitHub, financial transactions, and human LinkedIn messages"
)


@dataclass(frozen=True)
class SenderSummary:
    sender_email: str
    sender_name: str
    count: int
    earliest: str
    latest: str


def strict_bulk_messages(conn: sqlite3.Connection, *, folder: str) -> list[sqlite3.Row]:
    """Return cached messages matched by the strict bulk sender allowlist."""
    senders = sorted(STRICT_BULK_SENDERS)
    placeholders = ",".join("?" for _ in senders)
    return list(
        conn.execute(
            f"""
            SELECT uid, folder, sender_email, sender_name, subject, date_iso, size, flags,
                   list_unsub
            FROM messages
            WHERE folder = ? AND sender_email IN ({placeholders})
            ORDER BY date_iso DESC
            """,
            [folder, *senders],
        )
    )


def strict_sender_summary(conn: sqlite3.Connection, *, folder: str) -> list[SenderSummary]:
    """Summarize strict cleanup candidates by sender for review output."""
    senders = sorted(STRICT_BULK_SENDERS)
    placeholders = ",".join("?" for _ in senders)
    rows = conn.execute(
        f"""
        SELECT sender_email, sender_name, COUNT(*) AS n,
               MIN(date_iso) AS earliest,
               MAX(date_iso) AS latest
        FROM messages
        WHERE folder = ? AND sender_email IN ({placeholders})
        GROUP BY sender_email
        ORDER BY n DESC
        """,
        [folder, *senders],
    )
    return [
        SenderSummary(
            sender_email=row["sender_email"],
            sender_name=row["sender_name"],
            count=int(row["n"]),
            earliest=row["earliest"],
            latest=row["latest"],
        )
        for row in rows
    ]
