# icloud-mail-agent

CRUD layer for iCloud Mail over IMAP. Designed to be **driven by an AI agent** (Cascade in Windsurf) — the agent does the thinking (classify, summarize, draft replies), this tool does the hands (fetch, move, delete, append).

> **Status:** Phase 2 — read-only sync/list plus gated triage/apply. Mutating commands are dry-run by default and require `--apply`.

## Architecture

```
┌────────────────────────────────────────────┐
│  Cascade (Windsurf chat)                   │
│  - reads stats / metadata                  │
│  - proposes triage plan                    │
│  - asks user for approval                  │
└──────────────┬─────────────────────────────┘
               │ shell
               ▼
┌────────────────────────────────────────────┐
│  mail-agent CLI                            │
│  - auth.py  (macOS Keychain)               │
│  - imap_client.py  (imaplib wrapper)       │
│  - cache.py  (SQLite metadata cache)       │
│  - cli.py  (subcommands)                   │
└──────────────┬─────────────────────────────┘
               │ IMAP/SSL
               ▼
┌────────────────────────────────────────────┐
│  imap.mail.me.com:993                      │
└────────────────────────────────────────────┘
```

**No external LLM API.** Email content stays on this Mac except for what enters the chat session.

## Setup (one-time)

### 1. Generate app-specific password

iCloud blocks regular passwords for IMAP. You need an app-specific password.

1. Go to <https://appleid.apple.com>
2. Sign in → **Sign-In and Security** → **App-Specific Passwords**
3. Click **+** → label it `mail-agent` → generate
4. Copy the password (format: `xxxx-xxxx-xxxx-xxxx`)

### 2. Install

```bash
cd /Users/Shared/Development/tools/icloud-mail-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 3. Store credentials in macOS Keychain

```bash
mail-agent setup --email you@icloud.com
# Will prompt for the app-specific password (hidden input)
# Stores it securely in macOS Keychain — never written to disk in plaintext.
```

### 4. Verify connection

```bash
mail-agent stats
```

Should print folder list with message counts. If you see them, you're done with Phase 1.

## Commands

| Command | Action |
|---|---|
| `mail-agent setup --email <addr>` | Store credentials in Keychain (one-time) |
| `mail-agent stats` | Show folder list + message counts |
| `mail-agent sync [--folder INBOX] [--since 7d]` | Pull metadata into local SQLite cache |
| `mail-agent list [--folder INBOX] [--limit 50]` | List cached messages (sender, subject, date) |
| `mail-agent peek <uid> [--folder INBOX]` | Show body of a single message |
| `mail-agent senders [--folder INBOX] [--top 30]` | Top senders by count (for triage decisions) |
| `mail-agent triage ... --move-to <folder> --plan-file plan.json` | Build a dry-run move plan from cached metadata |
| `mail-agent triage ... --delete --plan-file plan.json` | Build a dry-run delete plan from cached metadata |
| `mail-agent apply plan.json` | Show what would be applied; does not mutate |
| `mail-agent apply plan.json --apply` | Backup `.eml` files, then move/delete via IMAP |
| `mail-agent cleanup strict [--apply]` | Full-sync INBOX, then plan/delete known bulk promo and job-alert senders |
| `mail-agent jobs collect --since 7d --out plans/jobs.json` | Extract and score job leads from job-alert emails; review-only |

`triage` only reads the local cache. `apply` is the only command that mutates iCloud Mail, and it refuses to do so unless `--apply` is present.

## Phase 2 workflow

Start by syncing metadata:

```bash
mail-agent sync --folder INBOX --since 30d
mail-agent senders --folder INBOX --top 30
```

Create a reviewed plan:

```bash
mail-agent triage \
  --folder INBOX \
  --sender-like newsletter \
  --has-list-unsubscribe \
  --older-than 30d \
  --move-to Trash \
  --plan-file plans/newsletters-to-trash.json
```

Review the generated JSON, then do a final dry-run:

```bash
mail-agent apply plans/newsletters-to-trash.json
```

Apply only after approval:

```bash
mail-agent apply plans/newsletters-to-trash.json --apply
```

## Strict automatic cleanup

For routine bulk cleanup, use the strict workflow. It syncs the whole INBOX,
matches only explicit bulk sender addresses, writes a JSON plan, and stays
dry-run unless `--apply` is present.

```bash
mail-agent cleanup strict
```

Delete the matched bulk mail after review:

```bash
mail-agent cleanup strict --apply
```

The strict policy intentionally excludes account/security alerts, receipts,
GitHub notifications, financial transaction mail, and human LinkedIn messages.

## Job lead workflow

Job-alert handling is intentionally separate from mailbox cleanup. The command below
fetches matching alert bodies, extracts role/company/location/apply URLs, scores the
leads locally, and writes a review plan. It never submits applications.

```bash
mail-agent jobs collect \
  --folder INBOX \
  --since 7d \
  --limit 50 \
  --min-score 35 \
  --out plans/job-leads.json
```

Review the JSON before taking action. Future auto-apply automation should consume this
plan and keep a human approval gate before opening links or submitting forms.

## Data location

- Credentials: macOS Keychain, service `icloud-mail-agent`
- Cache DB: `~/Library/Application Support/icloud-mail-agent/cache.db`
- Backups (Phase 2): `~/Library/Application Support/icloud-mail-agent/backups/`

## Safety model

- **Dry-run by default.** Destructive operations require explicit `--apply`.
- **Backup before mutation.** Every email about to be moved or deleted is first written to `backups/` as `.eml`.
- **Move, don't delete (when possible).** Prefer moving to `Bulk` / `Trash` folders over IMAP `\Deleted` flag.
- **Local git.** Repo is git-init'd locally so config/rule changes are reversible.

## Why not pyicloud / iCloud API?

Apple does not expose a public iCloud Mail API. The only stable interface is IMAP, which is what every mail client (Mail.app, Thunderbird, etc.) uses. App-specific password + IMAP is the supported path.
