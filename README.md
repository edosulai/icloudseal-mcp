# icloud-agent

CRUD layer for **iCloud services** — driven by an AI agent. The agent does the
thinking (classify, summarize, decide); this tool does the hands (fetch, list,
create, move, delete). Started as `icloud-mail-agent` (mail only); now a
multi-domain agent.

> **Status**
> - **Mail (IMAP)** — stable: sync/list/triage plus gated cleanup & job leads.
> - **Contacts (CardDAV)** — new: live list/search/export plus gated create/update/delete.
> - **Calendar/Reminders, Messages/SMS, Notes, Drive** — planned (see Roadmap).
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
        ┌──────────────────────────────────────┐
        │  icloud_agent/                        │
        │   auth.py     macOS Keychain creds    │
        │   paths.py    shared storage paths     │
        │   common.py   shared CLI helpers       │
        │   cli.py      icloud-agent / mail-agent│
        │   mail/       IMAP (imap_client+cache) │
        │   contacts/   CardDAV (carddav)        │
        │   dav/        shared CardDAV/CalDAV     │
        └───────┬───────────────────┬────────────┘
        IMAP/SSL│                   │ CardDAV/HTTPS
                ▼                   ▼
      imap.mail.me.com:993   contacts.icloud.com
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

## Safety model

- **Dry-run by default.** Destructive ops require explicit `--apply`.
- **Backup before mutation.** Mail → `.eml`; contacts → `.vcf` under `backups/`.
- **ETag-guarded writes.** Contact updates/deletes use `If-Match` so concurrent
  edits don't get clobbered; creates use `If-None-Match: *`.
- **One credential, least surprise.** Same Keychain item as mail.

## Data location

- Credentials: macOS Keychain, service `icloud-mail-agent`
- Cache DB: `~/Library/Application Support/icloud-mail-agent/cache.db`
- Backups: `~/Library/Application Support/icloud-mail-agent/backups/`
- Plans: `~/Library/Application Support/icloud-mail-agent/plans/`

## Roadmap (planned domains)

Access mechanics differ per domain — three tiers:

| Domain | Mechanism | Credential | Notes |
|---|---|---|---|
| **Calendar + Reminders** | CalDAV (`caldav.icloud.com`) | same app-specific password | Reuses `dav/` client; next up. |
| **Messages / SMS** | local `~/Library/Messages/chat.db` (read) + AppleScript (send) | **Full Disk Access** (TCC) | Mac-local only; sending is fragile. |
| **Notes** | AppleScript / local SQLite | Full Disk Access | CRUD via AppleScript. |
| **iCloud Drive** | filesystem `~/Library/Mobile Documents/com~apple~CloudDocs/` | — | Plain file ops. |

CalDAV is the cleanest next step because it reuses the proven `dav/` discovery
the Contacts domain validated.

## Why not a unified iCloud API?

Apple exposes **no** public iCloud REST API. Mail = IMAP, Contacts/Calendar =
CardDAV/CalDAV (all app-specific-password auth). Messages/Notes have no remote
API at all — only on-device data stores. This tool meets each domain on its own
supported interface rather than relying on a fragile reverse-engineered client.
