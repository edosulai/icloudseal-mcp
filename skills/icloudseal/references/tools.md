# icloudseal tools

Prefer MCP tools when the host has the `icloudseal` server attached. CLI
commands run from the icloudseal-mcp checkout:

`icloudseal-mcp <domain> <action>`

Legacy mail-only alias: `mail-agent <action>` = `icloudseal-mcp mail <action>`.

Live MCP catalog: 103 tools. Do not invent tools that are not in this list.

## Onboarding / readiness

| MCP | CLI | Notes |
| --- | --- | --- |
| `icloud_doctor` | — | Preferred first call |
| `icloud_status` | — | Domain readiness |
| `icloud_security_audit` | — | Local hygiene check |
| `icloud_list_domains` | — | Fourteen domains |

## Mail

| MCP | CLI |
| --- | --- |
| `icloud_mail_stats` | `mail stats` |
| `icloud_mail_sync` | `mail sync` |
| `icloud_mail_list` | `mail list` |
| `icloud_mail_senders` | `mail senders` |
| `icloud_mail_peek` | `mail peek` |
| `icloud_mail_attachments` | `mail attachments` |
| `icloud_mail_export_attachment` | `mail export-attachment` |
| `icloud_mail_triage` | `mail triage` |
| `icloud_mail_jobs_collect` | `mail jobs` / `mail collect` |
| `icloud_prepare_mail_apply` | `mail apply --apply` |
| `icloud_prepare_mail_cleanup_strict` | `mail cleanup strict --apply` |
| `icloud_prepare_mail_send` | `mail send --apply` |
| `icloud_prepare_mail_forward` | `mail forward --apply` |
| `icloud_prepare_mail_flags` | `mail flags` / `mark-read` / `mark-unread --apply` |
| `icloud_prepare_mail_move` | `mail move --apply` |
| `icloud_prepare_mail_trash` | `mail trash --apply` |
| `icloud_prepare_mail_create_folder` | `mail create-folder --apply` |

CLI also has `mail setup` (Keychain). Do not run setup unless the user asked.

## Contacts

| MCP | CLI |
| --- | --- |
| `icloud_contacts_list` | `contacts list` |
| `icloud_contacts_search` | `contacts search` |
| `icloud_contacts_export` | `contacts export` |
| `icloud_prepare_contacts_create` | `contacts create --apply` |
| `icloud_prepare_contacts_update` | `contacts update --apply` |
| `icloud_prepare_contacts_delete` | `contacts delete --apply` |

## Calendar + Reminders

| MCP | CLI |
| --- | --- |
| `icloud_calendar_list` | `calendar calendars` |
| `icloud_calendar_events` | `calendar events` |
| `icloud_calendar_reminders` | `calendar reminders` |
| `icloud_calendar_timezones` | `calendar timezones` |
| `icloud_prepare_event_add` | `calendar event-add --apply` |
| `icloud_prepare_event_update` | `calendar event-update --apply` |
| `icloud_prepare_event_rm` | `calendar event-rm --apply` |
| `icloud_prepare_reminder_add` | `calendar reminder-add --apply` |
| `icloud_prepare_reminder_update` | `calendar reminder-update --apply` |
| `icloud_prepare_reminder_done` | `calendar reminder-done --apply` |
| `icloud_prepare_reminder_rm` | `calendar reminder-rm --apply` |

Event create/update may include ATTENDEE PARTSTAT, TZID, RRULE, VALARM.
Reminder create/update may include PRIORITY 1-9.

## Messages

| MCP | CLI |
| --- | --- |
| `icloud_messages_chats` | `messages chats` |
| `icloud_messages_list` | `messages list` |
| `icloud_messages_search` | `messages search` |
| `icloud_messages_export` | `messages export` |
| `icloud_prepare_messages_send` | `messages send --apply` |

Reads need Full Disk Access. Send is Messages.app AppleScript.

## Notes

| MCP | CLI |
| --- | --- |
| `icloud_notes_list` | `notes list` |
| `icloud_notes_search` | `notes search` |
| `icloud_notes_read` | `notes read` |
| `icloud_notes_accounts` | `notes accounts` |
| `icloud_notes_folders` | `notes folders` |
| `icloud_prepare_notes_create` | `notes create --apply` |
| `icloud_prepare_notes_update` | `notes update --apply` |
| `icloud_prepare_notes_delete` | `notes delete --apply` |

## Drive

| MCP | CLI |
| --- | --- |
| `icloud_drive_ls` | `drive ls` |
| `icloud_drive_tree` | `drive tree` |
| `icloud_drive_find` | `drive find` |
| `icloud_drive_read` | `drive read` |
| `icloud_prepare_drive_mkdir` | `drive mkdir --apply` |
| `icloud_prepare_drive_put` | `drive put --apply` |
| `icloud_prepare_drive_rm` | `drive rm --apply` |
| `icloud_prepare_drive_rename` | `drive rename --apply` |
| `icloud_prepare_drive_move` | `drive move --apply` |
| `icloud_prepare_drive_copy` | `drive copy --apply` |

Paths stay inside CloudDocs. `rm` goes to Trash. Overwrite must be explicit.

## Photos

| MCP | CLI |
| --- | --- |
| `icloud_photos_stats` | `photos stats` |
| `icloud_photos_albums` | `photos albums` |
| `icloud_photos_list` | `photos list` |
| `icloud_prepare_photos_export` | `photos export --apply` |
| `icloud_prepare_photos_favorite` | `photos favorite --apply` |
| `icloud_prepare_photos_album_add` | `photos album-add --apply` |
| `icloud_prepare_photos_album_create` | `photos album-create --apply` |
| `icloud_prepare_photos_album_remove` | `photos album-remove --apply` |
| `icloud_prepare_photos_album_delete` | `photos album-delete --apply` |

Import/upload is not implemented. Album-remove does not delete the asset.

## Safari

| MCP | CLI |
| --- | --- |
| `icloud_safari_list_tabs` | `safari tabs` |
| `icloud_safari_current_tab` | `safari current` |
| `icloud_safari_page_text` | `safari source` |
| `icloud_safari_extract` | `safari extract` |
| `icloud_safari_bookmarks` | `safari bookmarks` |
| `icloud_safari_history` | `safari history` |
| `icloud_prepare_safari_open_url` | `safari open --apply` |
| `icloud_prepare_safari_search` | `safari search --apply` |
| `icloud_prepare_safari_close_tab` | `safari close --apply` |
| `icloud_prepare_safari_bookmark_add` | `safari bookmark-add --apply` |
| `icloud_prepare_safari_bookmark_rm` | `safari bookmark-rm --apply` |
| `icloud_prepare_safari_reading_list_add` | `safari reading-list-add --apply` |
| `icloud_prepare_safari_reading_list_rm` | `safari reading-list-rm --apply` |

Only `http`/`https`. Extract is allowlisted `title_text`. User JS is refused.
History is read-only.

## Music

| MCP | CLI |
| --- | --- |
| `icloud_music_now_playing` | `music now` |
| `icloud_music_search` | `music search` |
| `icloud_music_playlists` | `music playlists` |
| `icloud_prepare_music_playpause` | `music playpause --apply` |
| `icloud_prepare_music_next` | `music next --apply` |
| `icloud_prepare_music_previous` | `music previous --apply` |
| `icloud_prepare_music_volume` | `music volume --apply` |
| `icloud_prepare_music_shuffle` | `music shuffle --apply` |
| `icloud_prepare_music_repeat` | `music repeat --apply` |
| `icloud_prepare_music_play` | `music play --apply` |
| `icloud_prepare_music_playlist_play` | `music playlist-play --apply` |

AirPlay is not exposed. Search and playlist list never play.

## Weather / Maps / Health / Ops / Shortcuts

| MCP | CLI |
| --- | --- |
| `icloud_weather_forecast` | `weather forecast` |
| `icloud_maps_search` | `maps search` |
| `icloud_prepare_maps_open` | `maps open --apply` |
| `icloud_health_status` | `health status` |
| `icloud_prepare_ops_cleanup_agent` | `ops cleanup-agent --apply` |
| `icloud_shortcuts_list` | `shortcuts list` |
| `icloud_prepare_shortcuts_run` | `shortcuts run --apply` |

Weather is Open-Meteo (not WeatherKit / not device location). Maps open freezes
a `https://maps.apple.com/?…` URL. Health is fail-closed. Ops writes a plist
and never `launchctl load`s it. Shortcuts run freezes an exact installed name;
optional text input is a temp `--input-path`.

## Gate

| MCP | CLI |
| --- | --- |
| `icloud_request_local_approval` | — |
| `icloud_action_outcome` | — |

Mutations: prepare → show exact preview → explicit chat OK → Touch ID → on
timeout/uncertainty `icloud_action_outcome`. Never claim success without that.

## Agent skill install (this file)

| CLI | Notes |
| --- | --- |
| `install-skill [--platform P] [--project]` | Copy `skills/icloudseal/` to agent skill dirs |
| `uninstall-skill [--platform P] [--project]` | Remove only the icloudseal skill copy |

Default platform is `copilot` only (`~/.copilot/skills/icloudseal/`).
Pass `--platform all` or a comma list (`claude,codex,…`) explicitly.
`mcp-wrapper.sh` runs `install-skill` so hosts pick up `/icloudseal`
without a manual copy. stdout stays reserved for MCP stdio.
