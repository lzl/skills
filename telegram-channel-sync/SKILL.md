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
uv run --with telethon --with python-dotenv \
  python scripts/sync_telegram_channel.py doctor --env .env
uv run --with telethon --with python-dotenv \
  python scripts/sync_telegram_channel.py sync --env .env https://t.me/c/1445373305/27567
uv run --with telethon --with python-dotenv \
  python scripts/sync_telegram_channel.py sync --env .env https://t.me/c/1445373305/27567 --since-hours 24
```

Install runtime dependencies if `doctor` reports they are missing. Prefer `uv`
when it is available:

```bash
uv run --with telethon --with python-dotenv python scripts/sync_telegram_channel.py doctor --env .env
```

Without `uv`, use:

```bash
python -m pip install telethon python-dotenv
```

The first `sync` run creates `TG_SESSION_PATH` through phone-code login using
`TG_PHONE`. Telegram may ask for a login code and cloud password. Later runs
reuse `TG_SESSION_PATH`; keep that session file private.

By default, runtime artifacts for this skill are written under
`./output/telegram-channel-sync/` so generated data stays grouped by skill.

## .env Template

Always required:

```dotenv
TG_API_ID=123456
TG_API_HASH=your_api_hash_from_my_telegram_org
```

Required for first login when `TG_SESSION_PATH` is not already authorized:

```dotenv
TG_PHONE=+15551234567
```

Optional:

```dotenv
TG_CHANNEL=@channel_username_or_numeric_id_or_t_me_link
TG_DB_PATH=./output/telegram-channel-sync/telegram_sync.sqlite3
TG_MEDIA_DIR=./output/telegram-channel-sync/telegram_media
TG_SESSION_PATH=./output/telegram-channel-sync/telegram_sync.session
TG_JOIN_INVITE=0
TG_INVITE_LINK=
TG_USE_TAKEOUT=auto
TG_WAIT_TIME_SECONDS=1.2
TG_JITTER_SECONDS=0.3
TG_MAX_AUTO_SLEEP_SECONDS=3600
TG_DOWNLOAD_MEDIA=1
TG_MAX_MEDIA_BYTES=0
TG_TRANSCRIBE_VOICE=1
TG_SINCE_HOURS=
TG_LOG_LEVEL=INFO
```

Explain missing `TG_API_ID` and `TG_API_HASH` plainly: the user gets them from
`https://my.telegram.org` under "API Development tools".

`TG_SESSION_PATH` defaults to
`./output/telegram-channel-sync/telegram_sync.session`; the user does not get it
from Telegram. It is the local Telethon session file created on first login.

`TG_PHONE` is required to create a new session. It may be omitted only when
`TG_SESSION_PATH` already points to an authorized Telethon session.

`TG_CHANNEL` is optional when the user passes the channel as a command argument
or with `--channel`. This is useful when the user may provide arbitrary channel
links at runtime.

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

With the default `TG_MEDIA_DIR`, media paths live under
`./output/telegram-channel-sync/telegram_media/...`.

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
- `--since-hours N` or `TG_SINCE_HOURS=N` for bounded recent-window syncs.
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
uv run --with telethon --with python-dotenv python scripts/sync_telegram_channel.py doctor --env .env
```

or, without `uv`:

```bash
python -m pip install telethon python-dotenv
```

If Telegram reports a long FloodWait, do not retry in a loop. Wait for the
reported seconds, then rerun the same command. The SQLite checkpoint preserves
progress after each processed message.

If Telegram sends a "data export request" confirmation to the mobile/desktop
client, ask the user to approve it there, then rerun the sync. Telegram may still
return `TakeoutInitDelayError`; if so, wait for the reported seconds or rely on
the script's normal-client fallback.

If Telethon reports that another takeout for the current session is unfinished
but the local session has an invalid empty takeout marker, the bundled script
normalizes that marker and continues. This is a local session hygiene issue, not
a Telegram account ban.

For links like `https://t.me/c/1445373305/27567`, use the link as `TG_CHANNEL`
or pass it through the script as the positional `CHANNEL` argument or `--channel`.
The script normalizes this private-channel message URL to the Telethon-style
channel reference `-1001445373305`; the Telegram account still needs to be a
member of that private channel.
