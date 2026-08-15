# icloudseal-mcp

**Sealed iCloud MCP** — local CLI + stdio MCP for AI agents. Part of the seal
family with `whatseal-mcp` (WhatsApp) and `instaseal-mcp` (Instagram).

The agent does the thinking (classify, summarize, decide); this tool does the
hands (fetch, list, create, move, delete). Mutations require Touch ID / macOS
password via the same two-phase prepare → native approval pattern as the other
seal tools. Started as `icloud-mail-agent` → `icloud-agent` → **`icloudseal-mcp`**.

> **Status — thirteen domains live (CLI + MCP)**
> - **Mail (IMAP + SMTP)** — sync/list/triage plus gated cleanup, send (reply/attach), flags, move, trash, and create-folder.
> - **Contacts (CardDAV)** — list/search/export plus gated create/update/delete.
> - **Calendar + Reminders (CalDAV)** — list plus gated add/update/rm/done with ATTENDEE/TZID.
> - **Messages / SMS** — read `chat.db` plus gated AppleScript send (optional file attach).
> - **Notes (AppleScript)** — list/search/read/accounts/folders plus gated create/update/delete.
> - **iCloud Drive (filesystem)** — ls/tree/find/read plus gated mkdir/put/rm (rm → Trash).
> - **Photos** — stats/albums/list plus gated export, favorite, and album-add. Import is not implemented.
> - **Safari (AppleScript)** — tabs/current/page-text plus gated http(s) open, search, and close-tab.
> - **Music (AppleScript)** — now-playing plus gated playpause/next/previous, volume, shuffle, repeat, play-by-name.
> - **Weather (Open-Meteo)** — current plus daily forecast; hourly is opt-in.
> - **Maps (maps.apple.com)** — local search/directions URL plus gated open (optional zoom/type).
> - **Health** — status only. Fail-closed until a signed HealthKit helper exists. Does not scrape Health.app.
> - **Ops** — write a mail-cleanup LaunchAgent plist. Does not `launchctl load`.
>
> CLI: mutating commands are dry-run by default and require `--apply`.
> MCP: mutating tools are `icloud_prepare_*` then `icloud_request_local_approval` (~83 tools).

## Seal family & security model

| Project | Domain | Approval |
|---|---|---|
| `whatseal-mcp` | WhatsApp | Touch ID for every externally visible action |
| `instaseal-mcp` | Instagram | Touch ID for every externally visible action |
| **`icloudseal-mcp`** | iCloud + Safari/Music/Weather/Maps | **CLI `--apply`; MCP prepare → native Touch ID** |

Principles:
- Data stays on this Mac except what enters the active agent/model context.
- Mutating CLI commands are dry-run by default and require `--apply`.
- MCP mutations use prepare → show exact preview → user OK in chat → native
  macOS authentication (`native-approval.swift`).
- MCP approval payloads contain immutable resource identities/snapshots rather
  than mutable search queries or arbitrary plan paths.
- MCP plans and exports are jailed to owner-only App Support directories; an
  existing output is never overwritten implicitly.
- App Support dir + Keychain service: `icloudseal-mcp` (migrated from legacy `icloud-mail-agent`).

## Two CLIs, one command set

| Command | Scope |
|---|---|
| `icloudseal-mcp <domain> <action>` | Multi-domain entry point (`mail`, `contacts`, …) |
| `mail-agent <action>` | Legacy alias = `icloudseal-mcp mail <action>` (kept for back-compat) |

So `mail-agent list` and `icloudseal-mcp mail list` are identical.
MCP setup (VS Code / Copilot / Claude Desktop)

Catalog key: `icloudseal` (dotfiles reconcile). Point the IDE at `mcp-wrapper.sh`:

```json
{
  "servers": {
    "icloudseal": {
      "type": "stdio",
      "command": "/absolute/path/to/icloudseal-mcp/mcp-wrapper.sh"
    }
  }
}
```

The wrapper self-bootstraps `.venv` + editable install if needed, then runs
`python -m icloudseal_mcp.mcp.server` over stdio.

### Agent workflow

1. First call: `icloud_doctor` or `icloud_status`
2. Reads: no Touch ID (`icloud_mail_*`, `icloud_contacts_*`, `icloud_messages_*`, …)
  Local export/plan tools write only to managed App Support directories.
3. Mutations: `icloud_prepare_*` → show exact preview → user OK → `icloud_request_local_approval`
4. After timeout: `icloud_action_outcome` (never blind re-prepare)

### MCP tool groups

| Group | Tools |
|---|---|
| Onboarding | `icloud_doctor`, `icloud_status`, `icloud_security_audit`, `icloud_list_domains` |
| Mail | stats/sync/list/senders/peek/triage/jobs/attachments + prepare apply/cleanup/send/flags/move/trash/create-folder |
| Contacts | list/search/export + prepare create/update/delete |
| Calendar | list/events/reminders + prepare event add/update/rm and reminder mutations |
| Messages | chats/list/search/export + prepare send (optional attach) |
| Notes | list/search/read/accounts/folders + prepare create/update/delete |
| Drive | ls/tree/find/read + prepare mkdir/put/rm |
| Photos | stats/albums/list + prepare export/favorite/album-add |
| Safari | tabs/current/page-text + prepare open-url/search/close-tab |
| Music | now-playing + prepare playpause/next/previous/volume/shuffle/repeat/play |
| Weather | forecast (Open-Meteo current+daily; hourly opt-in) |
| Maps | search URL + prepare open (optional zoom/type) |
| Health | status only (fail-closed) |
| Ops | prepare cleanup-agent (write plist only) |
| Gate | `icloud_request_local_approval`, `icloud_action_outcome` |

## Architecture

```
            ┌──────────────────────────────┐
            │  AI agent (chat session)     │
            │  reads → prepare → approve   │
            └───────┬──────────────┬───────┘
                    │ MCP stdio    │ shell CLI
                    ▼              ▼
        ┌──────────────────────────────────────────┐
        │  icloudseal_mcp/                         │
        │   mcp/       server + approval + services│
        │   auth/paths/common   shared infra       │
        │   cli.py     icloudseal-mcp / mail-agent │
        │   dav/       shared CardDAV/CalDAV       │
        │   mail/ IMAP  contacts/ CardDAV          │
        │   calendar/ CalDAV  messages/ chat.db    │
        │   notes/ AppleScript  drive/ fs  photos/ │
        │   safari/ music/ weather/ maps/          │
        │   health/ ops/                           │
        │   native-approval.swift (Touch ID gate)  │
                   └──────────────────────────────────────────┘
                             IMAP/SMTP/DAV · local DB · AppleScript · Open-Meteo
```

**One credential for everything.** The same iCloud app-specific password in the
Keychain (service `icloudseal-mcp`) authenticates IMAP, SMTP, **and** CardDAV/CalDAV.
**No external LLM API** — data stays on this Mac except what enters the chat.


## Setup (one-time)

### 1. App-specific password
iCloud blocks regular passwords for IMAP/CardDAV. Generate one at
<https://appleid.apple.com> → **Sign-In and Security** → **App-Specific
Passwords** (format `xxxx-xxxx-xxxx-xxxx`).

### 2. Install
```bash
cd /path/to/icloudseal-mcp
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 3. Store credentials
```bash
icloudseal-mcp mail setup --email you@icloud.com   # or: mail-agent setup --email ...
```

### 4. Verify
```bash
icloudseal-mcp mail stats          # lists mail folders + counts (IMAP)
icloudseal-mcp contacts list --limit 5   # lists contacts (CardDAV)
```

Contact-name cleanup is optional. Copy
[`scripts/contact_name_rules.example.json`](./scripts/contact_name_rules.example.json)
to `scripts/contact_name_rules.json` and add local skip names / tags there.
The live file is gitignored and must not be committed.

## Mail commands (`icloudseal-mcp mail …` / `mail-agent …`)

| Command | Action |
|---|---|
| `setup --email <addr>` | Store credentials in Keychain (one-time) |
| `stats` | Folder list + message counts |
| `sync [--folder INBOX] [--since 7d]` | Pull metadata into local SQLite cache |
| `list [--folder INBOX] [--limit 50]` | List cached messages |
| `peek <uid>` | Show body of one message |
| `senders [--top 30]` | Top senders by count |
| `triage … --move-to <f> / --delete --plan-file p.json` | Build a dry-run plan |
| `apply p.json [--apply]` | Backup `.eml`, then move/delete via IMAP |
| `cleanup strict [--apply]` | Full-sync INBOX, plan/delete known bulk senders |
| `jobs collect --since 7d --out p.json` | Extract & score job leads (review-only) |
| `send --to --subject --body [--cc --bcc --in-reply-to --references --attach] [--apply]` | Send via iCloud SMTP (From = Keychain email) |
| `mark-read --uids 1,2 [--folder INBOX] [--apply]` | Mark cached messages read (`\\Seen`) |
| `mark-unread --uids 1,2 [--folder INBOX] [--apply]` | Mark cached messages unread |
| `flags --uids 1,2 --flag +Flagged\|-Flagged\|+Answered\|-Answered [--folder INBOX] [--apply]` | Add or remove extra IMAP flags |
| `move --uids 1,2 --to Archive [--folder INBOX] [--apply]` | Move cached messages to another folder |
| `trash --uids 1,2 [--folder INBOX] [--apply]` | Move cached messages to Deleted Messages |
| `create-folder --name Archive/2026 [--apply]` | Create an IMAP folder |
| `attachments <uid> [--folder INBOX]` | List inbound attachments |
| `export-attachment <uid> --name <file> --dest <exports/…> [--folder INBOX] [--apply]` | Export one attachment into the exports jail |

## Contacts commands (`icloudseal-mcp contacts …`)

| Command | Action |
|---|---|
| `list [--limit N] [--json]` | List all contacts (live CardDAV) |
| `search <query> [--json]` | Match by name / email / phone / org |
| `export <file.json\|file.vcf>` | Export all contacts |
| `create --name … [--email … --phone … --org …] [--apply]` | Create a contact |
| `update <uid\|query> [--name --add-email --set-phone …] [--apply]` | Update a contact |
| `delete <query> [--apply]` | Delete matching contacts (vCards backed up first) |

Write commands print a dry-run preview and do nothing until `--apply` is added.
`update`/`delete` back up the affected vCards to `backups/` first.

## Calendar + Reminders commands (`icloudseal-mcp calendar …`)

| Command | Action |
|---|---|
| `calendars [--json]` | List calendars and reminder lists |
| `events [--days 30] [--json]` | Upcoming events |
| `reminders [--all] [--json]` | Reminders (open by default) |
| `event-add --title --start [--end --location --calendar --all-day --timezone --attendees] [--apply]` | Create event |
| `event-update <query> [--title --start --end --location --all-day --timezone --attendees] [--apply]` | Patch event (RRULE/VALARM kept; `.ics` backed up) |
| `event-rm <query> [--apply]` | Delete event (`.ics` backed up) |
| `reminder-add --title [--due --list] [--apply]` | Create reminder |
| `reminder-update <query> [--title --due] [--apply]` | Patch reminder (RRULE/VALARM kept; empty `--due` clears it) |
| `reminder-done <query> [--apply]` | Mark reminder complete |
| `reminder-rm <query> [--apply]` | Delete reminder (`.ics` backed up) |

Dates: `YYYY-MM-DD` (all-day) or `YYYY-MM-DD HH:MM` (timed).

## Messages commands (`icloudseal-mcp messages …`)

Reads need **Full Disk Access** on the running terminal/app.

| Command | Action |
|---|---|
| `chats [--limit 30]` | Recent conversations |
| `list <chat> [--limit 40]` | Messages in a conversation (id or name fragment) |
| `search <query> [--limit]` | Search message text |
| `export <chat> <file.json>` | Export a conversation |
| `send --to <handle> --text "…" [--service imessage\|sms --file <path>] [--apply]` | Send via Messages.app |

Sending is driven through Messages.app via AppleScript; SMS only works with iPhone Text Message Forwarding enabled.

## Notes commands (`icloudseal-mcp notes …`)

| Command | Action |
|---|---|
| `list [--limit] [--json]` / `search <q>` / `read <q>` | Browse notes |
| `accounts [--json]` / `folders [--json]` | List Notes accounts and folders |
| `create --title … [--body … --folder …] [--apply]` | Create a note (still iCloud-only) |
| `update <q> [--title --body] [--apply]` | Update a note (body backed up first) |
| `delete <q> [--apply]` | Delete a note (body backed up to `backups/`) |

## iCloud Drive commands (`icloudseal-mcp drive …`)

Paths are relative to the iCloud Drive root.

| Command | Action |
|---|---|
| `ls [path]` / `tree [path] [--depth]` / `find <glob> [path]` | Browse |
| `read <path>` | Print a text file (truncated at 200 KB) |
| `mkdir <path> [--apply]` | Create a folder (refuses Drive root; dest must not exist) |
| `put <local> <dest> [--apply]` | Copy a local file into iCloud Drive |
| `rm <path> [--apply]` | Move an item to the macOS Trash (never permanent) |

## Photos commands (`icloudseal-mcp photos …`)

Reads need **Full Disk Access**. With iCloud "Optimize Mac Storage", most
originals are in iCloud and not on disk — `export` copies only those already
downloaded and reports the rest.

| Command | Action |
|---|---|
| `stats [--json]` | Totals: photos / videos / favorites / albums |
| `albums [--json]` | Albums with item counts |
| `list [--album … --kind photo\|video --favorites --limit] [--json]` | List assets (newest first) |
| `export <dir> [--album --kind --favorites] [--apply]` | Copy locally-downloaded originals out |
| `favorite --filename <name> [--unfavorite] [--apply]` | Favorite or unfavorite by filename |
| `album-add --filename <name> --album <name> [--apply]` | Add an existing photo to an album. Import is not implemented. |

## Safari commands (`icloudseal-mcp safari …`)

Reads do **not** launch Safari. Opening a URL needs Automation access and `--apply`.
Only `http` / `https` URLs are accepted — no implicit `https://` prefix, no
`javascript:` / `file:` / `data:`.

| Command | Action |
|---|---|
| `tabs [--json]` | List open tabs (empty if Safari is not running) |
| `current [--json]` | Show the current tab (name + URL) |
| `source [--window --tab] [--json]` | Size-capped page text of the selected tab |
| `search --query <text> [--window] [--apply]` | Open a Google search URL in Safari |
| `open --url <http(s)> [--window] [--apply]` | Open the frozen URL in a new tab or window |
| `close --window N --tab N [--apply]` | Close a frozen tab after snapshotting name + URL |

## Music commands (`icloudseal-mcp music …`)

Reads do **not** launch Music.app. Playback is dry-run unless `--apply`.
AirPlay is not exposed.

| Command | Action |
|---|---|
| `now [--json]` | Now-playing (state/name/artist/album); `stopped` if Music is not running |
| `playpause [--apply]` | Toggle play/pause |
| `next [--apply]` | Skip to the next track |
| `previous [--apply]` | Skip to the previous track |
| `volume --level 0-100 [--apply]` | Set Music volume |
| `shuffle --mode off\|songs\|albums\|groupings [--apply]` | Set shuffle mode |
| `repeat --mode off\|one\|all [--apply]` | Set repeat mode |
| `play --query <name> [--apply]` | Search the library and play the first match |

## Weather commands (`icloudseal-mcp weather …`)

Public Open-Meteo forecast. Does **not** open Weather.app and does **not**
use device location. Provide either `--place` or both `--lat` and `--lon`.
Results include the required Open-Meteo attribution.

| Command | Action |
|---|---|
| `forecast --place <name> [--days 1-7] [--unit celsius\|fahrenheit --hourly] [--json]` | Geocode then forecast |
| `forecast --lat <lat> --lon <lon> [--days] [--unit --hourly] [--json]` | Forecast for coordinates |

## Maps commands (`icloudseal-mcp maps …`)

Search builds a documented `https://maps.apple.com/?…` URL locally and does
**not** open Maps.app. Opening is dry-run unless `--apply`. `maps:` /
`javascript:` / `file:` / `data:` are rejected.

| Command | Action |
|---|---|
| `search --query <text> [--lat --lon --zoom --type] [--json]` | Build a search URL (optional `ll` pin, `z`, `t`) |
| `open --query <text> [--lat --lon --zoom --type] [--apply]` | Open the frozen search URL in Maps.app |
| `open --daddr <dest> [--saddr] [--dirflg d\|w\|r --zoom --type] [--apply]` | Open frozen directions |

## Safety model

- **Dry-run by default.** Destructive ops require explicit `--apply`.
- **Two independent MCP confirmations.** A mutation requires explicit chat OK
  after prepare, then native Touch ID/macOS-password authorization.
- **Immutable execution targets.** Mail plans freeze UIDs, message metadata,
  `UIDVALIDITY`, and a canonical SHA-256. CardDAV/CalDAV targets freeze exact
  href + ETag + raw document. Notes freeze ID, modified date, body, and hash.
  Drive/Photos freeze exact local paths and content hashes.
- **Backup before DAV/Notes mutation.** Contacts → `.vcf`, calendar → `.ics`,
  notes → `.txt`, all under owner-only `backups/`. Mail delete means a move to
  `Deleted Messages` (no local `.eml` backup). Drive `rm` goes to macOS Trash.
- **ETag-guarded DAV writes.** Contact/calendar updates & deletes use `If-Match`
  so concurrent edits aren't clobbered; creates use `If-None-Match: *`.
- **Property-preserving updates.** Contact/event/reminder updates retain unknown
  vCard/iCalendar properties, recurrence rules, alarms, and attachments.
- **Read-only Messages DB.** `chat.db` is opened `mode=ro&immutable=1`; the tool
  never writes to it.
- **Filesystem isolation.** Drive paths cannot escape CloudDocs. MCP plan output
  is jailed to `plans/`; Contacts/Messages/Mail/Photos exports are jailed to
  `exports/`. Drive overwrite requires `overwrite=true` and an unchanged
  approved destination.
- **No AppleScript interpolation.** Notes, Messages, Finder, and Safari receive
  dynamic values through AppleScript `argv`, never executable script source.
  Music playback scripts are constant (no user values at all).
- **Safari URL allowlist.** Open-URL and search freeze the exact `http`/`https`
  string shown in preview. Missing schemes and `javascript:` / `file:` / `data:`
  fail closed. Page text is size-capped and read-only. `do JavaScript` is not
  exposed.
- **Weather hosts are pinned.** Forecast/geocode only call
  `api.open-meteo.com` and `geocoding-api.open-meteo.com` over HTTPS. User
  input is a place name or coordinates, never a URL. Redirects are refused.
- **Maps URL allowlist.** Search is local `urlencode` only. Open freezes
  `https://maps.apple.com/?…` and launches it with `/usr/bin/open`. `maps:`
  and undocumented guide schemes are not exposed.
- **Private local state.** State/backup/export directories use mode `0700` and
  sensitive files use `0600`; approval outcomes are atomically replaced.
- **One credential, least surprise.** Same Keychain item authenticates IMAP,
  SMTP, CardDAV, and CalDAV.

### Managed MCP output paths

- `icloud_mail_triage.plan_file`: filename or path under `plans/` only.
- `icloud_prepare_mail_apply.plan_file`: existing JSON under `plans/` only.
- Contacts, Messages, and Mail-jobs export paths: files under `exports/` only.
- `icloud_prepare_photos_export.dest`: a new directory under `exports/` only.
- Existing plans/exports are rejected rather than overwritten.
- `icloud_prepare_drive_put` accepts `overwrite=false` by default; set it to
  `true` only when the exact existing destination shown in preview may be replaced.

## Data location

- Credentials: macOS Keychain, service `icloudseal-mcp`
- Cache DB: `~/Library/Application Support/icloudseal-mcp/cache.db`
- Backups: `~/Library/Application Support/icloudseal-mcp/backups/`
- Plans: `~/Library/Application Support/icloudseal-mcp/plans/`
- Exports: `~/Library/Application Support/icloudseal-mcp/exports/`
- Approval outcomes: `~/Library/Application Support/icloudseal-mcp/approvals/outcomes/`

## Access mechanics per domain

Three tiers, by how macOS exposes each service:

| Domain | Mechanism | Credential / permission |
|---|---|---|
| Mail | IMAP `imap.mail.me.com` | app-specific password |
| Contacts | CardDAV `contacts.icloud.com` | app-specific password |
| Calendar + Reminders | CalDAV `caldav.icloud.com` | app-specific password |
| Messages / SMS | local `~/Library/Messages/chat.db` (read) + AppleScript (send) | **Full Disk Access** + Automation |
| Notes | AppleScript (Notes.app) | Automation |
| iCloud Drive | filesystem `~/Library/Mobile Documents/com~apple~CloudDocs/` | — |
| Photos | local `Photos.sqlite` catalog (read) + AppleScript (favorite/album) | **Full Disk Access** + Automation |
| Safari | AppleScript (Safari) | Automation |
| Music | AppleScript (Music.app) | Automation |
| Weather | Open-Meteo HTTPS | network |
| Maps | `https://maps.apple.com` URL | `/usr/bin/open` (gated) |
| Health | signed helper stub | fail-closed until entitled |
| Ops | LaunchAgent plist write | local filesystem |

## Health commands (`icloudseal-mcp health …`)

Status only. This is not a working HealthKit reader.

| Command | Action |
|---|---|
| `status [--json]` | Report that Health is unavailable until a signed helper exists |

## Ops commands (`icloudseal-mcp ops …`)

Writes a LaunchAgent plist. Does **not** `launchctl load`.

| Command | Action |
|---|---|
| `cleanup-agent [--interval 86400] [--apply]` | Write `dev.icloudseal.mail-cleanup.plist` under App Support |

## Roadmap

- **Photos import** — favorite / album-add ship via filename AppleScript.
  Upload/import still needs PhotoKit or Photos.app and stays unimplemented.
- **Health reads** — helper + MCP status ship fail-closed. A working read
  needs a *separate* signed native helper with HealthKit entitlements. Will
  not scrape Health.app.
- **Scheduled cleanup load** — plist generation ships; loading/enabling the
  LaunchAgent stays a manual `launchctl` step.

## Why not a unified iCloud API?

Apple exposes **no** public iCloud REST API. Mail = IMAP, Contacts/Calendar =
CardDAV/CalDAV (all app-specific-password auth). Messages/Notes have no remote
API at all — only on-device data stores. This tool meets each domain on its own
supported interface rather than relying on a fragile reverse-engineered client.
