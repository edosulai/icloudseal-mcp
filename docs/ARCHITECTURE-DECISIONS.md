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

## Security model (target)

| Class | Examples | Gate |
|---|---|---|
| Metadata / health | backend status, folder list counts without bodies | free or low gate |
| Sensitive read | SMS/iMessage bodies, email bodies, full contacts | session or per-request Touch ID (MCP) |
| Externally visible / mutate | send email, send iMessage, create/delete contacts, calendar write, drive rm | prepare preview → explicit approve → **Touch ID / macOS password** |

Current CLI: dry-run plans + `--apply` (no native biometric dialog yet).
MCP wrapper + `native-approval` path is the planned parity with whatseal/instaseal.

## Domains (live CLI)

1. Mail — IMAP + local SQLite cache
2. Contacts — CardDAV
3. Calendar + Reminders — CalDAV
4. Messages / SMS — `~/Library/Messages/chat.db` (Full Disk Access)
5. Notes — AppleScript
6. iCloud Drive — filesystem under CloudDocs
7. Photos — `Photos.sqlite` read + best-effort export

**Not supported:** WhatsApp (use `whatseal-mcp`). Instagram (use `instaseal-mcp`).

## Credential / storage identity (intentionally not renamed)

- Keychain service: `icloud-mail-agent`
- App Support: `~/Library/Application Support/icloud-mail-agent/`
- Reason: preserve existing app-specific password, cache DB, plans, backups across project renames.

## Related paths

- Repo: `/Users/Shared/Development/tools/icloudseal-mcp`
- GitHub: `https://github.com/edosulai/icloudseal-mcp` (private, seal family)
- Catalog key (MCP): `icloudseal` in `dotfiles/home/.agents/governance/mcp-configs/mcp-servers.json`
- Sibling tools: `whatseal-mcp`, `instaseal-mcp`

## Capability notes for agents

- SMS OTP reading: `icloudseal-mcp messages search "<query>" --limit N --json`
- Requires macOS Messages database access (FDA for Terminal/IDE).
- Freelancer / web OTP that only arrives on **WhatsApp** cannot be read here — use `whatseal-mcp`.
