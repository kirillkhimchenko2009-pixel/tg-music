"""Устойчивость Telegram-отправки: retry, fallback, документы, история."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from aiogram.exceptions import (TelegramBadRequest, TelegramNetworkError,
                                TelegramRetryAfter, TelegramServerError)
from aiogram.types import FSInputFile

from music.domain import (AudioAnalysis, AudioProbe, MediaKind, PreparedTrack,
                          TrackCandidate)
from music.enums import PermissionStatus, QualityCategory
from music.telegram import sender


def _prepared(tmp_path, *, file_format="mp3", media_kind=MediaKind.AUDIO,
              file_id="FILEID", with_file=True) -> PreparedTrack:
    path = tmp_path / f"track.{file_format}"
    if with_file:
        path.write_bytes(b"x" * 256)
    codec = {"mp3": "mp3", "flac": "flac", "m4a": "aac"}.get(file_format, file_format)
    probe = AudioProbe(codec=codec, container=file_format, bitrate_kbps=320,
                       sample_rate=44100, duration=12, has_audio=True)
    audio = AudioAnalysis(probe=probe, category=QualityCategory.HIGH_QUALITY,
                          score=80)
    candidate = TrackCandidate(
        provider="jamendo", external_id="1", title="Song", artist="Artist",
        album="Album", duration=12, source_url="https://example.com/track",
        permission=PermissionStatus.DOWNLOAD_ALLOWED)
    return PreparedTrack(
        path=str(path) if with_file else "", media_kind=media_kind,
        mime_type="audio/mpeg", file_format=file_format, size=256,
        duration=12, title="Song", artist="Artist", album="Album",
        candidate=candidate, audio=audio, telegram_file_id=file_id,
        telegram_file_unique_id="UNIQ", cache_key="cache-key")


def _message(*, audio=None, document=None):
    return SimpleNamespace(audio=audio, document=document)


class ScriptedBot:
    """Бот, который отдаёт заранее заданные ошибки/успехи по вызовам."""

    def __init__(self, *, audio_script=None, document_script=None):
        self.calls: list[tuple[str, dict]] = []
        self.audio_script = list(audio_script or [])
        self.document_script = list(document_script or [])
        self.audio_file_id = "NEW-AUDIO"
        self.document_file_id = "NEW-DOC"

    @staticmethod
    def _resolve(item, kwargs):
        if callable(item):
            return item(kwargs)
        return item

    async def send_audio(self, **kwargs):
        self.calls.append(("audio", kwargs))
        item = self.audio_script.pop(0) if self.audio_script else None
        exc = self._resolve(item, kwargs)
        if exc is not None:
            raise exc
        return _message(audio=SimpleNamespace(file_id=self.audio_file_id,
                                              file_unique_id="UNIQ-A"))

    async def send_document(self, **kwargs):
        self.calls.append(("document", kwargs))
        item = self.document_script.pop(0) if self.document_script else None
        exc = self._resolve(item, kwargs)
        if exc is not None:
            raise exc
        return _message(document=SimpleNamespace(file_id=self.document_file_id,
                                                  file_unique_id="UNIQ-D"))


@pytest.fixture(autouse=True)
def _sender_isolated(monkeypatch):
    async def remember(prepared, file_id, unique_id=""):
        prepared.telegram_file_id = file_id
        prepared.telegram_file_unique_id = unique_id

    monkeypatch.setattr(sender, "_remember_file_id", remember)
    monkeypatch.setattr(sender, "_cleanup", lambda prepared: None)
    monkeypatch.setattr(sender.asyncio, "sleep", _no_sleep)


async def _no_sleep(_delay):
    return None


def _bad_request(message: str) -> TelegramBadRequest:
    return TelegramBadRequest(method=None, message=message)


class TestRetry:
    async def test_retry_after_then_success(self, tmp_path, monkeypatch):
        delays: list[float] = []

        async def sleep(delay):
            delays.append(delay)

        monkeypatch.setattr(sender.asyncio, "sleep", sleep)
        bot = ScriptedBot(audio_script=[
            TelegramRetryAfter(method=None, message="flood", retry_after=2)])
        prepared = _prepared(tmp_path)

        await sender.send_prepared(bot, 10, prepared)

        assert [name for name, _ in bot.calls] == ["audio", "audio"]
        assert delays == [2.0]
        assert prepared.telegram_file_id == "NEW-AUDIO"

    async def test_network_error_then_success(self, tmp_path):
        bot = ScriptedBot(audio_script=[
            TelegramNetworkError(method=None, message="connection reset")])
        await sender.send_prepared(bot, 10, _prepared(tmp_path))
        assert len(bot.calls) == 2

    async def test_server_error_then_success(self, tmp_path):
        bot = ScriptedBot(document_script=[
            TelegramServerError(method=None, message="bad gateway")])
        prepared = _prepared(tmp_path, file_format="flac",
                             media_kind=MediaKind.DOCUMENT)
        await sender.send_prepared(bot, 10, prepared)
        assert len(bot.calls) == 2

    async def test_retry_exhaustion_is_raised(self, tmp_path):
        errors = [TelegramNetworkError(method=None, message="down")] * 3
        bot = ScriptedBot(audio_script=errors)
        with pytest.raises(TelegramNetworkError):
            await sender.send_prepared(bot, 10, _prepared(tmp_path))
        assert len(bot.calls) == 3


class TestFileIdFallback:
    async def test_stale_audio_file_id_falls_back_to_local(self, tmp_path,
                                                           monkeypatch):
        forgotten: list[PreparedTrack] = []

        async def forget(prepared):
            forgotten.append(prepared)
            prepared.telegram_file_id = None
            prepared.telegram_file_unique_id = None

        monkeypatch.setattr(sender, "_forget_file_id", forget)

        def fail_on_file_id(kwargs):
            if isinstance(kwargs.get("audio"), str):
                return _bad_request("wrong file identifier/HTTP URL specified")
            return None

        bot = ScriptedBot(audio_script=[fail_on_file_id])
        prepared = _prepared(tmp_path)
        await sender.send_prepared(bot, 10, prepared)

        assert len(forgotten) == 1
        assert [name for name, _ in bot.calls] == ["audio", "audio"]
        assert isinstance(bot.calls[0][1]["audio"], str)
        assert isinstance(bot.calls[1][1]["audio"], FSInputFile)
        assert bot.calls[1][1]["audio"].filename.endswith(".mp3")
        assert prepared.telegram_file_id == "NEW-AUDIO"

    async def test_stale_document_file_id_falls_back_to_local(self, tmp_path,
                                                              monkeypatch):
        async def forget(prepared):
            prepared.telegram_file_id = None
            prepared.telegram_file_unique_id = None

        monkeypatch.setattr(sender, "_forget_file_id", forget)

        def fail_on_file_id(kwargs):
            if isinstance(kwargs.get("document"), str):
                return _bad_request("wrong file identifier/HTTP URL specified")
            return None

        bot = ScriptedBot(document_script=[fail_on_file_id])
        prepared = _prepared(tmp_path, file_format="flac",
                             media_kind=MediaKind.DOCUMENT)
        await sender.send_prepared(bot, 10, prepared)

        assert len(bot.calls) == 2
        assert isinstance(bot.calls[1][1]["document"], FSInputFile)
        assert bot.calls[1][1]["document"].filename.endswith(".flac")
        assert "filename" not in bot.calls[0][1]
        assert "filename" not in bot.calls[1][1]

    async def test_stale_file_id_without_local_file_raises(self, tmp_path,
                                                           monkeypatch):
        async def forget(prepared):  # pragma: no cover - не должно вызваться
            raise AssertionError("нельзя забывать file_id без локального файла")

        monkeypatch.setattr(sender, "_forget_file_id", forget)
        bot = ScriptedBot(audio_script=[
            _bad_request("wrong file identifier/HTTP URL specified")])
        prepared = _prepared(tmp_path, with_file=False)
        with pytest.raises(TelegramBadRequest):
            await sender.send_prepared(bot, 10, prepared)
        assert len(bot.calls) == 1

    async def test_bad_request_for_local_file_is_not_retried(self, tmp_path):
        bot = ScriptedBot(audio_script=[_bad_request("audio is invalid")])
        prepared = _prepared(tmp_path, file_id=None)
        with pytest.raises(TelegramBadRequest):
            await sender.send_prepared(bot, 10, prepared)
        assert len(bot.calls) == 1

    async def test_thumbnail_rejection_retries_without_thumbnail(self, tmp_path):
        from music.domain import CoverAnalysis, CoverCandidate

        def fail_with_thumbnail(kwargs):
            if "thumbnail" in kwargs:
                return _bad_request("PHOTO_INVALID_DIMENSIONS")
            return None

        bot = ScriptedBot(audio_script=[fail_with_thumbnail])
        cover = CoverAnalysis(candidate=CoverCandidate(
            data=_jpeg_bytes(), width=600, height=600, mime="image/jpeg",
            size=100), accepted=True)
        prepared = _prepared(tmp_path)
        prepared.cover = cover
        await sender.send_prepared(bot, 10, prepared)

        assert len(bot.calls) == 2
        assert "thumbnail" in bot.calls[0][1]
        assert "thumbnail" not in bot.calls[1][1]


def _jpeg_bytes() -> bytes:
    import io

    from PIL import Image
    image = Image.new("RGB", (600, 600), (10, 20, 30))
    out = io.BytesIO()
    image.save(out, format="JPEG")
    return out.getvalue()


class TestHistoryFallback:
    @staticmethod
    def _history(**kwargs):
        base = dict(id=1, telegram_file_id="OLD", audio_format="mp3",
                    media_kind="audio", title="Song", artist="Artist",
                    duration=200, bitrate_kbps=320,
                    quality_category="HIGH_QUALITY", provider="jamendo",
                    source_url="https://example.com/track", cache_key="key")
        base.update(kwargs)
        return SimpleNamespace(**base)

    @pytest.fixture(autouse=True)
    def _history_db(self, monkeypatch):
        class DummySession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def commit(self):
                return None

        monkeypatch.setattr(sender, "AsyncSessionLocal", lambda: DummySession())

        async def noop(*args, **kwargs):
            return None

        monkeypatch.setattr(sender.repository, "update_history_telegram", noop)
        monkeypatch.setattr(sender, "set_telegram_file", noop)

    async def test_stale_history_file_id_uses_cached_path(self, tmp_path):
        path = tmp_path / "cached.mp3"
        path.write_bytes(b"x" * 256)

        def fail_on_file_id(kwargs):
            if isinstance(kwargs.get("audio"), str):
                return _bad_request("wrong file identifier/HTTP URL specified")
            return None

        bot = ScriptedBot(audio_script=[fail_on_file_id])
        await sender.send_history_entry(bot, 10, self._history(),
                                        cached_path=str(path))
        assert len(bot.calls) == 2
        assert isinstance(bot.calls[1][1]["audio"], FSInputFile)
        assert bot.calls[1][1]["audio"].filename.endswith(".mp3")

    async def test_stale_history_file_id_without_path_raises(self):
        bot = ScriptedBot(audio_script=[_bad_request("wrong file identifier")])
        with pytest.raises(TelegramBadRequest):
            await sender.send_history_entry(bot, 10, self._history())
        assert len(bot.calls) == 1
