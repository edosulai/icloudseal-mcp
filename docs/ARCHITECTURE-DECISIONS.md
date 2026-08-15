# Architecture decisions — icloudseal-mcp

## Name

- **Canonical project / folder / GitHub repo:** `icloudseal-mcp`
- **Python package:** `icloudseal_mcp`
- **CLI entry:** `icloudseal-mcp` (legacy `mail-agent` kept for mail-only muscle memory)
- Renamed from: `icloud-mail-agent` → `icloud-agent` → **`icloudseal-mcp`**

## Why "seal"

Aligns with:

- `whatseal-mcp` — sealed WhatsApp MCP
- `instaseal-mcp` — sealed Instagram MCP
- `icloudseal-mcp` — sealed iCloud / Apple personal-data access

"Seal" means: local-first, private by default, agent cannot perform sensitive
actions without explicit local approval.

## Security model

| Class | Examples | Gate |
|---|---|---|
| Metadata / health | `icloud_doctor`, folder counts, domain list | free |
| Sensitive read | SMS/iMessage bodies, email bodies, full contacts, notes | free over MCP (content enters agent context); FDA/Automation OS gates apply |
| Externally visible / mutate | send mail, flag/move/trash mail, send iMessage, create/update/delete contacts, calendar write, notes update, drive rm, mail apply, Safari open-url, Music playback, Maps open | **prepare preview → explicit chat OK → Touch ID / macOS password** |

- CLI: dry-run plans + `--apply`.
- MCP: `mcp-wrapper.sh` → `icloudseal_mcp.mcp.server` (~66 tools).  
  Draft store TTL 10 minutes, single-use; helper `native-approval.swift` compiled to
  `~/Library/Application Support/icloudseal-mcp/bin/native-approval` (mode `0500`).
- MCP SDK is pinned to the supported major range `mcp>=2,<3`; tool failures use
  MCP-native `ToolError`, so clients receive `isError: true`.
- Outcomes persist atomically under `.../icloudseal-mcp/approvals/outcomes/`
  using canonical UUID filenames and mode `0600`; state directories use `0700`.
- Cancelled native authentication restores an unexpired draft to `prepared`,
  and the durable outcome reports that same retryable state.

## Immutable approval contract

Prepare resolves every mutable selector before native approval:

- Mail: canonical embedded plan + SHA-256, exact UIDs/message metadata, and
  mailbox `UIDVALIDITY`; execution rechecks all values in the selected mailbox.
  SMTP send freezes From/To/Cc/Bcc/subject/body/Message-ID and rehashes before
  delivery. From is always the Keychain email.
- Contacts/Calendar: exact href, ETag, UID, and raw vCard/iCalendar document.
  Unknown properties, recurrence, and alarms are preserved on update.
- Notes: exact Notes.app ID, modified date, body, and body hash.
- Drive: resolved source/destination or removal target, stat identity, and
  content/tree hashes; overwrite must be explicit.
- Photos: exact asset UUID/catalog path and downloaded-original hash. Exports
  use UUID-prefixed filenames and a new destination directory.
- Safari: exact `http`/`https` URL plus `new_tab`/`new_window`. No implicit
  scheme prefix. Page source and JavaScript are not frozen or executed.
- Music: playback action plus a now-playing snapshot for the Touch ID preview.
  Scripts are constant (`playpause` / `next track` / `previous track`).
- Maps: exact `https://maps.apple.com/?…` URL plus the frozen query
  params. Open rebuilds the URL from those params, refuses if it no
  longer matches the approved URL, then launches `/usr/bin/open`.

Query strings, mutable plan-file references, and post-approval re-searches are
not executor inputs. ETag/UIDVALIDITY/hash mismatches fail closed.

## Filesystem and automation boundaries

- MCP plans: `~/Library/Application Support/icloudseal-mcp/plans/` only.
- MCP exports: `~/Library/Application Support/icloudseal-mcp/exports/` only.
- Existing outputs are not overwritten implicitly; Photos export destinations
  must be new and Drive overwrite requires explicit approval.
- Notes, Messages, Finder, and Safari AppleScript receive untrusted values
  through `argv`; no dynamic value is interpolated into AppleScript source.
  Music playback scripts take no arguments.

## Domains (live CLI + MCP)

1. Mail — IMAP + SMTP + local SQLite cache
2. Contacts — CardDAV
3. Calendar + Reminders — CalDAV
4. Messages / SMS — `~/Library/Messages/chat.db` (Full Disk Access)
5. Notes — AppleScript
6. iCloud Drive — filesystem under CloudDocs
7. Photos — `Photos.sqlite` read + best-effort export
8. Safari — AppleScript tabs/current + gated http(s) open
9. Music — AppleScript now-playing + gated playpause/next/previous
10. Weather — Open-Meteo HTTPS current + short daily forecast
11. Maps — documented `maps.apple.com` URL search + gated open

**Not supported:** WhatsApp (use `whatseal-mcp`). Instagram (use `instaseal-mcp`).
HealthKit (needs a signed native helper). WeatherKit / MapKit entitlements.
Weather.app / Maps.app scraping. Device location / CoreLocation.

## Credential / storage identity

- Keychain service: `icloudseal-mcp`
- App Support: `~/Library/Application Support/icloudseal-mcp/`
- Migrated from legacy `icloud-mail-agent` (mail-agent → icloud-agent → icloudseal-mcp).
- One-time migration: move App Support tree + re-store Keychain password under the new service name, then delete the old item.

## Related paths

- Repo folder: `icloudseal-mcp`
- Wrapper: `mcp-wrapper.sh`
- MCP catalog key: `icloudseal`
- Sibling tools: `whatseal-mcp`, `instaseal-mcp`

## Capability notes for agents

- SMS OTP reading: `icloud_messages_search` or `icloudseal-mcp messages search "<query>" --limit N --json`
- Requires macOS Messages database access (FDA for Terminal/IDE).
- Freelancer / web OTP that only arrives on **WhatsApp** cannot be read here — use `whatseal-mcp`.
- MCP mutations: never claim success unless `icloud_request_local_approval` / `icloud_action_outcome` reports success.
