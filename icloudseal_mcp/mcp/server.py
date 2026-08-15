"""icloudseal-mcp stdio MCP server.

Exposes iCloud plus local Safari/Music domains (Mail, Contacts, Calendar,
Messages, Notes, Drive, Photos, Safari, Music) with seal-family two-phase
mutations:

  prepare_* → show exact preview → user OK in chat → icloud_request_local_approval
"""

from __future__ import annotations

import sys
import traceback
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.server import MCPServer
from mcp.types import ToolAnnotations

from .. import __version__
from . import approval, services

MCP_INSTRUCTIONS = """icloudseal-mcp — sealed iCloud for local AI agents.

Before sensitive work:
1. Call icloud_doctor (preferred first call) or icloud_status.
2. If ready=false, follow userMessage / agentNextSteps (usually mail setup + Keychain).
3. Reads never need Touch ID.
4. Mutations are two-phase ONLY:
   icloud_prepare_* → show exact target+preview to the user → wait for explicit OK in chat
   → icloud_request_local_approval (Touch ID / macOS password).
5. On approval timeout or uncertainty: icloud_action_outcome first; never re-prepare a
   duplicate mutate blindly.
6. Messages/Photos need Full Disk Access. Notes/Safari/Music need Automation. Drive is local CloudDocs.

Domains: mail, contacts, calendar, messages, notes, drive, photos, safari, music.
Never claim a mutation succeeded unless request_local_approval / action_outcome reports success.
"""

server = MCPServer(
    name="icloudseal-mcp",
    version=__version__,
    instructions=MCP_INSTRUCTIONS,
)

READ_ANN = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)
PREPARE_ANN = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=False,
)
WRITE_LOCAL_ANN = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=False,
)
MUTATE_ANN = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=False,
    open_world_hint=True,
)


def _ok(result: Any) -> dict[str, Any]:
    return result if isinstance(result, dict) else {"result": result}


def _err(exc: BaseException) -> dict[str, Any]:
    """Raise an MCP-native failure so clients receive `is_error=true`."""
    raise ToolError(f"{type(exc).__name__}: {exc}") from exc


def _tool(name: str, description: str, annotations: ToolAnnotations):
    def deco(fn):
        server.add_tool(
            fn,
            name=name,
            description=description,
            annotations=annotations,
            structured_output=True,
        )
        return fn

    return deco


# ---------------------------------------------------------------------------
# Onboarding / diagnostics
# ---------------------------------------------------------------------------


@_tool(
    "icloud_doctor",
    "One-shot diagnosis: credentials, domain readiness "
    "(Mail/Contacts/Calendar/Messages/Notes/Drive/Photos/Safari/Music), and exact next steps. "
    "Call first in a new chat.",
    READ_ANN,
)
def icloud_doctor() -> dict[str, Any]:
    try:
        return services.doctor()
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool(
    "icloud_status",
    "Check whether Keychain credentials and core iCloud access are ready. Does not read content.",
    READ_ANN,
)
def icloud_status() -> dict[str, Any]:
    try:
        return services.status()
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool(
    "icloud_security_audit",
    "Audit local runtime isolation without reading content: App Support paths, "
    "Keychain service identity, native approval helper.",
    READ_ANN,
)
def icloud_security_audit() -> dict[str, Any]:
    try:
        return services.security_audit()
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool(
    "icloud_list_domains",
    "List supported iCloud domains and high-level capabilities (read vs prepare/mutate).",
    READ_ANN,
)
def icloud_list_domains() -> dict[str, Any]:
    return {
        "domains": [
            {
                "id": "mail",
                "transport": "IMAP + SMTP + local SQLite cache",
                "reads": ["stats", "sync", "list", "senders", "peek", "triage", "jobs"],
                "mutations": [
                    "apply plan",
                    "cleanup strict",
                    "send",
                    "flags",
                    "move",
                    "trash",
                ],
            },
            {
                "id": "contacts",
                "transport": "CardDAV",
                "reads": ["list", "search", "export"],
                "mutations": ["create", "update", "delete"],
            },
            {
                "id": "calendar",
                "transport": "CalDAV",
                "reads": ["calendars", "events", "reminders"],
                "mutations": [
                    "event-add",
                    "event-update",
                    "event-rm",
                    "reminder-add",
                    "reminder-update",
                    "reminder-done",
                    "reminder-rm",
                ],
            },
            {
                "id": "messages",
                "transport": "chat.db + AppleScript send",
                "reads": ["chats", "list", "search", "export"],
                "mutations": ["send"],
                "requires": "Full Disk Access",
            },
            {
                "id": "notes",
                "transport": "AppleScript Notes.app",
                "reads": ["list", "search", "read"],
                "mutations": ["create", "update", "delete"],
            },
            {
                "id": "drive",
                "transport": "CloudDocs filesystem",
                "reads": ["ls", "tree", "find", "read"],
                "mutations": ["put", "rm→Trash"],
            },
            {
                "id": "photos",
                "transport": "Photos.sqlite",
                "reads": ["stats", "albums", "list"],
                "mutations": ["export local originals"],
                "requires": "Full Disk Access",
            },
            {
                "id": "safari",
                "transport": "AppleScript Safari",
                "reads": ["tabs", "current"],
                "mutations": ["open-url"],
                "requires": "Automation",
            },
            {
                "id": "music",
                "transport": "AppleScript Music.app",
                "reads": ["now-playing"],
                "mutations": ["playpause", "next", "previous"],
                "requires": "Automation",
            },
        ],
        "approval": "prepare_* → user OK → icloud_request_local_approval",
    }


# ---------------------------------------------------------------------------
# Mail reads
# ---------------------------------------------------------------------------


@_tool("icloud_mail_stats", "List iCloud Mail folders with message counts (live IMAP).", READ_ANN)
def icloud_mail_stats() -> dict[str, Any]:
    try:
        return {"folders": services.mail_stats()}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool(
    "icloud_mail_sync",
    "Pull folder metadata into local SQLite cache. Prefer this before "
    "list/senders/triage on a cold cache.",
    READ_ANN,
)
def icloud_mail_sync(folder: str = "INBOX", since: str | None = None) -> dict[str, Any]:
    try:
        return services.mail_sync(folder=folder, since=since)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool(
    "icloud_mail_list",
    "List cached messages from the local SQLite cache for a folder.",
    READ_ANN,
)
def icloud_mail_list(folder: str = "INBOX", limit: int = 50) -> dict[str, Any]:
    try:
        return {"folder": folder, "messages": services.mail_list(folder=folder, limit=limit)}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool("icloud_mail_senders", "Top senders by count from the local cache.", READ_ANN)
def icloud_mail_senders(folder: str | None = "INBOX", top: int = 30) -> dict[str, Any]:
    try:
        return {"senders": services.mail_senders(folder=folder, top=top)}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool("icloud_mail_peek", "Fetch and return the body of one message by UID (live IMAP).", READ_ANN)
def icloud_mail_peek(
    uid: int, folder: str = "INBOX", max_body_chars: int = 8000
) -> dict[str, Any]:
    try:
        return services.mail_peek(uid=uid, folder=folder, max_body_chars=max_body_chars)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool(
    "icloud_mail_triage",
    "Build a dry-run move/delete plan from cached metadata. Does not mutate. "
    "Use prepare_mail_apply to execute a saved plan.",
    WRITE_LOCAL_ANN,
)
def icloud_mail_triage(
    folder: str = "INBOX",
    sender: str | None = None,
    sender_like: str | None = None,
    subject_like: str | None = None,
    older_than: str | None = None,
    has_list_unsubscribe: bool = False,
    limit: int = 200,
    move_to: str | None = None,
    delete: bool = False,
    plan_file: str | None = None,
) -> dict[str, Any]:
    try:
        return services.mail_triage(
            folder=folder,
            sender=sender,
            sender_like=sender_like,
            subject_like=subject_like,
            older_than=older_than,
            has_list_unsubscribe=has_list_unsubscribe,
            limit=limit,
            move_to=move_to,
            delete=delete,
            plan_file=plan_file,
        )
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool(
    "icloud_mail_jobs_collect",
    "Extract and score job-alert leads from mail (review-only; never applies).",
    WRITE_LOCAL_ANN,
)
def icloud_mail_jobs_collect(
    folder: str = "INBOX",
    since: str | None = "7d",
    limit: int | None = 200,
    min_score: int = 1,
    top: int = 50,
    out: str | None = None,
) -> dict[str, Any]:
    try:
        return services.mail_jobs_collect(
            folder=folder, since=since, limit=limit, min_score=min_score, top=top, out=out
        )
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


# ---------------------------------------------------------------------------
# Contacts reads
# ---------------------------------------------------------------------------


@_tool("icloud_contacts_list", "List iCloud contacts (live CardDAV).", READ_ANN)
def icloud_contacts_list(limit: int = 0) -> dict[str, Any]:
    try:
        contacts = services.contacts_list(limit=limit)
        return {"count": len(contacts), "contacts": contacts}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool(
    "icloud_contacts_search",
    "Search contacts by name / email / phone / organization.",
    READ_ANN,
)
def icloud_contacts_search(query: str) -> dict[str, Any]:
    try:
        contacts = services.contacts_search(query=query)
        return {"query": query, "count": len(contacts), "contacts": contacts}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool(
    "icloud_contacts_export",
    "Export all contacts to a new .json or .vcf file under the managed exports directory.",
    WRITE_LOCAL_ANN,
)
def icloud_contacts_export(path: str) -> dict[str, Any]:
    try:
        return services.contacts_export(path=path)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


# ---------------------------------------------------------------------------
# Calendar reads
# ---------------------------------------------------------------------------


@_tool("icloud_calendar_list", "List calendars and reminder lists.", READ_ANN)
def icloud_calendar_list() -> dict[str, Any]:
    try:
        cols = services.calendar_list()
        return {"count": len(cols), "collections": cols}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool("icloud_calendar_events", "List upcoming events (CalDAV).", READ_ANN)
def icloud_calendar_events(days: int = 30) -> dict[str, Any]:
    try:
        events = services.calendar_events(days=days)
        return {"days": days, "count": len(events), "events": events}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool("icloud_calendar_reminders", "List reminders (open by default).", READ_ANN)
def icloud_calendar_reminders(include_completed: bool = False) -> dict[str, Any]:
    try:
        rem = services.calendar_reminders(include_completed=include_completed)
        return {"includeCompleted": include_completed, "count": len(rem), "reminders": rem}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


# ---------------------------------------------------------------------------
# Messages reads
# ---------------------------------------------------------------------------


@_tool(
    "icloud_messages_chats",
    "List recent iMessage/SMS conversations from chat.db (needs Full Disk Access).",
    READ_ANN,
)
def icloud_messages_chats(limit: int = 30) -> dict[str, Any]:
    try:
        chats = services.messages_chats(limit=limit)
        return {"count": len(chats), "chats": chats}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool("icloud_messages_list", "List messages in a conversation (id or name fragment).", READ_ANN)
def icloud_messages_list(chat: str, limit: int = 40) -> dict[str, Any]:
    try:
        msgs = services.messages_list(chat=chat, limit=limit)
        return {"chat": chat, "count": len(msgs), "messages": msgs}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool("icloud_messages_search", "Search message text across chat.db.", READ_ANN)
def icloud_messages_search(query: str, limit: int = 40) -> dict[str, Any]:
    try:
        msgs = services.messages_search(query=query, limit=limit)
        return {"query": query, "count": len(msgs), "messages": msgs}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool(
    "icloud_messages_export",
    "Export a conversation to a new JSON file under the managed exports directory.",
    WRITE_LOCAL_ANN,
)
def icloud_messages_export(chat: str, path: str, limit: int = 1000) -> dict[str, Any]:
    try:
        return services.messages_export(chat=chat, path=path, limit=limit)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


# ---------------------------------------------------------------------------
# Notes reads
# ---------------------------------------------------------------------------


@_tool("icloud_notes_list", "List Notes.app notes.", READ_ANN)
def icloud_notes_list(limit: int = 0) -> dict[str, Any]:
    try:
        notes = services.notes_list(limit=limit)
        return {"count": len(notes), "notes": notes}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool("icloud_notes_search", "Search notes by title fragment or id.", READ_ANN)
def icloud_notes_search(query: str) -> dict[str, Any]:
    try:
        notes = services.notes_search(query=query)
        return {"query": query, "count": len(notes), "notes": notes}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool("icloud_notes_read", "Read one note body by unique title fragment or id.", READ_ANN)
def icloud_notes_read(query: str) -> dict[str, Any]:
    try:
        return services.notes_read(query=query)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


# ---------------------------------------------------------------------------
# Drive reads
# ---------------------------------------------------------------------------


@_tool(
    "icloud_drive_ls",
    "List a directory under iCloud Drive (paths relative to CloudDocs root).",
    READ_ANN,
)
def icloud_drive_ls(path: str | None = None) -> dict[str, Any]:
    try:
        entries = services.drive_ls(path=path)
        return {"path": path or "/", "count": len(entries), "entries": entries}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool("icloud_drive_tree", "Print a directory tree under iCloud Drive.", READ_ANN)
def icloud_drive_tree(path: str | None = None, depth: int = 2) -> dict[str, Any]:
    try:
        return services.drive_tree(path=path, depth=depth)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool("icloud_drive_find", "Find files by glob pattern under iCloud Drive.", READ_ANN)
def icloud_drive_find(
    pattern: str, path: str | None = None, limit: int = 100
) -> dict[str, Any]:
    try:
        hits = services.drive_find(pattern=pattern, path=path, limit=limit)
        return {"pattern": pattern, "count": len(hits), "hits": hits}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool("icloud_drive_read", "Read a text file from iCloud Drive (truncated for safety).", READ_ANN)
def icloud_drive_read(path: str, max_bytes: int = 200_000) -> dict[str, Any]:
    try:
        return services.drive_read(path=path, max_bytes=max_bytes)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


# ---------------------------------------------------------------------------
# Photos reads
# ---------------------------------------------------------------------------


@_tool("icloud_photos_stats", "Photos library totals (photos/videos/favorites/albums).", READ_ANN)
def icloud_photos_stats() -> dict[str, Any]:
    try:
        return services.photos_stats()
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool("icloud_photos_albums", "List albums with item counts.", READ_ANN)
def icloud_photos_albums() -> dict[str, Any]:
    try:
        albums = services.photos_albums()
        return {"count": len(albums), "albums": albums}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool("icloud_photos_list", "List photo/video assets (newest first).", READ_ANN)
def icloud_photos_list(
    album: str | None = None,
    kind: str | None = None,
    favorites: bool = False,
    limit: int = 50,
) -> dict[str, Any]:
    try:
        assets = services.photos_list(
            album=album, kind=kind, favorites=favorites, limit=limit
        )
        return {"count": len(assets), "assets": assets}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


# ---------------------------------------------------------------------------
# Safari reads
# ---------------------------------------------------------------------------


@_tool(
    "icloud_safari_list_tabs",
    "List open Safari tabs (window/tab index, name, URL, current). "
    "Does not launch Safari; empty list if Safari is not running. "
    "Does not return page source or text.",
    READ_ANN,
)
def icloud_safari_list_tabs() -> dict[str, Any]:
    try:
        tabs = services.safari_list_tabs()
        return {"count": len(tabs), "tabs": tabs}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool(
    "icloud_safari_current_tab",
    "Show the current Safari tab (name, URL, indices). "
    "Does not launch Safari and does not return page source.",
    READ_ANN,
)
def icloud_safari_current_tab() -> dict[str, Any]:
    try:
        return services.safari_current_tab()
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


# ---------------------------------------------------------------------------
# Music reads
# ---------------------------------------------------------------------------


@_tool(
    "icloud_music_now_playing",
    "Show Music.app now-playing (state, name, artist, album, duration, position). "
    "Does not launch Music; returns state=stopped if Music is not running.",
    READ_ANN,
)
def icloud_music_now_playing() -> dict[str, Any]:
    try:
        return services.music_now_playing()
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


# ---------------------------------------------------------------------------
# Prepare (mutations) — nothing executes here
# ---------------------------------------------------------------------------


@_tool(
    "icloud_prepare_mail_apply",
    "Prepare applying a reviewed mail triage/cleanup plan JSON. "
    "Show plan summary to the user before Touch ID.",
    PREPARE_ANN,
)
def icloud_prepare_mail_apply(plan_file: str) -> dict[str, Any]:
    try:
        source_path = services.resolve_plan_path(plan_file)
        plan = services.freeze_mail_plan(services.load_valid_mail_plan(source_path))
        action = plan["action"]
        dest = plan.get("destination")
        messages = plan["messages"]
        folder = plan["folder"]
        plan_hash = services.canonical_sha256(plan)
        uids = ", ".join(str(message["uid"]) for message in messages) or "(none)"
        preview = (
            f"Apply frozen mail plan {source_path.name}\n"
            f"Folder: {folder}\nUIDVALIDITY: {plan['uidvalidity']}\n"
            f"Action: {action}\nDestination: {dest}\nMessages: {len(messages)}\n"
            f"UIDs: {uids}\nPlan SHA-256: {plan_hash}"
        )
        return approval.prepare_action(
            action="mail.apply",
            target=f"mail:{folder}",
            preview=preview,
            payload={"plan": plan, "planSha256": plan_hash},
        )
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool(
    "icloud_prepare_mail_cleanup_strict",
    "Prepare strict bulk-sender cleanup (move known bulk senders to Trash). "
    "Show summary before Touch ID.",
    PREPARE_ANN,
)
def icloud_prepare_mail_cleanup_strict(
    folder: str = "INBOX", sync: bool = True
) -> dict[str, Any]:
    try:
        plan = services.prepare_strict_mail_plan(folder=folder, sync=sync)
        plan_hash = services.canonical_sha256(plan)
        messages = plan["messages"]
        uids = ", ".join(str(message["uid"]) for message in messages) or "(none)"
        preview = (
            f"Strict cleanup on {folder}\n"
            f"Frozen messages: {len(messages)}\nUIDVALIDITY: {plan['uidvalidity']}\n"
            f"UIDs: {uids}\nPlan SHA-256: {plan_hash}\n"
            "Moves known bulk senders to Deleted Messages (iCloud Trash ~30 days).\n"
            "No local .eml backup is created."
        )
        return approval.prepare_action(
            action="mail.cleanup_strict",
            target=f"mail:{folder}",
            preview=preview,
            payload={"plan": plan, "planSha256": plan_hash},
        )
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool(
    "icloud_prepare_mail_send",
    "Prepare sending an iCloud Mail message via SMTP. From is always the Keychain account. "
    "No attachments. Show exact recipients/subject/body before Touch ID.",
    PREPARE_ANN,
)
def icloud_prepare_mail_send(
    to: list[str] | str,
    subject: str,
    body: str,
    cc: list[str] | str | None = None,
    bcc: list[str] | str | None = None,
) -> dict[str, Any]:
    try:
        frozen = services.prepare_mail_send(
            to=to, subject=subject, body=body, cc=cc, bcc=bcc
        )
        recipients = ", ".join(frozen["to"])
        cc_line = ", ".join(frozen["cc"]) or "(none)"
        bcc_line = ", ".join(frozen["bcc"]) or "(none)"
        preview = (
            f"Send iCloud Mail\nFrom: {frozen['from']}\nTo: {recipients}\n"
            f"Cc: {cc_line}\nBcc: {bcc_line}\nSubject: {frozen['subject']}\n"
            f"Body chars: {len(frozen['body'])}\nMessage-ID: {frozen['messageId']}\n"
            f"Plan SHA-256: {frozen['planSha256']}"
        )
        return approval.prepare_action(
            action="mail.send",
            target=f"mail:send:{frozen['messageId']}",
            preview=preview,
            payload=frozen,
        )
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def _mail_mutation_preview(title: str, plan: dict[str, Any]) -> tuple[str, str]:
    plan_hash = services.canonical_sha256(plan)
    messages = plan["messages"]
    uids = ", ".join(str(message["uid"]) for message in messages) or "(none)"
    subjects = "; ".join(
        (message.get("subject") or "(no subject)")[:40] for message in messages[:8]
    )
    extra = ""
    if plan["action"] == "flags":
        extra = f"Flag: {plan['flag']}\n"
    elif plan.get("destination"):
        extra = f"Destination: {plan['destination']}\n"
    preview = (
        f"{title}\nFolder: {plan['folder']}\nUIDVALIDITY: {plan['uidvalidity']}\n"
        f"Action: {plan['action']}\n{extra}"
        f"Messages: {len(messages)}\nUIDs: {uids}\n"
        f"Subjects: {subjects}\nPlan SHA-256: {plan_hash}"
    )
    return preview, plan_hash


@_tool(
    "icloud_prepare_mail_flags",
    "Prepare marking cached messages read or unread (IMAP \\Seen). "
    "UIDs must already be in the folder cache. Show exact UIDs before Touch ID.",
    PREPARE_ANN,
)
def icloud_prepare_mail_flags(
    uids: list[int] | str,
    folder: str = "INBOX",
    seen: bool = True,
) -> dict[str, Any]:
    try:
        plan = services.prepare_mail_flags(folder=folder, uids=uids, seen=seen)
        title = "Mark mail read" if seen else "Mark mail unread"
        preview, plan_hash = _mail_mutation_preview(title, plan)
        return approval.prepare_action(
            action="mail.flags",
            target=f"mail:{folder}",
            preview=preview,
            payload={"plan": plan, "planSha256": plan_hash},
        )
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool(
    "icloud_prepare_mail_move",
    "Prepare moving cached messages to another IMAP folder. "
    "UIDs must already be in the source folder cache.",
    PREPARE_ANN,
)
def icloud_prepare_mail_move(
    uids: list[int] | str,
    destination: str,
    folder: str = "INBOX",
) -> dict[str, Any]:
    try:
        plan = services.prepare_mail_move(
            folder=folder, uids=uids, destination=destination
        )
        preview, plan_hash = _mail_mutation_preview(
            f"Move mail to {destination}", plan
        )
        return approval.prepare_action(
            action="mail.move",
            target=f"mail:{folder}->{destination}",
            preview=preview,
            payload={"plan": plan, "planSha256": plan_hash},
        )
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool(
    "icloud_prepare_mail_trash",
    "Prepare moving cached messages to Deleted Messages (iCloud Trash, ~30 days). "
    "UIDs must already be in the folder cache.",
    PREPARE_ANN,
)
def icloud_prepare_mail_trash(
    uids: list[int] | str,
    folder: str = "INBOX",
) -> dict[str, Any]:
    try:
        plan = services.prepare_mail_trash(folder=folder, uids=uids)
        preview, plan_hash = _mail_mutation_preview("Trash mail", plan)
        return approval.prepare_action(
            action="mail.trash",
            target=f"mail:{folder}->Deleted Messages",
            preview=preview,
            payload={"plan": plan, "planSha256": plan_hash},
        )
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool(
    "icloud_prepare_contacts_create",
    "Prepare creating an iCloud contact. Show exact fields before Touch ID.",
    PREPARE_ANN,
)
def icloud_prepare_contacts_create(
    name: str | None = None,
    first: str | None = None,
    last: str | None = None,
    org: str | None = None,
    emails: list[str] | None = None,
    phones: list[str] | None = None,
) -> dict[str, Any]:
    try:
        collection = services.prepare_contact_collection()
        uid = services.new_resource_uid()
        full = name or " ".join(p for p in (first, last) if p)
        preview = (
            f"Create contact\nUID: {uid}\nName: {full}\nOrg: {org or ''}\n"
            f"Emails: {', '.join(emails or [])}\nPhones: {', '.join(phones or [])}"
        )
        return approval.prepare_action(
            action="contacts.create",
            target=f"contact:{uid}",
            preview=preview,
            payload={
                "name": name,
                "first": first,
                "last": last,
                "org": org,
                "emails": emails or [],
                "phones": phones or [],
                "addressbookUrl": collection["url"],
                "uid": uid,
            },
        )
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool(
    "icloud_prepare_contacts_update",
    "Prepare updating a contact by UID or unique search term.",
    PREPARE_ANN,
)
def icloud_prepare_contacts_update(
    selector: str,
    name: str | None = None,
    first: str | None = None,
    last: str | None = None,
    org: str | None = None,
    add_emails: list[str] | None = None,
    add_phones: list[str] | None = None,
    set_emails: list[str] | None = None,
    set_phones: list[str] | None = None,
) -> dict[str, Any]:
    try:
        target = services.prepare_contact_update(selector)
        preview = (
            f"Update contact {target['full_name']}\nUID: {target['uid']}\n"
            f"ETag: {target['etag']}\n"
            f"name={name} first={first} last={last} org={org}\n"
            f"addEmails={add_emails} addPhones={add_phones}\n"
            f"setEmails={set_emails} setPhones={set_phones}"
        )
        return approval.prepare_action(
            action="contacts.update",
            target=f"contact:{target['uid']}",
            preview=preview,
            payload={
                "target": target,
                "name": name,
                "first": first,
                "last": last,
                "org": org,
                "addEmails": add_emails,
                "addPhones": add_phones,
                "setEmails": set_emails,
                "setPhones": set_phones,
            },
        )
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool(
    "icloud_prepare_contacts_delete",
    "Prepare deleting contacts matching a query. vCards are backed up first on execute.",
    PREPARE_ANN,
)
def icloud_prepare_contacts_delete(query: str) -> dict[str, Any]:
    try:
        matches = services.prepare_contact_delete(query)
        names = ", ".join(
            f"{contact['full_name']} [{contact['uid']}]" for contact in matches[:10]
        )
        preview = f"Delete frozen contacts matching {query!r}\nMatches: {len(matches)}\n{names}"
        return approval.prepare_action(
            action="contacts.delete",
            target=f"contacts:{len(matches)}-exact-targets",
            preview=preview,
            payload={"targets": matches},
        )
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool(
    "icloud_prepare_event_add",
    "Prepare creating a calendar event. Dates: YYYY-MM-DD or YYYY-MM-DD HH:MM.",
    PREPARE_ANN,
)
def icloud_prepare_event_add(
    title: str,
    start: str,
    end: str | None = None,
    location: str | None = None,
    calendar: str | None = None,
    all_day: bool = False,
) -> dict[str, Any]:
    try:
        collection = services.prepare_calendar_collection(calendar, events=True)
        uid = services.new_resource_uid()
        preview = (
            f"Create event\nUID: {uid}\nTitle: {title}\nStart: {start}\nEnd: {end}\n"
            f"Location: {location or ''}\nCalendar: {calendar or '(default)'}\nAll-day: {all_day}"
        )
        return approval.prepare_action(
            action="calendar.event_add",
            target=f"event:{uid}",
            preview=preview,
            payload={
                "title": title,
                "start": start,
                "end": end,
                "location": location,
                "calendar": calendar,
                "allDay": all_day,
                "collection": collection,
                "uid": uid,
            },
        )
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool(
    "icloud_prepare_event_rm",
    "Prepare deleting a calendar event by UID or unique match.",
    PREPARE_ANN,
)
def icloud_prepare_event_rm(query: str, days: int = 365) -> dict[str, Any]:
    try:
        target = services.prepare_event_target(query, days=days)
        preview = (
            f"Delete calendar event\nTitle: {target['summary']}\nUID: {target['uid']}\n"
            f"Start: {target['start']}\nETag: {target['etag']}"
        )
        return approval.prepare_action(
            action="calendar.event_rm",
            target=f"event:{target['uid']}",
            preview=preview,
            payload={"target": target},
        )
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool(
    "icloud_prepare_event_update",
    "Prepare updating a calendar event by UID or unique match. "
    "Only provided fields change; RRULE/VALARM/X-* are preserved.",
    PREPARE_ANN,
)
def icloud_prepare_event_update(
    query: str,
    title: str | None = None,
    start: str | None = None,
    end: str | None = None,
    location: str | None = None,
    all_day: bool | None = None,
    days: int = 365,
) -> dict[str, Any]:
    try:
        if all(value is None for value in (title, start, end, location)):
            raise services.ServiceError("Provide at least one event field to update.")
        target = services.prepare_event_target(query, days=days)
        preview = (
            f"Update calendar event\nTitle: {target['summary']}\nUID: {target['uid']}\n"
            f"Start: {target['start']}\nEnd: {target['end']}\n"
            f"Location: {target['location']}\nETag: {target['etag']}\n"
            f"New title={title}\nNew start={start}\nNew end={end}\n"
            f"New location={location}\nAll-day: {all_day}"
        )
        return approval.prepare_action(
            action="calendar.event_update",
            target=f"event:{target['uid']}",
            preview=preview,
            payload={
                "target": target,
                "title": title,
                "start": start,
                "end": end,
                "location": location,
                "allDay": all_day,
            },
        )
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool("icloud_prepare_reminder_add", "Prepare creating a reminder.", PREPARE_ANN)
def icloud_prepare_reminder_add(
    title: str, due: str | None = None, reminder_list: str | None = None
) -> dict[str, Any]:
    try:
        collection = services.prepare_calendar_collection(
            reminder_list,
            events=False,
        )
        uid = services.new_resource_uid()
        preview = (
            f"Create reminder\nUID: {uid}\nTitle: {title}\nDue: {due or ''}\n"
            f"List: {reminder_list or '(default)'}"
        )
        return approval.prepare_action(
            action="calendar.reminder_add",
            target=f"reminder:{uid}",
            preview=preview,
            payload={"uid": uid, "title": title, "due": due, "collection": collection},
        )
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool("icloud_prepare_reminder_done", "Prepare marking a reminder complete.", PREPARE_ANN)
def icloud_prepare_reminder_done(query: str) -> dict[str, Any]:
    try:
        target = services.prepare_reminder_target(query)
        preview = (
            f"Mark reminder complete\nTitle: {target['summary']}\n"
            f"UID: {target['uid']}\nETag: {target['etag']}"
        )
        return approval.prepare_action(
            action="calendar.reminder_done",
            target=f"reminder:{target['uid']}",
            preview=preview,
            payload={"target": target},
        )
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool("icloud_prepare_reminder_rm", "Prepare deleting a reminder.", PREPARE_ANN)
def icloud_prepare_reminder_rm(query: str) -> dict[str, Any]:
    try:
        target = services.prepare_reminder_target(query)
        preview = (
            f"Delete reminder\nTitle: {target['summary']}\n"
            f"UID: {target['uid']}\nETag: {target['etag']}"
        )
        return approval.prepare_action(
            action="calendar.reminder_rm",
            target=f"reminder:{target['uid']}",
            preview=preview,
            payload={"target": target},
        )
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool(
    "icloud_prepare_reminder_update",
    "Prepare updating a reminder by UID or unique match. "
    "Only provided fields change; RRULE/VALARM/X-* are preserved. "
    "Pass due='' to clear the due date.",
    PREPARE_ANN,
)
def icloud_prepare_reminder_update(
    query: str,
    title: str | None = None,
    due: str | None = None,
) -> dict[str, Any]:
    try:
        if title is None and due is None:
            raise services.ServiceError("Provide at least one of title or due to update.")
        target = services.prepare_reminder_target(query)
        preview = (
            f"Update reminder\nTitle: {target['summary']}\nUID: {target['uid']}\n"
            f"Due: {target.get('start') or '(none)'}\nETag: {target['etag']}\n"
            f"New title={title}\nNew due={due}"
        )
        return approval.prepare_action(
            action="calendar.reminder_update",
            target=f"reminder:{target['uid']}",
            preview=preview,
            payload={"target": target, "title": title, "due": due},
        )
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool(
    "icloud_prepare_messages_send",
    "Prepare sending an iMessage/SMS via Messages.app. "
    "Show exact recipient and text before Touch ID.",
    PREPARE_ANN,
)
def icloud_prepare_messages_send(
    to: str, text: str, service: str = "imessage"
) -> dict[str, Any]:
    try:
        if service not in {"imessage", "sms"}:
            raise services.ServiceError("service must be imessage or sms")
        if not text.strip():
            raise services.ServiceError("text cannot be empty")
        if len(text) > 10_000:
            raise services.ServiceError("text exceeds 10,000 characters")
        preview = f"Send via {service} to {to}\n\n{text}"
        return approval.prepare_action(
            action="messages.send",
            target=f"messages:{to}",
            preview=preview,
            payload={"to": to, "text": text, "service": service},
        )
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool("icloud_prepare_notes_create", "Prepare creating a Notes.app note.", PREPARE_ANN)
def icloud_prepare_notes_create(title: str, body: str | None = None) -> dict[str, Any]:
    try:
        preview = f"Create note\nTitle: {title}\n\n{body or ''}"
        return approval.prepare_action(
            action="notes.create",
            target=f"note:{title}",
            preview=preview,
            payload={"title": title, "body": body or ""},
        )
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool(
    "icloud_prepare_notes_delete",
    "Prepare deleting a note (body backed up first).",
    PREPARE_ANN,
)
def icloud_prepare_notes_delete(query: str) -> dict[str, Any]:
    try:
        target = services.prepare_note_delete(query)
        preview = (
            f"Delete note\nName: {target['name']}\nID: {target['id']}\n"
            f"Modified: {target['modified']}\nBody SHA-256: {target['bodySha256']}\n"
            "The exact approved body will be backed up first."
        )
        return approval.prepare_action(
            action="notes.delete",
            target=f"note:{target['id']}",
            preview=preview,
            payload={"target": target},
        )
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool(
    "icloud_prepare_notes_update",
    "Prepare updating a note title and/or body (body backed up first). "
    "Provide at least one of title or body.",
    PREPARE_ANN,
)
def icloud_prepare_notes_update(
    query: str,
    title: str | None = None,
    body: str | None = None,
) -> dict[str, Any]:
    try:
        if title is None and body is None:
            raise services.ServiceError("Provide at least one of title or body to update.")
        target = services.prepare_note_update(query)
        preview = (
            f"Update note\nName: {target['name']}\nID: {target['id']}\n"
            f"Modified: {target['modified']}\nBody SHA-256: {target['bodySha256']}\n"
            f"New title={title}\nNew body chars={len(body) if body is not None else '(unchanged)'}\n"
            "The exact approved body will be backed up first."
        )
        return approval.prepare_action(
            action="notes.update",
            target=f"note:{target['id']}",
            preview=preview,
            payload={"target": target, "title": title, "body": body},
        )
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool(
    "icloud_prepare_drive_put",
    "Prepare copying a local file into iCloud Drive.",
    PREPARE_ANN,
)
def icloud_prepare_drive_put(
    local: str,
    dest: str,
    overwrite: bool = False,
) -> dict[str, Any]:
    try:
        frozen = services.prepare_drive_put(local, dest, overwrite=overwrite)
        source = frozen["source"]
        destination = frozen["destination"]
        preview = (
            f"Copy frozen local file into iCloud Drive\nLocal: {source['path']}\n"
            f"Bytes: {source['size']}\nSHA-256: {source['sha256']}\n"
            f"Dest: {destination['relative']}\nOverwrite existing: {destination['existed']}"
        )
        return approval.prepare_action(
            action="drive.put",
            target=f"drive:{destination['relative']}",
            preview=preview,
            payload=frozen,
        )
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool(
    "icloud_prepare_drive_rm",
    "Prepare moving an iCloud Drive item to Trash (never permanent delete).",
    PREPARE_ANN,
)
def icloud_prepare_drive_rm(path: str) -> dict[str, Any]:
    try:
        frozen = services.prepare_drive_remove(path)
        preview = (
            f"Move exact item to Trash (Finder)\nPath: {frozen['relative']}\n"
            f"Directory: {frozen['isDirectory']}\nBytes: {frozen['size']}\n"
            f"SHA-256: {frozen['sha256'] or '(directory identity)'}"
        )
        return approval.prepare_action(
            action="drive.rm",
            target=f"drive:{frozen['relative']}",
            preview=preview,
            payload={"target": frozen},
        )
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool(
    "icloud_prepare_photos_export",
    "Prepare exporting locally-downloaded Photos originals to a directory.",
    PREPARE_ANN,
)
def icloud_prepare_photos_export(
    dest: str,
    album: str | None = None,
    kind: str | None = None,
    favorites: bool = False,
    limit: int = 200,
) -> dict[str, Any]:
    try:
        frozen = services.prepare_photos_export(
            dest=dest,
            album=album,
            kind=kind,
            favorites=favorites,
            limit=limit,
        )
        uuids = ", ".join(asset["uuid"] for asset in frozen["assets"]) or "(none)"
        preview = (
            f"Export frozen Photos originals to {frozen['destination']}\n"
            f"album={album} kind={kind} favorites={favorites} limit={limit}\n"
            f"Selected: {frozen['selected']}\nDownloaded: {len(frozen['assets'])}\n"
            f"Not downloaded: {len(frozen['notDownloaded'])}\nAsset UUIDs: {uuids}"
        )
        return approval.prepare_action(
            action="photos.export",
            target=f"photos:{frozen['destination']}",
            preview=preview,
            payload=frozen,
        )
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool(
    "icloud_prepare_safari_open_url",
    "Prepare opening an http(s) URL in Safari (new tab or new window). "
    "Rejects javascript:/file:/data: and URLs without an explicit scheme.",
    PREPARE_ANN,
)
def icloud_prepare_safari_open_url(url: str, target: str = "new_tab") -> dict[str, Any]:
    try:
        frozen = services.prepare_safari_open_url(url, target=target)
        preview = (
            f"Open URL in Safari\nURL: {frozen['url']}\n"
            f"Target: {frozen['target']}\n"
            "Only the frozen http(s) URL will be opened."
        )
        return approval.prepare_action(
            action="safari.open_url",
            target=f"safari:{frozen['url']}",
            preview=preview,
            payload=frozen,
        )
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def _music_playback_preview(action: str) -> dict[str, Any]:
    frozen = services.prepare_music_playback(action)
    now = frozen["nowPlaying"]
    now_line = now.get("name") or now.get("state") or "stopped"
    artist = now.get("artist")
    if artist:
        now_line = f"{now_line} — {artist}"
    preview = (
        f"Music {action}\nNow playing: {now_line}\n"
        f"State: {now.get('state')}\n"
        "Executes the constant Music.app command after Touch ID."
    )
    return approval.prepare_action(
        action=f"music.{action}",
        target=f"music:{action}",
        preview=preview,
        payload=frozen,
    )


@_tool(
    "icloud_prepare_music_playpause",
    "Prepare toggling Music.app play/pause. Preview snapshots now-playing.",
    PREPARE_ANN,
)
def icloud_prepare_music_playpause() -> dict[str, Any]:
    try:
        return _music_playback_preview("playpause")
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool(
    "icloud_prepare_music_next",
    "Prepare skipping to the next Music.app track. Preview snapshots now-playing.",
    PREPARE_ANN,
)
def icloud_prepare_music_next() -> dict[str, Any]:
    try:
        return _music_playback_preview("next")
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool(
    "icloud_prepare_music_previous",
    "Prepare skipping to the previous Music.app track. Preview snapshots now-playing.",
    PREPARE_ANN,
)
def icloud_prepare_music_previous() -> dict[str, Any]:
    try:
        return _music_playback_preview("previous")
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


# ---------------------------------------------------------------------------
# Approval gate
# ---------------------------------------------------------------------------


@_tool(
    "icloud_request_local_approval",
    "EXTERNALLY VISIBLE / MUTATING ACTION: opens a native macOS dialog with the "
    "immutable target, action type, and exact prepared preview. Executes only after "
    "Touch ID or macOS login-password authorization. Call only after showing the preview "
    "and receiving explicit user OK in chat.",
    MUTATE_ANN,
)
def icloud_request_local_approval(approval_id: str) -> dict[str, Any]:
    try:
        return approval.request_local_approval(approval_id)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@_tool(
    "icloud_action_outcome",
    "Read the durable outcome of a prepared action by approval ID. Use after timeout; "
    "never prepare a duplicate while state is executing or outcome-unknown.",
    READ_ANN,
)
def icloud_action_outcome(approval_id: str) -> dict[str, Any]:
    try:
        outcome = approval.get_outcome(approval_id)
        draft = approval.STORE.get(approval_id)
        if draft:
            current = draft.to_public()
            if outcome:
                current["lastAttempt"] = outcome.get("lastAttempt")
            return current
        if not outcome:
            return {
                "approvalId": approval_id,
                "state": "unknown",
                "error": "No outcome or draft found.",
            }
        if outcome.get("state") in {"prepared", "awaiting-local-approval", "executing"}:
            return {
                **outcome,
                "previousState": outcome["state"],
                "state": "orphaned",
                "error": "The MCP process restarted; this transient action cannot resume.",
            }
        return outcome
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------


def main() -> None:
    services.register_all_executors()
    # Pre-compile helper so first approval is faster / fails early if swiftc missing.
    try:
        approval.ensure_helper_binary()
    except Exception as exc:  # noqa: BLE001
        print(f"icloudseal-mcp: warning: native helper not ready: {exc}", file=sys.stderr)

    try:
        import anyio

        anyio.run(server.run_stdio_async)
    except KeyboardInterrupt:
        pass
    except Exception:
        traceback.print_exc(file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
