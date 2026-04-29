---
name: telegram-channel-sync
description: Use this skill whenever the user wants to archive, fetch, sync, export, backfill, index, or analyze Telegram channel messages with Telethon and SQLite. This is especially relevant for private channels, large all-time history syncs, media downloads, voice-message transcription through Telegram, .env setup, FloodWait/rate-limit handling, or resumable Telegram data collection. Prefer this skill even when the user only says "scrape a Telegram channel" or "save channel messages" and does not explicitly mention Telethon.
---

# Telegram Channel Sync

Use this skill to help a user sync one Telegram channel at a time into SQLite with
media files saved to disk. The bundled script is the source of truth for real
sync work; prefer running or adapting it instead of writing a fresh scraper.

## Boundaries

Only sync channels the user's Telegram account can lawfully access. Private
channel invite import is disabled by default. Only enable it when the user has
provided an invite link and explicitly configured `TG_JOIN_INVITE=1`.

Do not add external speech-to-text services in this skill. Voice transcription
uses Telegram's own `messages.TranscribeAudioRequest` through Telethon. If
Telegram requires Premium, returns pending, fails, or rate-limits the request,
record the status in SQLite and continue.

## Quick Start

From this skill directory:

```bash
python scripts/sync_telegram_channel.py doctor --env .env
python scripts/sync_telegram_channel.py sync --env .env
```

Install runtime dependencies if `doctor` reports they are missing:

```bash
python -m pip install telethon python-dotenv
```

The first `sync` run may prompt for a Telegram login code and 2FA password.
Later runs reuse `TG_SESSION_PATH`; keep that session file private.

## .env Template

Required:

```dotenv
TG_API_ID=123456
TG_API_HASH=your_api_hash_from_my_telegram_org
TG_PHONE=+15551234567
TG_CHANNEL=@channel_username_or_numeric_id_or_t_me_link
TG_DB_PATH=./telegram_sync.sqlite3
TG_MEDIA_DIR=./telegram_media
TG_SESSION_PATH=./telegram_sync.session
```

Optional:

```dotenv
TG_JOIN_INVITE=0
TG_INVITE_LINK=
TG_USE_TAKEOUT=auto
TG_WAIT_TIME_SECONDS=1.2
TG_JITTER_SECONDS=0.3
TG_MAX_AUTO_SLEEP_SECONDS=3600
TG_DOWNLOAD_MEDIA=1
TG_MAX_MEDIA_BYTES=0
TG_TRANSCRIBE_VOICE=1
TG_LOG_LEVEL=INFO
```

Explain missing `TG_API_ID` and `TG_API_HASH` plainly: the user gets them from
`https://my.telegram.org` under "API Development tools".

## What Gets Stored

SQLite stores structured records:

- `channels`: channel identity, source ref, title, username, access mode, sync times.
- `messages`: included message rows keyed by `(channel_id, message_id)`.
- `media`: media kind, mime type, path, size, dimensions, duration, status, error.
- `transcriptions`: voice transcription status, text, Telegram transcription id, errors.
- `sync_state`: newest synced id, oldest historical checkpoint, completion flag, last error.

Media bytes are not stored as SQLite BLOBs. They are saved under:

```text
TG_MEDIA_DIR/<channel_id>/<message_id>/<media_kind>.<ext>
```

Included message types:

- text and captions
- photos
- videos
- voice messages and Telegram transcription status
- audio
- ordinary documents
- webpage-preview metadata as message records

Excluded message types:

- stickers
- custom emoji and sticker documents
- dice/game-like emoji events
- service messages
- empty non-content updates

## Safe Sync Strategy

Keep the default workflow conservative:

- One channel per run.
- Serial history, media, and transcription operations.
- `iter_messages(limit=None, wait_time=...)` for all-time history.
- Idempotent SQLite upserts so reruns are safe.
- Incremental sync after `newest_synced_id`.
- Historical backfill resume from `oldest_attempted_id`.
- Jitter between message processing steps.
- Respect `FloodWaitError.seconds`.

If a FloodWait is at or below `TG_MAX_AUTO_SLEEP_SECONDS`, the script sleeps and
continues. If it is longer, the script saves state and exits with retry guidance.

`TG_USE_TAKEOUT=auto` tries Telethon takeout for export-friendly history work,
then falls back to normal client calls when takeout is delayed or unavailable.
Use `TG_USE_TAKEOUT=1` only when the user wants takeout to be required.

## Private Channels

Default behavior expects the Telegram account to already be a member of private
channels. If `TG_CHANNEL` is an invite link and `TG_JOIN_INVITE` is not enabled,
the script stops with instructions instead of joining automatically.

To import once:

```dotenv
TG_JOIN_INVITE=1
TG_INVITE_LINK=https://t.me/+...
```

Avoid repeated join attempts. They are unnecessary after membership exists and
increase account-risk surface.

## Troubleshooting

If `.env` is missing or incomplete, run:

```bash
python scripts/sync_telegram_channel.py doctor --env .env
```

If Telethon is missing, install:

```bash
python -m pip install telethon python-dotenv
```

If Telegram reports a long FloodWait, do not retry in a loop. Wait for the
reported seconds, then rerun the same command. The SQLite checkpoint preserves
progress after each processed message.
