import importlib.util
import pathlib
import sys
import unittest


SCRIPT_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "scripts"
    / "sync_telegram_channel.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location("sync_telegram_channel", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SyncTelegramChannelTests(unittest.TestCase):
    def test_validate_env_reports_missing_required_keys(self):
        sync = load_module()
        with self.subTest("missing file"):
            import tempfile

            with tempfile.TemporaryDirectory() as tmp_dir:
                result = sync.load_config(pathlib.Path(tmp_dir) / ".env")

        self.assertFalse(result.ok)
        self.assertIn("TG_API_ID", result.message)
        self.assertIn("TG_API_HASH", result.message)
        self.assertIn("https://my.telegram.org", result.message)
        self.assertIn("API Development tools", result.message)
        self.assertIn("Missing required keys: TG_API_ID, TG_API_HASH", result.message)
        first_line = next(
            line for line in result.message.splitlines() if line.startswith("Missing")
        )
        self.assertNotIn("TG_SESSION_PATH", first_line)
        self.assertNotIn("TG_PHONE", first_line)
        self.assertNotIn("TG_CHANNEL", first_line)
        self.assertIn("uv run --with telethon --with python-dotenv", result.message)
        self.assertIn("python -m pip install telethon python-dotenv", result.message)

    def test_minimal_env_uses_defaults_and_defers_phone_to_first_login(self):
        sync = load_module()
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            env_path = pathlib.Path(tmp_dir) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "TG_API_ID=123456",
                        "TG_API_HASH=0123456789abcdef0123456789abcdef",
                    ]
                ),
                encoding="utf-8",
            )
            result = sync.load_config(env_path)

        self.assertTrue(result.ok)
        self.assertIsNone(result.config.phone)
        self.assertIsNone(result.config.channel)
        self.assertEqual(result.config.db_path.name, "telegram_sync.sqlite3")
        self.assertEqual(result.config.media_dir.name, "telegram_media")
        self.assertEqual(result.config.session_path.name, "telegram_sync.session")

    def test_missing_phone_message_explains_first_login_requirement(self):
        sync = load_module()
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            env_path = pathlib.Path(tmp_dir) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "TG_API_ID=123456",
                        "TG_API_HASH=0123456789abcdef0123456789abcdef",
                    ]
                ),
                encoding="utf-8",
            )
            result = sync.load_config(env_path)

        message = sync.missing_phone_for_first_login_message(result.config)

        self.assertIn("TG_PHONE", message)
        self.assertIn("first login", message)
        self.assertIn("TG_SESSION_PATH", message)
        self.assertIn("later runs", message)

    def test_validate_env_parses_required_and_optional_values(self):
        sync = load_module()
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            env_path = pathlib.Path(tmp_dir) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "TG_API_ID=123456",
                        "TG_API_HASH=0123456789abcdef0123456789abcdef",
                        "TG_PHONE=+15551234567",
                        "TG_CHANNEL=@example",
                        "TG_DB_PATH=./archive.sqlite3",
                        "TG_MEDIA_DIR=./media",
                        "TG_SESSION_PATH=./session",
                        "TG_JOIN_INVITE=1",
                        "TG_INVITE_LINK=https://t.me/+AbCdEf123456",
                        "TG_WAIT_TIME_SECONDS=2.5",
                        "TG_JITTER_SECONDS=0.75",
                        "TG_MAX_AUTO_SLEEP_SECONDS=42",
                        "TG_DOWNLOAD_MEDIA=0",
                        "TG_MAX_MEDIA_BYTES=1048576",
                        "TG_TRANSCRIBE_VOICE=0",
                        "TG_SINCE_HOURS=24",
                    ]
                ),
                encoding="utf-8",
            )
            result = sync.load_config(env_path)

        self.assertTrue(result.ok)
        self.assertEqual(result.config.api_id, 123456)
        self.assertEqual(result.config.channel, "@example")
        self.assertIs(result.config.join_invite, True)
        self.assertEqual(result.config.wait_time_seconds, 2.5)
        self.assertEqual(result.config.jitter_seconds, 0.75)
        self.assertEqual(result.config.max_auto_sleep_seconds, 42)
        self.assertIs(result.config.download_media, False)
        self.assertEqual(result.config.max_media_bytes, 1048576)
        self.assertIs(result.config.transcribe_voice, False)
        self.assertEqual(result.config.since_hours, 24.0)

    def test_normalize_channel_ref_converts_private_message_link_to_peer_id(self):
        sync = load_module()

        normalized = sync.normalize_channel_ref("https://t.me/c/1445373305/27567")

        self.assertEqual(normalized, "-1001445373305")

    def test_apply_runtime_overrides_accepts_channel_argument(self):
        sync = load_module()
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            env_path = pathlib.Path(tmp_dir) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "TG_API_ID=123456",
                        "TG_API_HASH=0123456789abcdef0123456789abcdef",
                    ]
                ),
                encoding="utf-8",
            )
            result = sync.load_config(env_path)

        config = sync.apply_runtime_overrides(
            result.config,
            channel="https://t.me/c/1445373305/27567",
            since_hours=24,
        )

        self.assertEqual(config.channel, "-1001445373305")
        self.assertEqual(config.since_hours, 24)

    def test_classify_message_excludes_stickers_dice_and_empty_messages(self):
        sync = load_module()

        self.assertFalse(sync.classify_message(FakeMessage(sticker=True)).include)
        self.assertFalse(sync.classify_message(FakeMessage(dice=True)).include)
        self.assertFalse(sync.classify_message(FakeMessage()).include)

    def test_classify_message_includes_useful_content_types(self):
        sync = load_module()

        self.assertEqual(sync.classify_message(FakeMessage(text="hello")).kind, "text")
        self.assertEqual(sync.classify_message(FakeMessage(photo=True)).kind, "photo")
        self.assertEqual(sync.classify_message(FakeMessage(video=True)).kind, "video")
        self.assertEqual(sync.classify_message(FakeMessage(voice=True)).kind, "voice")
        self.assertEqual(sync.classify_message(FakeMessage(audio=True)).kind, "audio")
        self.assertEqual(
            sync.classify_message(FakeMessage(document=True)).kind, "document"
        )
        self.assertEqual(
            sync.classify_message(FakeMessage(web_preview=True)).kind, "webpage"
        )

    def test_media_path_is_channel_and_message_scoped(self):
        sync = load_module()
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = sync.build_media_path(
                pathlib.Path(tmp_dir), 987654321, 42, "photo", "image/jpeg"
            )

            self.assertEqual(path.parent, pathlib.Path(tmp_dir) / "987654321" / "42")
            self.assertEqual(path.name, "photo.jpg")

    def test_flood_wait_decision_sleeps_only_within_threshold(self):
        sync = load_module()

        self.assertTrue(
            sync.flood_wait_decision(wait_seconds=60, threshold_seconds=3600).should_sleep
        )
        decision = sync.flood_wait_decision(wait_seconds=7200, threshold_seconds=3600)
        self.assertIs(decision.should_sleep, False)
        self.assertIn("retry after 7200 seconds", decision.message)

    def test_clear_invalid_takeout_id_normalizes_empty_blob(self):
        sync = load_module()
        client = FakeClient(FakeSession(takeout_id=b""))

        self.assertTrue(sync.clear_invalid_takeout_id(client))
        self.assertIsNone(client.session.takeout_id)
        self.assertTrue(client.session.saved)

    def test_clear_invalid_takeout_id_keeps_valid_integer(self):
        sync = load_module()
        client = FakeClient(FakeSession(takeout_id=123456789))

        self.assertFalse(sync.clear_invalid_takeout_id(client))
        self.assertEqual(client.session.takeout_id, 123456789)
        self.assertFalse(client.session.saved)

    def test_takeout_max_file_size_uses_signed_int_limit_without_media_cap(self):
        sync = load_module()
        config = FakeConfig(download_media=True, max_media_bytes=0)

        self.assertEqual(
            sync.takeout_max_file_size(config),
            sync.TAKEOUT_UNLIMITED_FILE_SIZE,
        )

    def test_takeout_max_file_size_respects_disabled_media_and_explicit_cap(self):
        sync = load_module()

        self.assertIsNone(
            sync.takeout_max_file_size(
                FakeConfig(download_media=False, max_media_bytes=0)
            )
        )
        self.assertEqual(
            sync.takeout_max_file_size(
                FakeConfig(download_media=True, max_media_bytes=1024)
            ),
            1024,
        )


class FakeMessage:
    id = 1
    date = None
    edit_date = None
    sender_id = 7
    grouped_id = None
    reply_to_msg_id = None
    views = None
    forwards = None
    replies = None

    def __init__(
        self,
        text="",
        photo=False,
        video=False,
        voice=False,
        audio=False,
        document=False,
        sticker=False,
        dice=False,
        web_preview=False,
    ):
        self.message = text
        self.text = text
        self.raw_text = text
        self.photo = object() if photo else None
        self.video = object() if video else None
        self.voice = object() if voice else None
        self.audio = object() if audio else None
        self.document = object() if document or sticker else None
        self.sticker = object() if sticker else None
        self.dice = object() if dice else None
        self.web_preview = object() if web_preview else None
        self.media = (
            self.photo
            or self.video
            or self.voice
            or self.audio
            or self.document
            or self.web_preview
        )
        self.file = FakeFile("application/pdf", 1024) if document else None


class FakeFile:
    def __init__(self, mime_type, size):
        self.mime_type = mime_type
        self.size = size
        self.name = "document.pdf"
        self.ext = ".pdf"


class FakeSession:
    def __init__(self, takeout_id):
        self.takeout_id = takeout_id
        self.saved = False

    def save(self):
        self.saved = True


class FakeClient:
    def __init__(self, session):
        self.session = session


class FakeConfig:
    def __init__(self, download_media, max_media_bytes):
        self.download_media = download_media
        self.max_media_bytes = max_media_bytes
