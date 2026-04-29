#!/usr/bin/env python3
"""Conservative Telethon channel history sync into SQLite.

This script is intentionally usable without live Telegram credentials for
`doctor` and unit tests. The `sync` command imports Telethon only when it is
needed so missing runtime dependencies produce clear setup guidance.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import datetime as dt
import importlib.util
import json
import logging
import mimetypes
import pathlib
import random
import re
import sqlite3
import sys
from typing import Any


REQUIRED_KEYS = (
    "TG_API_ID",
    "TG_API_HASH",
)

DEFAULTS = {
    "TG_PHONE": "",
    "TG_CHANNEL": "",
    "TG_DB_PATH": "./telegram_sync.sqlite3",
    "TG_MEDIA_DIR": "./telegram_media",
    "TG_SESSION_PATH": "./telegram_sync.session",
    "TG_JOIN_INVITE": "0",
    "TG_INVITE_LINK": "",
    "TG_USE_TAKEOUT": "auto",
    "TG_WAIT_TIME_SECONDS": "1.2",
    "TG_JITTER_SECONDS": "0.3",
    "TG_MAX_AUTO_SLEEP_SECONDS": "3600",
    "TG_DOWNLOAD_MEDIA": "1",
    "TG_MAX_MEDIA_BYTES": "0",
    "TG_TRANSCRIBE_VOICE": "1",
    "TG_SINCE_HOURS": "",
    "TG_LOG_LEVEL": "INFO",
}

TAKEOUT_UNLIMITED_FILE_SIZE = (2**63) - 1

ENV_HELP = """Telegram channel sync requires a .env file with:

Required:
  TG_API_ID=123456
  TG_API_HASH=your_api_hash_from_my_telegram_org

Required for first login when TG_SESSION_PATH is not already authorized:
  TG_PHONE=+15551234567

Optional:
  TG_CHANNEL=@channel_username_or_numeric_id_or_t_me_link
  TG_DB_PATH=./telegram_sync.sqlite3
  TG_MEDIA_DIR=./telegram_media
  TG_SESSION_PATH=./telegram_sync.session
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

Get TG_API_ID and TG_API_HASH from https://my.telegram.org -> API Development tools.
TG_SESSION_PATH defaults to ./telegram_sync.session. The first login uses
TG_PHONE and may ask for a Telegram login code and cloud password. Later runs
reuse TG_SESSION_PATH, so keep that session file private and stable.
Install runtime dependencies with:
  uv run --with telethon --with python-dotenv python scripts/sync_telegram_channel.py doctor --env .env
or, without uv:
  python -m pip install telethon python-dotenv
"""


@dataclasses.dataclass(frozen=True)
class Config:
    api_id: int
    api_hash: str
    phone: str | None
    channel: str | None
    db_path: pathlib.Path
    media_dir: pathlib.Path
    session_path: pathlib.Path
    join_invite: bool
    invite_link: str
    use_takeout: str
    wait_time_seconds: float
    jitter_seconds: float
    max_auto_sleep_seconds: int
    download_media: bool
    max_media_bytes: int
    transcribe_voice: bool
    since_hours: float | None
    log_level: str
    env_path: pathlib.Path


@dataclasses.dataclass(frozen=True)
class ConfigResult:
    ok: bool
    config: Config | None
    message: str


@dataclasses.dataclass(frozen=True)
class MessageClassification:
    include: bool
    kind: str
    skip_reason: str | None = None


@dataclasses.dataclass(frozen=True)
class FloodWaitDecision:
    should_sleep: bool
    message: str


class SetupError(RuntimeError):
    pass


class LongFloodWait(RuntimeError):
    def __init__(self, seconds: int):
        self.seconds = seconds
        super().__init__(
            f"Telegram returned FloodWait. Sync state saved; retry after {seconds} seconds."
        )


def parse_env_file(path: pathlib.Path) -> dict[str, str]:
    if not path.exists():
        return {}

    try:
        from dotenv import dotenv_values  # type: ignore

        values = dotenv_values(path)
        return {key: str(value) for key, value in values.items() if value is not None}
    except ModuleNotFoundError:
        return parse_env_file_without_dependency(path)


def parse_env_file_without_dependency(path: pathlib.Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def parse_bool(value: str, key: str, errors: list[str]) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    errors.append(f"{key} must be a boolean like 1/0 or true/false.")
    return False


def parse_int(value: str, key: str, errors: list[str], minimum: int | None = None) -> int:
    try:
        parsed = int(value)
    except ValueError:
        errors.append(f"{key} must be an integer.")
        return 0
    if minimum is not None and parsed < minimum:
        errors.append(f"{key} must be >= {minimum}.")
    return parsed


def parse_float(
    value: str, key: str, errors: list[str], minimum: float | None = None
) -> float:
    try:
        parsed = float(value)
    except ValueError:
        errors.append(f"{key} must be a number.")
        return 0.0
    if minimum is not None and parsed < minimum:
        errors.append(f"{key} must be >= {minimum}.")
    return parsed


def resolve_env_path(env_path: pathlib.Path, value: str) -> pathlib.Path:
    candidate = pathlib.Path(value).expanduser()
    if candidate.is_absolute():
        return candidate
    return (env_path.parent / candidate).resolve()


def normalize_channel_ref(value: str) -> str:
    stripped = value.strip()
    match = re.search(r"t\.me/c/(\d+)(?:/\d+)?", stripped)
    if match:
        return f"-100{match.group(1)}"
    return stripped


def telethon_entity_ref(value: str) -> int | str:
    stripped = value.strip()
    if re.fullmatch(r"-?\d+", stripped):
        return int(stripped)
    return stripped


def apply_runtime_overrides(
    config: Config,
    channel: str | None = None,
    since_hours: float | None = None,
) -> Config:
    updates: dict[str, Any] = {}
    if channel and channel.strip():
        updates["channel"] = normalize_channel_ref(channel)
    if since_hours is not None:
        updates["since_hours"] = since_hours if since_hours > 0 else None
    return dataclasses.replace(config, **updates)


def missing_phone_for_first_login_message(config: Config) -> str:
    return (
        f"No authorized Telegram session was found at {config.session_path}, "
        "and TG_PHONE is not set. Add TG_PHONE=+15551234567 to .env for the "
        "first login, then rerun the command. Telegram may also ask for the "
        "login code and cloud password. After the session file is authorized, "
        "later runs can reuse TG_SESSION_PATH without TG_PHONE."
    )


async def authorize_client(client: Any, config: Config) -> None:
    if await client.is_user_authorized():
        return
    if not config.phone:
        raise SetupError(missing_phone_for_first_login_message(config))
    logging.info(
        "First login may ask for a Telegram code and cloud password; "
        "future runs reuse %s.",
        config.session_path,
    )
    await client.start(phone=config.phone)


def load_config(env_path: str | pathlib.Path) -> ConfigResult:
    path = pathlib.Path(env_path).expanduser().resolve()
    errors: list[str] = []
    raw = {**DEFAULTS, **parse_env_file(path)}

    if not path.exists():
        errors.append(f".env file not found: {path}")

    missing = [key for key in REQUIRED_KEYS if not raw.get(key, "").strip()]
    if missing:
        errors.append("Missing required keys: " + ", ".join(missing))

    api_id = 0
    if raw.get("TG_API_ID", "").strip():
        api_id = parse_int(raw["TG_API_ID"], "TG_API_ID", errors, minimum=1)
    wait_time = parse_float(
        raw["TG_WAIT_TIME_SECONDS"], "TG_WAIT_TIME_SECONDS", errors, minimum=0.0
    )
    jitter = parse_float(
        raw["TG_JITTER_SECONDS"], "TG_JITTER_SECONDS", errors, minimum=0.0
    )
    max_sleep = parse_int(
        raw["TG_MAX_AUTO_SLEEP_SECONDS"],
        "TG_MAX_AUTO_SLEEP_SECONDS",
        errors,
        minimum=0,
    )
    max_media = parse_int(
        raw["TG_MAX_MEDIA_BYTES"], "TG_MAX_MEDIA_BYTES", errors, minimum=0
    )
    join_invite = parse_bool(raw["TG_JOIN_INVITE"], "TG_JOIN_INVITE", errors)
    download_media = parse_bool(raw["TG_DOWNLOAD_MEDIA"], "TG_DOWNLOAD_MEDIA", errors)
    transcribe_voice = parse_bool(
        raw["TG_TRANSCRIBE_VOICE"], "TG_TRANSCRIBE_VOICE", errors
    )
    since_hours: float | None = None
    if raw.get("TG_SINCE_HOURS", "").strip():
        since_hours = parse_float(
            raw["TG_SINCE_HOURS"], "TG_SINCE_HOURS", errors, minimum=0.0
        )
        if since_hours == 0:
            since_hours = None

    use_takeout = raw["TG_USE_TAKEOUT"].strip().lower()
    if use_takeout not in {"auto", "1", "0", "true", "false", "yes", "no"}:
        errors.append("TG_USE_TAKEOUT must be auto, 1/0, true/false, or yes/no.")

    if join_invite and not raw.get("TG_INVITE_LINK", "").strip():
        errors.append("TG_JOIN_INVITE=1 requires TG_INVITE_LINK.")

    if errors:
        return ConfigResult(False, None, "\n".join(errors) + "\n\n" + ENV_HELP)

    config = Config(
        api_id=api_id,
        api_hash=raw["TG_API_HASH"].strip(),
        phone=raw["TG_PHONE"].strip() or None,
        channel=normalize_channel_ref(raw["TG_CHANNEL"]) if raw["TG_CHANNEL"].strip() else None,
        db_path=resolve_env_path(path, raw["TG_DB_PATH"].strip()),
        media_dir=resolve_env_path(path, raw["TG_MEDIA_DIR"].strip()),
        session_path=resolve_env_path(path, raw["TG_SESSION_PATH"].strip()),
        join_invite=join_invite,
        invite_link=raw["TG_INVITE_LINK"].strip(),
        use_takeout=use_takeout,
        wait_time_seconds=wait_time,
        jitter_seconds=jitter,
        max_auto_sleep_seconds=max_sleep,
        download_media=download_media,
        max_media_bytes=max_media,
        transcribe_voice=transcribe_voice,
        since_hours=since_hours,
        log_level=raw["TG_LOG_LEVEL"].strip().upper() or "INFO",
        env_path=path,
    )
    return ConfigResult(True, config, f"Loaded configuration from {path}")


def classify_message(message: Any) -> MessageClassification:
    if getattr(message, "action", None) is not None:
        return MessageClassification(False, "service", "service_message")
    if getattr(message, "sticker", None) is not None:
        return MessageClassification(False, "sticker", "sticker")
    if getattr(message, "dice", None) is not None:
        return MessageClassification(False, "dice", "dice")
    if document_has_sticker_or_custom_emoji(getattr(message, "document", None)):
        return MessageClassification(False, "sticker", "sticker_or_custom_emoji")

    if getattr(message, "photo", None) is not None:
        return MessageClassification(True, "photo")
    if getattr(message, "voice", None) is not None:
        return MessageClassification(True, "voice")
    if getattr(message, "video", None) is not None:
        return MessageClassification(True, "video")
    if getattr(message, "audio", None) is not None:
        return MessageClassification(True, "audio")
    if getattr(message, "document", None) is not None:
        return MessageClassification(True, "document")
    if (
        getattr(message, "web_preview", None) is not None
        or getattr(message, "webpage", None) is not None
    ):
        return MessageClassification(True, "webpage")

    text = get_message_text(message)
    if text:
        return MessageClassification(True, "text")
    return MessageClassification(False, "empty", "empty_non_content")


def document_has_sticker_or_custom_emoji(document: Any) -> bool:
    if document is None:
        return False
    for attr in getattr(document, "attributes", []) or []:
        name = attr.__class__.__name__.lower()
        if "sticker" in name or "customemoji" in name or "custom_emoji" in name:
            return True
    mime_type = (getattr(document, "mime_type", "") or "").lower()
    return mime_type in {"application/x-tgsticker", "application/x-tgsdice"}


def get_message_text(message: Any) -> str:
    for attr in ("raw_text", "text", "message"):
        value = getattr(message, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def build_media_path(
    media_root: pathlib.Path,
    channel_id: int | str,
    message_id: int,
    media_kind: str,
    mime_type: str | None,
) -> pathlib.Path:
    extension = guess_extension(media_kind, mime_type)
    return media_root / str(channel_id) / str(message_id) / f"{media_kind}{extension}"


def guess_extension(media_kind: str, mime_type: str | None) -> str:
    if mime_type:
        guessed = mimetypes.guess_extension(mime_type.split(";")[0].strip())
        if guessed:
            return ".jpg" if guessed == ".jpe" else guessed
    defaults = {
        "photo": ".jpg",
        "video": ".mp4",
        "voice": ".ogg",
        "audio": ".mp3",
        "document": ".bin",
        "webpage": ".json",
    }
    return defaults.get(media_kind, ".bin")


def flood_wait_decision(wait_seconds: int, threshold_seconds: int) -> FloodWaitDecision:
    if wait_seconds <= threshold_seconds:
        return FloodWaitDecision(
            True,
            f"Telegram requested FloodWait for {wait_seconds} seconds; sleeping before continuing.",
        )
    return FloodWaitDecision(
        False,
        f"Telegram requested FloodWait; sync state saved, retry after {wait_seconds} seconds.",
    )


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def iso_datetime(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.timezone.utc)
        return value.isoformat()
    return str(value)


def connect_database(config: Config) -> sqlite3.Connection:
    config.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    initialize_schema(conn)
    return conn


def initialize_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE NOT NULL,
            access_hash TEXT,
            source_ref TEXT NOT NULL,
            title TEXT,
            username TEXT,
            access_mode TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_synced_at TEXT
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
            message_id INTEGER NOT NULL,
            message_date TEXT,
            edit_date TEXT,
            sender_id INTEGER,
            text TEXT,
            kind TEXT NOT NULL,
            reply_to_msg_id INTEGER,
            grouped_id TEXT,
            views INTEGER,
            forwards INTEGER,
            replies_count INTEGER,
            has_media INTEGER NOT NULL DEFAULT 0,
            skip_reason TEXT,
            raw_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(channel_id, message_id)
        );

        CREATE TABLE IF NOT EXISTS media (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
            message_id INTEGER NOT NULL,
            media_kind TEXT NOT NULL,
            mime_type TEXT,
            file_path TEXT,
            file_size INTEGER,
            width INTEGER,
            height INTEGER,
            duration INTEGER,
            remote_size INTEGER,
            download_status TEXT NOT NULL,
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(channel_id, message_id, media_kind)
        );

        CREATE TABLE IF NOT EXISTS transcriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
            message_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            text TEXT,
            transcription_id TEXT,
            pending INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(channel_id, message_id)
        );

        CREATE TABLE IF NOT EXISTS sync_state (
            channel_id INTEGER PRIMARY KEY REFERENCES channels(id) ON DELETE CASCADE,
            newest_synced_id INTEGER NOT NULL DEFAULT 0,
            oldest_attempted_id INTEGER NOT NULL DEFAULT 0,
            backfill_complete INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            updated_at TEXT NOT NULL
        );
        """
    )
    conn.commit()


def upsert_channel(conn: sqlite3.Connection, entity: Any, source_ref: str) -> int:
    now = utc_now()
    telegram_id = int(getattr(entity, "id", 0))
    if telegram_id == 0:
        raise SetupError("Could not resolve a Telegram channel id.")
    title = getattr(entity, "title", None) or getattr(entity, "first_name", None)
    username = getattr(entity, "username", None)
    access_hash = getattr(entity, "access_hash", None)
    access_mode = "public" if username else "private_or_id"
    conn.execute(
        """
        INSERT INTO channels (
            telegram_id, access_hash, source_ref, title, username, access_mode,
            created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(telegram_id) DO UPDATE SET
            access_hash=excluded.access_hash,
            source_ref=excluded.source_ref,
            title=excluded.title,
            username=excluded.username,
            access_mode=excluded.access_mode,
            updated_at=excluded.updated_at
        """,
        (
            telegram_id,
            str(access_hash) if access_hash is not None else None,
            source_ref,
            title,
            username,
            access_mode,
            now,
            now,
        ),
    )
    row = conn.execute(
        "SELECT id FROM channels WHERE telegram_id = ?", (telegram_id,)
    ).fetchone()
    channel_id = int(row["id"])
    conn.execute(
        """
        INSERT INTO sync_state (channel_id, updated_at)
        VALUES (?, ?)
        ON CONFLICT(channel_id) DO NOTHING
        """,
        (channel_id, now),
    )
    conn.commit()
    return channel_id


def get_sync_state(conn: sqlite3.Connection, channel_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM sync_state WHERE channel_id = ?", (channel_id,)
    ).fetchone()
    if row is None:
        now = utc_now()
        conn.execute(
            "INSERT INTO sync_state (channel_id, updated_at) VALUES (?, ?)",
            (channel_id, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM sync_state WHERE channel_id = ?", (channel_id,)
        ).fetchone()
    return row


def update_sync_state(conn: sqlite3.Connection, channel_id: int, **fields: Any) -> None:
    allowed = {
        "newest_synced_id",
        "oldest_attempted_id",
        "backfill_complete",
        "last_error",
    }
    assignments: list[str] = []
    values: list[Any] = []
    for key, value in fields.items():
        if key not in allowed:
            raise ValueError(f"Unknown sync_state field: {key}")
        assignments.append(f"{key} = ?")
        values.append(value)
    assignments.append("updated_at = ?")
    values.append(utc_now())
    values.append(channel_id)
    conn.execute(
        f"UPDATE sync_state SET {', '.join(assignments)} WHERE channel_id = ?",
        values,
    )
    conn.commit()


def upsert_message(
    conn: sqlite3.Connection,
    channel_id: int,
    message: Any,
    classification: MessageClassification,
) -> None:
    now = utc_now()
    replies = getattr(message, "replies", None)
    replies_count = getattr(replies, "replies", None) if replies is not None else None
    raw_json = safe_message_json(message)
    conn.execute(
        """
        INSERT INTO messages (
            channel_id, message_id, message_date, edit_date, sender_id, text, kind,
            reply_to_msg_id, grouped_id, views, forwards, replies_count, has_media,
            skip_reason, raw_json, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(channel_id, message_id) DO UPDATE SET
            message_date=excluded.message_date,
            edit_date=excluded.edit_date,
            sender_id=excluded.sender_id,
            text=excluded.text,
            kind=excluded.kind,
            reply_to_msg_id=excluded.reply_to_msg_id,
            grouped_id=excluded.grouped_id,
            views=excluded.views,
            forwards=excluded.forwards,
            replies_count=excluded.replies_count,
            has_media=excluded.has_media,
            skip_reason=excluded.skip_reason,
            raw_json=excluded.raw_json,
            updated_at=excluded.updated_at
        """,
        (
            channel_id,
            int(message.id),
            iso_datetime(getattr(message, "date", None)),
            iso_datetime(getattr(message, "edit_date", None)),
            getattr(message, "sender_id", None),
            get_message_text(message) or None,
            classification.kind,
            getattr(message, "reply_to_msg_id", None),
            str(getattr(message, "grouped_id", "")) or None,
            getattr(message, "views", None),
            getattr(message, "forwards", None),
            replies_count,
            1 if getattr(message, "media", None) is not None else 0,
            classification.skip_reason,
            raw_json,
            now,
            now,
        ),
    )


def safe_message_json(message: Any) -> str | None:
    try:
        if hasattr(message, "to_dict"):
            return json.dumps(message.to_dict(), default=str, ensure_ascii=False)
    except Exception:
        return None
    return None


def upsert_media(
    conn: sqlite3.Connection,
    channel_id: int,
    message_id: int,
    media_kind: str,
    mime_type: str | None,
    file_path: pathlib.Path | None,
    file_size: int | None,
    remote_size: int | None,
    status: str,
    error: str | None = None,
    width: int | None = None,
    height: int | None = None,
    duration: int | None = None,
) -> None:
    now = utc_now()
    conn.execute(
        """
        INSERT INTO media (
            channel_id, message_id, media_kind, mime_type, file_path, file_size,
            width, height, duration, remote_size, download_status, error,
            created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(channel_id, message_id, media_kind) DO UPDATE SET
            mime_type=excluded.mime_type,
            file_path=excluded.file_path,
            file_size=excluded.file_size,
            width=excluded.width,
            height=excluded.height,
            duration=excluded.duration,
            remote_size=excluded.remote_size,
            download_status=excluded.download_status,
            error=excluded.error,
            updated_at=excluded.updated_at
        """,
        (
            channel_id,
            message_id,
            media_kind,
            mime_type,
            str(file_path) if file_path else None,
            file_size,
            width,
            height,
            duration,
            remote_size,
            status,
            error,
            now,
            now,
        ),
    )


def upsert_transcription(
    conn: sqlite3.Connection,
    channel_id: int,
    message_id: int,
    status: str,
    text: str | None = None,
    transcription_id: str | None = None,
    pending: bool = False,
    error: str | None = None,
) -> None:
    now = utc_now()
    conn.execute(
        """
        INSERT INTO transcriptions (
            channel_id, message_id, status, text, transcription_id, pending, error,
            attempts, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        ON CONFLICT(channel_id, message_id) DO UPDATE SET
            status=excluded.status,
            text=excluded.text,
            transcription_id=excluded.transcription_id,
            pending=excluded.pending,
            error=excluded.error,
            attempts=transcriptions.attempts + 1,
            updated_at=excluded.updated_at
        """,
        (
            channel_id,
            message_id,
            status,
            text,
            transcription_id,
            1 if pending else 0,
            error,
            now,
            now,
        ),
    )


def media_metadata(message: Any) -> dict[str, int | str | None]:
    file_obj = getattr(message, "file", None)
    metadata: dict[str, int | str | None] = {
        "mime_type": getattr(file_obj, "mime_type", None),
        "remote_size": getattr(file_obj, "size", None),
        "width": getattr(file_obj, "width", None),
        "height": getattr(file_obj, "height", None),
        "duration": getattr(file_obj, "duration", None),
    }
    document = getattr(message, "document", None)
    for attr in getattr(document, "attributes", []) or []:
        for key in ("w", "h", "duration"):
            value = getattr(attr, key, None)
            if value is not None:
                mapped = {"w": "width", "h": "height", "duration": "duration"}[key]
                metadata[mapped] = value
    if not metadata["mime_type"]:
        media = getattr(message, "media", None)
        document = getattr(media, "document", None) if media is not None else document
        metadata["mime_type"] = getattr(document, "mime_type", None)
    return metadata


def require_telethon() -> tuple[Any, Any, Any]:
    if importlib.util.find_spec("telethon") is None:
        raise SetupError(
            "Telethon is not installed. Install runtime dependencies with:\n"
            "  uv run --with telethon --with python-dotenv "
            "python scripts/sync_telegram_channel.py doctor --env .env\n"
            "or, without uv:\n"
            "  python -m pip install telethon python-dotenv"
        )
    from telethon import TelegramClient, errors, functions

    return TelegramClient, errors, functions


def extract_invite_hash(link: str) -> str:
    stripped = link.strip()
    patterns = (
        r"t\.me/\+([A-Za-z0-9_-]+)",
        r"t\.me/joinchat/([A-Za-z0-9_-]+)",
        r"^joinchat/([A-Za-z0-9_-]+)$",
        r"^\+([A-Za-z0-9_-]+)$",
        r"^([A-Za-z0-9_-]{12,})$",
    )
    for pattern in patterns:
        match = re.search(pattern, stripped)
        if match:
            return match.group(1)
    raise SetupError(
        "Could not parse TG_INVITE_LINK. Use a t.me/+... or t.me/joinchat/... link."
    )


def looks_like_invite_ref(value: str) -> bool:
    return "joinchat/" in value or "t.me/+" in value or value.strip().startswith("+")


async def resolve_entity(client: Any, config: Config) -> Any:
    if not config.channel:
        raise SetupError("No channel specified. Set TG_CHANNEL or pass CHANNEL/--channel.")
    if config.join_invite:
        _, _, functions = require_telethon()
        invite_hash = extract_invite_hash(config.invite_link)
        logging.info("Importing private invite because TG_JOIN_INVITE=1.")
        updates = await client(functions.messages.ImportChatInviteRequest(invite_hash))
        chats = getattr(updates, "chats", None) or []
        if chats:
            return chats[0]
        logging.info("Invite import did not return chats; resolving TG_CHANNEL.")
        return await client.get_entity(telethon_entity_ref(config.channel))

    if looks_like_invite_ref(config.channel):
        raise SetupError(
            "TG_CHANNEL looks like a private invite link, but TG_JOIN_INVITE is not 1. "
            "Join the channel manually first, or set TG_JOIN_INVITE=1 with TG_INVITE_LINK."
        )
    return await client.get_entity(telethon_entity_ref(config.channel))


async def sync_channel_history(
    client: Any,
    conn: sqlite3.Connection,
    entity: Any,
    channel_id: int,
    config: Config,
) -> None:
    state = get_sync_state(conn, channel_id)
    if config.since_hours is not None:
        await sync_recent_window(client, conn, entity, channel_id, config)
        conn.execute(
            "UPDATE channels SET last_synced_at = ?, updated_at = ? WHERE id = ?",
            (utc_now(), utc_now(), channel_id),
        )
        conn.commit()
        return

    state = get_sync_state(conn, channel_id)
    newest = int(state["newest_synced_id"])

    if newest > 0:
        logging.info("Syncing new messages after id %s.", newest)
        await iter_phase(
            client,
            conn,
            entity,
            channel_id,
            config,
            {"min_id": newest, "reverse": True},
            phase="incremental",
        )

    state = get_sync_state(conn, channel_id)
    if not int(state["backfill_complete"]):
        oldest = int(state["oldest_attempted_id"])
        kwargs: dict[str, Any] = {}
        if oldest > 0:
            kwargs["offset_id"] = oldest
            logging.info("Resuming historical backfill older than id %s.", oldest)
        else:
            logging.info("Starting all-time historical backfill.")
        await iter_phase(
            client,
            conn,
            entity,
            channel_id,
            config,
            kwargs,
            phase="backfill",
        )
        update_sync_state(conn, channel_id, backfill_complete=1, last_error=None)

    conn.execute(
        "UPDATE channels SET last_synced_at = ?, updated_at = ? WHERE id = ?",
        (utc_now(), utc_now(), channel_id),
    )
    conn.commit()


async def sync_recent_window(
    client: Any,
    conn: sqlite3.Connection,
    entity: Any,
    channel_id: int,
    config: Config,
) -> None:
    assert config.since_hours is not None
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=config.since_hours)
    logging.info("Syncing messages since %s.", cutoff.isoformat())
    _, errors, _ = require_telethon()
    while True:
        try:
            async for message in client.iter_messages(
                entity,
                limit=None,
                wait_time=config.wait_time_seconds,
            ):
                message_date = getattr(message, "date", None)
                if isinstance(message_date, dt.datetime):
                    if message_date.tzinfo is None:
                        message_date = message_date.replace(tzinfo=dt.timezone.utc)
                    if message_date < cutoff:
                        update_sync_state(conn, channel_id, last_error=None)
                        return
                await process_message(client, conn, entity, channel_id, message, config)
                current_state = get_sync_state(conn, channel_id)
                fields: dict[str, Any] = {"last_error": None}
                if int(message.id) > int(current_state["newest_synced_id"]):
                    fields["newest_synced_id"] = int(message.id)
                update_sync_state(conn, channel_id, **fields)
                await jitter_sleep(config)
            return
        except errors.FloodWaitError as exc:
            await handle_flood_wait(conn, channel_id, int(exc.seconds), config)


async def iter_phase(
    client: Any,
    conn: sqlite3.Connection,
    entity: Any,
    channel_id: int,
    config: Config,
    extra_kwargs: dict[str, Any],
    phase: str,
) -> None:
    _, errors, _ = require_telethon()
    while True:
        try:
            async for message in client.iter_messages(
                entity,
                limit=None,
                wait_time=config.wait_time_seconds,
                **extra_kwargs,
            ):
                await process_message(client, conn, entity, channel_id, message, config)
                current_state = get_sync_state(conn, channel_id)
                fields: dict[str, Any] = {"last_error": None}
                if int(message.id) > int(current_state["newest_synced_id"]):
                    fields["newest_synced_id"] = int(message.id)
                if phase == "backfill":
                    fields["oldest_attempted_id"] = int(message.id)
                update_sync_state(conn, channel_id, **fields)
                if phase == "backfill":
                    extra_kwargs["offset_id"] = int(message.id)
                elif phase == "incremental":
                    extra_kwargs["min_id"] = int(message.id)
                await jitter_sleep(config)
            return
        except errors.FloodWaitError as exc:
            await handle_flood_wait(conn, channel_id, int(exc.seconds), config)


async def process_message(
    client: Any,
    conn: sqlite3.Connection,
    entity: Any,
    channel_id: int,
    message: Any,
    config: Config,
) -> None:
    classification = classify_message(message)
    if not classification.include:
        logging.debug(
            "Skipping message %s: %s", getattr(message, "id", "?"), classification.skip_reason
        )
        return

    upsert_message(conn, channel_id, message, classification)
    if config.download_media and classification.kind in {
        "photo",
        "video",
        "voice",
        "audio",
        "document",
    }:
        await download_message_media(client, conn, channel_id, message, classification, config)

    if config.transcribe_voice and classification.kind == "voice":
        await transcribe_voice_message(client, conn, entity, channel_id, message, config)

    conn.commit()


async def download_message_media(
    client: Any,
    conn: sqlite3.Connection,
    channel_id: int,
    message: Any,
    classification: MessageClassification,
    config: Config,
) -> None:
    metadata = media_metadata(message)
    remote_size = metadata["remote_size"]
    if (
        config.max_media_bytes
        and isinstance(remote_size, int)
        and remote_size > config.max_media_bytes
    ):
        upsert_media(
            conn,
            channel_id,
            int(message.id),
            classification.kind,
            str(metadata["mime_type"]) if metadata["mime_type"] else None,
            None,
            None,
            remote_size,
            "skipped_size_limit",
            f"remote size {remote_size} exceeds TG_MAX_MEDIA_BYTES",
            width=metadata["width"] if isinstance(metadata["width"], int) else None,
            height=metadata["height"] if isinstance(metadata["height"], int) else None,
            duration=metadata["duration"] if isinstance(metadata["duration"], int) else None,
        )
        conn.commit()
        return

    target = build_media_path(
        config.media_dir,
        channel_id,
        int(message.id),
        classification.kind,
        str(metadata["mime_type"]) if metadata["mime_type"] else None,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        upsert_media(
            conn,
            channel_id,
            int(message.id),
            classification.kind,
            str(metadata["mime_type"]) if metadata["mime_type"] else None,
            target,
            target.stat().st_size,
            remote_size if isinstance(remote_size, int) else None,
            "exists",
            width=metadata["width"] if isinstance(metadata["width"], int) else None,
            height=metadata["height"] if isinstance(metadata["height"], int) else None,
            duration=metadata["duration"] if isinstance(metadata["duration"], int) else None,
        )
        conn.commit()
        return

    _, errors, _ = require_telethon()
    try:
        downloaded = await client.download_media(message, file=str(target))
        final_path = pathlib.Path(downloaded) if downloaded else target
        upsert_media(
            conn,
            channel_id,
            int(message.id),
            classification.kind,
            str(metadata["mime_type"]) if metadata["mime_type"] else None,
            final_path,
            final_path.stat().st_size if final_path.exists() else None,
            remote_size if isinstance(remote_size, int) else None,
            "downloaded" if downloaded else "failed",
            None if downloaded else "Telethon returned no downloaded path.",
            width=metadata["width"] if isinstance(metadata["width"], int) else None,
            height=metadata["height"] if isinstance(metadata["height"], int) else None,
            duration=metadata["duration"] if isinstance(metadata["duration"], int) else None,
        )
    except errors.FloodWaitError as exc:
        await handle_flood_wait(conn, channel_id, int(exc.seconds), config)
        return await download_message_media(
            client, conn, channel_id, message, classification, config
        )
    except Exception as exc:
        upsert_media(
            conn,
            channel_id,
            int(message.id),
            classification.kind,
            str(metadata["mime_type"]) if metadata["mime_type"] else None,
            target,
            None,
            remote_size if isinstance(remote_size, int) else None,
            "failed",
            f"{exc.__class__.__name__}: {exc}",
            width=metadata["width"] if isinstance(metadata["width"], int) else None,
            height=metadata["height"] if isinstance(metadata["height"], int) else None,
            duration=metadata["duration"] if isinstance(metadata["duration"], int) else None,
        )
    conn.commit()


async def transcribe_voice_message(
    client: Any,
    conn: sqlite3.Connection,
    entity: Any,
    channel_id: int,
    message: Any,
    config: Config,
) -> None:
    _, errors, functions = require_telethon()
    try:
        result = await client(
            functions.messages.TranscribeAudioRequest(peer=entity, msg_id=int(message.id))
        )
        pending = bool(getattr(result, "pending", False))
        text = getattr(result, "text", None)
        transcription_id = getattr(result, "transcription_id", None)
        upsert_transcription(
            conn,
            channel_id,
            int(message.id),
            "pending" if pending else "complete",
            text=text,
            transcription_id=str(transcription_id) if transcription_id is not None else None,
            pending=pending,
            error=None,
        )
    except errors.FloodWaitError as exc:
        decision = flood_wait_decision(
            int(exc.seconds), config.max_auto_sleep_seconds
        )
        if decision.should_sleep:
            logging.warning(decision.message)
            await asyncio.sleep(int(exc.seconds) + random.random())
            return await transcribe_voice_message(
                client, conn, entity, channel_id, message, config
            )
        upsert_transcription(
            conn,
            channel_id,
            int(message.id),
            "flood_wait",
            error=decision.message,
        )
    except Exception as exc:
        error_name = exc.__class__.__name__
        status = "failed"
        if "Premium" in error_name or "PREMIUM" in str(exc):
            status = "premium_required"
        elif "VOICE" in str(exc).upper() or "Voice" in error_name:
            status = "not_voice"
        upsert_transcription(
            conn,
            channel_id,
            int(message.id),
            status,
            error=f"{error_name}: {exc}",
        )
    conn.commit()


async def handle_flood_wait(
    conn: sqlite3.Connection, channel_id: int, seconds: int, config: Config
) -> None:
    decision = flood_wait_decision(seconds, config.max_auto_sleep_seconds)
    update_sync_state(conn, channel_id, last_error=decision.message)
    if not decision.should_sleep:
        raise LongFloodWait(seconds)
    logging.warning(decision.message)
    await asyncio.sleep(seconds + random.uniform(0, max(config.jitter_seconds, 0.0)))


async def jitter_sleep(config: Config) -> None:
    if config.jitter_seconds > 0:
        await asyncio.sleep(random.uniform(0, config.jitter_seconds))


def takeout_enabled(config: Config) -> bool:
    return config.use_takeout in {"1", "true", "yes", "auto"}


def takeout_required(config: Config) -> bool:
    return config.use_takeout in {"1", "true", "yes"}


def takeout_max_file_size(config: Config) -> int | None:
    if not config.download_media:
        return None
    return config.max_media_bytes or TAKEOUT_UNLIMITED_FILE_SIZE


def clear_invalid_takeout_id(client: Any) -> bool:
    takeout_id = getattr(client.session, "takeout_id", None)
    if takeout_id is None or type(takeout_id) is int:
        return False
    log = logging.debug if takeout_id == b"" else logging.warning
    log(
        "Ignoring invalid local Telethon takeout_id %r. Treating this session as "
        "having no active takeout export.",
        takeout_id,
    )
    client.session.takeout_id = None
    save = getattr(client.session, "save", None)
    if callable(save):
        save()
    return True


async def run_sync(config: Config) -> int:
    TelegramClient, errors, _ = require_telethon()
    configure_logging(config.log_level)
    conn = connect_database(config)
    config.media_dir.mkdir(parents=True, exist_ok=True)

    client = TelegramClient(str(config.session_path), config.api_id, config.api_hash)
    client.flood_sleep_threshold = config.max_auto_sleep_seconds

    try:
        await client.connect()
        await authorize_client(client, config)
        entity = await resolve_entity(client, config)
        channel_id = upsert_channel(conn, entity, config.channel)
        clear_invalid_takeout_id(client)

        if takeout_enabled(config):
            try:
                async with client.takeout(
                    channels=True,
                    megagroups=True,
                    files=config.download_media,
                    max_file_size=takeout_max_file_size(config),
                ) as takeout:
                    logging.info("Using Telegram takeout session for export-friendly sync.")
                    await sync_channel_history(takeout, conn, entity, channel_id, config)
                    return 0
            except errors.TakeoutInitDelayError as exc:
                message = (
                    f"Telegram takeout is delayed for {int(exc.seconds)} seconds."
                )
                update_sync_state(conn, channel_id, last_error=message)
                if takeout_required(config):
                    print(message + " Sync state saved; retry later.", file=sys.stderr)
                    return 2
                logging.warning("%s Falling back to normal client calls.", message)
            except Exception as exc:
                if takeout_required(config):
                    update_sync_state(
                        conn,
                        channel_id,
                        last_error=f"takeout failed: {exc.__class__.__name__}: {exc}",
                    )
                    raise
                logging.warning(
                    "Takeout unavailable (%s: %s); falling back to normal client calls.",
                    exc.__class__.__name__,
                    exc,
                )

        await sync_channel_history(client, conn, entity, channel_id, config)
        return 0
    finally:
        await client.disconnect()


def configure_logging(level: str) -> None:
    numeric = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric,
        format="%(asctime)s %(levelname)s %(message)s",
    )


def command_doctor(args: argparse.Namespace) -> int:
    result = load_config(args.env)
    if not result.ok:
        print(result.message, file=sys.stderr)
        return 1

    config = result.config
    assert config is not None
    print(result.message)
    print(f"Database: {config.db_path}")
    print(f"Media dir: {config.media_dir}")
    print(f"Session: {config.session_path}")
    print(
        "Phone: "
        + (
            config.phone
            or "(not set; required for first login unless session is already authorized)"
        )
    )
    print(f"Channel: {config.channel or '(not set; pass CHANNEL/--channel to sync)'}")
    print(f"Join private invite: {config.join_invite}")
    missing: list[str] = []
    if importlib.util.find_spec("telethon") is None:
        missing.append("telethon")
    if importlib.util.find_spec("dotenv") is None:
        missing.append("python-dotenv")
    if missing:
        print(
            "Missing optional/runtime packages: "
            + ", ".join(missing)
            + "\nRun with uv: uv run --with telethon --with python-dotenv "
            + "python scripts/sync_telegram_channel.py doctor --env .env"
            + "\nOr without uv: python -m pip install telethon python-dotenv",
            file=sys.stderr,
        )
        return 1
    print("Runtime dependencies are available.")
    return 0


def command_sync(args: argparse.Namespace) -> int:
    result = load_config(args.env)
    if not result.ok:
        print(result.message, file=sys.stderr)
        return 1
    assert result.config is not None
    channel = args.channel_option or args.channel_arg
    config = apply_runtime_overrides(
        result.config,
        channel=channel,
        since_hours=args.since_hours,
    )
    if not config.channel:
        print("No channel specified. Set TG_CHANNEL or pass CHANNEL/--channel.", file=sys.stderr)
        return 1
    try:
        return asyncio.run(run_sync(config))
    except SetupError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except LongFloodWait as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted. Sync state already persists after each message.", file=sys.stderr)
        return 130


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sync one Telegram channel history into SQLite with media files on disk."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Validate .env and dependencies.")
    doctor.add_argument("--env", default=".env", help="Path to .env file.")
    doctor.set_defaults(func=command_doctor)

    sync = subparsers.add_parser("sync", help="Run resumable channel sync.")
    sync.add_argument(
        "channel_arg",
        nargs="?",
        help="Channel username/id/link. Overrides TG_CHANNEL when provided.",
    )
    sync.add_argument("--env", default=".env", help="Path to .env file.")
    sync.add_argument(
        "--channel",
        dest="channel_option",
        default=None,
        help="Channel username/id/link. Overrides TG_CHANNEL.",
    )
    sync.add_argument(
        "--since-hours",
        type=float,
        default=None,
        help="Only sync messages from the last N hours; overrides TG_SINCE_HOURS.",
    )
    sync.set_defaults(func=command_sync)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
