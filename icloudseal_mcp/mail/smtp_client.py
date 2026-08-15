"""SMTP sender for iCloud Mail.

iCloud SMTP: smtp.mail.me.com:587 (STARTTLS). The same Keychain app-specific
password used for IMAP authenticates SMTP. Attachments are size-capped and
must already be frozen as local path snapshots.
"""

from __future__ import annotations

import mimetypes
import re
import smtplib
from collections.abc import Iterable
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from pathlib import Path
from typing import Any

SMTP_HOST = "smtp.mail.me.com"
SMTP_PORT = 587
SMTP_TIMEOUT_S = 30

MAX_SUBJECT_CHARS = 200
MAX_BODY_CHARS = 20_000
MAX_RECIPIENTS = 20
MAX_ATTACHMENTS = 5
MAX_ATTACHMENT_BYTES = 5_000_000
MAX_HEADER_CHARS = 200

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


def _header_token(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    token = _single_line(value, field)
    if not token:
        return None
    if len(token) > MAX_HEADER_CHARS:
        raise SMTPError(f"{field} exceeds {MAX_HEADER_CHARS} characters.")
    return token


def _freeze_attachments(values: Iterable[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not values:
        return []
    out: list[dict[str, Any]] = []
    for item in values:
        if not isinstance(item, dict):
            raise SMTPError("Each attachment must be an object.")
        path = Path(str(item.get("path") or "")).expanduser()
        name = _single_line(str(item.get("name") or path.name), "attachment name")
        if not name or "/" in name or "\\" in name:
            raise SMTPError("Attachment name must be a basename.")
        try:
            size = int(item.get("size") or 0)
        except (TypeError, ValueError) as exc:
            raise SMTPError("Attachment size must be an integer.") from exc
        sha = _single_line(str(item.get("sha256") or ""), "attachment sha256")
        if size <= 0 or size > MAX_ATTACHMENT_BYTES:
            raise SMTPError(
                f"Attachment {name!r} exceeds the {MAX_ATTACHMENT_BYTES} byte cap."
            )
        if len(sha) != 64 or any(ch not in "0123456789abcdef" for ch in sha.lower()):
            raise SMTPError("Attachment sha256 must be a 64-char hex digest.")
        out.append(
            {
                "path": str(path),
                "name": name,
                "size": size,
                "sha256": sha.lower(),
            }
        )
    if len(out) > MAX_ATTACHMENTS:
        raise SMTPError(f"Too many attachments ({len(out)}); max is {MAX_ATTACHMENTS}.")
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
    in_reply_to: str | None = None,
    references: str | None = None,
    attachments: Iterable[dict[str, Any]] | None = None,
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
    reply = _header_token(in_reply_to, "in-reply-to")
    refs = _header_token(references, "references")
    frozen_attachments = _freeze_attachments(attachments)

    return {
        "from": sender[0],
        "to": recipients_to,
        "cc": recipients_cc,
        "bcc": recipients_bcc,
        "subject": subject_text,
        "body": body,
        "messageId": mid,
        "inReplyTo": reply,
        "references": refs,
        "attachments": frozen_attachments,
    }


def build_message(frozen: dict[str, Any]) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = frozen["from"]
    msg["To"] = ", ".join(frozen["to"])
    if frozen.get("cc"):
        msg["Cc"] = ", ".join(frozen["cc"])
    msg["Subject"] = frozen["subject"]
    msg["Message-ID"] = frozen["messageId"]
    if frozen.get("inReplyTo"):
        msg["In-Reply-To"] = frozen["inReplyTo"]
    if frozen.get("references"):
        msg["References"] = frozen["references"]
    msg["Date"] = formatdate(localtime=True)
    msg.set_content(frozen["body"])
    for item in frozen.get("attachments") or []:
        path = Path(item["path"])
        data = path.read_bytes()
        ctype, _encoding = mimetypes.guess_type(item["name"])
        maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
        msg.add_attachment(
            data,
            maintype=maintype,
            subtype=subtype,
            filename=item["name"],
        )
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
    "MAX_ATTACHMENT_BYTES",
    "MAX_ATTACHMENTS",
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
