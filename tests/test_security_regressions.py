"""Security and immutability regressions (no network or native dialogs)."""

from __future__ import annotations

import json
import stat
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from icloudseal_mcp.calendar.caldav import complete_reminder
from icloudseal_mcp.contacts.carddav import parse_vcard, update_vcard
from icloudseal_mcp.mcp import approval, services
from icloudseal_mcp.notes import applescript
from icloudseal_mcp.paths import managed_path


def test_outcome_ids_cannot_escape_and_files_are_private(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = tmp_path / "app"
    monkeypatch.setattr(approval, "APP_DIR", app)
    monkeypatch.setattr(approval, "ensure_app_dir", lambda: None)

    with pytest.raises(approval.ApprovalError):
        approval.get_outcome("../../escape")
    assert not (tmp_path / "escape.json").exists()

    approval_id = str(uuid.uuid4())
    record = approval.record_outcome(approval_id, {"state": "prepared"})
    outcome = app / "approvals" / "outcomes" / f"{approval_id}.json"

    assert record["approvalId"] == approval_id
    assert json.loads(outcome.read_text(encoding="utf-8"))["state"] == "prepared"
    assert stat.S_IMODE(app.stat().st_mode) == 0o700
    assert stat.S_IMODE(outcome.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(outcome.stat().st_mode) == 0o600
    assert not list(outcome.parent.glob("*.tmp"))


def test_managed_paths_reject_escape_and_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "managed"
    monkeypatch.setattr("icloudseal_mcp.paths.APP_DIR", tmp_path / "app")

    with pytest.raises(ValueError):
        managed_path(root, "../outside.json")

    outside = tmp_path / "outside"
    outside.mkdir()
    root.mkdir(mode=0o700, exist_ok=True)
    (root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        managed_path(root, "link/file.json")


def test_managed_root_cannot_be_a_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "managed-link"
    root.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr("icloudseal_mcp.paths.APP_DIR", tmp_path / "app")

    with pytest.raises(ValueError, match="real directory"):
        managed_path(root, "export.json")


def test_vcard_update_preserves_unexposed_properties() -> None:
    raw = (
        "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:u1\r\nN:Doe;Jane;;;\r\n"
        "FN:Jane Doe\r\nEMAIL;TYPE=HOME:jane@example.com\r\n"
        "PHOTO;ENCODING=b:AAAA\r\nX-CUSTOM:keep\r\nEND:VCARD\r\n"
    )
    contact = parse_vcard(raw)
    contact.full_name = "Jane Smith"
    contact.emails = ["new@example.com"]

    updated = update_vcard(contact)

    assert "PHOTO;ENCODING=b:AAAA" in updated
    assert "X-CUSTOM:keep" in updated
    assert "FN:Jane Smith" in updated
    assert "new@example.com" in updated
    assert "jane@example.com" not in updated


def test_complete_reminder_preserves_recurrence_and_alarm() -> None:
    raw = (
        "BEGIN:VCALENDAR\r\nBEGIN:VTODO\r\nUID:t1\r\nSUMMARY:Test\r\n"
        "RRULE:FREQ=DAILY\r\nBEGIN:VALARM\r\nACTION:DISPLAY\r\n"
        "END:VALARM\r\nSTATUS:NEEDS-ACTION\r\nEND:VTODO\r\nEND:VCALENDAR\r\n"
    )

    updated = complete_reminder(raw)

    assert "RRULE:FREQ=DAILY" in updated
    assert "BEGIN:VALARM" in updated
    assert updated.count("STATUS:COMPLETED") == 1
    assert "PERCENT-COMPLETE:100" in updated


def test_notes_values_are_passed_via_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    malicious = 'id" & do shell script "touch /tmp/pwned" & "'

    def fake_run(args: list[str], **_: object) -> SimpleNamespace:
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="<p>body</p>", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    applescript.read_note(malicious)
    applescript.create_note(malicious, malicious)
    applescript.delete_note(malicious)

    for command in calls:
        script = command[2]
        assert malicious not in script
        assert "--" in command
    assert malicious in calls[0][4:]


def test_drive_source_change_aborts_before_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    drive = tmp_path / "CloudDocs"
    drive.mkdir()
    source = tmp_path / "source.txt"
    source.write_text("approved", encoding="utf-8")
    monkeypatch.setattr(services, "DRIVE_ROOT", drive)

    frozen = services.prepare_drive_put(str(source), "dest.txt", overwrite=False)
    source.write_text("changed", encoding="utf-8")

    with pytest.raises(services.ServiceError, match="source changed"):
        services.exec_drive_put(frozen)
    assert not (drive / "dest.txt").exists()


def test_drive_existing_destination_requires_explicit_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    drive = tmp_path / "CloudDocs"
    drive.mkdir()
    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")
    (drive / "dest.txt").write_text("existing", encoding="utf-8")
    monkeypatch.setattr(services, "DRIVE_ROOT", drive)

    with pytest.raises(services.ServiceError, match="overwrite=true"):
        services.prepare_drive_put(str(source), "dest.txt", overwrite=False)


def test_drive_directory_content_change_aborts_before_trash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    drive = tmp_path / "CloudDocs"
    folder = drive / "folder"
    folder.mkdir(parents=True)
    child = folder / "child.txt"
    child.write_text("approved", encoding="utf-8")
    monkeypatch.setattr(services, "DRIVE_ROOT", drive)

    target = services.prepare_drive_remove("folder")
    child.write_text("changed", encoding="utf-8")

    with pytest.raises(services.ServiceError, match="target changed"):
        services.exec_drive_rm({"target": target})


def test_mail_plan_hash_and_schema_are_bounded() -> None:
    plan = {
        "version": 1,
        "folder": "INBOX",
        "action": "move",
        "destination": "Archive",
        "messages": [{"uid": 7, "subject": "Review me"}],
    }
    normalized = services.validate_mail_plan(plan)
    frozen = {**normalized, "uidvalidity": 99}

    assert services.validate_mail_plan(frozen, require_frozen=True) == frozen
    assert services.canonical_sha256(frozen) == services.canonical_sha256(
        dict(reversed(list(frozen.items())))
    )

    duplicate = {**plan, "messages": [{"uid": 7}, {"uid": 7}]}
    with pytest.raises(services.ServiceError, match="unique"):
        services.validate_mail_plan(duplicate)


def test_mcp_failure_sets_is_error_true() -> None:
    async def scenario() -> None:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "icloudseal_mcp.mcp.server"],
            cwd=str(Path(__file__).resolve().parents[1]),
        )
        with open("/dev/null", "w", encoding="utf-8") as errlog:
            async with stdio_client(params, errlog=errlog) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        "icloud_action_outcome",
                        {"approval_id": "../../escape"},
                    )
                    assert result.is_error is True

    anyio.run(scenario)
