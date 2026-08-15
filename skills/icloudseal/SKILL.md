---
name: icloudseal
description: "Use for iCloud Mail, Contacts, Calendar, Reminders, Messages, Notes, Drive, Photos, Safari, Music, Weather, Maps, Shortcuts, Health status, or sealed MCP readiness. Reads are free; every create/update/delete needs Touch ID."
---

# /icloudseal

Sealed iCloud MCP — local CLI + stdio MCP for AI agents. Part of the seal family
with `whatseal-mcp` (WhatsApp) and `instaseal-mcp` (Instagram).

The agent thinks (classify, summarize, decide). This tool does the hands (fetch,
list, create, move, delete). Reads are free. Every mutation is sealed until
Touch ID or the macOS login password.

Prefer MCP tools when they are available. The CLI is `icloudseal-mcp <domain>
<action>` from the icloudseal-mcp checkout. Do not invent mail, events, contacts,
or claim a mutation succeeded.

## Usage

```
/icloudseal                                 # doctor + domain status
/icloudseal mail list                       # recent mail (read)
/icloudseal calendar events                 # upcoming events (read)
/icloudseal messages search "<query>"       # SMS/iMessage search (read)
/icloudseal drive ls [path]                 # iCloud Drive listing (read)
/icloudseal prepare mail send …             # prepare send — do not send
/icloudseal prepare event add …             # prepare create — do not write
```

MCP equivalents: `icloud_doctor`, `icloud_mail_list`, `icloud_calendar_events`,
`icloud_messages_search`, `icloud_drive_ls`, `icloud_prepare_mail_send`,
`icloud_prepare_event_add`. Full catalog: `references/tools.md`.

## What You Must Do When Invoked

If the user asked `/icloudseal --help` or `/icloudseal -h` with no other
arguments, print the `## Usage` block above verbatim and stop.

Follow these steps in order. Do not skip them.

### Step 1 — Readiness (always first)

1. Call `icloud_doctor` or `icloud_status` (preferred first call in a new chat).
2. If `ready=false`, show the returned `userMessage` / `agentNextSteps`.
   That is usually Keychain setup (`icloudseal-mcp mail setup`) plus Full Disk
   Access or Automation prompts. Do not mint Keychain items or start IMAP/DAV
   unless the user asked.
3. Messages, Photos, and Safari bookmarks/history need Full Disk Access.
   Notes, Safari, and Music need Automation. Drive is local CloudDocs.
4. Health is status-only and fail-closed. Do not scrape Health.app. Do not
   claim HealthKit works.
5. Weather is Open-Meteo (not WeatherKit). Maps search is a local
   `maps.apple.com` URL; opening Maps.app is gated. Shortcuts uses the
   `shortcuts` CLI. Ops writes a plist and does not `launchctl load`.

### Step 2 — Reads are free

No Touch ID for reads. Mail, contacts, calendar, messages, notes, drive,
photos catalog, Safari tabs/bookmarks/history, Music now-playing/search,
weather, maps search URLs, shortcuts list, and health status are all reads.

- In chat with the user, use aliases — not raw emails, phone numbers, or
  home paths.
- Do not invent messages, events, contacts, or file listings.
- Photos import/upload is not implemented. Safari extract is allowlisted
  `title_text` only. User JavaScript is refused.

### Step 3 — Writes are two-phase + Touch ID

Create / update / delete / send / open / play / run:

1. `icloud_prepare_*` (freeze the exact identity shown in preview).
2. Show the exact target + preview in chat.
3. Wait for an explicit OK from the user.
4. `icloud_request_local_approval` (Touch ID / macOS password).
5. On timeout or uncertainty: `icloud_action_outcome` first. Never
   re-prepare a duplicate mutate blindly.

CLI mutations stay dry-run unless `--apply`.

Never claim a mutation succeeded unless approval / `icloud_action_outcome`
reports success.

### Step 4 — Do not freelance the machine

- Credentials live in the Keychain (service `icloudseal-mcp`). Cache, plans,
  exports, backups, and compiled helpers live under App Support
  `icloudseal-mcp`. Never copy that tree into the repo.
- Do not start IMAP/DAV, mint Keychain items, compile `native-approval.swift`
  into App Support, or touch live iCloud accounts unless the user asked.
- Do not `launchctl load`. Do not force-push. Do not rewrite git history
  unless the user explicitly asked.
- Public docs and fixtures use placeholders only.

## What icloudseal is for

Local, sealed iCloud for the same Mac user who unlocked the session. Agents
read, classify, and draft; the human seals every externally visible action.
