"""JSON service layer over existing domain modules.

Read helpers return structured dict/list payloads for MCP tools.
Mutating helpers perform the actual side effect and are only invoked after
Touch ID approval (via ``approval.register_executor``).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

from .. import auth
from ..calendar import caldav
from ..calendar.caldav import CalendarSession, CalItem
from ..common import parse_age_cutoff, parse_since, write_json_file
from ..contacts import carddav
from ..contacts.carddav import Contact, ContactsSession
from ..drive import commands as drive_commands
from ..health import health_status
from ..mail import cache, cleanup, jobs
from ..mail.imap_client import IMAPError, open_session
from ..mail.smtp_client import (
    MAX_ATTACHMENT_BYTES,
    SMTPError,
    freeze_send,
    send_frozen,
)
from ..maps import urls as maps_urls
from ..maps.urls import MapsError
from ..messages import chatdb
from ..messages import commands as messages_commands
from ..messages.chatdb import MessagesAccessError
from ..music import applescript as music_script
from ..music.applescript import MusicError
from ..notes import applescript
from ..notes.applescript import NotesError
from ..ops import generate_mail_cleanup_plist, write_mail_cleanup_plist
from ..paths import (
    APP_DIR,
    BACKUP_DIR,
    default_plan_path,
    export_path,
    now_utc_iso,
    plan_path,
    timestamp_slug,
)
from ..photos import applescript as photos_script
from ..photos import photosdb
from ..photos.applescript import PhotosScriptError
from ..photos.photosdb import PhotosAccessError
from ..safari import applescript as safari_script
from ..safari.applescript import SafariError
from ..weather import client as weather_client
from ..weather.client import WeatherError

DRIVE_ROOT = drive_commands.DRIVE_ROOT
MAX_READ_BYTES = drive_commands.MAX_READ_BYTES

SEND_HASH_KEYS = (
    "from",
    "to",
    "cc",
    "bcc",
    "subject",
    "body",
    "messageId",
    "inReplyTo",
    "references",
    "attachments",
)
ALLOWED_MAIL_FLAGS = {
    "+Seen": r"(\Seen)",
    "-Seen": r"(\Seen)",
    "+Flagged": r"(\Flagged)",
    "-Flagged": r"(\Flagged)",
    "+Answered": r"(\Answered)",
    "-Answered": r"(\Answered)",
}
MAIL_FLAG_ADD = frozenset({"+Seen", "+Flagged", "+Answered"})


class ServiceError(RuntimeError):
    """Domain/service failure with a clean message for agents."""


# ---------------------------------------------------------------------------
# Mail plan validation / freezing
# ---------------------------------------------------------------------------

def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_snapshot(path: Path) -> dict[str, Any]:
    stat_result = path.stat()
    return {
        "path": str(path),
        "size": stat_result.st_size,
        "mtimeNs": stat_result.st_mtime_ns,
        "sha256": _file_sha256(path),
    }


def _snapshot_matches(path: Path, snapshot: dict[str, Any]) -> bool:
    if not path.is_file() or str(path) != snapshot.get("path"):
        return False
    stat_result = path.stat()
    return (
        stat_result.st_size == snapshot.get("size")
        and stat_result.st_mtime_ns == snapshot.get("mtimeNs")
        and _file_sha256(path) == snapshot.get("sha256")
    )


def _tree_snapshot(root: Path, *, maximum_entries: int = 10_000) -> dict[str, Any]:
    """Hash directory membership, metadata, links, and file contents."""
    digest = hashlib.sha256()
    entries = 0
    for current, dir_names, file_names in os.walk(root, followlinks=False):
        dir_names.sort()
        file_names.sort()
        for name in [*dir_names, *file_names]:
            entries += 1
            if entries > maximum_entries:
                raise ServiceError(
                    f"Drive directory approvals are limited to {maximum_entries} entries."
                )
            path = Path(current) / name
            relative = path.relative_to(root).as_posix()
            stat_result = path.lstat()
            digest.update(relative.encode("utf-8", errors="surrogateescape"))
            digest.update(
                f"\0{stat_result.st_mode}\0{stat_result.st_size}\0"
                f"{stat_result.st_mtime_ns}\0{stat_result.st_ino}\0".encode()
            )
            if path.is_symlink():
                digest.update(os.readlink(path).encode("utf-8", errors="surrogateescape"))
            elif path.is_file():
                digest.update(_file_sha256(path).encode("ascii"))
    return {"entries": entries, "sha256": digest.hexdigest()}


def new_resource_uid() -> str:
    return str(uuid.uuid4()).upper()


# ---------------------------------------------------------------------------
# Status / doctor
# ---------------------------------------------------------------------------

def doctor() -> dict[str, Any]:
    email = None
    creds_ok = False
    creds_error = None
    try:
        if auth.EMAIL_FILE.exists():
            email = auth.EMAIL_FILE.read_text().strip()
        creds = auth.load_credentials()
        email = creds.email
        creds_ok = bool(creds.password)
    except Exception as exc:  # noqa: BLE001
        creds_error = str(exc)

    messages = _probe_messages()
    notes = _probe_notes()
    photos = _probe_photos()
    safari = _probe_safari()
    music = _probe_music()
    health = health_status()
    drive = {
        "root": str(DRIVE_ROOT),
        "exists": DRIVE_ROOT.exists(),
    }

    ready = bool(creds_ok)
    phase = "ready" if ready else "needs_setup"
    steps: list[str] = []
    if not ready:
        steps.extend(
            [
                "Run: icloudseal-mcp mail setup --email <you@icloud.com>",
                "Use an app-specific password from https://appleid.apple.com",
            ]
        )
    if messages.get("ok") is False:
        steps.append(
            "Grant Full Disk Access to Terminal/IDE for Messages (chat.db) "
            "if SMS/iMessage reads are needed."
        )
    if photos.get("ok") is False:
        steps.append("Grant Full Disk Access for Photos.sqlite if photo tools are needed.")
    if notes.get("ok") is False:
        steps.append("Grant Automation access for Notes.app if note tools are needed.")
    if safari.get("ok") is False:
        steps.append("Grant Automation access for Safari if Safari tools are needed.")
    if music.get("ok") is False:
        steps.append("Grant Automation access for Music.app if Music tools are needed.")

    return {
        "ok": ready,
        "phase": phase,
        "ready": ready,
        "email": email,
        "credentials": {"ok": creds_ok, "error": creds_error, "service": auth.SERVICE_NAME},
        "domains": {
            "mail": {"transport": "IMAP + SMTP", "ready": creds_ok},
            "contacts": {"transport": "CardDAV", "ready": creds_ok},
            "calendar": {"transport": "CalDAV", "ready": creds_ok},
            "messages": messages,
            "notes": notes,
            "drive": drive,
            "photos": photos,
            "safari": safari,
            "music": music,
            "weather": {
                "ok": True,
                "transport": "Open-Meteo HTTPS",
                "ready": True,
                "needsNetwork": True,
            },
            "maps": {
                "ok": True,
                "transport": "maps.apple.com URL",
                "ready": True,
            },
            "health": health,
        },
        "workflow": {
            "firstCall": "icloud_doctor",
            "reads": "icloud_* list/search/read tools (no Touch ID)",
            "mutations": (
                "icloud_prepare_* → show preview → user OK → "
                "icloud_request_local_approval"
            ),
            "afterTimeout": "icloud_action_outcome",
        },
        "agentNextSteps": steps
        or [
            "Call domain read tools (mail/contacts/calendar/messages/notes/"
            "drive/photos/safari/music/weather/maps).",
            "For mutations: prepare_* then request_local_approval after explicit user OK.",
        ],
        "userMessage": (
            f"icloudseal-mcp ready for {email}."
            if ready
            else "icloudseal-mcp needs Keychain credentials (mail setup)."
        ),
    }


def status() -> dict[str, Any]:
    d = doctor()
    return {
        "ready": d["ready"],
        "phase": d["phase"],
        "email": d.get("email"),
        "credentials": d["credentials"],
        "userMessage": d["userMessage"],
        "agentNextSteps": d["agentNextSteps"],
    }


def security_audit() -> dict[str, Any]:
    from . import approval as approval_mod

    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    email_file = auth.EMAIL_FILE
    check(
        "app-support-dir",
        auth.CONFIG_DIR.exists(),
        str(auth.CONFIG_DIR),
    )
    check(
        "email-file-mode",
        email_file.exists(),
        oct(email_file.stat().st_mode & 0o777) if email_file.exists() else "missing",
    )
    try:
        helper = approval_mod.ensure_helper_binary()
        check("native-approval-helper", helper.exists(), str(helper))
    except Exception as exc:  # noqa: BLE001
        check("native-approval-helper", False, str(exc))

    check(
        "keychain-service",
        True,
        f"service={auth.SERVICE_NAME} (generic-password, not plaintext)",
    )
    check(
        "no-tcp-listener",
        True,
        "MCP is stdio-only; domain clients use IMAP/DAV/local DB/"
        "AppleScript/Open-Meteo/maps.apple.com",
    )
    return {
        "ok": all(
            check["ok"]
            for check in checks
            if check["name"] != "email-file-mode" or auth.EMAIL_FILE.exists()
        ),
        "checks": checks,
    }


def _probe_messages() -> dict[str, Any]:
    try:
        chats = chatdb.list_chats(limit=1)
        return {"ok": True, "sampleChats": len(chats), "needsFullDiskAccess": False}
    except MessagesAccessError as exc:
        return {"ok": False, "error": str(exc), "needsFullDiskAccess": True}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def _probe_notes() -> dict[str, Any]:
    try:
        notes = applescript.list_notes()
        return {"ok": True, "count": len(notes)}
    except NotesError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def _probe_photos() -> dict[str, Any]:
    try:
        s = photosdb.stats()
        return {"ok": True, "stats": s}
    except PhotosAccessError as exc:
        return {"ok": False, "error": str(exc), "needsFullDiskAccess": True}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def _probe_safari() -> dict[str, Any]:
    try:
        running = safari_script.safari_is_running()
        return {
            "ok": True,
            "running": running,
            "transport": "AppleScript",
            "needsAutomation": True,
        }
    except SafariError as exc:
        return {"ok": False, "error": str(exc), "needsAutomation": True}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "needsAutomation": True}


def _probe_music() -> dict[str, Any]:
    try:
        running = music_script.music_is_running()
        return {
            "ok": True,
            "running": running,
            "transport": "AppleScript",
            "needsAutomation": True,
        }
    except MusicError as exc:
        return {"ok": False, "error": str(exc), "needsAutomation": True}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "needsAutomation": True}


# ---------------------------------------------------------------------------
# Mail
# ---------------------------------------------------------------------------

def mail_stats() -> list[dict[str, Any]]:
    creds = auth.load_credentials()
    with open_session(creds.email, creds.password) as imap:
        folders = imap.list_folders()
        rows = []
        for f in folders:
            if r"\Noselect" in f.flags:
                continue
            try:
                count = imap.folder_count(f.name)
            except Exception:  # noqa: BLE001
                count = -1
            rows.append({"folder": f.name, "count": count, "flags": list(f.flags)})
        return rows


def mail_sync(*, folder: str = "INBOX", since: str | None = None) -> dict[str, Any]:
    since_imap = parse_since(since)
    criteria = f"SINCE {since_imap}" if since_imap else "ALL"
    creds = auth.load_credentials()
    with open_session(creds.email, creds.password) as imap:
        total = imap.select(folder, readonly=True)
        uids = imap.search_uids(criteria)
        written = 0
        if uids:
            with cache.connect() as conn:
                buffer: list = []
                for meta in imap.fetch_metadata(uids):
                    buffer.append(meta)
                    if len(buffer) >= 100:
                        written += cache.upsert_messages(conn, buffer)
                        buffer.clear()
                if buffer:
                    written += cache.upsert_messages(conn, buffer)
                cache.update_sync_state(conn, folder, max(uids), total)
        return {
            "folder": folder,
            "serverTotal": total,
            "matched": len(uids),
            "cached": written,
            "criteria": criteria,
        }


def mail_list(*, folder: str = "INBOX", limit: int = 50) -> list[dict[str, Any]]:
    with cache.connect() as conn:
        rows = cache.list_messages(conn, folder, limit=limit)
    return [dict(r) for r in rows]


def mail_senders(*, folder: str | None = "INBOX", top: int = 30) -> list[dict[str, Any]]:
    with cache.connect() as conn:
        rows = cache.top_senders(conn, folder, limit=top)
    return [dict(r) for r in rows]


def mail_peek(*, uid: int, folder: str = "INBOX", max_body_chars: int = 8000) -> dict[str, Any]:
    from ..mail.commands import _plain_body

    creds = auth.load_credentials()
    with open_session(creds.email, creds.password) as imap:
        imap.select(folder, readonly=True)
        msg = imap.fetch_body(uid)
    body_text = _plain_body(msg)
    return {
        "uid": uid,
        "folder": folder,
        "from": msg.get("From"),
        "to": msg.get("To"),
        "subject": msg.get("Subject"),
        "date": msg.get("Date"),
        "message_id": msg.get("Message-ID"),
        "body": body_text[:max_body_chars],
        "truncated": len(body_text) > max_body_chars,
    }


def _forward_subject(subject: str | None) -> str:
    text = (subject or "(no subject)").strip() or "(no subject)"
    if text.lower().startswith("fwd:"):
        return text
    return f"Fwd: {text}"


def build_forward_send(
    *,
    source: dict[str, Any],
    to: list[str] | str,
    note: str | None = None,
    cc: list[str] | str | None = None,
    bcc: list[str] | str | None = None,
    attachments: list[str] | str | None = None,
) -> dict[str, Any]:
    """Freeze a forward using the existing SMTP send contract."""
    original_subject = source.get("subject") or "(no subject)"
    header = (
        "---------- Forwarded message ----------\n"
        f"From: {source.get('from') or '(unknown)'}\n"
        f"Date: {source.get('date') or ''}\n"
        f"To: {source.get('to') or ''}\n"
        f"Subject: {original_subject}\n\n"
        f"{source.get('body') or ''}"
    )
    extra = (note or "").rstrip()
    body = f"{extra}\n\n{header}" if extra else header
    mid = source.get("message_id") or None
    return prepare_mail_send(
        to=to,
        subject=_forward_subject(str(original_subject)),
        body=body,
        cc=cc,
        bcc=bcc,
        in_reply_to=mid,
        references=mid,
        attachments=attachments,
    )


def prepare_mail_forward(
    *,
    uid: int,
    to: list[str] | str,
    folder: str = "INBOX",
    note: str | None = None,
    cc: list[str] | str | None = None,
    bcc: list[str] | str | None = None,
    attachments: list[str] | str | None = None,
) -> dict[str, Any]:
    source = mail_peek(uid=uid, folder=folder)
    frozen = build_forward_send(
        source=source,
        to=to,
        note=note,
        cc=cc,
        bcc=bcc,
        attachments=attachments,
    )
    frozen["sourceUid"] = uid
    frozen["sourceFolder"] = folder
    return frozen


def exec_mail_forward(payload: dict[str, Any]) -> dict[str, Any]:
    return exec_mail_send(payload)


def mail_triage(
    *,
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
    if not delete and not move_to:
        raise ServiceError("Provide move_to or delete=true for triage.")
    older_than_iso = parse_age_cutoff(older_than)
    with cache.connect() as conn:
        rows = cache.find_messages(
            conn,
            folder=folder,
            sender=sender,
            sender_like=sender_like,
            subject_like=subject_like,
            older_than_iso=older_than_iso,
            has_list_unsubscribe=has_list_unsubscribe,
            limit=limit,
        )
    action = "delete" if delete else "move"
    plan = {
        "version": 1,
        "created_at": now_utc_iso(),
        "source": "icloudseal-mcp mail triage",
        "folder": folder,
        "action": action,
        "destination": move_to if action == "move" else None,
        "filters": {
            "sender": sender,
            "sender_like": sender_like,
            "subject_like": subject_like,
            "older_than": older_than,
            "older_than_iso": older_than_iso,
            "has_list_unsubscribe": has_list_unsubscribe,
        },
        "messages": [dict(r) for r in rows],
    }
    try:
        output_path = plan_path(plan_file) if plan_file else default_plan_path("mail-triage")
    except ValueError as exc:
        raise ServiceError(str(exc)) from exc
    if output_path.exists():
        raise ServiceError(f"Plan output already exists: {output_path}")
    write_json_file(output_path, plan)
    plan["planFile"] = str(output_path)
    plan["count"] = len(rows)
    return plan


def mail_jobs_collect(
    *,
    folder: str = "INBOX",
    since: str | None = "7d",
    limit: int | None = 200,
    min_score: int = 1,
    top: int = 50,
    out: str | None = None,
) -> dict[str, Any]:
    creds = auth.load_credentials()
    since_imap = parse_since(since)
    criteria = f"SINCE {since_imap}" if since_imap else "ALL"
    leads: list[dict[str, Any]] = []
    scanned = 0
    with open_session(creds.email, creds.password) as imap:
        imap.select(folder, readonly=True)
        uids = imap.search_uids(criteria)
        if limit:
            uids = uids[-limit:]
        metas = list(imap.fetch_metadata(uids))
        for meta in metas:
            if not jobs.is_job_alert(meta.sender_email, meta.sender_name, meta.subject):
                continue
            scanned += 1
            msg = imap.fetch_body(meta.uid)
            leads.extend(
                lead.to_dict()
                for lead in jobs.extract_job_leads(
                    uid=meta.uid,
                    sender_email=meta.sender_email,
                    sender_name=meta.sender_name,
                    subject=meta.subject,
                    msg=msg,
                )
                if lead.score >= min_score
            )
    leads.sort(key=lambda item: item["score"], reverse=True)
    plan = {
        "version": 1,
        "kind": "job-leads",
        "created_at": now_utc_iso(),
        "source": "icloudseal-mcp mail jobs collect",
        "folder": folder,
        "filters": {"since": since, "criteria": criteria, "limit": limit, "min_score": min_score},
        "messages_scanned": len(uids),
        "job_alert_messages": scanned,
        "leads": leads[:top],
        "approval_gate": {
            "auto_apply": False,
            "note": "Review leads only. This never submits applications.",
        },
    }
    if out:
        try:
            output_path = export_path(out)
        except ValueError as exc:
            raise ServiceError(str(exc)) from exc
        if output_path.suffix.lower() != ".json":
            raise ServiceError("Mail jobs export must use a .json filename.")
        if output_path.exists():
            raise ServiceError(f"Export output already exists: {output_path}")
        write_json_file(output_path, plan)
        plan["out"] = str(output_path)
    return plan


def resolve_plan_path(value: str) -> Path:
    try:
        return plan_path(value, must_exist=True)
    except ValueError as exc:
        raise ServiceError(str(exc)) from exc


def load_valid_mail_plan(path: Path) -> dict[str, Any]:
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ServiceError(f"Cannot read mail plan {path}: {exc}") from exc
    return validate_mail_plan(plan)


def validate_mail_plan(
    plan: Any,
    *,
    require_frozen: bool = False,
) -> dict[str, Any]:
    """Validate and normalize the bounded mail mutation schema."""
    if not isinstance(plan, dict) or plan.get("version") != 1:
        raise ServiceError("Unsupported mail plan version.")
    folder = plan.get("folder")
    if not isinstance(folder, str) or not folder.strip() or len(folder) > 255:
        raise ServiceError("Mail plan folder must be a non-empty string.")
    action = plan.get("action")
    if action not in {"move", "delete", "flags"}:
        raise ServiceError("Mail plan action must be 'move', 'delete', or 'flags'.")
    destination = plan.get("destination")
    if action == "move" and (
        not isinstance(destination, str) or not destination.strip() or len(destination) > 255
    ):
        raise ServiceError("Move plan requires a valid destination folder.")
    flag = plan.get("flag")
    if action == "flags" and flag not in ALLOWED_MAIL_FLAGS:
        raise ServiceError(
            "Flag plan requires +Seen/-Seen, +Flagged/-Flagged, or +Answered/-Answered."
        )
    messages = plan.get("messages")
    if not isinstance(messages, list):
        raise ServiceError("Mail plan is missing a messages list.")
    if len(messages) > 500:
        raise ServiceError("Mail plans are limited to 500 messages per approval.")
    normalized_messages: list[dict[str, Any]] = []
    seen: set[int] = set()
    for message in messages:
        if not isinstance(message, dict):
            raise ServiceError("Each mail plan message must be an object.")
        raw_uid = message.get("uid")
        if isinstance(raw_uid, bool):
            raise ServiceError("Mail UIDs must be positive integers.")
        try:
            uid = int(raw_uid)
        except (TypeError, ValueError) as exc:
            raise ServiceError("Mail UIDs must be positive integers.") from exc
        if uid <= 0 or uid in seen:
            raise ServiceError("Mail UIDs must be positive and unique.")
        seen.add(uid)
        normalized = {"uid": uid}
        for key in ("sender_name", "sender_email", "subject", "date_iso"):
            normalized[key] = str(message.get(key) or "")[:2_000]
        try:
            normalized["size"] = int(message.get("size") or 0)
        except (TypeError, ValueError) as exc:
            raise ServiceError("Mail message size must be an integer.") from exc
        normalized_messages.append(normalized)
    normalized_plan: dict[str, Any] = {
        "version": 1,
        "created_at": str(plan.get("created_at") or "")[:100],
        "source": str(plan.get("source") or "")[:500],
        "folder": folder,
        "action": action,
        "destination": destination if action == "move" else None,
        "messages": normalized_messages,
    }
    if action == "flags":
        normalized_plan["flag"] = flag
    if require_frozen:
        validity = plan.get("uidvalidity")
        if isinstance(validity, bool):
            raise ServiceError("Frozen mail plan is missing UIDVALIDITY.")
        try:
            normalized_plan["uidvalidity"] = int(validity)
        except (TypeError, ValueError) as exc:
            raise ServiceError("Frozen mail plan is missing UIDVALIDITY.") from exc
    return normalized_plan


def freeze_mail_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Resolve a reviewed cache plan to current immutable IMAP identities."""
    normalized = validate_mail_plan(plan)
    uids = [message["uid"] for message in normalized["messages"]]
    creds = auth.load_credentials()
    with open_session(creds.email, creds.password) as imap:
        imap.select(normalized["folder"], readonly=True)
        validity = imap.uidvalidity()
        live_messages = list(imap.fetch_metadata(uids)) if uids else []
    live_by_uid = {item.uid: item for item in live_messages}
    if set(live_by_uid) != set(uids):
        found = set(live_by_uid)
        missing = [uid for uid in uids if uid not in found]
        raise ServiceError(f"Mail plan contains missing UIDs: {missing[:20]}")
    frozen_messages = [
        {
            "uid": live_by_uid[uid].uid,
            "sender_name": live_by_uid[uid].sender_name,
            "sender_email": live_by_uid[uid].sender_email,
            "subject": live_by_uid[uid].subject,
            "date_iso": live_by_uid[uid].date_iso,
            "size": live_by_uid[uid].size,
        }
        for uid in uids
    ]
    return {**normalized, "uidvalidity": validity, "messages": frozen_messages}


def prepare_strict_mail_plan(*, folder: str, sync: bool) -> dict[str, Any]:
    """Freeze strict cleanup candidates during prepare, never after approval."""
    if sync:
        mail_sync(folder=folder, since=None)
    with cache.connect() as conn:
        messages = [dict(row) for row in cleanup.strict_bulk_messages(conn, folder=folder)]
    return freeze_mail_plan(
        {
            "version": 1,
            "created_at": now_utc_iso(),
            "source": "icloudseal-mcp mail cleanup strict",
            "folder": folder,
            "action": "move",
            "destination": "Deleted Messages",
            "messages": messages,
        }
    )


def exec_mail_apply(payload: dict[str, Any]) -> dict[str, Any]:
    plan = validate_mail_plan(payload.get("plan"), require_frozen=True)
    expected_hash = payload.get("planSha256")
    if not isinstance(expected_hash, str) or canonical_sha256(plan) != expected_hash:
        raise ServiceError("Approved mail plan failed its integrity check.")
    if plan.get("version") != 1:
        raise ServiceError("Unsupported plan version.")
    messages = plan.get("messages") or []
    if not messages:
        return {"moved": 0, "note": "Plan has no messages."}
    folder = plan["folder"]
    action = plan["action"]
    if action not in {"move", "delete"}:
        raise ServiceError("Approved mail apply plan must move or delete.")
    dest = plan.get("destination")
    uids = [int(m["uid"]) for m in messages]
    if action == "move" and not dest:
        raise ServiceError("Move plan is missing destination.")
    if action == "delete":
        dest = "Deleted Messages"
        action = "move"
    creds = auth.load_credentials()
    with open_session(creds.email, creds.password) as imap:
        imap.select(folder, readonly=False)
        if imap.uidvalidity() != plan["uidvalidity"]:
            raise ServiceError("Mailbox UIDVALIDITY changed after approval; refusing to execute.")
        current_by_uid = {item.uid: item for item in imap.fetch_metadata(uids)}
        current = [
            {
                "uid": current_by_uid[uid].uid,
                "sender_name": current_by_uid[uid].sender_name,
                "sender_email": current_by_uid[uid].sender_email,
                "subject": current_by_uid[uid].subject,
                "date_iso": current_by_uid[uid].date_iso,
                "size": current_by_uid[uid].size,
            }
            for uid in uids
            if uid in current_by_uid
        ]
        if current != messages:
            raise ServiceError("One or more approved mail targets changed; refusing to execute.")
        imap.move(uids, dest)
    with cache.connect() as conn:
        removed = cache.remove_cached_messages(conn, folder=folder, uids=uids)
    return {"moved": len(uids), "destination": dest, "cacheRemoved": removed, "folder": folder}


def exec_mail_cleanup_strict(payload: dict[str, Any]) -> dict[str, Any]:
    result = exec_mail_apply(payload)
    result["planned"] = len(payload["plan"]["messages"])
    return result


def _freeze_local_attachments(paths: list[str] | str | None) -> list[dict[str, Any]]:
    if paths is None:
        return []
    raw = [paths] if isinstance(paths, str) else list(paths)
    snapshots: list[dict[str, Any]] = []
    for item in raw:
        source = Path(str(item)).expanduser().resolve()
        if not source.is_file():
            raise ServiceError(f"Attachment not found: {source}")
        size = source.stat().st_size
        if size <= 0 or size > MAX_ATTACHMENT_BYTES:
            raise ServiceError(
                f"Attachment {source.name!r} exceeds the {MAX_ATTACHMENT_BYTES} byte cap."
            )
        snapshots.append(
            {
                "path": str(source),
                "name": source.name,
                "size": size,
                "sha256": _file_sha256(source),
            }
        )
    return snapshots


def prepare_mail_send(
    *,
    to: list[str] | str,
    subject: str,
    body: str,
    cc: list[str] | str | None = None,
    bcc: list[str] | str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
    attachments: list[str] | str | None = None,
) -> dict[str, Any]:
    creds = auth.load_credentials()
    try:
        frozen = freeze_send(
            from_addr=creds.email,
            to=to,
            subject=subject,
            body=body,
            cc=cc,
            bcc=bcc,
            in_reply_to=in_reply_to,
            references=references,
            attachments=_freeze_local_attachments(attachments),
        )
    except SMTPError as exc:
        raise ServiceError(str(exc)) from exc
    frozen["planSha256"] = canonical_sha256({key: frozen[key] for key in SEND_HASH_KEYS})
    return frozen


def exec_mail_send(payload: dict[str, Any]) -> dict[str, Any]:
    expected = payload.get("planSha256")
    try:
        frozen = freeze_send(
            from_addr=payload["from"],
            to=payload["to"],
            subject=payload["subject"],
            body=payload["body"],
            cc=payload.get("cc"),
            bcc=payload.get("bcc"),
            message_id=payload.get("messageId"),
            in_reply_to=payload.get("inReplyTo"),
            references=payload.get("references"),
            attachments=payload.get("attachments"),
        )
    except SMTPError as exc:
        raise ServiceError(str(exc)) from exc
    material = {key: frozen[key] for key in SEND_HASH_KEYS}
    if not isinstance(expected, str) or canonical_sha256(material) != expected:
        raise ServiceError("Approved mail send failed its integrity check.")
    creds = auth.load_credentials()
    if creds.email != frozen["from"]:
        raise ServiceError("Keychain From address no longer matches the approved sender.")
    try:
        return send_frozen(frozen, password=creds.password)
    except SMTPError as exc:
        raise ServiceError(str(exc)) from exc


def parse_mail_uids(value: list[int] | list[str] | str | int) -> list[int]:
    if isinstance(value, bool):
        raise ServiceError("Mail UIDs must be positive integers.")
    if isinstance(value, int):
        raw: list[object] = [value]
    elif isinstance(value, str):
        raw = [part.strip() for part in value.split(",") if part.strip()]
    elif isinstance(value, list):
        raw = list(value)
    else:
        raise ServiceError("Mail UIDs must be a list or comma-separated string.")
    uids: list[int] = []
    seen: set[int] = set()
    for item in raw:
        if isinstance(item, bool):
            raise ServiceError("Mail UIDs must be positive integers.")
        try:
            uid = int(item)
        except (TypeError, ValueError) as exc:
            raise ServiceError("Mail UIDs must be positive integers.") from exc
        if uid <= 0 or uid in seen:
            raise ServiceError("Mail UIDs must be positive and unique.")
        seen.add(uid)
        uids.append(uid)
    if not uids:
        raise ServiceError("Provide at least one mail UID.")
    if len(uids) > 500:
        raise ServiceError("Mail mutations are limited to 500 messages per approval.")
    return uids


def _resolve_cached_mail_messages(
    *, folder: str, uids: list[int] | list[str] | str | int
) -> list[dict[str, Any]]:
    uids = parse_mail_uids(uids)
    with cache.connect() as conn:
        rows = {
            int(row["uid"]): dict(row)
            for row in cache.get_messages(conn, folder=folder, uids=uids)
        }
    missing = [uid for uid in uids if uid not in rows]
    if missing:
        raise ServiceError(
            f"UIDs not in the {folder!r} cache: {missing[:20]}. Sync the folder first."
        )
    return [rows[uid] for uid in uids]


def prepare_mail_flags(
    *,
    folder: str,
    uids: list[int],
    seen: bool | None = None,
    flag: str | None = None,
) -> dict[str, Any]:
    token = flag
    if token is None:
        if seen is None:
            raise ServiceError("Provide flag or seen.")
        token = "+Seen" if seen else "-Seen"
    elif seen is not None:
        implied = "+Seen" if seen else "-Seen"
        if token != implied:
            raise ServiceError("flag and seen disagree.")
    if token not in ALLOWED_MAIL_FLAGS:
        raise ServiceError(
            "flag must be +Seen/-Seen, +Flagged/-Flagged, or +Answered/-Answered."
        )
    messages = _resolve_cached_mail_messages(folder=folder, uids=uids)
    return freeze_mail_plan(
        {
            "version": 1,
            "created_at": now_utc_iso(),
            "source": "icloudseal-mcp mail flags",
            "folder": folder,
            "action": "flags",
            "destination": None,
            "flag": token,
            "messages": messages,
        }
    )


def prepare_mail_move(*, folder: str, uids: list[int], destination: str) -> dict[str, Any]:
    messages = _resolve_cached_mail_messages(folder=folder, uids=uids)
    return freeze_mail_plan(
        {
            "version": 1,
            "created_at": now_utc_iso(),
            "source": "icloudseal-mcp mail move",
            "folder": folder,
            "action": "move",
            "destination": destination,
            "messages": messages,
        }
    )


def prepare_mail_trash(*, folder: str, uids: list[int]) -> dict[str, Any]:
    messages = _resolve_cached_mail_messages(folder=folder, uids=uids)
    return freeze_mail_plan(
        {
            "version": 1,
            "created_at": now_utc_iso(),
            "source": "icloudseal-mcp mail trash",
            "folder": folder,
            "action": "move",
            "destination": "Deleted Messages",
            "messages": messages,
        }
    )


def _recheck_frozen_mail(imap, plan: dict[str, Any]) -> list[int]:
    messages = plan["messages"]
    uids = [int(message["uid"]) for message in messages]
    if imap.uidvalidity() != plan["uidvalidity"]:
        raise ServiceError("Mailbox UIDVALIDITY changed after approval; refusing to execute.")
    current_by_uid = {item.uid: item for item in imap.fetch_metadata(uids)}
    current = [
        {
            "uid": current_by_uid[uid].uid,
            "sender_name": current_by_uid[uid].sender_name,
            "sender_email": current_by_uid[uid].sender_email,
            "subject": current_by_uid[uid].subject,
            "date_iso": current_by_uid[uid].date_iso,
            "size": current_by_uid[uid].size,
        }
        for uid in uids
        if uid in current_by_uid
    ]
    if current != messages:
        raise ServiceError("One or more approved mail targets changed; refusing to execute.")
    return uids


def exec_mail_flags(payload: dict[str, Any]) -> dict[str, Any]:
    plan = validate_mail_plan(payload.get("plan"), require_frozen=True)
    expected_hash = payload.get("planSha256")
    if not isinstance(expected_hash, str) or canonical_sha256(plan) != expected_hash:
        raise ServiceError("Approved mail flag plan failed its integrity check.")
    if plan["action"] != "flags":
        raise ServiceError("Approved mail flag plan has the wrong action.")
    flag = plan.get("flag")
    creds = auth.load_credentials()
    with open_session(creds.email, creds.password) as imap:
        imap.select(plan["folder"], readonly=False)
        uids = _recheck_frozen_mail(imap, plan)
        imap_flag = ALLOWED_MAIL_FLAGS.get(flag)
        if imap_flag is None:
            raise ServiceError("Approved mail flag plan has an unknown flag.")
        if flag in MAIL_FLAG_ADD:
            imap.add_flags(uids, imap_flag)
        else:
            imap.remove_flags(uids, imap_flag)
        refreshed = list(imap.fetch_metadata(uids))
    with cache.connect() as conn:
        cache.upsert_messages(conn, refreshed)
    return {
        "folder": plan["folder"],
        "flag": flag,
        "updated": len(uids),
        "uids": uids,
    }


def exec_mail_move(payload: dict[str, Any]) -> dict[str, Any]:
    result = exec_mail_apply(payload)
    result["action"] = "move"
    return result


def prepare_mail_create_folder(folder: str) -> dict[str, Any]:
    name = (folder or "").strip()
    if not name or len(name) > 255:
        raise ServiceError("Folder name must be a non-empty string.")
    if any(ch in name for ch in "\r\n\x00"):
        raise ServiceError("Folder name must not contain control characters.")
    return {"folder": name}


def exec_mail_create_folder(payload: dict[str, Any]) -> dict[str, Any]:
    folder = str(payload.get("folder") or "").strip()
    if not folder:
        raise ServiceError("Approved mail folder is missing.")
    creds = auth.load_credentials()
    try:
        with open_session(creds.email, creds.password) as imap:
            imap.create_folder(folder)
    except IMAPError as exc:
        raise ServiceError(str(exc)) from exc
    return {"folder": folder, "created": True}


def mail_list_attachments(*, folder: str, uid: int) -> list[dict[str, Any]]:
    parse_mail_uids(uid)
    creds = auth.load_credentials()
    try:
        with open_session(creds.email, creds.password) as imap:
            imap.select(folder, readonly=True)
            return imap.list_attachments(uid)
    except IMAPError as exc:
        raise ServiceError(str(exc)) from exc


def mail_export_attachment(
    *, folder: str, uid: int, index: int, dest: str
) -> dict[str, Any]:
    parse_mail_uids(uid)
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise ServiceError("Attachment index must be a non-negative integer.")
    try:
        out = export_path(dest)
    except ValueError as exc:
        raise ServiceError(str(exc)) from exc
    if out.exists():
        raise ServiceError(f"Export output already exists: {out}")
    creds = auth.load_credentials()
    try:
        with open_session(creds.email, creds.password) as imap:
            imap.select(folder, readonly=True)
            result = imap.export_attachment(uid, index, str(out))
    except IMAPError as exc:
        raise ServiceError(str(exc)) from exc
    return result


def exec_mail_trash(payload: dict[str, Any]) -> dict[str, Any]:
    result = exec_mail_apply(payload)
    result["action"] = "trash"
    return result


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------

def contacts_list(*, limit: int = 0) -> list[dict[str, Any]]:
    session = ContactsSession.connect()
    contacts = session.list_contacts()
    if limit:
        contacts = contacts[:limit]
    return [c.to_dict() for c in contacts]


def contacts_search(*, query: str) -> list[dict[str, Any]]:
    session = ContactsSession.connect()
    contacts = [c for c in session.list_contacts() if carddav.matches(c, query)]
    return [c.to_dict() for c in contacts]


def contacts_export(*, path: str) -> dict[str, Any]:
    session = ContactsSession.connect()
    contacts = session.list_contacts()
    try:
        out = export_path(path)
    except ValueError as exc:
        raise ServiceError(str(exc)) from exc
    if out.suffix.lower() not in {".json", ".vcf"}:
        raise ServiceError("Contacts export must use a .json or .vcf filename.")
    if out.exists():
        raise ServiceError(f"Export output already exists: {out}")
    if out.suffix.lower() == ".json":
        write_json_file(out, [contact.to_dict() for contact in contacts])
    else:
        out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        out.write_text(
            "".join(contact.raw for contact in contacts if contact.raw),
            encoding="utf-8",
        )
        out.chmod(0o600)
    return {"path": str(out), "count": len(contacts)}


def _backup_vcards(contacts: list[Contact], label: str) -> Path:
    root = BACKUP_DIR / f"contacts-{label}-{timestamp_slug()}"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    for contact in contacts:
        raw = contact.raw or carddav.build_vcard(
            uid=contact.uid,
            full_name=contact.full_name,
            first=contact.first,
            last=contact.last,
            org=contact.org,
            emails=contact.emails,
            phones=contact.phones,
        )
        filename = f"{canonical_sha256(contact.uid)[:24]}.vcf"
        output = root / filename
        output.write_text(raw, encoding="utf-8")
        output.chmod(0o600)
    return root


def _resolve_contact(session: ContactsSession, selector: str) -> Contact:
    contacts = session.list_contacts()
    by_uid = [c for c in contacts if c.uid == selector]
    if by_uid:
        return by_uid[0]
    matched = [c for c in contacts if carddav.matches(c, selector)]
    if not matched:
        raise ServiceError(f"No contact matches {selector!r}.")
    if len(matched) > 1:
        raise ServiceError(
            f"{len(matched)} contacts match {selector!r}; be more specific "
            f"({', '.join(c.full_name for c in matched[:5])})."
        )
    return matched[0]


def _contact_target(contact: Contact) -> dict[str, Any]:
    if not contact.href or not contact.etag:
        raise ServiceError(f"Contact {contact.uid!r} has no immutable href/ETag target.")
    return {
        "uid": contact.uid,
        "href": contact.href,
        "etag": contact.etag,
        "full_name": contact.full_name,
        "first": contact.first,
        "last": contact.last,
        "org": contact.org,
        "emails": list(contact.emails),
        "phones": list(contact.phones),
        "raw": contact.raw,
    }


def _contact_from_target(target: dict[str, Any]) -> Contact:
    return Contact(
        uid=target["uid"],
        href=target["href"],
        etag=target["etag"],
        full_name=target["full_name"],
        first=target.get("first") or "",
        last=target.get("last") or "",
        org=target.get("org") or "",
        emails=list(target.get("emails") or []),
        phones=list(target.get("phones") or []),
        raw=target.get("raw") or "",
    )


def prepare_contact_collection() -> dict[str, str]:
    session = ContactsSession.connect()
    return {"url": session.addressbook_url}


def prepare_contact_update(selector: str) -> dict[str, Any]:
    session = ContactsSession.connect()
    return _contact_target(_resolve_contact(session, selector))


def prepare_contact_delete(query: str) -> list[dict[str, Any]]:
    session = ContactsSession.connect()
    targets = [contact for contact in session.list_contacts() if carddav.matches(contact, query)]
    if not targets:
        raise ServiceError(f"No contacts match {query!r}.")
    if len(targets) > 100:
        raise ServiceError("Contact deletion is limited to 100 exact targets per approval.")
    return [_contact_target(contact) for contact in targets]


def exec_contacts_create(payload: dict[str, Any]) -> dict[str, Any]:
    full_name = payload.get("name") or " ".join(
        p for p in (payload.get("first"), payload.get("last")) if p
    )
    if not full_name:
        raise ServiceError("Provide name (or first/last).")
    contact = Contact(
        uid=payload["uid"],
        href=None,
        etag=None,
        full_name=full_name,
        first=payload.get("first") or "",
        last=payload.get("last") or "",
        org=payload.get("org") or "",
        emails=list(payload.get("emails") or []),
        phones=list(payload.get("phones") or []),
    )
    session = ContactsSession.connect()
    href = session.create(contact, addressbook_url=payload["addressbookUrl"])
    return {"uid": contact.uid, "name": contact.full_name, "href": href}


def exec_contacts_update(payload: dict[str, Any]) -> dict[str, Any]:
    session = ContactsSession.connect()
    contact = _contact_from_target(payload["target"])
    if payload.get("name"):
        contact.full_name = payload["name"]
    if payload.get("first") is not None:
        contact.first = payload["first"]
    if payload.get("last") is not None:
        contact.last = payload["last"]
    if payload.get("org") is not None:
        contact.org = payload["org"]
    if payload.get("addEmails"):
        contact.emails.extend(e for e in payload["addEmails"] if e not in contact.emails)
    if payload.get("addPhones"):
        contact.phones.extend(p for p in payload["addPhones"] if p not in contact.phones)
    if payload.get("setEmails") is not None:
        contact.emails = list(payload["setEmails"])
    if payload.get("setPhones") is not None:
        contact.phones = list(payload["setPhones"])
    backup = _backup_vcards([contact], "update")
    session.update(contact)
    return {
        "uid": contact.uid,
        "name": contact.full_name,
        "emails": contact.emails,
        "phones": contact.phones,
        "backup": str(backup),
    }


def exec_contacts_delete(payload: dict[str, Any]) -> dict[str, Any]:
    session = ContactsSession.connect()
    targets = [_contact_from_target(target) for target in payload["targets"]]
    backup = _backup_vcards(targets, "delete")
    for c in targets:
        session.delete(c)
    return {
        "deleted": len(targets),
        "names": [c.full_name for c in targets],
        "backup": str(backup),
    }


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------

def calendar_list() -> list[dict[str, Any]]:
    session = CalendarSession.connect()
    cols = session.collections()
    return [
        {
            "name": c.name,
            "url": c.url,
            "components": sorted(c.components),
            "kind": "events" if c.is_events else ("reminders" if c.is_reminders else "unknown"),
        }
        for c in cols
    ]


def calendar_timezones(*, query: str | None = None, limit: int = 50) -> list[str]:
    try:
        return caldav.list_timezones(query=query, limit=limit)
    except ValueError as exc:
        raise ServiceError(str(exc)) from exc


def calendar_events(*, days: int = 30) -> list[dict[str, Any]]:
    session = CalendarSession.connect()
    return [e.to_dict() for e in session.list_events(days=days)]


def calendar_reminders(*, include_completed: bool = False) -> list[dict[str, Any]]:
    session = CalendarSession.connect()
    return [r.to_dict() for r in session.list_reminders(include_completed=include_completed)]


def _backup_cal(items: list[CalItem], label: str) -> Path:
    root = BACKUP_DIR / f"calendar-{label}-{timestamp_slug()}"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    for item in items:
        if item.raw:
            filename = f"{canonical_sha256(item.uid)[:24]}.ics"
            output = root / filename
            output.write_text(item.raw, encoding="utf-8")
            output.chmod(0o600)
    return root


def _resolve_cal_item(items: list[CalItem], query: str) -> CalItem:
    by_uid = [i for i in items if i.uid == query]
    if by_uid:
        return by_uid[0]
    matched = [i for i in items if caldav.matches(i, query)]
    if not matched:
        raise ServiceError(f"No item matches {query!r}.")
    if len(matched) > 1:
        raise ServiceError(f"{len(matched)} items match {query!r}; be more specific.")
    return matched[0]


def _cal_target(item: CalItem) -> dict[str, Any]:
    if not item.href or not item.etag:
        raise ServiceError(f"Calendar item {item.uid!r} has no immutable href/ETag target.")
    return {
        "uid": item.uid,
        "href": item.href,
        "etag": item.etag,
        "summary": item.summary,
        "kind": item.kind,
        "start": item.start,
        "end": item.end,
        "location": item.location,
        "status": item.status,
        "raw": item.raw,
    }


def _cal_from_target(target: dict[str, Any]) -> CalItem:
    return CalItem(
        uid=target["uid"],
        href=target["href"],
        etag=target["etag"],
        summary=target["summary"],
        kind=target["kind"],
        start=target.get("start") or "",
        end=target.get("end") or "",
        location=target.get("location") or "",
        status=target.get("status") or "",
        raw=target.get("raw") or "",
    )


def prepare_calendar_collection(name: str | None, *, events: bool) -> dict[str, str]:
    collection = CalendarSession.connect().find_collection(name, events=events)
    return {"url": collection.url, "name": collection.name}


def prepare_event_target(query: str, *, days: int) -> dict[str, Any]:
    session = CalendarSession.connect()
    return _cal_target(_resolve_cal_item(session.list_events(days=days), query))


def prepare_reminder_target(query: str) -> dict[str, Any]:
    session = CalendarSession.connect()
    return _cal_target(
        _resolve_cal_item(session.list_reminders(include_completed=True), query)
    )


def exec_event_add(payload: dict[str, Any]) -> dict[str, Any]:
    uid = payload["uid"]
    try:
        ics = caldav.build_event(
            uid=uid,
            summary=payload["title"],
            start=payload["start"],
            end=payload.get("end"),
            location=payload.get("location") or "",
            all_day=bool(payload.get("allDay")),
            timezone=payload.get("timezone"),
            attendees=payload.get("attendees"),
        )
    except ValueError as exc:
        raise ServiceError(str(exc)) from exc
    session = CalendarSession.connect()
    collection = payload["collection"]
    href = session.put_item(collection["url"], uid, ics)
    return {
        "uid": uid,
        "title": payload["title"],
        "calendar": collection["name"],
        "href": href,
    }


def exec_event_rm(payload: dict[str, Any]) -> dict[str, Any]:
    session = CalendarSession.connect()
    target = _cal_from_target(payload["target"])
    backup = _backup_cal([target], "event-delete")
    session.delete(target)
    return {
        "uid": target.uid,
        "summary": target.summary,
        "start": target.start,
        "backup": str(backup),
    }


def exec_event_update(payload: dict[str, Any]) -> dict[str, Any]:
    session = CalendarSession.connect()
    target = _cal_from_target(payload["target"])
    try:
        ics = caldav.update_event(
            target.raw,
            summary=payload.get("title"),
            start=payload.get("start"),
            end=payload.get("end"),
            location=payload.get("location"),
            all_day=payload.get("allDay"),
            timezone=payload.get("timezone"),
            attendees=payload.get("attendees"),
        )
    except ValueError as exc:
        raise ServiceError(str(exc)) from exc
    backup = _backup_cal([target], "event-update")
    session.update(target, ics)
    updated = caldav.parse_calitem(ics, "event")
    return {
        "uid": target.uid,
        "summary": updated.summary,
        "start": updated.start,
        "end": updated.end,
        "location": updated.location,
        "backup": str(backup),
    }


def exec_reminder_add(payload: dict[str, Any]) -> dict[str, Any]:
    uid = payload["uid"]
    ics = caldav.build_reminder(uid=uid, summary=payload["title"], due=payload.get("due"))
    session = CalendarSession.connect()
    collection = payload["collection"]
    href = session.put_item(collection["url"], uid, ics)
    return {"uid": uid, "title": payload["title"], "list": collection["name"], "href": href}


def exec_reminder_done(payload: dict[str, Any]) -> dict[str, Any]:
    session = CalendarSession.connect()
    target = _cal_from_target(payload["target"])
    ics = caldav.complete_reminder(target.raw)
    backup = _backup_cal([target], "reminder-done")
    session.update(target, ics)
    return {"uid": target.uid, "summary": target.summary, "backup": str(backup)}


def exec_reminder_rm(payload: dict[str, Any]) -> dict[str, Any]:
    session = CalendarSession.connect()
    target = _cal_from_target(payload["target"])
    backup = _backup_cal([target], "reminder-delete")
    session.delete(target)
    return {"uid": target.uid, "summary": target.summary, "backup": str(backup)}


def exec_reminder_update(payload: dict[str, Any]) -> dict[str, Any]:
    session = CalendarSession.connect()
    target = _cal_from_target(payload["target"])
    try:
        ics = caldav.update_reminder(
            target.raw,
            summary=payload.get("title"),
            due=payload.get("due"),
        )
    except ValueError as exc:
        raise ServiceError(str(exc)) from exc
    backup = _backup_cal([target], "reminder-update")
    session.update(target, ics)
    updated = caldav.parse_calitem(ics, "reminder")
    return {
        "uid": target.uid,
        "summary": updated.summary,
        "due": updated.start,
        "backup": str(backup),
    }


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

def messages_chats(*, limit: int = 30) -> list[dict[str, Any]]:
    try:
        chats = chatdb.list_chats(limit=limit)
    except MessagesAccessError as exc:
        raise ServiceError(str(exc)) from exc
    return [c.__dict__ for c in chats]


def messages_list(*, chat: str, limit: int = 40) -> list[dict[str, Any]]:
    try:
        msgs = chatdb.chat_messages(chat, limit=limit)
    except MessagesAccessError as exc:
        raise ServiceError(str(exc)) from exc
    return [m.__dict__ for m in msgs]


def messages_search(*, query: str, limit: int = 40) -> list[dict[str, Any]]:
    try:
        msgs = chatdb.search(query, limit=limit)
    except MessagesAccessError as exc:
        raise ServiceError(str(exc)) from exc
    return [m.__dict__ for m in msgs]


def messages_export(*, chat: str, path: str, limit: int = 1000) -> dict[str, Any]:
    try:
        msgs = chatdb.chat_messages(chat, limit=limit)
    except MessagesAccessError as exc:
        raise ServiceError(str(exc)) from exc
    try:
        out = export_path(path)
    except ValueError as exc:
        raise ServiceError(str(exc)) from exc
    if out.suffix.lower() != ".json":
        raise ServiceError("Messages export must use a .json filename.")
    if out.exists():
        raise ServiceError(f"Export output already exists: {out}")
    write_json_file(out, [message.__dict__ for message in msgs])
    return {"path": str(out), "count": len(msgs)}


def prepare_messages_send(
    *,
    to: str,
    text: str,
    service: str = "imessage",
    attachment: str | None = None,
) -> dict[str, Any]:
    if service not in {"imessage", "sms"}:
        raise ServiceError("service must be imessage or sms")
    if not text.strip():
        raise ServiceError("text cannot be empty")
    if len(text) > 10_000:
        raise ServiceError("text exceeds 10,000 characters")
    frozen: dict[str, Any] = {"to": to, "text": text, "service": service}
    if attachment:
        snaps = _freeze_local_attachments([attachment])
        frozen["attachment"] = snaps[0]
    return frozen


def exec_messages_send(payload: dict[str, Any]) -> dict[str, Any]:
    to = payload["to"]
    text = payload["text"]
    service = payload.get("service") or "imessage"
    attachment = payload.get("attachment")
    path = None
    if attachment:
        src = Path(attachment["path"]).expanduser().resolve()
        if not _snapshot_matches(src, attachment):
            raise ServiceError("Approved Messages attachment changed; refusing to send.")
        path = str(src)
    try:
        messages_commands._applescript_send(to, text, service=service, attachment=path)
    except Exception as exc:  # noqa: BLE001
        raise ServiceError(str(exc)) from exc
    result: dict[str, Any] = {"to": to, "service": service, "characters": len(text)}
    if path:
        result["attachment"] = attachment["name"]
    return result


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

def notes_list(*, limit: int = 0) -> list[dict[str, Any]]:
    try:
        notes = applescript.list_notes()
    except NotesError as exc:
        raise ServiceError(str(exc)) from exc
    if limit:
        notes = notes[:limit]
    return [n.__dict__ for n in notes]


def notes_search(*, query: str) -> list[dict[str, Any]]:
    try:
        notes = applescript.list_notes()
    except NotesError as exc:
        raise ServiceError(str(exc)) from exc
    q = query.lower()
    hits = [n for n in notes if q in n.name.lower() or n.id == query]
    return [n.__dict__ for n in hits]


def notes_read(*, query: str) -> dict[str, Any]:
    try:
        notes = applescript.list_notes()
    except NotesError as exc:
        raise ServiceError(str(exc)) from exc
    q = query.lower()
    hits = [n for n in notes if q in n.name.lower() or n.id == query]
    if not hits:
        raise ServiceError(f"No note matches {query!r}.")
    if len(hits) > 1:
        raise ServiceError(f"{len(hits)} notes match {query!r}; be more specific.")
    note = hits[0]
    body = applescript.read_note(note.id)
    return {"id": note.id, "name": note.name, "modified": note.modified, "body": body}


def notes_accounts() -> list[str]:
    try:
        return applescript.list_accounts()
    except NotesError as exc:
        raise ServiceError(str(exc)) from exc


def notes_folders() -> list[dict[str, Any]]:
    try:
        return [folder.__dict__ for folder in applescript.list_folders()]
    except NotesError as exc:
        raise ServiceError(str(exc)) from exc


def exec_notes_create(payload: dict[str, Any]) -> dict[str, Any]:
    title = payload["title"]
    body = payload.get("body") or ""
    folder = payload.get("folder") or None
    account = payload.get("account") or None
    try:
        applescript.create_note(title, body, folder=folder, account=account)
    except NotesError as exc:
        raise ServiceError(str(exc)) from exc
    return {
        "title": title,
        "bodyChars": len(body),
        "folder": folder,
        "account": account or applescript.DEFAULT_ACCOUNT,
    }


def prepare_note_delete(query: str) -> dict[str, Any]:
    note = notes_read(query=query)
    return {
        "id": note["id"],
        "name": note["name"],
        "modified": note["modified"],
        "body": note["body"],
        "bodySha256": canonical_sha256(note["body"]),
    }


def prepare_note_update(query: str) -> dict[str, Any]:
    return prepare_note_delete(query)


def exec_notes_delete(payload: dict[str, Any]) -> dict[str, Any]:
    target = payload["target"]
    try:
        notes = applescript.list_notes()
    except NotesError as exc:
        raise ServiceError(str(exc)) from exc
    hits = [note for note in notes if note.id == target["id"]]
    if not hits:
        raise ServiceError(f"Approved note {target['id']!r} no longer exists.")
    note = hits[0]
    if note.name != target["name"] or note.modified != target["modified"]:
        raise ServiceError("Approved note metadata changed; refusing to delete.")
    body = applescript.read_note(note.id)
    if body != target["body"] or canonical_sha256(body) != target["bodySha256"]:
        raise ServiceError("Approved note body changed; refusing to delete.")
    root = BACKUP_DIR / f"notes-delete-{timestamp_slug()}"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    safe = "".join(c if c.isalnum() else "_" for c in note.name)[:60] or "note"
    backup = root / f"{safe}.txt"
    backup.write_text(f"{note.name}\n\n{body}", encoding="utf-8")
    backup.chmod(0o600)
    applescript.delete_note(note.id)
    return {"id": note.id, "name": note.name, "backup": str(root)}


def exec_notes_update(payload: dict[str, Any]) -> dict[str, Any]:
    target = payload["target"]
    title = payload.get("title")
    body = payload.get("body")
    if title is None and body is None:
        raise ServiceError("Provide at least one of title or body to update.")
    try:
        notes = applescript.list_notes()
    except NotesError as exc:
        raise ServiceError(str(exc)) from exc
    hits = [note for note in notes if note.id == target["id"]]
    if not hits:
        raise ServiceError(f"Approved note {target['id']!r} no longer exists.")
    note = hits[0]
    if note.name != target["name"] or note.modified != target["modified"]:
        raise ServiceError("Approved note metadata changed; refusing to update.")
    current_body = applescript.read_note(note.id)
    if current_body != target["body"] or canonical_sha256(current_body) != target["bodySha256"]:
        raise ServiceError("Approved note body changed; refusing to update.")
    root = BACKUP_DIR / f"notes-update-{timestamp_slug()}"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    safe = "".join(c if c.isalnum() else "_" for c in note.name)[:60] or "note"
    backup = root / f"{safe}.txt"
    backup.write_text(f"{note.name}\n\n{current_body}", encoding="utf-8")
    backup.chmod(0o600)
    try:
        applescript.update_note(note.id, title=title, body=body)
    except NotesError as exc:
        raise ServiceError(str(exc)) from exc
    return {
        "id": note.id,
        "name": title if title is not None else note.name,
        "backup": str(root),
    }


# ---------------------------------------------------------------------------
# Drive
# ---------------------------------------------------------------------------

def _drive_resolve(path: str | None) -> Path:
    if not path:
        return DRIVE_ROOT
    p = Path(path)
    candidate = p if p.is_absolute() else DRIVE_ROOT / p
    resolved = candidate.expanduser().resolve()
    root = DRIVE_ROOT.resolve()
    if root not in resolved.parents and resolved != root:
        raise ServiceError(f"Path {resolved} is outside iCloud Drive.")
    return resolved


def _drive_rel(p: Path) -> str:
    try:
        return str(p.relative_to(DRIVE_ROOT.resolve()))
    except ValueError:
        return str(p)


def drive_ls(*, path: str | None = None) -> list[dict[str, Any]]:
    base = _drive_resolve(path)
    if not base.exists():
        raise ServiceError(f"Not found: {base}")
    if base.is_file():
        return [{"path": _drive_rel(base), "type": "file", "size": base.stat().st_size}]
    entries = sorted(base.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    out = []
    for e in entries:
        kind = "dir" if e.is_dir() else "file"
        name = e.name
        placeholder = False
        if e.name.startswith(".") and e.name.endswith(".icloud"):
            name = e.name[1:-7]
            placeholder = True
        out.append(
            {
                "name": name,
                "path": _drive_rel(e),
                "type": kind,
                "size": None if e.is_dir() else e.stat().st_size,
                "icloudPlaceholder": placeholder,
            }
        )
    return out


def drive_tree(*, path: str | None = None, depth: int = 2) -> dict[str, Any]:
    base = _drive_resolve(path)
    lines: list[str] = [_drive_rel(base) or "/"]

    def walk(d: Path, prefix: str, dpth: int) -> None:
        if dpth > depth:
            return
        kids = sorted(d.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        for i, k in enumerate(kids):
            last = i == len(kids) - 1
            lines.append(f"{prefix}{'└── ' if last else '├── '}{k.name}")
            if k.is_dir():
                walk(k, prefix + ("    " if last else "│   "), dpth + 1)

    if base.is_dir():
        walk(base, "", 1)
    return {"root": _drive_rel(base) or "/", "tree": "\n".join(lines)}


def drive_find(*, pattern: str, path: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    import fnmatch

    base = _drive_resolve(path)
    hits = [p for p in base.rglob("*") if fnmatch.fnmatch(p.name, pattern)]
    out = []
    for p in hits[:limit]:
        out.append(
            {
                "path": _drive_rel(p),
                "type": "dir" if p.is_dir() else "file",
                "size": None if p.is_dir() else p.stat().st_size,
            }
        )
    return out


def drive_read(*, path: str, max_bytes: int = MAX_READ_BYTES) -> dict[str, Any]:
    p = _drive_resolve(path)
    if not p.is_file():
        raise ServiceError(f"Not a file: {p}")
    data = p.read_bytes()[:max_bytes]
    return {
        "path": _drive_rel(p),
        "size": p.stat().st_size,
        "truncated": p.stat().st_size > max_bytes,
        "text": data.decode("utf-8", errors="replace"),
    }


def prepare_drive_put(local: str, dest: str, *, overwrite: bool) -> dict[str, Any]:
    source = Path(local).expanduser().resolve()
    if not source.is_file():
        raise ServiceError(f"Local file not found: {source}")
    target = _drive_resolve(dest)
    if target.is_dir():
        target = (target / source.name).resolve()
    if target == DRIVE_ROOT.resolve():
        raise ServiceError("Destination must be a file inside iCloud Drive.")
    if target.exists() and not target.is_file():
        raise ServiceError(f"Destination is not a regular file: {target}")
    if target.exists() and not overwrite:
        raise ServiceError("Destination exists; set overwrite=true to replace it explicitly.")
    return {
        "source": _file_snapshot(source),
        "destination": {
            "path": str(target),
            "relative": _drive_rel(target),
            "existed": target.exists(),
            "snapshot": _file_snapshot(target) if target.exists() else None,
        },
        "overwrite": overwrite,
    }


def prepare_drive_mkdir(path: str) -> dict[str, Any]:
    dest = _drive_resolve(path)
    if dest == DRIVE_ROOT.resolve():
        raise ServiceError("Refusing to mkdir the iCloud Drive root.")
    if dest.exists():
        raise ServiceError(f"Already exists: {_drive_rel(dest)}")
    return {
        "path": str(dest),
        "relative": _drive_rel(dest),
    }


def exec_drive_mkdir(payload: dict[str, Any]) -> dict[str, Any]:
    dest = _drive_resolve(payload["path"])
    if str(dest) != payload["path"]:
        raise ServiceError("Approved iCloud Drive destination changed through a symlink.")
    if dest == DRIVE_ROOT.resolve():
        raise ServiceError("Refusing to mkdir the iCloud Drive root.")
    if dest.exists():
        raise ServiceError(f"Already exists: {_drive_rel(dest)}")
    dest.mkdir(parents=True, exist_ok=False)
    return {"path": payload["relative"], "created": True}


def prepare_drive_remove(path: str) -> dict[str, Any]:
    target = _drive_resolve(path)
    if target == DRIVE_ROOT.resolve():
        raise ServiceError("Refusing to trash the iCloud Drive root.")
    if not target.exists():
        raise ServiceError(f"Not found: {target}")
    stat_result = target.stat()
    tree = _tree_snapshot(target) if target.is_dir() else None
    return {
        "path": str(target),
        "relative": _drive_rel(target),
        "isDirectory": target.is_dir(),
        "size": stat_result.st_size,
        "mtimeNs": stat_result.st_mtime_ns,
        "inode": stat_result.st_ino,
        "sha256": _file_sha256(target) if target.is_file() else None,
        "tree": tree,
    }


def exec_drive_put(payload: dict[str, Any]) -> dict[str, Any]:
    source = payload["source"]
    src = Path(source["path"]).resolve()
    if not _snapshot_matches(src, source):
        raise ServiceError("Approved local source changed; refusing to copy.")
    destination = payload["destination"]
    dest = _drive_resolve(destination["path"])
    if str(dest) != destination["path"]:
        raise ServiceError("Approved iCloud Drive destination changed through a symlink.")
    if destination["existed"]:
        if not payload.get("overwrite") or not _snapshot_matches(dest, destination["snapshot"]):
            raise ServiceError("Approved destination changed; refusing to overwrite it.")
    elif dest.exists():
        raise ServiceError("Destination appeared after approval; refusing to overwrite it.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp = dest.with_name(f".{dest.name}.{uuid.uuid4()}.tmp")
    try:
        shutil.copy2(src, temp)
        if _file_sha256(temp) != source["sha256"]:
            raise ServiceError("Copied file failed its integrity check.")
        if destination["existed"]:
            os.replace(temp, dest)
        else:
            os.link(temp, dest)
            temp.unlink()
    finally:
        temp.unlink(missing_ok=True)
    return {"src": str(src), "dest": _drive_rel(dest), "bytes": dest.stat().st_size}


def exec_drive_rm(payload: dict[str, Any]) -> dict[str, Any]:
    target = payload["target"]
    p = _drive_resolve(target["path"])
    if not p.exists():
        raise ServiceError(f"Not found: {p}")
    stat_result = p.stat()
    unchanged = (
        str(p) == target["path"]
        and p.is_dir() == target["isDirectory"]
        and stat_result.st_size == target["size"]
        and stat_result.st_mtime_ns == target["mtimeNs"]
        and stat_result.st_ino == target["inode"]
    )
    if p.is_file():
        unchanged = unchanged and _file_sha256(p) == target["sha256"]
    elif p.is_dir():
        unchanged = unchanged and _tree_snapshot(p) == target["tree"]
    if not unchanged:
        raise ServiceError("Approved iCloud Drive target changed; refusing to trash it.")
    script = """on run argv
    set targetPath to item 1 of argv
    tell application "Finder" to delete (POSIX file targetPath as alias)
end run"""
    result = subprocess.run(
        ["osascript", "-e", script, "--", str(p)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ServiceError(result.stderr.strip() or "Trash failed")
    return {"path": target["relative"], "trashed": True}


# ---------------------------------------------------------------------------
# Photos
# ---------------------------------------------------------------------------

def photos_stats() -> dict[str, Any]:
    try:
        return photosdb.stats()
    except PhotosAccessError as exc:
        raise ServiceError(str(exc)) from exc


def photos_albums() -> list[dict[str, Any]]:
    try:
        albums = photosdb.list_albums()
    except PhotosAccessError as exc:
        raise ServiceError(str(exc)) from exc
    return [a.__dict__ for a in albums]


def photos_list(
    *,
    album: str | None = None,
    kind: str | None = None,
    favorites: bool = False,
    limit: int = 50,
) -> list[dict[str, Any]]:
    try:
        assets = photosdb.list_assets(
            album=album, limit=limit, kind=kind, favorites_only=favorites
        )
    except PhotosAccessError as exc:
        raise ServiceError(str(exc)) from exc
    return [a.__dict__ for a in assets]


def prepare_photos_export(
    *,
    dest: str,
    album: str | None,
    kind: str | None,
    favorites: bool,
    limit: int,
) -> dict[str, Any]:
    if not 1 <= limit <= 500:
        raise ServiceError("Photos export limit must be between 1 and 500.")
    try:
        destination = export_path(dest)
    except ValueError as exc:
        raise ServiceError(str(exc)) from exc
    if destination.exists():
        raise ServiceError("Photos export destination must be a new directory.")
    try:
        assets = photosdb.list_assets(
            album=album,
            limit=limit,
            kind=kind,
            favorites_only=favorites,
        )
    except PhotosAccessError as exc:
        raise ServiceError(str(exc)) from exc
    local: list[dict[str, Any]] = []
    remote: list[dict[str, str]] = []
    for asset in assets:
        try:
            asset_uuid = str(uuid.UUID(asset.uuid)).upper()
        except (ValueError, AttributeError, TypeError) as exc:
            raise ServiceError(
                f"Photos catalog contains an invalid asset UUID: {asset.uuid!r}"
            ) from exc
        source = photosdb.find_local_original(asset)
        identity = {"uuid": asset_uuid, "filename": Path(asset.filename).name}
        if source is None:
            remote.append(identity)
            continue
        local.append(
            {
                **identity,
                "exportName": f"{asset_uuid}-{Path(asset.filename).name}",
                "source": _file_snapshot(source.resolve()),
            }
        )
    return {
        "destination": str(destination),
        "assets": local,
        "notDownloaded": remote,
        "selected": len(assets),
    }


def prepare_photos_favorite(filename: str, *, favorite: bool) -> dict[str, Any]:
    name = (filename or "").strip()
    if not name:
        raise ServiceError("filename is required.")
    return {"filename": name, "favorite": bool(favorite)}


def exec_photos_favorite(payload: dict[str, Any]) -> dict[str, Any]:
    filename = str(payload.get("filename") or "")
    favorite = bool(payload.get("favorite"))
    try:
        photos_script.set_favorite(filename, favorite=favorite)
    except PhotosScriptError as exc:
        raise ServiceError(str(exc)) from exc
    return {"filename": filename, "favorite": favorite, "ok": True}


def prepare_photos_album_add(filename: str, album: str) -> dict[str, Any]:
    name = (filename or "").strip()
    album_name = (album or "").strip()
    if not name or not album_name:
        raise ServiceError("filename and album are required.")
    return {"filename": name, "album": album_name}


def exec_photos_album_add(payload: dict[str, Any]) -> dict[str, Any]:
    filename = str(payload.get("filename") or "")
    album = str(payload.get("album") or "")
    try:
        photos_script.add_to_album(filename, album)
    except PhotosScriptError as exc:
        raise ServiceError(str(exc)) from exc
    return {"filename": filename, "album": album, "ok": True}


def prepare_photos_album_create(album: str) -> dict[str, Any]:
    album_name = (album or "").strip()
    if not album_name:
        raise ServiceError("album is required.")
    return {"album": album_name}


def exec_photos_album_create(payload: dict[str, Any]) -> dict[str, Any]:
    album = str(payload.get("album") or "")
    try:
        photos_script.create_album(album)
    except PhotosScriptError as exc:
        raise ServiceError(str(exc)) from exc
    return {"album": album, "created": True}


def safari_list_tabs() -> list[dict[str, Any]]:
    try:
        tabs = safari_script.list_tabs()
    except SafariError as exc:
        raise ServiceError(str(exc)) from exc
    return [
        {
            "window_index": tab.window_index,
            "tab_index": tab.tab_index,
            "name": tab.name,
            "url": tab.url,
            "is_current": tab.is_current,
        }
        for tab in tabs
    ]


def safari_current_tab() -> dict[str, Any]:
    try:
        tab = safari_script.current_tab()
    except SafariError as exc:
        raise ServiceError(str(exc)) from exc
    if tab is None:
        return {"running": False}
    return {
        "running": True,
        "window_index": tab.window_index,
        "tab_index": tab.tab_index,
        "name": tab.name,
        "url": tab.url,
        "is_current": True,
    }


def prepare_safari_open_url(url: str, *, target: str = "new_tab") -> dict[str, Any]:
    try:
        canonical = safari_script.validate_url(url)
    except SafariError as exc:
        raise ServiceError(str(exc)) from exc
    if target not in safari_script.ALLOWED_TARGETS:
        raise ServiceError("target must be new_tab or new_window.")
    return {"url": canonical, "target": target}


def exec_safari_open_url(payload: dict[str, Any]) -> dict[str, Any]:
    url = payload.get("url")
    target = payload.get("target", "new_tab")
    try:
        canonical = safari_script.validate_url(str(url or ""))
    except SafariError as exc:
        raise ServiceError(str(exc)) from exc
    if canonical != url:
        raise ServiceError("Approved Safari URL changed; refusing to open.")
    if target not in safari_script.ALLOWED_TARGETS:
        raise ServiceError("target must be new_tab or new_window.")
    try:
        safari_script.open_url(canonical, target=target)
    except SafariError as exc:
        raise ServiceError(str(exc)) from exc
    return {"url": canonical, "target": target, "opened": True}


def prepare_safari_search(query: str, *, target: str = "new_tab") -> dict[str, Any]:
    try:
        url = safari_script.search_url(query)
    except SafariError as exc:
        raise ServiceError(str(exc)) from exc
    return prepare_safari_open_url(url, target=target)


def prepare_safari_close_tab(
    *,
    window_index: int,
    tab_index: int,
) -> dict[str, Any]:
    if isinstance(window_index, bool) or not isinstance(window_index, int) or window_index < 1:
        raise ServiceError("window_index must be a positive integer.")
    if isinstance(tab_index, bool) or not isinstance(tab_index, int) or tab_index < 1:
        raise ServiceError("tab_index must be a positive integer.")
    tabs = safari_list_tabs()
    match = next(
        (
            tab
            for tab in tabs
            if tab["window_index"] == window_index and tab["tab_index"] == tab_index
        ),
        None,
    )
    if match is None:
        raise ServiceError(f"No Safari tab at window {window_index} tab {tab_index}.")
    return {
        "window_index": window_index,
        "tab_index": tab_index,
        "name": match["name"],
        "url": match["url"],
    }


def exec_safari_close_tab(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        safari_script.close_tab(
            window_index=int(payload["window_index"]),
            tab_index=int(payload["tab_index"]),
            name=str(payload.get("name") or ""),
            url=str(payload.get("url") or ""),
        )
    except SafariError as exc:
        raise ServiceError(str(exc)) from exc
    return {
        "window_index": payload["window_index"],
        "tab_index": payload["tab_index"],
        "closed": True,
    }


def safari_page_text(
    *,
    window_index: int | None = None,
    tab_index: int | None = None,
) -> dict[str, Any]:
    try:
        if window_index is None or tab_index is None:
            current = safari_current_tab()
            if not current.get("running"):
                raise ServiceError("Safari is not running.")
            window_index = int(current["window_index"])
            tab_index = int(current["tab_index"])
            name = str(current.get("name") or "")
            url = str(current.get("url") or "")
        else:
            tabs = safari_list_tabs()
            match = next(
                (
                    tab
                    for tab in tabs
                    if tab["window_index"] == window_index and tab["tab_index"] == tab_index
                ),
                None,
            )
            if match is None:
                raise ServiceError(f"No Safari tab at window {window_index} tab {tab_index}.")
            name = str(match["name"])
            url = str(match["url"])
        text = safari_script.page_text(
            window_index=window_index,
            tab_index=tab_index,
            name=name,
            url=url,
        )
    except SafariError as exc:
        raise ServiceError(str(exc)) from exc
    return {
        "window_index": window_index,
        "tab_index": tab_index,
        "name": name,
        "url": url,
        "chars": len(text),
        "text": text,
    }


def safari_page_extract(
    *,
    window_index: int | None = None,
    tab_index: int | None = None,
    extract: str = "title_text",
) -> dict[str, Any]:
    try:
        if window_index is None or tab_index is None:
            current = safari_current_tab()
            if not current.get("running"):
                raise ServiceError("Safari is not running.")
            window_index = int(current["window_index"])
            tab_index = int(current["tab_index"])
            name = str(current.get("name") or "")
            url = str(current.get("url") or "")
        else:
            tabs = safari_list_tabs()
            match = next(
                (
                    tab
                    for tab in tabs
                    if tab["window_index"] == window_index and tab["tab_index"] == tab_index
                ),
                None,
            )
            if match is None:
                raise ServiceError(f"No Safari tab at window {window_index} tab {tab_index}.")
            name = str(match["name"])
            url = str(match["url"])
        text = safari_script.page_extract(
            window_index=window_index,
            tab_index=tab_index,
            name=name,
            url=url,
            extract=extract,
        )
    except SafariError as exc:
        raise ServiceError(str(exc)) from exc
    return {
        "window_index": window_index,
        "tab_index": tab_index,
        "name": name,
        "url": url,
        "extract": extract,
        "chars": len(text),
        "text": text,
    }


def music_now_playing() -> dict[str, Any]:
    try:
        return music_script.now_playing().to_dict()
    except MusicError as exc:
        raise ServiceError(str(exc)) from exc


def prepare_music_playback(action: str) -> dict[str, Any]:
    if action not in music_script.ALLOWED_PLAYBACK:
        raise ServiceError("playback action must be playpause, next, or previous.")
    now = music_now_playing()
    return {"action": action, "nowPlaying": now}


def exec_music_playback(payload: dict[str, Any]) -> dict[str, Any]:
    action = payload.get("action")
    if action not in music_script.ALLOWED_PLAYBACK:
        raise ServiceError("playback action must be playpause, next, or previous.")
    try:
        music_script.playback(action)
    except MusicError as exc:
        raise ServiceError(str(exc)) from exc
    return {"action": action, "ok": True}


def exec_music_playpause(payload: dict[str, Any]) -> dict[str, Any]:
    return exec_music_playback({**payload, "action": "playpause"})


def exec_music_next(payload: dict[str, Any]) -> dict[str, Any]:
    return exec_music_playback({**payload, "action": "next"})


def exec_music_previous(payload: dict[str, Any]) -> dict[str, Any]:
    return exec_music_playback({**payload, "action": "previous"})


def prepare_music_volume(level: int) -> dict[str, Any]:
    if isinstance(level, bool) or not isinstance(level, int) or not 0 <= level <= 100:
        raise ServiceError("volume must be an integer from 0 to 100.")
    return {"level": level, "nowPlaying": music_now_playing()}


def exec_music_volume(payload: dict[str, Any]) -> dict[str, Any]:
    level = payload.get("level")
    if isinstance(level, bool) or not isinstance(level, int) or not 0 <= level <= 100:
        raise ServiceError("volume must be an integer from 0 to 100.")
    try:
        music_script.set_volume(level)
    except MusicError as exc:
        raise ServiceError(str(exc)) from exc
    return {"level": level, "ok": True}


def prepare_music_shuffle(mode: str) -> dict[str, Any]:
    if mode not in music_script.ALLOWED_SHUFFLE:
        raise ServiceError("shuffle must be off, songs, albums, or groupings.")
    return {"mode": mode, "nowPlaying": music_now_playing()}


def exec_music_shuffle(payload: dict[str, Any]) -> dict[str, Any]:
    mode = str(payload.get("mode") or "")
    if mode not in music_script.ALLOWED_SHUFFLE:
        raise ServiceError("shuffle must be off, songs, albums, or groupings.")
    try:
        music_script.set_shuffle(mode)
    except MusicError as exc:
        raise ServiceError(str(exc)) from exc
    return {"mode": mode, "ok": True}


def prepare_music_repeat(mode: str) -> dict[str, Any]:
    if mode not in music_script.ALLOWED_REPEAT:
        raise ServiceError("repeat must be off, one, or all.")
    return {"mode": mode, "nowPlaying": music_now_playing()}


def exec_music_repeat(payload: dict[str, Any]) -> dict[str, Any]:
    mode = str(payload.get("mode") or "")
    if mode not in music_script.ALLOWED_REPEAT:
        raise ServiceError("repeat must be off, one, or all.")
    try:
        music_script.set_repeat(mode)
    except MusicError as exc:
        raise ServiceError(str(exc)) from exc
    return {"mode": mode, "ok": True}


def prepare_music_play(query: str) -> dict[str, Any]:
    candidate = (query or "").strip()
    if not candidate:
        raise ServiceError("query is required.")
    return {"query": candidate, "nowPlaying": music_now_playing()}


def exec_music_play(payload: dict[str, Any]) -> dict[str, Any]:
    query = str(payload.get("query") or "").strip()
    if not query:
        raise ServiceError("query is required.")
    try:
        music_script.play_by_name(query)
    except MusicError as exc:
        raise ServiceError(str(exc)) from exc
    return {"query": query, "ok": True}


def music_search(query: str, *, limit: int = music_script.MAX_SEARCH_RESULTS) -> dict[str, Any]:
    try:
        tracks = music_script.search_tracks(query, limit=limit)
    except MusicError as exc:
        raise ServiceError(str(exc)) from exc
    return {"query": query, "count": len(tracks), "tracks": tracks}


def weather_forecast(
    *,
    place: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    days: int = weather_client.DEFAULT_DAYS,
    temperature_unit: str = "celsius",
    hourly: bool = False,
    minutely: bool = False,
) -> dict[str, Any]:
    try:
        return weather_client.forecast(
            place=place,
            latitude=latitude,
            longitude=longitude,
            days=days,
            temperature_unit=temperature_unit,
            hourly=hourly,
            minutely=minutely,
        )
    except WeatherError as exc:
        raise ServiceError(str(exc)) from exc


def maps_search(
    query: str,
    *,
    latitude: float | None = None,
    longitude: float | None = None,
    zoom: int | None = None,
    map_type: str | None = None,
) -> dict[str, Any]:
    try:
        return maps_urls.build_search_url(
            query,
            latitude=latitude,
            longitude=longitude,
            zoom=zoom,
            map_type=map_type,
        )
    except MapsError as exc:
        raise ServiceError(str(exc)) from exc


def prepare_maps_open(
    *,
    query: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    saddr: str | None = None,
    daddr: str | None = None,
    dirflg: str | None = None,
    zoom: int | None = None,
    map_type: str | None = None,
) -> dict[str, Any]:
    has_query = bool((query or "").strip())
    has_dest = bool((daddr or "").strip())
    if has_query == has_dest:
        raise ServiceError("Provide either query or daddr, not both.")
    try:
        if has_query:
            if saddr or dirflg:
                raise ServiceError("saddr/dirflg apply only to directions.")
            built = maps_urls.build_search_url(
                query or "",
                latitude=latitude,
                longitude=longitude,
                zoom=zoom,
                map_type=map_type,
            )
        else:
            if latitude is not None or longitude is not None:
                raise ServiceError("latitude/longitude apply only to search.")
            built = maps_urls.build_directions_url(
                daddr=daddr or "",
                saddr=saddr,
                dirflg=dirflg,
                zoom=zoom,
                map_type=map_type,
            )
        canonical = maps_urls.validate_maps_url(built["url"])
    except MapsError as exc:
        raise ServiceError(str(exc)) from exc
    return {**built, "url": canonical}


def _rebuild_maps_url(payload: dict[str, Any]) -> dict[str, Any]:
    mode = payload.get("mode")
    query = payload.get("query") if isinstance(payload.get("query"), dict) else {}
    zoom = query.get("z")
    map_type = query.get("t")
    if mode == "search":
        ll = query.get("ll")
        latitude = longitude = None
        if ll:
            parts = str(ll).split(",")
            if len(parts) != 2:
                raise ServiceError("Approved Maps pin is invalid.")
            try:
                latitude = float(parts[0])
                longitude = float(parts[1])
            except ValueError as exc:
                raise ServiceError("Approved Maps pin is invalid.") from exc
        return maps_urls.build_search_url(
            str(query.get("q") or ""),
            latitude=latitude,
            longitude=longitude,
            zoom=int(zoom) if zoom is not None else None,
            map_type=map_type,
        )
    if mode == "directions":
        return maps_urls.build_directions_url(
            daddr=str(query.get("daddr") or ""),
            saddr=query.get("saddr"),
            dirflg=query.get("dirflg"),
            zoom=int(zoom) if zoom is not None else None,
            map_type=map_type,
        )
    raise ServiceError("Approved Maps payload is missing mode.")


def exec_maps_open(payload: dict[str, Any]) -> dict[str, Any]:
    url = payload.get("url")
    try:
        rebuilt = _rebuild_maps_url(payload)
        canonical = maps_urls.validate_maps_url(rebuilt["url"])
    except MapsError as exc:
        raise ServiceError(str(exc)) from exc
    if canonical != url:
        raise ServiceError("Approved Maps URL changed; refusing to open.")
    try:
        maps_urls.open_maps_url(canonical)
    except MapsError as exc:
        raise ServiceError(str(exc)) from exc
    return {"url": canonical, "opened": True}


def exec_photos_export(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        dest = export_path(payload["destination"])
    except ValueError as exc:
        raise ServiceError(str(exc)) from exc
    if str(dest) != payload["destination"] or dest.exists():
        raise ServiceError("Approved Photos export destination changed or already exists.")
    assets = payload["assets"]
    for asset in assets:
        source = Path(asset["source"]["path"]).resolve()
        if not _snapshot_matches(source, asset["source"]):
            raise ServiceError(
                f"Approved Photos original {asset['uuid']} changed; refusing export."
            )
    dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    claimed_destination = False
    try:
        dest.mkdir(mode=0o700)
        claimed_destination = True
        for asset in assets:
            source = Path(asset["source"]["path"])
            output = dest / asset["exportName"]
            shutil.copy2(source, output)
            if _file_sha256(output) != asset["source"]["sha256"]:
                raise ServiceError(
                    f"Exported Photos asset {asset['uuid']} failed integrity check."
                )
            output.chmod(0o600)
    except Exception:
        if claimed_destination:
            shutil.rmtree(dest, ignore_errors=True)
        raise
    return {
        "dest": str(dest),
        "exported": len(assets),
        "notDownloaded": len(payload["notDownloaded"]),
        "selected": payload["selected"],
    }


def health_read() -> dict[str, Any]:
    return health_status()


def prepare_ops_cleanup_agent(*, interval: int = 86_400) -> dict[str, Any]:
    dest = APP_DIR / "LaunchAgents" / "dev.icloudseal.mail-cleanup.plist"
    try:
        plist = generate_mail_cleanup_plist(interval=interval)
    except ValueError as exc:
        raise ServiceError(str(exc)) from exc
    return {
        "destination": str(dest),
        "interval": interval,
        "label": "dev.icloudseal.mail-cleanup",
        "plistSha256": canonical_sha256(plist),
    }


def exec_ops_cleanup_agent(payload: dict[str, Any]) -> dict[str, Any]:
    dest = Path(payload["destination"])
    if dest != APP_DIR / "LaunchAgents" / "dev.icloudseal.mail-cleanup.plist":
        raise ServiceError("Approved LaunchAgent destination changed.")
    try:
        plist = generate_mail_cleanup_plist(interval=int(payload["interval"]))
    except ValueError as exc:
        raise ServiceError(str(exc)) from exc
    if canonical_sha256(plist) != payload.get("plistSha256"):
        raise ServiceError("Approved LaunchAgent template changed.")
    written = write_mail_cleanup_plist(dest, interval=int(payload["interval"]))
    return {
        "path": str(written),
        "written": True,
        "loaded": False,
        "note": "Plist written only. launchctl load was not run.",
    }


# ---------------------------------------------------------------------------
# Register mutation executors
# ---------------------------------------------------------------------------

def register_all_executors() -> None:
    from . import approval

    approval.register_executor("mail.apply", exec_mail_apply)
    approval.register_executor("mail.cleanup_strict", exec_mail_cleanup_strict)
    approval.register_executor("mail.send", exec_mail_send)
    approval.register_executor("mail.forward", exec_mail_forward)
    approval.register_executor("mail.flags", exec_mail_flags)
    approval.register_executor("mail.move", exec_mail_move)
    approval.register_executor("mail.trash", exec_mail_trash)
    approval.register_executor("mail.create_folder", exec_mail_create_folder)
    approval.register_executor("contacts.create", exec_contacts_create)
    approval.register_executor("contacts.update", exec_contacts_update)
    approval.register_executor("contacts.delete", exec_contacts_delete)
    approval.register_executor("calendar.event_add", exec_event_add)
    approval.register_executor("calendar.event_rm", exec_event_rm)
    approval.register_executor("calendar.event_update", exec_event_update)
    approval.register_executor("calendar.reminder_add", exec_reminder_add)
    approval.register_executor("calendar.reminder_done", exec_reminder_done)
    approval.register_executor("calendar.reminder_rm", exec_reminder_rm)
    approval.register_executor("calendar.reminder_update", exec_reminder_update)
    approval.register_executor("messages.send", exec_messages_send)
    approval.register_executor("notes.create", exec_notes_create)
    approval.register_executor("notes.delete", exec_notes_delete)
    approval.register_executor("notes.update", exec_notes_update)
    approval.register_executor("drive.put", exec_drive_put)
    approval.register_executor("drive.mkdir", exec_drive_mkdir)
    approval.register_executor("drive.rm", exec_drive_rm)
    approval.register_executor("photos.export", exec_photos_export)
    approval.register_executor("photos.favorite", exec_photos_favorite)
    approval.register_executor("photos.album_add", exec_photos_album_add)
    approval.register_executor("photos.album_create", exec_photos_album_create)
    approval.register_executor("safari.open_url", exec_safari_open_url)
    approval.register_executor("safari.close_tab", exec_safari_close_tab)
    approval.register_executor("music.playpause", exec_music_playpause)
    approval.register_executor("music.next", exec_music_next)
    approval.register_executor("music.previous", exec_music_previous)
    approval.register_executor("music.volume", exec_music_volume)
    approval.register_executor("music.shuffle", exec_music_shuffle)
    approval.register_executor("music.repeat", exec_music_repeat)
    approval.register_executor("music.play", exec_music_play)
    approval.register_executor("maps.open", exec_maps_open)
    approval.register_executor("ops.cleanup_agent", exec_ops_cleanup_agent)
