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

from icloudseal_mcp import auth
from icloudseal_mcp.calendar.caldav import (
    build_event,
    complete_reminder,
    update_event,
    update_reminder,
    validate_timezone,
)
from icloudseal_mcp.contacts.carddav import parse_vcard, update_vcard
from icloudseal_mcp.health.helper import HealthError, health_status, read_samples
from icloudseal_mcp.mail.smtp_client import SMTPError, freeze_send, send_frozen
from icloudseal_mcp.ops.cleanup_agent import generate_mail_cleanup_plist
from icloudseal_mcp.maps import urls as maps_urls
from icloudseal_mcp.mcp import approval, services
from icloudseal_mcp.music import applescript as music_script
from icloudseal_mcp.notes import applescript
from icloudseal_mcp.paths import managed_path
from icloudseal_mcp.photos import applescript as photos_script
from icloudseal_mcp.safari import applescript as safari_script
from icloudseal_mcp.weather import client as weather_client


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
    applescript.create_note(malicious, malicious, folder=malicious)
    applescript.update_note(malicious, title=malicious, body=malicious)
    applescript.delete_note(malicious)

    for command in calls:
        script = command[2]
        assert malicious not in script
        assert "--" in command
    assert malicious in calls[0][4:]
    assert malicious in calls[1][4:]


def test_safari_url_rejects_unsafe_schemes() -> None:
    assert safari_script.validate_url("https://example.com/path") == "https://example.com/path"
    assert safari_script.validate_url("http://example.com") == "http://example.com"
    for bad in (
        "javascript:alert(1)",
        "file:///etc/passwd",
        "data:text/html,hi",
        "example.com",
        "https://",
        "ftp://example.com",
        "https://example.com/\nhttps://evil.test",
    ):
        with pytest.raises(safari_script.SafariError):
            safari_script.validate_url(bad)


def test_safari_open_url_values_are_passed_via_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    malicious = 'https://example.com/" & do shell script "touch /tmp/pwned" & "'

    def fake_run(args: list[str], **_: object) -> SimpleNamespace:
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    safari_script.open_url(malicious, target="new_tab")

    assert len(calls) == 1
    command = calls[0]
    script = command[2]
    assert malicious not in script
    assert "--" in command
    assert malicious in command[4:]
    assert "tab" in command[4:]


def test_music_playback_scripts_have_no_user_interpolation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **_: object) -> SimpleNamespace:
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    music_script.playback("playpause")
    music_script.playback("next")
    music_script.playback("previous")

    assert len(calls) == 3
    for command, expected in zip(
        calls,
        (
            music_script.PLAYBACK_SCRIPTS["playpause"],
            music_script.PLAYBACK_SCRIPTS["next"],
            music_script.PLAYBACK_SCRIPTS["previous"],
        ),
        strict=True,
    ):
        assert command[2] == expected
        assert command[3:] == ["--"]
    with pytest.raises(music_script.MusicError):
        music_script.playback("search")


def test_prepare_safari_open_url_freezes_canonical_url() -> None:
    frozen = services.prepare_safari_open_url(
        "  https://example.com/docs  ",
        target="new_window",
    )
    assert frozen == {"url": "https://example.com/docs", "target": "new_window"}
    with pytest.raises(services.ServiceError, match="http or https"):
        services.prepare_safari_open_url("javascript:alert(1)")


def test_maps_search_is_local_and_rejects_unsafe_urls() -> None:
    built = maps_urls.build_search_url("Cupertino")
    assert built["url"] == "https://maps.apple.com/?q=Cupertino"
    assert built["host"] == "maps.apple.com"
    assert built["scheme"] == "https"
    assert maps_urls.validate_maps_url(built["url"]) == built["url"]

    pin = maps_urls.build_search_url("Apple Park", latitude=37.323, longitude=-122.032)
    assert pin["query"]["ll"] == "37.323,-122.032"
    assert "q=Apple+Park" in pin["url"] or "q=Apple%20Park" in pin["url"]

    directions = maps_urls.build_directions_url(
        saddr="Cupertino",
        daddr="San Francisco",
        dirflg="d",
    )
    assert directions["mode"] == "directions"
    assert directions["query"]["dirflg"] == "d"

    for bad in (
        "javascript:alert(1)",
        "file:///etc/passwd",
        "data:text/html,hi",
        "maps://?q=Cupertino",
        "http://maps.apple.com/?q=Cupertino",
        "https://evil.test/?q=Cupertino",
        "https://maps.apple.com/?q=Cupertino\nhttps://evil.test",
        "https://user:pass@maps.apple.com/?q=x",
        "https://maps.apple.com/search?q=x",
        "https://maps.apple.com/?q=x#frag",
    ):
        with pytest.raises(maps_urls.MapsError):
            maps_urls.validate_maps_url(bad)

    with pytest.raises(maps_urls.MapsError, match="control"):
        maps_urls.build_search_url("Cupertino\nSan Francisco")
    with pytest.raises(maps_urls.MapsError, match="latitude"):
        maps_urls.build_search_url("x", latitude=91, longitude=0)
    with pytest.raises(maps_urls.MapsError, match="dirflg"):
        maps_urls.build_directions_url(daddr="SF", dirflg="fly")


def test_maps_open_uses_usr_bin_open_and_frozen_https(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **_: object) -> SimpleNamespace:
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    frozen = services.prepare_maps_open(query="Cupertino")
    assert frozen["url"] == "https://maps.apple.com/?q=Cupertino"
    result = services.exec_maps_open(frozen)
    assert result["opened"] is True
    assert calls == [["/usr/bin/open", "https://maps.apple.com/?q=Cupertino"]]

    with pytest.raises(services.ServiceError, match="changed"):
        services.exec_maps_open({**frozen, "url": "https://maps.apple.com/?q=Evil"})
    with pytest.raises(services.ServiceError, match="changed"):
        services.exec_maps_open({**frozen, "url": "maps://?q=Cupertino"})
    with pytest.raises(services.ServiceError, match="missing mode"):
        services.exec_maps_open({"url": "maps://?q=Cupertino"})
    with pytest.raises(services.ServiceError, match="query or daddr"):
        services.prepare_maps_open()

    pinned = services.prepare_maps_open(
        query="Cupertino",
        latitude=37.323,
        longitude=-122.032,
    )
    assert pinned["query"]["ll"] == "37.323,-122.032"
    assert services.exec_maps_open(pinned)["opened"] is True
    with pytest.raises(services.ServiceError, match="changed"):
        services.exec_maps_open({**pinned, "query": {**pinned["query"], "q": "Evil"}})


def test_weather_forecast_uses_pinned_hosts_and_mocked_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[str] = []

    class _Resp:
        def __init__(self, payload: dict) -> None:
            self._raw = json.dumps(payload).encode("utf-8")

        def read(self) -> bytes:
            return self._raw

        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

    geocode_body = {
        "results": [
            {
                "name": "Berlin",
                "country": "Germany",
                "admin1": "Berlin",
                "latitude": 52.52,
                "longitude": 13.41,
                "timezone": "Europe/Berlin",
            }
        ]
    }
    forecast_body = {
        "latitude": 52.52,
        "longitude": 13.41,
        "timezone": "Europe/Berlin",
        "current_units": {
            "temperature_2m": "°C",
            "weather_code": "wmo code",
            "wind_speed_10m": "km/h",
            "precipitation": "mm",
        },
        "current": {
            "time": "2026-03-26T12:00",
            "temperature_2m": 11.2,
            "weather_code": 3,
            "wind_speed_10m": 14.0,
            "precipitation": 0.0,
        },
        "daily_units": {
            "temperature_2m_max": "°C",
            "temperature_2m_min": "°C",
            "precipitation_sum": "mm",
            "weather_code": "wmo code",
        },
        "daily": {
            "time": ["2026-03-26", "2026-03-27", "2026-03-28"],
            "weather_code": [3, 61, 0],
            "temperature_2m_max": [12.0, 9.0, 14.0],
            "temperature_2m_min": [6.0, 4.0, 5.0],
            "precipitation_sum": [0.0, 2.5, 0.0],
        },
    }

    def fake_open(req: object, timeout: float = 0) -> _Resp:
        url = req.full_url if hasattr(req, "full_url") else str(req)
        requests.append(url)
        if "geocoding-api.open-meteo.com" in url:
            return _Resp(geocode_body)
        if "api.open-meteo.com" in url:
            return _Resp(forecast_body)
        raise AssertionError(f"unexpected host: {url}")

    payload = weather_client.forecast(place="Berlin", opener=fake_open)
    assert payload["attribution"] == "Weather data by Open-Meteo.com"
    assert payload["place"]["name"] == "Berlin"
    assert payload["current"]["condition"] == "Overcast"
    assert payload["current"]["temperature"] == 11.2
    assert len(payload["daily"]) == 3
    assert all("open-meteo.com" in url for url in requests)
    assert all(url.startswith("https://") for url in requests)
    assert any("geocoding-api.open-meteo.com" in url for url in requests)
    assert any("api.open-meteo.com/v1/forecast" in url for url in requests)

    coords = weather_client.forecast(latitude=37.323, longitude=-122.032, opener=fake_open)
    assert coords["place"]["latitude"] == 37.323
    assert coords["attribution"] == "Weather data by Open-Meteo.com"

    with pytest.raises(weather_client.WeatherError, match="either place"):
        weather_client.forecast(place="Berlin", latitude=52.52, longitude=13.41)
    with pytest.raises(weather_client.WeatherError, match="latitude"):
        weather_client.forecast(latitude=91, longitude=0)
    with pytest.raises(weather_client.WeatherError, match="control"):
        weather_client.forecast(place="Berlin\nhttps://evil.test")
    with pytest.raises(weather_client.WeatherError, match="days"):
        weather_client.forecast(latitude=52.52, longitude=13.41, days=99)

    def evil_open(req: object, timeout: float = 0) -> _Resp:
        raise AssertionError("must not be called")

    with pytest.raises(weather_client.WeatherError, match="non-Open-Meteo"):
        weather_client._request("https://evil.test/v1/forecast", opener=evil_open)


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

    flags = {
        "version": 1,
        "folder": "INBOX",
        "action": "flags",
        "flag": "+Seen",
        "messages": [{"uid": 7, "subject": "Review me"}],
    }
    normalized_flags = services.validate_mail_plan(flags)
    assert normalized_flags["flag"] == "+Seen"
    assert normalized_flags["destination"] is None
    with pytest.raises(services.ServiceError, match="Flag plan"):
        services.validate_mail_plan({**flags, "flag": "\\Flagged"})
    apply_payload = {
        "plan": {**normalized_flags, "uidvalidity": 99},
        "planSha256": services.canonical_sha256({**normalized_flags, "uidvalidity": 99}),
    }
    with pytest.raises(services.ServiceError, match="move or delete"):
        services.exec_mail_apply(apply_payload)


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


def test_freeze_send_rejects_newlines_empty_and_too_many() -> None:
    with pytest.raises(SMTPError, match="newlines"):
        freeze_send(
            from_addr="jane@example.com",
            to="boss@example.com",
            subject="Hello\nBcc: evil@example.com",
            body="Hi",
        )
    with pytest.raises(SMTPError, match="empty"):
        freeze_send(
            from_addr="jane@example.com",
            to="boss@example.com",
            subject="Hello",
            body="   ",
        )
    with pytest.raises(SMTPError, match="Too many recipients"):
        freeze_send(
            from_addr="jane@example.com",
            to=[f"user{i}@example.com" for i in range(21)],
            subject="Hello",
            body="Hi",
        )


def test_send_frozen_uses_factory_and_never_opens_a_socket() -> None:
    frozen = freeze_send(
        from_addr="jane@example.com",
        to="boss@example.com",
        cc="cc@example.com",
        bcc="bcc@example.com",
        subject="Hello",
        body="Hi there",
        message_id="<test@icloudseal-mcp.local>",
    )
    smtp = _FakeSMTP()

    result = send_frozen(frozen, password="app-specific", smtp_factory=lambda: smtp)

    assert smtp.calls == ["ehlo", "starttls", "ehlo", "login", "send_message"]
    assert smtp.login_args == ("jane@example.com", "app-specific")
    assert smtp.to_addrs == ["boss@example.com", "cc@example.com", "bcc@example.com"]
    assert smtp.from_addr == "jane@example.com"
    assert smtp.message["Subject"] == "Hello"
    assert smtp.message["Bcc"] is None
    assert result["recipients"] == 3
    assert result["messageId"] == "<test@icloudseal-mcp.local>"


def test_exec_mail_send_rejects_tampered_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        auth,
        "load_credentials",
        lambda: auth.Credentials(email="jane@example.com", password="secret"),
    )
    frozen = services.prepare_mail_send(
        to="boss@example.com",
        subject="Hello",
        body="Hi there",
    )
    frozen["body"] = "Tampered"

    with pytest.raises(services.ServiceError, match="integrity"):
        services.exec_mail_send(frozen)


def test_update_event_preserves_recurrence_and_alarm() -> None:
    raw = (
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:e1\r\nSUMMARY:Standup\r\n"
        "DTSTART:20260115T090000\r\nDTEND:20260115T093000\r\n"
        "RRULE:FREQ=WEEKLY\r\nX-CUSTOM:keep\r\nSEQUENCE:2\r\n"
        "BEGIN:VALARM\r\nACTION:DISPLAY\r\nDESCRIPTION:ping\r\n"
        "END:VALARM\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )

    updated = update_event(raw, summary="Daily standup", location="Room 2")

    assert "SUMMARY:Daily standup" in updated
    assert "LOCATION:Room 2" in updated
    assert "RRULE:FREQ=WEEKLY" in updated
    assert "X-CUSTOM:keep" in updated
    assert "BEGIN:VALARM" in updated
    assert "DESCRIPTION:ping" in updated
    assert "SEQUENCE:3" in updated
    assert updated.count("SUMMARY:") == 1
    with pytest.raises(ValueError, match="at least one"):
        update_event(raw)


def test_update_reminder_preserves_recurrence_and_alarm() -> None:
    raw = (
        "BEGIN:VCALENDAR\r\nBEGIN:VTODO\r\nUID:t1\r\nSUMMARY:Buy milk\r\n"
        "DUE:20260115T180000\r\nRRULE:FREQ=WEEKLY\r\nX-CUSTOM:keep\r\n"
        "SEQUENCE:4\r\nBEGIN:VALARM\r\nACTION:DISPLAY\r\nDESCRIPTION:ping\r\n"
        "END:VALARM\r\nSTATUS:NEEDS-ACTION\r\nEND:VTODO\r\nEND:VCALENDAR\r\n"
    )

    updated = update_reminder(raw, summary="Buy oat milk", due="")

    assert "SUMMARY:Buy oat milk" in updated
    assert "DUE:" not in updated
    assert "RRULE:FREQ=WEEKLY" in updated
    assert "X-CUSTOM:keep" in updated
    assert "BEGIN:VALARM" in updated
    assert "DESCRIPTION:ping" in updated
    assert "SEQUENCE:5" in updated
    assert updated.count("SUMMARY:") == 1
    with pytest.raises(ValueError, match="at least one"):
        update_reminder(raw)


def test_parse_mail_uids_rejects_empty_and_duplicates() -> None:
    assert services.parse_mail_uids("7, 8") == [7, 8]
    assert services.parse_mail_uids([9]) == [9]
    with pytest.raises(services.ServiceError, match="at least one"):
        services.parse_mail_uids("")
    with pytest.raises(services.ServiceError, match="unique"):
        services.parse_mail_uids("7,7")


class _FakeSMTP:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.login_args: tuple[str, str] | None = None
        self.message = None
        self.from_addr = None
        self.to_addrs: list[str] = []

    def __enter__(self) -> _FakeSMTP:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def ehlo(self) -> None:
        self.calls.append("ehlo")

    def starttls(self) -> None:
        self.calls.append("starttls")

    def login(self, username: str, password: str) -> None:
        self.calls.append("login")
        self.login_args = (username, password)

    def send_message(self, message, from_addr=None, to_addrs=None) -> None:
        self.calls.append("send_message")
        self.message = message
        self.from_addr = from_addr
        self.to_addrs = list(to_addrs or [])


def test_freeze_send_keeps_reply_headers_and_attachment_snapshots(
    tmp_path: Path,
) -> None:
    attachment = tmp_path / "note.txt"
    attachment.write_text("hello", encoding="utf-8")
    digest = "a" * 64
    frozen = freeze_send(
        from_addr="jane@example.com",
        to="boss@example.com",
        subject="Re: Hello",
        body="Hi again",
        in_reply_to="<orig@example.com>",
        references="<orig@example.com>",
        attachments=[
            {
                "path": str(attachment),
                "name": "note.txt",
                "size": attachment.stat().st_size,
                "sha256": digest,
            }
        ],
    )
    assert frozen["inReplyTo"] == "<orig@example.com>"
    assert frozen["references"] == "<orig@example.com>"
    assert frozen["attachments"][0]["name"] == "note.txt"
    assert frozen["attachments"][0]["sha256"] == digest
    with pytest.raises(SMTPError, match="newlines"):
        freeze_send(
            from_addr="jane@example.com",
            to="boss@example.com",
            subject="Hello",
            body="Hi",
            in_reply_to="bad\nid",
        )


def test_mail_flags_accept_extra_tokens_but_still_reject_raw_imap() -> None:
    for token in ("+Flagged", "-Flagged", "+Answered", "-Answered"):
        plan = {
            "version": 1,
            "folder": "INBOX",
            "action": "flags",
            "flag": token,
            "messages": [{"uid": 7, "subject": "Review me"}],
        }
        assert services.validate_mail_plan(plan)["flag"] == token
    with pytest.raises(services.ServiceError, match="Flag plan"):
        services.validate_mail_plan(
            {
                "version": 1,
                "folder": "INBOX",
                "action": "flags",
                "flag": "\\Flagged",
                "messages": [{"uid": 7, "subject": "Review me"}],
            }
        )


def test_prepare_mail_create_folder_rejects_controls() -> None:
    frozen = services.prepare_mail_create_folder("Archive/2026")
    assert frozen == {"folder": "Archive/2026"}
    with pytest.raises(services.ServiceError, match="control"):
        services.prepare_mail_create_folder("Inbox\nINBOX")


def test_drive_mkdir_refuses_root_and_existing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    drive = tmp_path / "CloudDocs"
    drive.mkdir()
    (drive / "exists").mkdir()
    monkeypatch.setattr(services, "DRIVE_ROOT", drive)

    frozen = services.prepare_drive_mkdir("inbox/2026")
    assert frozen["relative"] == "inbox/2026"
    assert not Path(frozen["path"]).exists()
    result = services.exec_drive_mkdir(frozen)
    assert result["created"] is True
    assert (drive / "inbox" / "2026").is_dir()

    with pytest.raises(services.ServiceError, match="Already exists"):
        services.prepare_drive_mkdir("exists")
    with pytest.raises(services.ServiceError, match="root"):
        services.prepare_drive_mkdir(".")


def test_event_attendees_and_timezone_are_validated() -> None:
    ics = build_event(
        uid="E1",
        summary="Sync",
        start="2026-04-01 09:00",
        end="2026-04-01 10:00",
        timezone="America/Los_Angeles",
        attendees="ada@example.com, bob@example.com",
    )
    assert "TZID=America/Los_Angeles" in ics
    assert "ATTENDEE:mailto:ada@example.com" in ics
    assert "ATTENDEE:mailto:bob@example.com" in ics
    with pytest.raises(ValueError, match="IANA"):
        validate_timezone("America/Los Angeles")

    raw = (
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:e1\r\nSUMMARY:Standup\r\n"
        "DTSTART:20260115T090000\r\nDTEND:20260115T093000\r\n"
        "RRULE:FREQ=WEEKLY\r\nSEQUENCE:2\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    updated = update_event(
        raw,
        timezone="Europe/Berlin",
        start="2026-01-15 10:00",
        attendees="ada@example.com",
    )
    assert "TZID=Europe/Berlin" in updated
    assert "ATTENDEE:mailto:ada@example.com" in updated
    assert "RRULE:FREQ=WEEKLY" in updated
    assert "SEQUENCE:3" in updated


def test_weather_hourly_is_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Resp:
        def __init__(self, payload: dict) -> None:
            self._raw = json.dumps(payload).encode("utf-8")

        def read(self) -> bytes:
            return self._raw

        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

    forecast_body = {
        "latitude": 52.52,
        "longitude": 13.41,
        "timezone": "Europe/Berlin",
        "current_units": {
            "temperature_2m": "°C",
            "weather_code": "wmo code",
            "wind_speed_10m": "km/h",
            "precipitation": "mm",
        },
        "current": {
            "time": "2026-03-26T12:00",
            "temperature_2m": 11.2,
            "weather_code": 3,
            "wind_speed_10m": 14.0,
            "precipitation": 0.0,
        },
        "daily_units": {
            "temperature_2m_max": "°C",
            "temperature_2m_min": "°C",
            "precipitation_sum": "mm",
            "weather_code": "wmo code",
        },
        "daily": {
            "time": ["2026-03-26"],
            "weather_code": [3],
            "temperature_2m_max": [12.0],
            "temperature_2m_min": [6.0],
            "precipitation_sum": [0.0],
        },
        "hourly": {
            "time": ["2026-03-26T12:00"],
            "weather_code": [3],
            "temperature_2m": [11.2],
            "precipitation": [0.0],
            "wind_speed_10m": [14.0],
        },
    }
    urls: list[str] = []

    def fake_open(req: object, timeout: float = 0) -> _Resp:
        url = req.full_url if hasattr(req, "full_url") else str(req)
        urls.append(url)
        return _Resp(forecast_body)

    default = weather_client.forecast(
        latitude=52.52, longitude=13.41, opener=fake_open
    )
    assert "hourly" not in default
    assert "hourly=" not in urls[0]

    hourly = weather_client.forecast(
        latitude=52.52, longitude=13.41, hourly=True, opener=fake_open
    )
    assert "hourly=" in urls[1]
    assert hourly["hourly"][0]["temperature"] == 11.2


def test_maps_optional_zoom_and_type_stay_off_by_default() -> None:
    built = maps_urls.build_search_url("Cupertino")
    assert built["url"] == "https://maps.apple.com/?q=Cupertino"
    extra = maps_urls.build_search_url("Cupertino", zoom=12, map_type="k")
    assert extra["query"]["z"] == "12"
    assert extra["query"]["t"] == "k"
    with pytest.raises(maps_urls.MapsError, match="zoom"):
        maps_urls.build_search_url("Cupertino", zoom=99)
    with pytest.raises(maps_urls.MapsError, match="map type"):
        maps_urls.build_search_url("Cupertino", map_type="fly")


def test_safari_search_and_close_use_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    assert safari_script.search_url("icloud seal") == (
        "https://www.google.com/search?q=icloud+seal"
    )
    frozen = services.prepare_safari_search("icloud seal", target="new_window")
    assert frozen == {
        "url": "https://www.google.com/search?q=icloud+seal",
        "target": "new_window",
    }
    calls: list[list[str]] = []

    def fake_run(args: list[str], **_: object) -> SimpleNamespace:
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    safari_script.close_tab(
        window_index=1,
        tab_index=2,
        name="Docs",
        url="https://example.com/docs",
    )
    command = calls[0]
    assert "https://example.com/docs" not in command[2]
    assert command[4:] == ["1", "2", "Docs", "https://example.com/docs"]


def test_music_controls_use_argv_and_reject_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **_: object) -> SimpleNamespace:
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    music_script.set_volume(40)
    music_script.set_shuffle("albums")
    music_script.set_repeat("one")
    music_script.play_by_name('track" & do shell script "id"')
    assert calls[0][4:] == ["40"]
    assert calls[1][4:] == ["albums"]
    assert calls[2][4:] == ["one"]
    assert 'track" & do shell script "id"' in calls[3][4:]
    assert 'do shell script "id"' not in calls[3][2]
    with pytest.raises(music_script.MusicError):
        music_script.set_shuffle("random")
    with pytest.raises(music_script.MusicError):
        music_script.playback("search")


def test_photos_mutations_match_filename_via_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **_: object) -> SimpleNamespace:
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    photos_script.set_favorite("IMG_0001.HEIC", favorite=True)
    photos_script.add_to_album("IMG_0001.HEIC", "Trip")
    assert calls[0][4:] == ["IMG_0001.HEIC", "1"]
    assert calls[1][4:] == ["IMG_0001.HEIC", "Trip"]
    frozen = services.prepare_photos_favorite("IMG_0001.HEIC", favorite=False)
    assert frozen == {"filename": "IMG_0001.HEIC", "favorite": False}


def test_health_status_is_fail_closed() -> None:
    status = health_status()
    assert status["ok"] is False
    assert "HealthKit" in status["reason"]
    assert services.health_read()["ok"] is False
    with pytest.raises(HealthError, match="HealthKit"):
        read_samples(kind="steps")


def test_ops_cleanup_agent_writes_interval_plist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(services, "APP_DIR", tmp_path)
    plist = generate_mail_cleanup_plist(python_exe="/usr/bin/python3", interval=7200)
    assert "StartInterval" in plist
    assert "<integer>7200</integer>" in plist
    assert "mail</string>" in plist
    assert "cleanup</string>" in plist
    assert "strict</string>" in plist
    assert "--apply" in plist
    frozen = services.prepare_ops_cleanup_agent(interval=7200)
    assert frozen["interval"] == 7200
    assert frozen["destination"].endswith("dev.icloudseal.mail-cleanup.plist")
    result = services.exec_ops_cleanup_agent(frozen)
    assert result["written"] is True
    assert result["loaded"] is False
    written = Path(result["path"])
    assert written.is_file()
    assert "StartInterval" in written.read_text(encoding="utf-8")
    with pytest.raises(services.ServiceError, match="destination"):
        services.exec_ops_cleanup_agent({**frozen, "destination": str(tmp_path / "other.plist")})
