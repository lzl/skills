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
        self.assertIn("TG_SESSION_PATH", result.message)

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
