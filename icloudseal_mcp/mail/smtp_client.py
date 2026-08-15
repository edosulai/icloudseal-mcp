"""SMTP sender for iCloud Mail.

iCloud SMTP: smtp.mail.me.com:587 (STARTTLS). The same Keychain app-specific
password used for IMAP authenticates SMTP. No attachments in this first cut.
"""

from __future__ import annotations

import re
import smtplib
from collections.abc import Iterable
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from typing import Any

SMTP_HOST = "smtp.mail.me.com"
SMTP_PORT = 587
SMTP_TIMEOUT_S = 30

MAX_SUBJECT_CHARS = 200
MAX_BODY_CHARS = 20_000
MAX_RECIPIENTS = 20

_EMAIL_RE = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")


class SMTPError(RuntimeError):
    """Raised when a send cannot be prepared or delivered."""


def _single_line(value: str, field: str) -> str:
    if any(char in value for char in "\r\n\x00"):
        raise SMTPError(f"{field} cannot contain newlines or NUL.")
    return value.strip()


def normalize_addresses(values: Iterable[str] | str | None, *, field: str) -> list[str]:
    """Parse a comma/semicolon list or iterable into unique bare addresses."""
    if values is None:
        return []
    if isinstance(values, str):
        parts = [part.strip() for part in values.replace(";", ",").split(",")]
    else:
        parts = [str(part).strip() for part in values]

    out: list[str] = []
    for part in parts:
        if not part:
            continue
        _single_line(part, field)
        _name, addr = parseaddr(part)
        addr = addr.strip()
        if not addr or not _EMAIL_RE.match(addr):
            raise SMTPError(f"Invalid {field} address: {part!r}")
        if addr not in out:
            out.append(addr)
    return out


def freeze_send(
    *,
    from_addr: str,
    to: Iterable[str] | str,
    subject: str,
    body: str,
    cc: Iterable[str] | str | None = None,
    bcc: Iterable[str] | str | None = None,
    message_id: str | None = None,
) -> dict[str, Any]:
    """Validate and freeze an outbound message. Does not contact SMTP."""
    sender = normalize_addresses([from_addr], field="from")
    if len(sender) != 1:
        raise SMTPError("A single From address is required.")
    recipients_to = normalize_addresses(to, field="to")
    recipients_cc = normalize_addresses(cc, field="cc")
    recipients_bcc = normalize_addresses(bcc, field="bcc")
    if not recipients_to:
        raise SMTPError("At least one To recipient is required.")
    total = len(recipients_to) + len(recipients_cc) + len(recipients_bcc)
    if total > MAX_RECIPIENTS:
        raise SMTPError(f"Too many recipients ({total}); max is {MAX_RECIPIENTS}.")

    subject_text = _single_line(subject, "subject")
    if not subject_text:
        raise SMTPError("Subject cannot be empty.")
    if len(subject_text) > MAX_SUBJECT_CHARS:
        raise SMTPError(f"Subject exceeds {MAX_SUBJECT_CHARS} characters.")

    if "\x00" in body:
        raise SMTPError("Body cannot contain NUL.")
    if len(body) > MAX_BODY_CHARS:
        raise SMTPError(f"Body exceeds {MAX_BODY_CHARS} characters.")
    if not body.strip():
        raise SMTPError("Body cannot be empty.")

    mid = message_id or make_msgid(domain="icloudseal-mcp.local")
    _single_line(mid, "message-id")

    return {
        "from": sender[0],
        "to": recipients_to,
        "cc": recipients_cc,
        "bcc": recipients_bcc,
        "subject": subject_text,
        "body": body,
        "messageId": mid,
    }


def build_message(frozen: dict[str, Any]) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = frozen["from"]
    msg["To"] = ", ".join(frozen["to"])
    if frozen.get("cc"):
        msg["Cc"] = ", ".join(frozen["cc"])
    msg["Subject"] = frozen["subject"]
    msg["Message-ID"] = frozen["messageId"]
    msg["Date"] = formatdate(localtime=True)
    msg.set_content(frozen["body"])
    return msg


def envelope_recipients(frozen: dict[str, Any]) -> list[str]:
    return [
        *frozen["to"],
        *(frozen.get("cc") or []),
        *(frozen.get("bcc") or []),
    ]


def send_frozen(
    frozen: dict[str, Any],
    *,
    password: str,
    smtp_factory: Any = None,
) -> dict[str, Any]:
    """Deliver a previously frozen message. ``smtp_factory`` is for tests."""
    message = build_message(frozen)
    recipients = envelope_recipients(frozen)
    factory = smtp_factory or (
        lambda: smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT_S)
    )
    try:
        with factory() as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(frozen["from"], password)
            smtp.send_message(message, from_addr=frozen["from"], to_addrs=recipients)
    except SMTPError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise SMTPError(f"SMTP send failed: {exc}") from exc
    return {
        "from": frozen["from"],
        "to": frozen["to"],
        "cc": frozen["cc"],
        "bcc": frozen["bcc"],
        "subject": frozen["subject"],
        "messageId": frozen["messageId"],
        "recipients": len(recipients),
    }


__all__ = [
    "MAX_BODY_CHARS",
    "MAX_RECIPIENTS",
    "MAX_SUBJECT_CHARS",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTPError",
    "build_message",
    "envelope_recipients",
    "freeze_send",
    "send_frozen",
]
