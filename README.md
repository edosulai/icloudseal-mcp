# icloud-agent

CRUD layer for **iCloud services** — driven by an AI agent. The agent does the
thinking (classify, summarize, decide); this tool does the hands (fetch, list,
create, move, delete). Started as `icloud-mail-agent` (mail only); now a
multi-domain agent.

> **Status — seven domains live**
> - **Mail (IMAP)** — sync/list/triage plus gated cleanup & job leads.
> - **Contacts (CardDAV)** — list/search/export plus gated create/update/delete.
> - **Calendar + Reminders (CalDAV)** — list calendars/events/reminders plus gated add/rm/done.
> - **Messages / SMS** — read `chat.db` (chats/list/search/export) plus gated AppleScript send.
> - **Notes (AppleScript)** — list/search/read plus gated create/delete.
> - **iCloud Drive (filesystem)** — ls/tree/find/read plus gated put/rm (rm → Trash).
> - **Photos** — read-only stats/albums/list (reads `Photos.sqlite`) plus best-effort export.
>
> Mutating commands are dry-run by default and require `--apply`.

## Two CLIs, one command set

| Command | Scope |
|---|---|
| `icloud-agent <domain> <action>` | Multi-domain entry point (`mail`, `contacts`, …) |
| `mail-agent <action>` | Legacy alias = `icloud-agent mail <action>` (kept for back-compat) |

So `mail-agent list` and `icloud-agent mail list` are identical.

## Architecture

```
            ┌──────────────────────────────┐
            │  AI agent (chat session)     │
            │  reads → proposes → asks      │
            └──────────────┬───────────────┘
                           │ shell
                           ▼
        ┌──────────────────────────────────────────┐
        │  icloud_agent/                            │
        │   auth/paths/common   shared infra        │
        │   cli.py     icloud-agent / mail-agent    │
        │   dav/       shared CardDAV/CalDAV client  │
        │   mail/      IMAP        contacts/ CardDAV │
        │   calendar/  CalDAV      messages/ chat.db │
        │   notes/     AppleScript drive/    fs      │
        └──┬────────┬────────┬────────┬────────┬─────┘
       IMAP│   DAV  │  CalDAV│  SQLite│ Apple- │ fs
           ▼        ▼        ▼   +AS  ▼ Script ▼
   imap.mail   contacts. caldav.  chat.db  Notes.app /
   .me.com     icloud    icloud   (FDA)    CloudDocs
```

**One credential for everything.** The same iCloud app-specific password in the
Keychain (service `icloud-mail-agent`) authenticates IMAP **and** CardDAV/CalDAV.
**No external LLM API** — data stays on this Mac except what enters the chat.

## Why the storage name stays `icloud-mail-agent`

The project was renamed `icloud-mail-agent` → `icloud-agent`, but the on-disk
directory `~/Library/Application Support/icloud-mail-agent/` and the Keychain
service name `icloud-mail-agent` are intentionally **unchanged**, so the existing
app-specific password, metadata cache, and historical backups keep working.

## Setup (one-time)

### 1. App-specific password
iCloud blocks regular passwords for IMAP/CardDAV. Generate one at
<https://appleid.apple.com> → **Sign-In and Security** → **App-Specific
Passwords** (format `xxxx-xxxx-xxxx-xxxx`).

### 2. Install
```bash
cd /Users/Shared/Development/tools/icloud-agent
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 3. Store credentials
```bash
icloud-agent mail setup --email you@icloud.com   # or: mail-agent setup --email ...
```

### 4. Verify
```bash
icloud-agent mail stats          # lists mail folders + counts (IMAP)
icloud-agent contacts list --limit 5   # lists contacts (CardDAV)
```

## Mail commands (`icloud-agent mail …` / `mail-agent …`)

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

## Contacts commands (`icloud-agent contacts …`)

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

## Calendar + Reminders commands (`icloud-agent calendar …`)

| Command | Action |
|---|---|
| `calendars [--json]` | List calendars and reminder lists |
| `events [--days 30] [--json]` | Upcoming events |
| `reminders [--all] [--json]` | Reminders (open by default) |
| `event-add --title --start [--end --location --calendar --all-day] [--apply]` | Create event |
| `event-rm <query> [--apply]` | Delete event (`.ics` backed up) |
| `reminder-add --title [--due --list] [--apply]` | Create reminder |
| `reminder-done <query> [--apply]` | Mark reminder complete |
| `reminder-rm <query> [--apply]` | Delete reminder (`.ics` backed up) |

Dates: `YYYY-MM-DD` (all-day) or `YYYY-MM-DD HH:MM` (timed).

## Messages commands (`icloud-agent messages …`)

Reads need **Full Disk Access** on the running terminal/app.

| Command | Action |
|---|---|
| `chats [--limit 30]` | Recent conversations |
| `list <chat> [--limit 40]` | Messages in a conversation (id or name fragment) |
| `search <query> [--limit]` | Search message text |
| `export <chat> <file.json>` | Export a conversation |
| `send --to <handle> --text "…" [--service imessage\|sms] [--apply]` | Send via Messages.app |

Sending is driven through Messages.app via AppleScript; SMS only works with iPhone Text Message Forwarding enabled.

## Notes commands (`icloud-agent notes …`)

| Command | Action |
|---|---|
| `list [--limit] [--json]` / `search <q>` / `read <q>` | Browse notes |
| `create --title … [--body …] [--apply]` | Create a note |
| `delete <q> [--apply]` | Delete a note (body backed up to `backups/`) |

## iCloud Drive commands (`icloud-agent drive …`)

Paths are relative to the iCloud Drive root.

| Command | Action |
|---|---|
| `ls [path]` / `tree [path] [--depth]` / `find <glob> [path]` | Browse |
| `read <path>` | Print a text file (truncated at 200 KB) |
| `put <local> <dest> [--apply]` | Copy a local file into iCloud Drive |
| `rm <path> [--apply]` | Move an item to the macOS Trash (never permanent) |

## Photos commands (`icloud-agent photos …`)

Reads need **Full Disk Access**. With iCloud "Optimize Mac Storage", most
originals are in iCloud and not on disk — `export` copies only those already
downloaded and reports the rest.

| Command | Action |
|---|---|
| `stats [--json]` | Totals: photos / videos / favorites / albums |
| `albums [--json]` | Albums with item counts |
| `list [--album … --kind photo\|video --favorites --limit] [--json]` | List assets (newest first) |
| `export <dir> [--album --kind --favorites] [--apply]` | Copy locally-downloaded originals out |

## Safety model

- **Dry-run by default.** Destructive ops require explicit `--apply`.
- **Backup before mutation.** Mail → `.eml`, contacts → `.vcf`, calendar → `.ics`,
  notes → `.txt`, all under `backups/`. Drive `rm` goes to the macOS Trash.
- **ETag-guarded DAV writes.** Contact/calendar updates & deletes use `If-Match`
  so concurrent edits aren't clobbered; creates use `If-None-Match: *`.
- **Read-only Messages DB.** `chat.db` is opened `mode=ro&immutable=1`; the tool
  never writes to it.
- **Drive sandboxing.** Drive paths are resolved and rejected if they escape the
  iCloud Drive root.
- **One credential, least surprise.** Same Keychain item authenticates IMAP,
  CardDAV, and CalDAV.

## Data location

- Credentials: macOS Keychain, service `icloud-mail-agent`
- Cache DB: `~/Library/Application Support/icloud-mail-agent/cache.db`
- Backups: `~/Library/Application Support/icloud-mail-agent/backups/`
- Plans: `~/Library/Application Support/icloud-mail-agent/plans/`

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
| Photos | local `Photos.sqlite` catalog (read) | **Full Disk Access** |

## Roadmap

- **Photos write** — current Photos support is read + best-effort export only.
  Album edits / imports would need PhotoKit (`osxphotos`) or Photos.app
  automation; deferred because automation blocks on TCC and originals are
  iCloud-offloaded.
- **Scheduled cleanup** — optional LaunchAgent for recurring `mail cleanup strict`.

## Why not a unified iCloud API?

Apple exposes **no** public iCloud REST API. Mail = IMAP, Contacts/Calendar =
CardDAV/CalDAV (all app-specific-password auth). Messages/Notes have no remote
API at all — only on-device data stores. This tool meets each domain on its own
supported interface rather than relying on a fragile reverse-engineered client.
