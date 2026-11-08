"""Telegram-хендлеры: защита от двойного нажатия и уборка seed-каталога."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from music.config import music_settings
from music.domain import DownloadOutcome, TrackCandidate
from music.enums import PermissionStatus
from music.errors import DuplicateJobError
from music.telegram import handlers


def _candidate(**kwargs):
    base = dict(provider="jamendo", external_id="1", title="Song",
                artist="Artist", duration=180,
                permission=PermissionStatus.DOWNLOAD_ALLOWED)
    base.update(kwargs)
    return TrackCandidate(**base)


class FakeStatus:
    def __init__(self):
        self.edits: list[tuple[str, dict]] = []
        self.markups: list = []
        self.chat = SimpleNamespace(id=1)
        self.message_id = 1

    async def edit_text(self, text, **kwargs):
        self.edits.append((text, kwargs))

    async def edit_reply_markup(self, reply_markup=None):
        self.markups.append(reply_markup)


class FakeIncoming:
    def __init__(self):
        self.answers: list[tuple[str, dict]] = []
        self.statuses: list[FakeStatus] = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))
        status = FakeStatus()
        self.statuses.append(status)
        return status


class FakeCallback:
    def __init__(self, user_id=1):
        self.from_user = SimpleNamespace(id=user_id, username="user",
                                         first_name="User")
        self.bot = SimpleNamespace()
        self.message = FakeIncoming()
        self.answers: list[tuple[str, dict]] = []

    async def answer(self, text="", **kwargs):
        self.answers.append((text, kwargs))


class FakeState:
    def __init__(self):
        self.cleared = False

    async def clear(self):
        self.cleared = True


@pytest.fixture(autouse=True)
def _fast_handler(monkeypatch):
    monkeypatch.setattr(handlers, "_allow", lambda *a, **k: True)

    async def ensure_user(user):
        return None

    monkeypatch.setattr(handlers, "_ensure_user", ensure_user)


class TestDoubleClick:
    async def test_second_click_does_not_enqueue(self, monkeypatch):
        existing = SimpleNamespace(id="existing-job")
        enqueued: list = []

        async def enqueue(*args, **kwargs):
            enqueued.append((args, kwargs))
            raise AssertionError("enqueue не должен вызываться")

        monkeypatch.setattr(handlers, "queue", SimpleNamespace(
            find_active=lambda user_id, candidate: existing,
            enqueue=enqueue))

        cb = FakeCallback(user_id=1)
        await handlers._start_download(cb, _candidate())

        assert enqueued == []
        assert cb.answers == [("⏳ Эта загрузка уже выполняется",
                               {"show_alert": True})]
        assert cb.message.answers == []

    async def test_duplicate_from_queue_finishes_progress(self, monkeypatch):
        async def enqueue(*args, **kwargs):
            raise DuplicateJobError("уже есть", job_id="old")

        monkeypatch.setattr(handlers, "queue", SimpleNamespace(
            find_active=lambda user_id, candidate: None, enqueue=enqueue))

        cb = FakeCallback(user_id=2)
        await handlers._start_download(cb, _candidate())

        assert cb.answers and cb.answers[0][0] == "Ставлю в очередь…"
        assert len(cb.message.answers) == 1
        assert "В очереди" in cb.message.answers[0][0]
        assert "уже выполняется" in cb.message.statuses[-1].edits[-1][0]

    async def test_successful_enqueue_wires_cancel_and_on_done(self, monkeypatch):
        captured = {}

        async def enqueue(user_id, candidate, **kwargs):
            captured.update(user_id=user_id, candidate=candidate, **kwargs)
            return SimpleNamespace(id="new-job")

        monkeypatch.setattr(handlers, "queue", SimpleNamespace(
            find_active=lambda user_id, candidate: None, enqueue=enqueue))

        cb = FakeCallback(user_id=3)
        await handlers._start_download(cb, _candidate())

        assert captured["user_id"] == 3
        assert callable(captured["on_progress"])
        assert callable(captured["on_done"])
        assert cb.message.statuses[-1].markups[-1] is not None


class TestUploadSeedCleanup:
    def _message(self, *, download_error=False):
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=10, username="u", first_name="U"),
            chat=SimpleNamespace(id=10),
            audio=SimpleNamespace(file_name="song.mp3", mime_type="audio/mpeg",
                                  file_size=1024, title="", performer="",
                                  duration=12),
            document=None,
            answers=[],
            statuses=[],
        )

        async def download(media, destination):
            if download_error:
                raise RuntimeError("telegram download failed")
            Path(destination).write_bytes(b"ID3" + b"\x00" * 512)

        message.bot = SimpleNamespace(download=download)

        async def answer(text, **kwargs):
            message.answers.append((text, kwargs))
            status = FakeStatus()
            message.statuses.append(status)
            return status

        message.answer = answer
        return message

    async def test_seed_removed_after_delivery(self, monkeypatch,
                                               temp_music_dir):
        captured = {}

        async def enqueue(user_id, candidate, **kwargs):
            captured.update(user_id=user_id, candidate=candidate, **kwargs)
            return SimpleNamespace(id="upload-job")

        monkeypatch.setattr(handlers, "queue", SimpleNamespace(
            find_active=lambda user_id, candidate: None, enqueue=enqueue))

        message = self._message()
        state = FakeState()
        await handlers.upload_file(message, state)

        assert state.cleared is True
        assert captured["candidate"].extra["force_upload"] is True
        seeds = [p for p in music_settings.tmp_dir.iterdir() if p.is_dir()]
        assert len(seeds) == 1
        assert captured["on_done"] is not None

        await captured["on_done"](DownloadOutcome(
            ok=False, job_id="upload-job", error="boom"))

        assert seeds[0].exists() is False
        assert list(music_settings.tmp_dir.iterdir()) == []

    async def test_seed_removed_when_enqueue_fails(self, monkeypatch,
                                                   temp_music_dir):
        async def enqueue(*args, **kwargs):
            raise DuplicateJobError("уже обрабатывается", job_id="old")

        monkeypatch.setattr(handlers, "queue", SimpleNamespace(
            find_active=lambda user_id, candidate: None, enqueue=enqueue))

        message = self._message()
        await handlers.upload_file(message, FakeState())

        assert list(music_settings.tmp_dir.iterdir()) == []
        # статус-сообщение — последнее, в нём текст про уже идущую обработку
        assert "уже обрабатывается" in message.statuses[-1].edits[-1][0]

    async def test_seed_removed_when_telegram_download_fails(self, monkeypatch,
                                                             temp_music_dir):
        message = self._message(download_error=True)
        await handlers.upload_file(message, FakeState())

        assert list(music_settings.tmp_dir.iterdir()) == []
        assert "Не удалось скачать" in message.answers[-1][0]
