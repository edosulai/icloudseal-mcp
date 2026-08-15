"""High-level iCloud Calendar + Reminders access over CalDAV.

Events live in VEVENT calendars; reminders are VTODO task lists — both are
CalDAV collections under the same calendar-home-set. Reuses the shared
``dav`` client (proven by the contacts domain) and adds a minimal iCalendar
parser/builder for the fields we expose.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urljoin

from .. import auth
from ..dav.client import CALDAV_BASE, NS, DavClient, DavError
from ..mail.smtp_client import normalize_addresses

_TZID_RE = re.compile(r"^[A-Za-z0-9_+\-/]+$")

CAL_NS = "urn:ietf:params:xml:ns:caldav"


@dataclass
class CalendarCollection:
    url: str
    name: str
    components: set[str] = field(default_factory=set)

    @property
    def is_events(self) -> bool:
        return "VEVENT" in self.components

    @property
    def is_reminders(self) -> bool:
        return "VTODO" in self.components


@dataclass
class CalItem:
    uid: str
    href: str | None
    etag: str | None
    summary: str
    kind: str               # "event" or "reminder"
    start: str = ""         # events: DTSTART; reminders: DUE
    end: str = ""
    location: str = ""
    status: str = ""
    raw: str = ""

    def to_dict(self) -> dict:
        return {
            "uid": self.uid,
            "href": self.href,
            "summary": self.summary,
            "kind": self.kind,
            "start": self.start,
            "end": self.end,
            "location": self.location,
            "status": self.status,
        }


# ---- iCalendar parse / build ------------------------------------------


def _unfold(text: str) -> list[str]:
    out: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def _fmt_ical_dt(value: str) -> str:
    """Best-effort format of an iCal date/datetime value to a readable string."""
    v = value.strip()
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2})Z?)?", v)
    if not m:
        return v
    y, mo, d, hh, mm, _ss = m.groups()
    if hh is None:
        return f"{y}-{mo}-{d}"
    return f"{y}-{mo}-{d} {hh}:{mm}"


def parse_calitem(text: str, kind: str) -> CalItem:
    uid = summary = start = end = location = status = ""
    block = "VEVENT" if kind == "event" else "VTODO"
    in_block = False
    for line in _unfold(text):
        head = line.split(":", 1)[0].split(";", 1)[0].upper()
        value = line.split(":", 1)[1].strip() if ":" in line else ""
        if line.strip() == f"BEGIN:{block}":
            in_block = True
            continue
        if line.strip() == f"END:{block}":
            break
        if not in_block:
            continue
        if head == "UID":
            uid = value
        elif head == "SUMMARY":
            summary = value
        elif head == "DTSTART":
            start = _fmt_ical_dt(value)
        elif head == "DTEND":
            end = _fmt_ical_dt(value)
        elif head == "DUE":
            start = _fmt_ical_dt(value)
        elif head == "LOCATION":
            location = value
        elif head == "STATUS":
            status = value
    if not uid:
        uid = str(uuid.uuid4()).upper()
    return CalItem(
        uid=uid, href=None, etag=None, summary=summary or "(no title)", kind=kind,
        start=start, end=end, location=location, status=status, raw=text,
    )


def validate_timezone(value: str) -> str:
    token = (value or "").strip()
    if not token or len(token) > 64 or not _TZID_RE.fullmatch(token):
        raise ValueError("timezone must be an IANA-like token (e.g. America/Los_Angeles).")
    return token


COMMON_TIMEZONES = (
    "UTC",
    "America/Los_Angeles",
    "America/Denver",
    "America/Chicago",
    "America/New_York",
    "America/Sao_Paulo",
    "Europe/London",
    "Europe/Paris",
    "Europe/Berlin",
    "Africa/Johannesburg",
    "Asia/Dubai",
    "Asia/Kolkata",
    "Asia/Singapore",
    "Asia/Tokyo",
    "Australia/Sydney",
    "Pacific/Auckland",
)


def list_timezones(*, query: str | None = None, limit: int = 50) -> list[str]:
    """Read-only IANA-like timezone picker. Does not mutate calendars."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
        raise ValueError("limit must be an integer from 1 to 200.")
    needle = (query or "").strip()
    if any(ch in needle for ch in "\r\n\x00"):
        raise ValueError("query must not contain control characters.")
    try:
        from zoneinfo import available_timezones

        zones = sorted(z for z in available_timezones() if z and not z.startswith("SystemV/"))
    except Exception:
        zones = list(COMMON_TIMEZONES)
    if needle:
        lowered = needle.lower()
        zones = [z for z in zones if lowered in z.lower()]
    return [z for z in zones if _TZID_RE.fullmatch(z)][:limit]


def _ical_dt(value: str, *, all_day: bool = False, timezone: str | None = None) -> tuple[str, str]:
    """Return (param, formatted) for a DTSTART/DTEND/DUE value.

    Accepts 'YYYY-MM-DD' (all-day) or 'YYYY-MM-DD HH:MM' (floating local time).
    Optional TZID is a validated IANA-like token and never interpolated raw.
    """
    value = value.strip()
    if all_day or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        d = datetime.strptime(value[:10], "%Y-%m-%d")
        return ";VALUE=DATE", d.strftime("%Y%m%d")
    dt = datetime.strptime(value, "%Y-%m-%d %H:%M")
    if timezone:
        tzid = validate_timezone(timezone)
        return f";TZID={tzid}", dt.strftime("%Y%m%dT%H%M%S")
    return "", dt.strftime("%Y%m%dT%H%M%S")


def _attendee_lines(attendees: list[str] | str | None) -> list[str]:
    addresses = normalize_addresses(attendees, field="attendee")
    return [f"ATTENDEE:mailto:{addr}" for addr in addresses]


def _now_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def build_event(
    *, uid: str, summary: str, start: str, end: str | None = None,
    location: str = "", all_day: bool = False,
    timezone: str | None = None, attendees: list[str] | str | None = None,
) -> str:
    sp, sv = _ical_dt(start, all_day=all_day, timezone=timezone)
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//icloudseal-mcp//EN",
        "BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{_now_stamp()}",
        f"DTSTART{sp}:{sv}", f"SUMMARY:{summary}",
    ]
    if end:
        ep, ev = _ical_dt(end, all_day=all_day, timezone=timezone)
        lines.append(f"DTEND{ep}:{ev}")
    if location:
        lines.append(f"LOCATION:{location}")
    lines.extend(_attendee_lines(attendees))
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"


def build_reminder(
    *, uid: str, summary: str, due: str | None = None, completed: bool = False,
) -> str:
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//icloudseal-mcp//EN",
        "BEGIN:VTODO", f"UID:{uid}", f"DTSTAMP:{_now_stamp()}", f"SUMMARY:{summary}",
    ]
    if due:
        dp, dv = _ical_dt(due)
        lines.append(f"DUE{dp}:{dv}")
    if completed:
        lines += ["STATUS:COMPLETED", f"COMPLETED:{_now_stamp()}", "PERCENT-COMPLETE:100"]
    else:
        lines.append("STATUS:NEEDS-ACTION")
    lines += ["END:VTODO", "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"


def update_event(
    text: str,
    *,
    summary: str | None = None,
    start: str | None = None,
    end: str | None = None,
    location: str | None = None,
    all_day: bool | None = None,
    timezone: str | None = None,
    attendees: list[str] | str | None = None,
) -> str:
    """Patch exposed VEVENT fields without erasing recurrence, alarms, or metadata."""
    if all(
        value is None
        for value in (summary, start, end, location, attendees, timezone)
    ):
        raise ValueError("Provide at least one event field to update.")

    stamp = _now_stamp()
    start_is_date = bool(start and re.fullmatch(r"\d{4}-\d{2}-\d{2}", start.strip()))
    use_all_day = bool(all_day) or start_is_date
    replacements: dict[str, str | None] = {
        "DTSTAMP": f"DTSTAMP:{stamp}",
        "LAST-MODIFIED": f"LAST-MODIFIED:{stamp}",
    }
    if summary is not None:
        replacements["SUMMARY"] = f"SUMMARY:{summary}"
    if start is not None:
        start_param, start_value = _ical_dt(start, all_day=use_all_day, timezone=timezone)
        replacements["DTSTART"] = f"DTSTART{start_param}:{start_value}"
    if end is not None:
        end_is_date = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", end.strip()))
        end_param, end_value = _ical_dt(end, all_day=use_all_day or end_is_date, timezone=timezone)
        replacements["DTEND"] = f"DTEND{end_param}:{end_value}"
    if location is not None:
        replacements["LOCATION"] = f"LOCATION:{location}" if location else None
    attendee_lines = None if attendees is None else _attendee_lines(attendees)

    emitted: set[str] = set()
    sequence_emitted = False
    current_seq = 0
    in_event = False
    in_alarm = False
    output: list[str] = []
    for line in _unfold(text):
        upper = line.strip().upper()
        if upper == "BEGIN:VALARM":
            in_alarm = True
            output.append(line)
            continue
        if upper == "END:VALARM":
            in_alarm = False
            output.append(line)
            continue
        if upper == "BEGIN:VEVENT":
            in_event = True
            output.append(line)
            continue
        if upper == "END:VEVENT":
            for name, value in replacements.items():
                if name not in emitted and value is not None:
                    output.append(value)
            if attendee_lines is not None:
                output.extend(attendee_lines)
            if not sequence_emitted:
                output.append(f"SEQUENCE:{current_seq + 1}")
            in_event = False
            output.append(line)
            continue
        name = line.split(":", 1)[0].split(";", 1)[0].upper()
        if in_event and not in_alarm and attendee_lines is not None and name == "ATTENDEE":
            continue
        if in_event and not in_alarm and name == "SEQUENCE":
            try:
                current_seq = int(line.split(":", 1)[1].strip())
            except ValueError:
                current_seq = 0
            output.append(f"SEQUENCE:{current_seq + 1}")
            sequence_emitted = True
            continue
        if in_event and not in_alarm and name in replacements:
            if name not in emitted:
                value = replacements[name]
                if value is not None:
                    output.append(value)
                emitted.add(name)
            continue
        output.append(line)
    return "\r\n".join(output).rstrip("\r\n") + "\r\n"


def update_reminder(
    text: str,
    *,
    summary: str | None = None,
    due: str | None = None,
) -> str:
    """Patch exposed VTODO fields without erasing recurrence, alarms, or metadata."""
    if all(value is None for value in (summary, due)):
        raise ValueError("Provide at least one reminder field to update.")

    stamp = _now_stamp()
    replacements: dict[str, str | None] = {
        "DTSTAMP": f"DTSTAMP:{stamp}",
        "LAST-MODIFIED": f"LAST-MODIFIED:{stamp}",
    }
    if summary is not None:
        replacements["SUMMARY"] = f"SUMMARY:{summary}"
    if due is not None:
        if due:
            due_param, due_value = _ical_dt(due)
            replacements["DUE"] = f"DUE{due_param}:{due_value}"
        else:
            replacements["DUE"] = None

    emitted: set[str] = set()
    sequence_emitted = False
    current_seq = 0
    in_todo = False
    in_alarm = False
    output: list[str] = []
    for line in _unfold(text):
        upper = line.strip().upper()
        if upper == "BEGIN:VALARM":
            in_alarm = True
            output.append(line)
            continue
        if upper == "END:VALARM":
            in_alarm = False
            output.append(line)
            continue
        if upper == "BEGIN:VTODO":
            in_todo = True
            output.append(line)
            continue
        if upper == "END:VTODO":
            for name, value in replacements.items():
                if name not in emitted and value is not None:
                    output.append(value)
            if not sequence_emitted:
                output.append(f"SEQUENCE:{current_seq + 1}")
            in_todo = False
            output.append(line)
            continue
        name = line.split(":", 1)[0].split(";", 1)[0].upper()
        if in_todo and not in_alarm and name == "SEQUENCE":
            try:
                current_seq = int(line.split(":", 1)[1].strip())
            except ValueError:
                current_seq = 0
            output.append(f"SEQUENCE:{current_seq + 1}")
            sequence_emitted = True
            continue
        if in_todo and not in_alarm and name in replacements:
            if name not in emitted:
                value = replacements[name]
                if value is not None:
                    output.append(value)
                emitted.add(name)
            continue
        output.append(line)
    return "\r\n".join(output).rstrip("\r\n") + "\r\n"


def complete_reminder(text: str) -> str:
    """Mark a VTODO complete without erasing recurrence, alarms, or metadata."""
    stamp = _now_stamp()
    replacements = {
        "STATUS": "STATUS:COMPLETED",
        "COMPLETED": f"COMPLETED:{stamp}",
        "PERCENT-COMPLETE": "PERCENT-COMPLETE:100",
    }
    emitted: set[str] = set()
    in_todo = False
    output: list[str] = []
    for line in _unfold(text):
        upper = line.strip().upper()
        if upper == "BEGIN:VTODO":
            in_todo = True
            output.append(line)
            continue
        if upper == "END:VTODO":
            for name in ("STATUS", "COMPLETED", "PERCENT-COMPLETE"):
                if name not in emitted:
                    output.append(replacements[name])
            in_todo = False
            output.append(line)
            continue
        name = line.split(":", 1)[0].split(";", 1)[0].upper()
        if in_todo and name in replacements:
            if name not in emitted:
                output.append(replacements[name])
                emitted.add(name)
            continue
        output.append(line)
    return "\r\n".join(output).rstrip("\r\n") + "\r\n"


# ---- session ----------------------------------------------------------


class CalendarSession:
    def __init__(self, client: DavClient):
        self._client = client
        self._collections: list[CalendarCollection] | None = None

    @classmethod
    def connect(cls) -> CalendarSession:
        creds = auth.load_credentials()
        return cls(DavClient(CALDAV_BASE, creds.email, creds.password))

    def collections(self) -> list[CalendarCollection]:
        if self._collections is None:
            principal = self._client.current_user_principal()
            home = self._client.home_set(principal, kind="cal")
            body = (
                '<?xml version="1.0" encoding="utf-8"?>'
                '<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
                "<d:prop><d:resourcetype/><d:displayname/>"
                "<c:supported-calendar-component-set/></d:prop></d:propfind>"
            )
            root = self._client.propfind(home, body, depth="1")
            out: list[CalendarCollection] = []
            for resp in root.findall("d:response", NS):
                rtype = resp.find(".//d:resourcetype", NS)
                if rtype is None or rtype.find(f"{{{CAL_NS}}}calendar") is None:
                    continue
                href = resp.findtext("d:href", default="", namespaces=NS).strip()
                name = resp.findtext(".//d:displayname", default="", namespaces=NS).strip()
                comps = {
                    c.get("name")
                    for c in resp.findall(f".//{{{CAL_NS}}}comp")
                    if c.get("name")
                }
                out.append(CalendarCollection(urljoin(home, href), name or "(unnamed)", comps))
            self._collections = out
        return self._collections

    def _query(self, collection_url: str, comp: str, *, time_range: str = "") -> list[CalItem]:
        kind = "event" if comp == "VEVENT" else "reminder"
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
            "<d:prop><d:getetag/><c:calendar-data/></d:prop>"
            '<c:filter><c:comp-filter name="VCALENDAR">'
            f'<c:comp-filter name="{comp}">{time_range}</c:comp-filter>'
            "</c:comp-filter></c:filter></c:calendar-query>"
        )
        root = self._client.report(collection_url, body, depth="1")
        items: list[CalItem] = []
        for resp in root.findall("d:response", NS):
            href = resp.findtext("d:href", default="", namespaces=NS).strip()
            data_el = resp.find(f".//{{{CAL_NS}}}calendar-data")
            if not href or data_el is None or not (data_el.text or "").strip():
                continue
            etag_el = resp.find(".//d:getetag", NS)
            etag = etag_el.text.strip() if etag_el is not None and etag_el.text else None
            item = parse_calitem(data_el.text, kind)
            item.href = urljoin(collection_url, href)
            item.etag = etag
            items.append(item)
        return items

    def list_events(self, *, days: int = 30) -> list[CalItem]:
        start = datetime.now(UTC).strftime("%Y%m%dT000000Z")
        end_dt = datetime.now(UTC).timestamp() + days * 86400
        end = datetime.fromtimestamp(end_dt, UTC).strftime("%Y%m%dT000000Z")
        tr = f'<c:time-range start="{start}" end="{end}"/>'
        out: list[CalItem] = []
        for col in self.collections():
            if col.is_events:
                out.extend(self._query(col.url, "VEVENT", time_range=tr))
        out.sort(key=lambda i: i.start)
        return out

    def list_reminders(self, *, include_completed: bool = False) -> list[CalItem]:
        out: list[CalItem] = []
        for col in self.collections():
            if col.is_reminders:
                out.extend(self._query(col.url, "VTODO"))
        if not include_completed:
            out = [r for r in out if r.status.upper() != "COMPLETED"]
        out.sort(key=lambda i: (i.start == "", i.start))
        return out

    def _first(self, *, events: bool) -> CalendarCollection:
        for col in self.collections():
            if (events and col.is_events) or (not events and col.is_reminders):
                return col
        raise DavError("No suitable calendar/reminder list found.")

    def find_collection(self, name: str | None, *, events: bool) -> CalendarCollection:
        if not name:
            return self._first(events=events)
        for col in self.collections():
            ok = col.is_events if events else col.is_reminders
            if ok and name.lower() in col.name.lower():
                return col
        raise DavError(f"No {'calendar' if events else 'reminder list'} matches {name!r}.")

    def put_item(self, collection_url: str, uid: str, ics: str) -> str:
        href = collection_url.rstrip("/") + f"/{uid}.ics"
        self._client.put(
            href,
            ics,
            content_type="text/calendar; charset=utf-8",
            if_none_match=True,
        )
        return href

    def delete(self, item: CalItem) -> None:
        if not item.href:
            raise DavError("Cannot delete an item without an href.")
        self._client.delete(item.href, etag=item.etag)

    def update(self, item: CalItem, ics: str) -> None:
        if not item.href:
            raise DavError("Cannot update an item without an href.")
        self._client.put(
            item.href, ics, content_type="text/calendar; charset=utf-8", etag=item.etag
        )


def matches(item: CalItem, query: str) -> bool:
    return query.lower() in " ".join([item.summary, item.location]).lower()
