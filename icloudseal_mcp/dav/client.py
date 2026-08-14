"""Minimal WebDAV client for iCloud CardDAV/CalDAV.

iCloud has no public REST API; its contacts and calendars are exposed over the
standard DAV protocols, authenticated with the *same* app-specific password the
mail domain already uses. This module implements just enough of RFC 4918 /
4791 / 6352 to discover collections and do CRUD on resources.

Discovery flow (per service base host):
    1. PROPFIND base "/"            -> current-user-principal
    2. PROPFIND principal           -> {addressbook,calendar}-home-set
    3. PROPFIND home (Depth: 1)     -> collections (filter by resourcetype)
    4. PROPFIND collection (Depth:1)-> resource hrefs + etags

iCloud answers the home-set query with an absolute URL on a per-account
partition host (e.g. ``p123-contacts.icloud.com``); we always resolve relative
hrefs against the responding URL so we follow the account to its partition.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import urljoin

try:
    import requests
except ModuleNotFoundError as exc:  # pragma: no cover - dependency guard
    raise SystemExit(
        "The 'requests' package is required for contacts/calendar. "
        "Install it: pip install requests"
    ) from exc

NS = {
    "d": "DAV:",
    "card": "urn:ietf:params:xml:ns:carddav",
    "cal": "urn:ietf:params:xml:ns:caldav",
}

CARDDAV_BASE = "https://contacts.icloud.com"
CALDAV_BASE = "https://caldav.icloud.com"


class DavError(RuntimeError):
    """Raised when a DAV request fails."""


@dataclass(frozen=True)
class DavResource:
    href: str          # absolute URL
    etag: str | None


class DavClient:
    def __init__(self, base_url: str, email: str, password: str, *, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._session = requests.Session()
        self._session.auth = (email, password)
        self._session.headers.update({"User-Agent": "icloudseal-mcp/0.2"})

    # ---- low-level ----------------------------------------------------

    def _propfind(self, url: str, body: str, *, depth: str = "0") -> ET.Element:
        resp = self._session.request(
            "PROPFIND",
            url,
            data=body.encode("utf-8"),
            headers={"Depth": depth, "Content-Type": "application/xml; charset=utf-8"},
            timeout=self._timeout,
        )
        if resp.status_code not in (207, 200):
            raise DavError(f"PROPFIND {url} -> HTTP {resp.status_code}")
        try:
            return ET.fromstring(resp.content)
        except ET.ParseError as exc:
            raise DavError(f"Invalid XML from {url}: {exc}") from exc

    def propfind(self, url: str, body: str, *, depth: str = "0") -> ET.Element:
        """Public PROPFIND for domain modules that need custom properties."""
        return self._propfind(url, body, depth=depth)

    def report(self, url: str, body: str, *, depth: str = "1") -> ET.Element:
        resp = self._session.request(
            "REPORT",
            url,
            data=body.encode("utf-8"),
            headers={"Depth": depth, "Content-Type": "application/xml; charset=utf-8"},
            timeout=self._timeout,
        )
        if resp.status_code not in (207, 200):
            raise DavError(f"REPORT {url} -> HTTP {resp.status_code}")
        try:
            return ET.fromstring(resp.content)
        except ET.ParseError as exc:
            raise DavError(f"Invalid XML from {url}: {exc}") from exc

    @staticmethod
    def _first_text(root: ET.Element, path: str) -> str | None:
        el = root.find(path, NS)
        if el is None or el.text is None:
            return None
        return el.text.strip()

    # ---- discovery ----------------------------------------------------

    def current_user_principal(self) -> str:
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<d:propfind xmlns:d="DAV:"><d:prop>'
            "<d:current-user-principal/></d:prop></d:propfind>"
        )
        root = self._propfind(self.base_url + "/", body)
        href = self._first_text(root, ".//d:current-user-principal/d:href")
        if not href:
            raise DavError("Could not resolve current-user-principal.")
        return urljoin(self.base_url + "/", href)

    def home_set(self, principal_url: str, *, kind: str) -> str:
        """kind: 'card' (addressbook-home-set) or 'cal' (calendar-home-set)."""
        prop = "addressbook-home-set" if kind == "card" else "calendar-home-set"
        ns_uri = NS[kind]
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            f'<d:propfind xmlns:d="DAV:" xmlns:x="{ns_uri}">'
            f"<d:prop><x:{prop}/></d:prop></d:propfind>"
        )
        root = self._propfind(principal_url, body)
        href = self._first_text(root, f".//{{{ns_uri}}}{prop}/d:href")
        if not href:
            raise DavError(f"Could not resolve {prop}.")
        return urljoin(principal_url, href)

    def collections(self, home_url: str, *, kind: str) -> list[str]:
        """Return collection URLs whose resourcetype contains the kind's type."""
        type_tag = "addressbook" if kind == "card" else "calendar"
        ns_uri = NS[kind]
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<d:propfind xmlns:d="DAV:"><d:prop>'
            "<d:resourcetype/><d:displayname/></d:prop></d:propfind>"
        )
        root = self._propfind(home_url, body, depth="1")
        found: list[str] = []
        for resp in root.findall("d:response", NS):
            rtype = resp.find(".//d:resourcetype", NS)
            if rtype is None:
                continue
            if rtype.find(f"{{{ns_uri}}}{type_tag}") is None:
                continue
            href = resp.findtext("d:href", default="", namespaces=NS).strip()
            if href:
                found.append(urljoin(home_url, href))
        return found

    def list_resources(self, collection_url: str) -> list[DavResource]:
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<d:propfind xmlns:d="DAV:"><d:prop>'
            "<d:getetag/></d:prop></d:propfind>"
        )
        root = self._propfind(collection_url, body, depth="1")
        out: list[DavResource] = []
        for resp in root.findall("d:response", NS):
            href = resp.findtext("d:href", default="", namespaces=NS).strip()
            if not href or href.rstrip("/") == _path(collection_url).rstrip("/"):
                continue  # skip the collection itself
            etag = None
            etag_el = resp.find(".//d:getetag", NS)
            if etag_el is not None and etag_el.text:
                etag = etag_el.text.strip()
            out.append(DavResource(href=urljoin(collection_url, href), etag=etag))
        return out

    # ---- resource CRUD ------------------------------------------------

    def get(self, url: str) -> str:
        resp = self._session.get(url, timeout=self._timeout)
        if resp.status_code != 200:
            raise DavError(f"GET {url} -> HTTP {resp.status_code}")
        return resp.text

    def put(
        self,
        url: str,
        data: str,
        *,
        content_type: str,
        etag: str | None = None,
        if_none_match: bool = False,
    ) -> None:
        headers = {"Content-Type": content_type}
        if if_none_match:
            headers["If-None-Match"] = "*"
        elif etag:
            headers["If-Match"] = etag
        resp = self._session.put(
            url, data=data.encode("utf-8"), headers=headers, timeout=self._timeout
        )
        if resp.status_code not in (200, 201, 204):
            raise DavError(f"PUT {url} -> HTTP {resp.status_code}: {resp.text[:200]}")

    def delete(self, url: str, *, etag: str | None = None) -> None:
        headers = {"If-Match": etag} if etag else {}
        resp = self._session.delete(url, headers=headers, timeout=self._timeout)
        if resp.status_code not in (200, 204):
            raise DavError(f"DELETE {url} -> HTTP {resp.status_code}")


def _path(url: str) -> str:
    from urllib.parse import urlsplit

    return urlsplit(url).path
