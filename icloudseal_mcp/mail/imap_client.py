"""IMAP wrapper for iCloud Mail.

Thin layer over `imaplib.IMAP4_SSL` providing:
- Context-manager lifecycle
- Folder listing (with iCloud's namespace quirks handled)
- UID-based search and metadata fetch
- Body fetch on demand
- Move/copy/flag operations (used in Phase 2)

iCloud IMAP endpoint: imap.mail.me.com:993 (SSL)
SMTP send lives in smtp_client.py: smtp.mail.me.com:587 (STARTTLS)
"""

from __future__ import annotations

import email
import imaplib
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime

IMAP_HOST = "imap.mail.me.com"
IMAP_PORT = 993

# imaplib has a 10K default; iCloud can return more. Bump it.
imaplib._MAXLINE = 10_000_000


class IMAPError(RuntimeError):
    """Raised when an IMAP operation fails."""


@dataclass(frozen=True)
class FolderInfo:
    name: str          # raw IMAP name (may contain "/" namespace)
    delimiter: str
    flags: tuple[str, ...]


@dataclass(frozen=True)
class MessageMeta:
    uid: int
    folder: str
    sender_name: str
    sender_email: str
    subject: str
    date_iso: str       # ISO 8601, UTC
    size: int
    flags: tuple[str, ...]
    list_unsubscribe: str | None


_LIST_RE = re.compile(rb'\((?P<flags>.*?)\) "(?P<delim>.*?)" (?P<name>.+)')


def _decode_header_value(raw: str | None) -> str:
    """Decode RFC2047-encoded header values to unicode."""
    if not raw:
        return ""
    parts = email.header.decode_header(raw)
    out = []
    for chunk, charset in parts:
        if isinstance(chunk, bytes):
            try:
                out.append(chunk.decode(charset or "utf-8", errors="replace"))
            except (LookupError, TypeError):
                out.append(chunk.decode("utf-8", errors="replace"))
        else:
            out.append(chunk)
    return "".join(out).strip()


def _parse_folder_line(line: bytes) -> FolderInfo | None:
    m = _LIST_RE.match(line)
    if not m:
        return None
    flags = tuple(m.group("flags").decode().split())
    delim = m.group("delim").decode()
    name_raw = m.group("name").decode()
    # Folder name may be quoted or in modified UTF-7
    if name_raw.startswith('"') and name_raw.endswith('"'):
        name_raw = name_raw[1:-1]
    return FolderInfo(name=name_raw, delimiter=delim, flags=flags)


class ICloudIMAP:
    """Connection wrapper. Use via `with` block."""

    def __init__(self, email_addr: str, password: str, *, timeout: float = 30.0):
        self.email = email_addr
        self._password = password
        self._timeout = timeout
        self._conn: imaplib.IMAP4_SSL | None = None
        self._selected: str | None = None

    # ---- lifecycle -----------------------------------------------------

    def connect(self) -> None:
        if self._conn is not None:
            return
        try:
            self._conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=self._timeout)
            self._conn.login(self.email, self._password)
        except (imaplib.IMAP4.error, OSError) as exc:
            raise IMAPError(f"Login to {IMAP_HOST} failed: {exc}") from exc

    def close(self) -> None:
        if self._conn is None:
            return
        try:
            if self._selected:
                self._conn.close()
            self._conn.logout()
        except Exception:  # noqa: BLE001
            pass
        finally:
            self._conn = None
            self._selected = None

    def __enter__(self) -> ICloudIMAP:
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ---- folders -------------------------------------------------------

    def list_folders(self) -> list[FolderInfo]:
        assert self._conn is not None
        typ, data = self._conn.list()
        if typ != "OK" or data is None:
            raise IMAPError(f"LIST failed: {typ}")
        out: list[FolderInfo] = []
        for line in data:
            if not line:
                continue
            info = _parse_folder_line(line)
            if info is not None:
                out.append(info)
        return out

    def select(self, folder: str, *, readonly: bool = True) -> int:
        """Select a folder. Returns the message count."""
        assert self._conn is not None
        # Folder name needs quoting if it contains spaces or special chars
        quoted = f'"{folder}"' if not (folder.startswith('"') and folder.endswith('"')) else folder
        typ, data = self._conn.select(quoted, readonly=readonly)
        if typ != "OK":
            raise IMAPError(f"SELECT {folder!r} failed: {typ} {data!r}")
        self._selected = folder
        return int(data[0]) if data and data[0] else 0

    def uidvalidity(self) -> int:
        """Return UIDVALIDITY for the selected mailbox."""
        assert self._conn is not None
        if self._selected is None:
            raise IMAPError("Must select a folder before reading UIDVALIDITY.")
        _, values = self._conn.response("UIDVALIDITY")
        if not values or not values[0]:
            raise IMAPError("Server did not provide UIDVALIDITY for selected folder.")
        raw = values[0]
        if isinstance(raw, bytes):
            raw = raw.decode("ascii", errors="strict")
        try:
            return int(raw)
        except (TypeError, ValueError) as exc:
            raise IMAPError(f"Invalid UIDVALIDITY response: {raw!r}") from exc

    def folder_count(self, folder: str) -> int:
        """Get message count for a folder without disturbing current selection."""
        assert self._conn is not None
        quoted = f'"{folder}"'
        typ, data = self._conn.status(quoted, "(MESSAGES)")
        if typ != "OK":
            raise IMAPError(f"STATUS {folder!r} failed")
        # data like: [b'"INBOX" (MESSAGES 1234)']
        m = re.search(rb"MESSAGES (\d+)", data[0])
        return int(m.group(1)) if m else 0

    # ---- search & fetch ------------------------------------------------

    def search_uids(self, criteria: str = "ALL") -> list[int]:
        """Run UID SEARCH. `criteria` is raw IMAP search syntax."""
        assert self._conn is not None
        if self._selected is None:
            raise IMAPError("Must select a folder before searching.")
        typ, data = self._conn.uid("SEARCH", None, criteria)
        if typ != "OK":
            raise IMAPError(f"UID SEARCH failed: {typ}")
        if not data or not data[0]:
            return []
        return [int(x) for x in data[0].split()]

    def fetch_metadata(self, uids: list[int]) -> Iterator[MessageMeta]:
        """Yield metadata for each UID. Uses BODY.PEEK so messages stay UNSEEN."""
        if not uids:
            return
        assert self._conn is not None
        if self._selected is None:
            raise IMAPError("Must select a folder before fetching.")

        folder = self._selected
        # Batch to keep the FETCH command line under server limits
        BATCH = 200
        for i in range(0, len(uids), BATCH):
            batch = uids[i : i + BATCH]
            uid_set = ",".join(str(u) for u in batch)
            fetch_items = (
                "(UID FLAGS RFC822.SIZE "
                "BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE LIST-UNSUBSCRIBE)])"
            )
            typ, data = self._conn.uid("FETCH", uid_set, fetch_items)
            if typ != "OK":
                raise IMAPError(f"FETCH metadata failed: {typ}")

            # Response is a list of tuples / bytes interleaved per message
            for item in data:
                if not isinstance(item, tuple) or len(item) < 2:
                    continue
                envelope, header_bytes = item[0], item[1]
                if not isinstance(envelope, bytes) or not isinstance(header_bytes, bytes):
                    continue

                env = envelope.decode("utf-8", errors="replace")
                uid_match = re.search(r"UID (\d+)", env)
                size_match = re.search(r"RFC822\.SIZE (\d+)", env)
                flags_match = re.search(r"FLAGS \(([^)]*)\)", env)
                if not uid_match:
                    continue
                uid = int(uid_match.group(1))
                size = int(size_match.group(1)) if size_match else 0
                flags = tuple(flags_match.group(1).split()) if flags_match else ()

                msg: Message = email.message_from_bytes(header_bytes)
                from_raw = _decode_header_value(msg.get("From"))
                sender_name, sender_email = parseaddr(from_raw)
                sender_name = sender_name or ""
                subject = _decode_header_value(msg.get("Subject"))

                date_raw = msg.get("Date")
                date_iso = ""
                if date_raw:
                    try:
                        dt = parsedate_to_datetime(date_raw)
                        date_iso = dt.astimezone().isoformat()
                    except (TypeError, ValueError):
                        date_iso = ""

                list_unsub = _decode_header_value(msg.get("List-Unsubscribe")) or None

                yield MessageMeta(
                    uid=uid,
                    folder=folder,
                    sender_name=sender_name,
                    sender_email=sender_email.lower(),
                    subject=subject,
                    date_iso=date_iso,
                    size=size,
                    flags=flags,
                    list_unsubscribe=list_unsub,
                )

    def fetch_body(self, uid: int) -> Message:
        """Fetch the full RFC822 body of a single message."""
        assert self._conn is not None
        if self._selected is None:
            raise IMAPError("Must select a folder before fetching.")
        typ, data = self._conn.uid("FETCH", str(uid), "(BODY.PEEK[])")
        if typ != "OK":
            raise IMAPError(f"FETCH body failed: {typ}")
        if not data or not isinstance(data[0], tuple):
            raise IMAPError(f"No body returned for UID {uid}")
        return email.message_from_bytes(data[0][1])

    # ---- mutations (Phase 2) ------------------------------------------

    def move(self, uids: list[int], dest_folder: str) -> None:
        """Move messages by UID. Falls back to COPY+STORE if MOVE unsupported."""
        if not uids:
            return
        assert self._conn is not None
        uid_set = ",".join(str(u) for u in uids)
        quoted_dest = f'"{dest_folder}"'
        try:
            typ, _ = self._conn.uid("MOVE", uid_set, quoted_dest)
            if typ == "OK":
                return
        except imaplib.IMAP4.error:
            pass
        # Fallback for servers without MOVE
        typ, _ = self._conn.uid("COPY", uid_set, quoted_dest)
        if typ != "OK":
            raise IMAPError(f"COPY to {dest_folder!r} failed")
        typ, _ = self._conn.uid("STORE", uid_set, "+FLAGS", r"(\Deleted)")
        if typ != "OK":
            raise IMAPError("STORE \\Deleted failed")
        self._conn.expunge()

    def mark_deleted(self, uids: list[int]) -> None:
        if not uids:
            return
        assert self._conn is not None
        uid_set = ",".join(str(u) for u in uids)
        typ, _ = self._conn.uid("STORE", uid_set, "+FLAGS", r"(\Deleted)")
        if typ != "OK":
            raise IMAPError("STORE \\Deleted failed")

    def expunge(self) -> None:
        assert self._conn is not None
        self._conn.expunge()


@contextmanager
def open_session(email_addr: str, password: str) -> Iterator[ICloudIMAP]:
    """Convenience context manager with retry on transient connect errors."""
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            with ICloudIMAP(email_addr, password) as conn:
                yield conn
                return
        except IMAPError as exc:
            last_exc = exc
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise
    if last_exc:
        raise last_exc
