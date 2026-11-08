"""Telegram-слой: выбор sendAudio/sendDocument, caption, thumbnail, история."""
from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from PIL import Image

from music.domain import (AudioAnalysis, AudioProbe, CoverAnalysis,
                          CoverCandidate, MediaKind, PreparedTrack,
                          TrackCandidate)
from music.enums import PermissionStatus, QualityCategory
from music.telegram import sender


def _cover_bytes() -> bytes:
    image = Image.new("RGB", (600, 600), (200, 40, 40))
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=90)
    return out.getvalue()


def _prepared(**kwargs) -> PreparedTrack:
    candidate = kwargs.pop("candidate", None) or TrackCandidate(
        provider="jamendo", external_id="1", title="One More Time",
        artist="Daft Punk", album="Discovery", year=2001,
        duration=320, source_url="https://example.com/track/1",
        permission=PermissionStatus.DOWNLOAD_ALLOWED,
        provider_label="Jamendo", license_name="CC BY")
    probe = AudioProbe(codec="mp3", container="mp3", bitrate_kbps=320,
                       sample_rate=44100, duration=320, has_audio=True)
    audio = AudioAnalysis(probe=probe, category=QualityCategory.HIGH_QUALITY,
                          score=82)
    base = dict(path="", media_kind=MediaKind.AUDIO, mime_type="audio/mpeg",
                file_format="mp3", size=5_000_000, duration=320,
                title=candidate.title, artist=candidate.artist,
                album=candidate.album, candidate=candidate, audio=audio,
                telegram_file_id="FILEID", telegram_file_unique_id="UNIQUE")
    base.update(kwargs)
    return PreparedTrack(**base)


class FakeMessage:
    def __init__(self, *, audio=None, document=None):
        self.audio = audio
        self.document = document


class FakeBot:
    def __init__(self, *, audio_file_id="FILEID", document_file_id="DOCID"):
        self.audio_file_id = audio_file_id
        self.document_file_id = document_file_id
        self.calls: list[tuple[str, dict]] = []

    async def send_audio(self, **kwargs):
        self.calls.append(("audio", kwargs))
        return FakeMessage(audio=SimpleNamespace(file_id=self.audio_file_id,
                                                 file_unique_id="UNIQ-A"))

    async def send_document(self, **kwargs):
        self.calls.append(("document", kwargs))
        return FakeMessage(document=SimpleNamespace(file_id=self.document_file_id,
                                                    file_unique_id="UNIQ-D"))


@pytest.fixture(autouse=True)
def _no_side_effects(monkeypatch):
    async def remember(prepared, file_id, unique_id=""):
        prepared.telegram_file_id = file_id
        prepared.telegram_file_unique_id = unique_id

    monkeypatch.setattr(sender, "_remember_file_id", remember)
    monkeypatch.setattr(sender, "_cleanup", lambda prepared: None)


class TestCaption:
    def test_contains_artist_title_album_and_source(self):
        caption = sender.build_caption(_prepared())
        assert "Daft Punk — One More Time" in caption
        assert "Discovery · 2001" in caption
        assert "MP3" in caption
        assert "320 kbps" in caption
        assert "Высокое качество" in caption
        assert "Jamendo" in caption
        assert "CC BY" in caption
        assert "https://example.com/track/1" in caption

    def test_escapes_html(self):
        candidate = TrackCandidate(provider="x", external_id="1",
                                   title="A & B <script>", artist="<b>Artist</b>")
        caption = sender.build_caption(_prepared(candidate=candidate))
        assert "&lt;b&gt;Artist&lt;/b&gt;" in caption
        assert "A &amp; B &lt;script&gt;" in caption

    def test_dangerous_source_url_is_not_linked(self):
        candidate = TrackCandidate(provider="x", external_id="1", title="Song",
                                   artist="Artist",
                                   source_url="javascript:alert(1)")
        caption = sender.build_caption(_prepared(candidate=candidate))
        assert "javascript:" not in caption
        assert "<a href" not in caption

    def test_safe_url(self):
        assert sender.safe_url("https://example.com/x") == "https://example.com/x"
        assert sender.safe_url("http://example.com/x") == "http://example.com/x"
        assert sender.safe_url("javascript:alert(1)") == ""
        assert sender.safe_url("file:///etc/passwd") == ""
        assert sender.safe_url(None) == ""
        assert sender.safe_url("") == ""

    def test_long_caption_is_clipped(self):
        candidate = TrackCandidate(provider="x", external_id="1",
                                   title="T" * 300, artist="A" * 300,
                                   album="B" * 300, source_url="")
        caption = sender.build_caption(_prepared(candidate=candidate))
        assert len(caption) <= 1000


class TestSendPrepared:
    async def test_mp3_uses_send_audio(self):
        bot = FakeBot()
        prepared = _prepared()
        message = await sender.send_prepared(bot, 10, prepared)
        assert message.audio is not None
        assert [name for name, _ in bot.calls] == ["audio"]
        kwargs = bot.calls[0][1]
        assert kwargs["chat_id"] == 10
        assert kwargs["audio"] == "FILEID"
        assert kwargs["title"] == "One More Time"
        assert kwargs["performer"] == "Daft Punk"
        assert kwargs["duration"] == 320
        assert "Daft Punk" in kwargs["caption"]
        assert prepared.telegram_file_id == "FILEID"

    async def test_flac_uses_send_document(self):
        bot = FakeBot()
        prepared = _prepared(media_kind=MediaKind.DOCUMENT, file_format="flac",
                             mime_type="audio/flac")
        await sender.send_prepared(bot, 10, prepared)
        assert bot.calls[0][0] == "document"
        kwargs = bot.calls[0][1]
        assert kwargs["document"] == "FILEID"
        # filename — не аргумент Bot.send_document; иначе TypeError на каждом файле
        assert "filename" not in kwargs

    async def test_local_flac_document_filename_comes_from_input_file(self, tmp_path):
        bot = FakeBot()
        path = tmp_path / "track.flac"
        path.write_bytes(b"fLaC" + b"\x00" * 100)
        prepared = _prepared(media_kind=MediaKind.DOCUMENT, file_format="flac",
                             mime_type="audio/flac", telegram_file_id=None,
                             path=str(path), size=path.stat().st_size)
        await sender.send_prepared(bot, 10, prepared)
        kwargs = bot.calls[0][1]
        assert "filename" not in kwargs
        assert getattr(kwargs["document"], "filename", "").endswith(".flac")

    async def test_send_mode_document_overrides_mp3(self):
        bot = FakeBot()
        await sender.send_prepared(bot, 10, _prepared(), send_mode="document")
        assert bot.calls[0][0] == "document"

    async def test_send_mode_audio_keeps_flac_as_document(self):
        bot = FakeBot()
        prepared = _prepared(media_kind=MediaKind.DOCUMENT, file_format="flac")
        await sender.send_prepared(bot, 10, prepared, send_mode="audio")
        assert bot.calls[0][0] == "document"

    async def test_send_mode_audio_uses_audio_for_mp3(self):
        bot = FakeBot()
        await sender.send_prepared(bot, 10, _prepared(), send_mode="audio")
        assert bot.calls[0][0] == "audio"

    async def test_reply_parameters(self):
        bot = FakeBot()
        await sender.send_prepared(bot, 10, _prepared(), reply_to_message_id=77)
        kwargs = bot.calls[0][1]
        assert kwargs["reply_to_message_id"] == 77
        assert kwargs["allow_sending_without_reply"] is True

    async def test_thumbnail_sent_when_cover_valid(self):
        bot = FakeBot()
        cover = CoverAnalysis(candidate=CoverCandidate(
            data=_cover_bytes(), width=600, height=600, format="JPEG",
            mime="image/jpeg", size=1000), accepted=True)
        prepared = _prepared(cover=cover)
        await sender.send_prepared(bot, 10, prepared)
        kwargs = bot.calls[0][1]
        assert "thumbnail" in kwargs
        assert kwargs["thumbnail"].filename == "cover.jpg"

    async def test_thumbnail_skipped_when_cover_invalid(self):
        bot = FakeBot()
        cover = CoverAnalysis(candidate=CoverCandidate(data=b"broken"))
        prepared = _prepared(cover=cover)
        await sender.send_prepared(bot, 10, prepared)
        assert "thumbnail" not in bot.calls[0][1]

    async def test_missing_file_without_file_id_fails(self):
        bot = FakeBot()
        prepared = _prepared(telegram_file_id=None, path="")
        with pytest.raises(FileNotFoundError):
            await sender.send_prepared(bot, 10, prepared)
        assert bot.calls == []


class TestHistory:
    def _history(self, **kwargs):
        base = dict(id=1, telegram_file_id="FILEID", audio_format="mp3",
                    media_kind="audio", title="Song", artist="Artist",
                    duration=200, bitrate_kbps=320, quality_category="HIGH_QUALITY",
                    provider="jamendo", source_url="https://example.com/track",
                    cache_key="")
        base.update(kwargs)
        return SimpleNamespace(**base)

    async def test_audio_history(self):
        bot = FakeBot()
        await sender.send_history_entry(bot, 10, self._history())
        assert bot.calls[0][0] == "audio"
        kwargs = bot.calls[0][1]
        assert kwargs["audio"] == "FILEID"
        assert "Artist" in kwargs["caption"]
        assert "320 kbps" in kwargs["caption"]

    async def test_document_history(self):
        bot = FakeBot()
        await sender.send_history_entry(
            bot, 10, self._history(media_kind="document", audio_format="flac"))
        assert bot.calls[0][0] == "document"
        kwargs = bot.calls[0][1]
        assert kwargs["document"] == "FILEID"
        assert "filename" not in kwargs

    async def test_local_document_history_filename_from_input_file(self, tmp_path):
        bot = FakeBot()
        path = tmp_path / "song.flac"
        path.write_bytes(b"fLaC" + b"\x00" * 100)
        history = self._history(media_kind="document", audio_format="flac",
                                telegram_file_id="")
        await sender.send_history_entry(bot, 10, history, cached_path=str(path))
        kwargs = bot.calls[0][1]
        assert "filename" not in kwargs
        assert getattr(kwargs["document"], "filename", "").endswith(".flac")

    async def test_missing_history_file(self, tmp_path):
        bot = FakeBot()
        history = self._history(telegram_file_id="", cache_key="")
        with pytest.raises(FileNotFoundError):
            await sender.send_history_entry(bot, 10, history)

    async def test_history_escapes_html(self):
        bot = FakeBot()
        history = self._history(title="A & B", artist="<Artist>")
        await sender.send_history_entry(bot, 10, history)
        caption = bot.calls[0][1]["caption"]
        assert "&lt;Artist&gt;" in caption
        assert "A &amp; B" in caption
