"""High-level iCloud Contacts access over CardDAV.

Wraps :class:`icloudseal_mcp.dav.client.DavClient` with contact discovery, a
small vCard parser/builder for the fields we expose (name, emails, phones,
org), and CRUD primitives. Destructive operations are exposed but the CLI gates
them behind a dry-run plan + ``--apply``.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from .. import auth
from ..dav.client import CARDDAV_BASE, NS, DavClient, DavError


@dataclass
class Contact:
    uid: str
    href: str | None
    etag: str | None
    full_name: str
    first: str = ""
    last: str = ""
    org: str = ""
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    raw: str = ""

    def to_dict(self) -> dict:
        return {
            "uid": self.uid,
            "href": self.href,
            "etag": self.etag,
            "full_name": self.full_name,
            "first": self.first,
            "last": self.last,
            "org": self.org,
            "emails": self.emails,
            "phones": self.phones,
        }


# ---- vCard parsing / building -----------------------------------------


def _unfold(text: str) -> list[str]:
    """Unfold RFC 6350 line continuations (a line starting with space/tab)."""
    out: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def _split_prop(line: str) -> tuple[str, str]:
    """Return (name-with-params, value). vCard separates them at the first ':'."""
    idx = line.find(":")
    if idx == -1:
        return line, ""
    return line[:idx], line[idx + 1 :]


def _prop_name(name_with_params: str) -> str:
    # Drop a group prefix like "item1.TEL" / "ITEM1.EMAIL" before params.
    head = name_with_params.split(";", 1)[0]
    head = head.split(".", 1)[-1]
    return head.upper()


def parse_vcard(text: str) -> Contact:
    uid = ""
    full_name = ""
    first = last = org = ""
    emails: list[str] = []
    phones: list[str] = []

    for line in _unfold(text):
        head, value = _split_prop(line)
        name = _prop_name(head)
        value = value.strip()
        if not value:
            continue
        if name == "UID":
            uid = value
        elif name == "FN":
            full_name = value
        elif name == "N":
            parts = value.split(";")
            last = parts[0] if len(parts) > 0 else ""
            first = parts[1] if len(parts) > 1 else ""
        elif name == "ORG":
            org = value.split(";")[0]
        elif name == "EMAIL":
            emails.append(value)
        elif name == "TEL":
            phones.append(value)

    if not full_name:
        full_name = " ".join(p for p in (first, last) if p) or "(no name)"
    if not uid:
        uid = str(uuid.uuid4()).upper()

    return Contact(
        uid=uid,
        href=None,
        etag=None,
        full_name=full_name,
        first=first,
        last=last,
        org=org,
        emails=emails,
        phones=phones,
        raw=text,
    )


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;")


def build_vcard(
    *,
    uid: str,
    full_name: str,
    first: str = "",
    last: str = "",
    org: str = "",
    emails: list[str] | None = None,
    phones: list[str] | None = None,
) -> str:
    lines = ["BEGIN:VCARD", "VERSION:3.0", f"UID:{uid}"]
    lines.append(f"N:{_escape(last)};{_escape(first)};;;")
    lines.append(f"FN:{_escape(full_name)}")
    if org:
        lines.append(f"ORG:{_escape(org)}")
    for em in emails or []:
        lines.append(f"EMAIL;TYPE=INTERNET:{em}")
    for tel in phones or []:
        lines.append(f"TEL;TYPE=CELL:{tel}")
    lines.append("END:VCARD")
    return "\r\n".join(lines) + "\r\n"


# ---- session ----------------------------------------------------------


class ContactsSession:
    """Resolves the iCloud address book and performs contact operations."""

    def __init__(self, client: DavClient):
        self._client = client
        self._addressbook: str | None = None

    @classmethod
    def connect(cls) -> ContactsSession:
        creds = auth.load_credentials()
        client = DavClient(CARDDAV_BASE, creds.email, creds.password)
        return cls(client)

    @property
    def addressbook_url(self) -> str:
        if self._addressbook is None:
            principal = self._client.current_user_principal()
            home = self._client.home_set(principal, kind="card")
            collections = self._client.collections(home, kind="card")
            if not collections:
                raise DavError("No CardDAV address book found for this account.")
            # iCloud's primary address book is the "card" collection.
            self._addressbook = next(
                (c for c in collections if c.rstrip("/").endswith("card")),
                collections[0],
            )
        return self._addressbook

    def list_contacts(self) -> list[Contact]:
        """Fetch all contacts with vCard data in a single addressbook-query."""
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<card:addressbook-query xmlns:d="DAV:" '
            'xmlns:card="urn:ietf:params:xml:ns:carddav">'
            "<d:prop><d:getetag/><card:address-data/></d:prop>"
            "<card:filter/></card:addressbook-query>"
        )
        root = self._client.report(self.addressbook_url, body, depth="1")
        contacts: list[Contact] = []
        for resp in root.findall("d:response", NS):
            href = resp.findtext("d:href", default="", namespaces=NS).strip()
            data_el = resp.find(".//{urn:ietf:params:xml:ns:carddav}address-data")
            if not href or data_el is None or not (data_el.text or "").strip():
                continue
            etag_el = resp.find(".//d:getetag", NS)
            etag = etag_el.text.strip() if etag_el is not None and etag_el.text else None
            contact = parse_vcard(data_el.text)
            from urllib.parse import urljoin

            contact.href = urljoin(self.addressbook_url, href)
            contact.etag = etag
            contacts.append(contact)
        contacts.sort(key=lambda c: c.full_name.lower())
        return contacts

    def _href_for(self, uid: str) -> str:
        return self.addressbook_url.rstrip("/") + f"/{uid}.vcf"

    def create(self, contact: Contact) -> str:
        vcard = build_vcard(
            uid=contact.uid,
            full_name=contact.full_name,
            first=contact.first,
            last=contact.last,
            org=contact.org,
            emails=contact.emails,
            phones=contact.phones,
        )
        href = self._href_for(contact.uid)
        self._client.put(href, vcard, content_type="text/vcard; charset=utf-8", if_none_match=True)
        return href

    def update(self, contact: Contact) -> None:
        if not contact.href:
            raise DavError("Cannot update a contact without an href.")
        vcard = build_vcard(
            uid=contact.uid,
            full_name=contact.full_name,
            first=contact.first,
            last=contact.last,
            org=contact.org,
            emails=contact.emails,
            phones=contact.phones,
        )
        self._client.put(
            contact.href, vcard, content_type="text/vcard; charset=utf-8", etag=contact.etag
        )

    def delete(self, contact: Contact) -> None:
        if not contact.href:
            raise DavError("Cannot delete a contact without an href.")
        self._client.delete(contact.href, etag=contact.etag)


def matches(contact: Contact, query: str) -> bool:
    q = query.lower()
    haystack = " ".join(
        [contact.full_name, contact.org, *contact.emails, *contact.phones]
    ).lower()
    # also match phone digits ignoring formatting
    digits = re.sub(r"\D", "", q)
    if digits and any(digits in re.sub(r"\D", "", p) for p in contact.phones):
        return True
    return q in haystack
