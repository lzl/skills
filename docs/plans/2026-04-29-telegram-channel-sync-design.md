# Telegram Channel Sync Skill Design

## Goal

Create a reusable Codex skill that syncs one Telegram channel at a time into
SQLite using Telethon, with media files stored on disk and conservative account
safety defaults.

## Design

The skill packages a Python CLI at
`telegram-channel-sync/scripts/sync_telegram_channel.py`. The CLI has two
commands:

```bash
python scripts/sync_telegram_channel.py doctor --env .env
python scripts/sync_telegram_channel.py sync --env .env
```

`doctor` validates `.env` and dependencies without contacting Telegram. `sync`
loads credentials, starts Telethon, resolves the configured channel, initializes
SQLite, and performs all-time backfill plus incremental continuation.

The first login may prompt for Telegram code and 2FA. Later runs reuse
`TG_SESSION_PATH`, which should be kept private.

## Data Flow

Configuration comes only from `.env`. Required values are `TG_API_ID`,
`TG_API_HASH`, `TG_PHONE`, `TG_CHANNEL`, `TG_DB_PATH`, `TG_MEDIA_DIR`, and
`TG_SESSION_PATH`. Missing or invalid configuration prints a checklist and
explains that API credentials come from `https://my.telegram.org` under
"API Development tools".

SQLite stores channel identity, included messages, media metadata,
transcription status, and sync checkpoints. Media bytes are written to
`TG_MEDIA_DIR/<channel_id>/<message_id>/...` and referenced by path.

## Sync Rules

The script includes text/captions, photos, videos, voice messages, audio,
ordinary documents, and webpage-preview records. It excludes stickers, custom
emoji/sticker documents, dice-like events, service messages, and empty updates.

Backfill uses `iter_messages(limit=None, wait_time=...)`. Reruns use SQLite
checkpoints: `newest_synced_id` for incremental messages and
`oldest_attempted_id` for historical continuation. Upserts make repeated runs
safe.

Private invite import is disabled by default. Users must explicitly configure
`TG_JOIN_INVITE=1` and `TG_INVITE_LINK` to import an invite once.

## Safety

The implementation is serial by default: one channel, one history iterator,
one media download, and one transcription request at a time. It uses Telethon
`wait_time`, configurable jitter, and FloodWait handling. Short FloodWaits are
slept through; long FloodWaits save state and exit with retry guidance.

`TG_USE_TAKEOUT=auto` tries a takeout session for export-friendly sync, but
falls back to normal client calls if takeout is delayed or unavailable.

## Verification

Automated tests do not require live Telegram credentials. They cover `.env`
validation, message classification, sticker exclusion, media path generation,
and FloodWait decisions. Live acceptance requires a user-controlled Telegram
account and a channel the account can access.
