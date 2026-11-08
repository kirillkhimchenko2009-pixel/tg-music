"""End-to-end: очередь → Download Manager → sender → Telegram (fake bot).

Проверяем именно связку, которую не покрывали отдельные тесты:
скачивание, анализ, кэш, отправка в чат и повторная отправка по file_id.
"""
from __future__ import annotations

import asyncio
import importlib
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select

from music.config import MusicSettings
from music.domain import (AudioSource, PermissionInfo, ProviderCapabilities,
                          TrackCandidate)
from music.enums import JobStatus, PermissionStatus
from music.models import (MusicCachedFile, MusicDownloadHistory,
                          MusicDownloadJob)
from music.providers.base import MusicProvider
from music.storage import new_job_id

queue_module = importlib.import_module("music.downloader.queue")
worker_module = importlib.import_module("music.downloader.worker")
manager_module = importlib.import_module("music.downloader.manager")
sender_module = importlib.import_module("music.telegram.sender")


class FakeBot:
    """Повторяет только те методы, которые вызывает sender."""

    def __init__(self, *, audio_errors: list[Exception] | None = None,
                 document_errors: list[Exception] | None = None):
        self.calls: list[tuple[str, dict]] = []
        self._audio_errors = list(audio_errors or [])
        self._document_errors = list(document_errors or [])
        self.sent_audio: list[str] = []

    async def send_audio(self, **kwargs):
        self.calls.append(("audio", kwargs))
        if self._audio_errors:
            raise self._audio_errors.pop(0)
        audio = kwargs.get("audio")
        if isinstance(audio, str):
            self.sent_audio.append(audio)
        return SimpleNamespace(
            audio=SimpleNamespace(file_id="FILE_ID", file_unique_id="UNIQ-A"),
            document=None)

    async def send_document(self, document, **kwargs):
        self.calls.append(("document", {"document": document, **kwargs}))
        if self._document_errors:
            raise self._document_errors.pop(0)
        return SimpleNamespace(
            audio=None,
            document=SimpleNamespace(file_id="DOC_FILE_ID", file_unique_id="UNIQ-D"))


class LocalProvider(MusicProvider):
    """Локальный файл вместо сети: детерминированный источник."""

    name = "local"
    display_name = "Local Test"

    def __init__(self, path: str | Path,
                 permission: PermissionStatus = PermissionStatus.DOWNLOAD_ALLOWED):
        super().__init__()
        self.capabilities = ProviderCapabilities(search=False, direct_link=False,
                                                 download=True, stream=True)
        self.path = Path(path)
        self.permission = permission
        self.stream_calls = 0
        self.gate: asyncio.Event | None = None
        self.started = asyncio.Event()

    async def get_download_permissions(self, track: TrackCandidate) -> PermissionInfo:
        return PermissionInfo(status=self.permission,
                              source_url="https://example.com/official")

    async def get_audio_stream(self, track: TrackCandidate) -> AudioSource:
        self.stream_calls += 1
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        return AudioSource(url="", filename=self.path.name, mime="audio/mpeg",
                           expected_format=self.path.suffix.lstrip(".") or "mp3",
                           extra={"local_path": str(self.path)})

    def get_source_url(self, track: TrackCandidate) -> str:
        return "https://example.com/official"


def _candidate(**kwargs) -> TrackCandidate:
    base = dict(provider="local", external_id="track-1", title="Test Song",
                artist="Test Artist", album="Test Album", duration=12,
                permission=PermissionStatus.DOWNLOAD_ALLOWED,
                source_url="https://example.com/track-1")
    base.update(kwargs)
    return TrackCandidate(**base)


async def _wait_status(queue, job_id: str, *statuses: str, timeout: float = 40.0) -> str:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        current = queue.status(job_id)
        if current and current["status"] in statuses:
            return current["status"]
        await asyncio.sleep(0.02)
    current = queue.status(job_id)
    raise AssertionError(f"job {job_id} не дошёл до {statuses}: {current}")


@pytest.fixture
async def stack(monkeypatch, db_maker, temp_music_dir, audio_files):
    """Реальная очередь + менеджер + sender, но локальный файл и fake bot."""
    monkeypatch.setattr(queue_module, "AsyncSessionLocal", db_maker)
    monkeypatch.setattr(worker_module, "AsyncSessionLocal", db_maker)
    monkeypatch.setattr(sender_module, "AsyncSessionLocal", db_maker)
    monkeypatch.setattr(queue_module, "cleanup_temp", lambda *a, **k: 0)

    provider = LocalProvider(audio_files["mp3"])
    monkeypatch.setattr(queue_module, "get_provider", lambda name: provider)

    settings = MusicSettings(workers=1, user_active_jobs=2, user_daily_jobs=0,
                             max_attempts=1, retry_backoff=0.0, job_timeout=90.0)
    manager = manager_module.DownloadManager()
    queue = queue_module.DownloadQueue(manager=manager, settings=settings)
    bot = FakeBot()
    outcomes = []
    done = asyncio.Event()

    async def on_done(outcome):
        outcomes.append(outcome)
        try:
            if outcome.ok and outcome.prepared is not None:
                await sender_module.send_prepared(bot, 777, outcome.prepared)
        finally:
            done.set()

    # Каждая задача должна пройти через on_done: иначе тест ждёт событие,
    # которое никто не выставит. Оборачиваем enqueue, чтобы не дублировать
    # on_done= в каждом тесте.
    original_enqueue = queue.enqueue

    async def enqueue(user_id, candidate, **kwargs):
        kwargs.setdefault("on_done", on_done)
        return await original_enqueue(user_id, candidate, **kwargs)

    queue.enqueue = enqueue

    await queue.start()
    wrapper = SimpleNamespace(queue=queue, bot=bot, outcomes=outcomes, done=done,
                              provider=provider, manager=manager,
                              db_maker=db_maker, settings=settings)
    yield wrapper
    await queue.stop()


async def _history(db_maker):
    async with db_maker() as session:
        return (await session.execute(
            select(MusicDownloadHistory))).scalars().all()


async def _cached(db_maker):
    async with db_maker() as session:
        return (await session.execute(
            select(MusicCachedFile))).scalars().all()


class TestHubPipeline:
    async def test_mp3_is_downloaded_and_sent_as_audio(self, stack):
        job = await stack.queue.enqueue(1, _candidate())
        await asyncio.wait_for(stack.done.wait(), 60)
        await _wait_status(stack.queue, job.id, JobStatus.COMPLETED.value)

        assert len(stack.outcomes) == 1
        outcome = stack.outcomes[0]
        assert outcome.ok is True
        prepared = outcome.prepared
        assert prepared is not None
        assert prepared.file_format == "mp3"
        assert Path(prepared.path).exists()

        assert [name for name, _ in stack.bot.calls] == ["audio"]
        call = stack.bot.calls[0][1]
        assert call["title"] == "Test Song"
        assert call["performer"] == "Test Artist"
        assert call["duration"] == 12
        assert "Test Artist — Test Song" in call["caption"]

        history = await _history(stack.db_maker)
        assert len(history) == 1
        assert history[0].telegram_file_id == "FILE_ID"
        cached = await _cached(stack.db_maker)
        assert len(cached) == 1
        assert cached[0].telegram_file_id == "FILE_ID"

        async with stack.db_maker() as session:
            row = await session.get(MusicDownloadJob, job.id)
            assert row.status == JobStatus.COMPLETED.value

    async def test_flac_is_sent_as_document_without_filename_kwarg(self, stack,
                                                                   audio_files):
        stack.provider.path = audio_files["flac"]
        job = await stack.queue.enqueue(1, _candidate(duration=12))
        await asyncio.wait_for(stack.done.wait(), 60)
        await _wait_status(stack.queue, job.id, JobStatus.COMPLETED.value)

        prepared = stack.outcomes[0].prepared
        assert prepared is not None
        assert prepared.file_format == "flac"
        assert [name for name, _ in stack.bot.calls] == ["document"]
        call = stack.bot.calls[0][1]
        # Bot.send_document в aiogram 3 не принимает filename: имя должно
        # браться из FSInputFile, иначе каждый документ идёт через TypeError.
        assert "filename" not in call
        assert getattr(call["document"], "filename", "").endswith(".flac")

    async def test_second_send_uses_cached_file_id_without_download(self, stack,
                                                                     audio_files):
        job = await stack.queue.enqueue(1, _candidate())
        await asyncio.wait_for(stack.done.wait(), 60)
        await _wait_status(stack.queue, job.id, JobStatus.COMPLETED.value)
        first = stack.outcomes[0].prepared
        assert first is not None and first.telegram_file_id == "FILE_ID"

        # Имитируем очистку файлового кэша: остаётся только file_id.
        Path(first.path).unlink()
        stack.provider.stream_calls = 0

        async with stack.db_maker() as session:
            outcome = await stack.manager.prepare(
                new_job_id(), 1, _candidate(), provider=stack.provider,
                session=session)
        assert outcome.ok is True
        assert outcome.prepared is not None
        assert outcome.prepared.cache_hit is True
        assert outcome.prepared.path == ""
        assert stack.provider.stream_calls == 0

        stack.done.clear()
        await sender_module.send_prepared(stack.bot, 777, outcome.prepared)
        assert stack.bot.calls[-1][0] == "audio"
        assert stack.bot.calls[-1][1]["audio"] == "FILE_ID"

    async def test_permission_denied_never_sends_file(self, stack):
        stack.provider.permission = PermissionStatus.STREAM_ONLY
        job = await stack.queue.enqueue(2, _candidate(
            permission=PermissionStatus.STREAM_ONLY))
        await asyncio.wait_for(stack.done.wait(), 60)
        await _wait_status(stack.queue, job.id, JobStatus.FAILED.value)

        outcome = stack.outcomes[0]
        assert outcome.ok is False
        assert outcome.error_code == "STREAM_ONLY"
        assert outcome.official_url == "https://example.com/official"
        assert stack.bot.calls == []
        assert await _cached(stack.db_maker) == []

    async def test_corrupted_file_never_sends(self, stack, tmp_path):
        broken = tmp_path / "broken.mp3"
        broken.write_bytes(b"this is definitely not audio" * 1000)
        stack.provider.path = broken
        job = await stack.queue.enqueue(3, _candidate())
        await asyncio.wait_for(stack.done.wait(), 60)
        await _wait_status(stack.queue, job.id, JobStatus.FAILED.value)

        outcome = stack.outcomes[0]
        assert outcome.ok is False
        assert outcome.error_code in ("NO_AUDIO", "DAMAGED", "INVALID_AUDIO")
        assert stack.bot.calls == []

    async def test_cancel_during_download_does_not_send(self, stack):
        stack.provider.gate = asyncio.Event()
        job = await stack.queue.enqueue(4, _candidate())
        await asyncio.wait_for(stack.provider.started.wait(), 20)
        assert await stack.queue.cancel(job.id, user_id=4) is True
        await asyncio.wait_for(stack.done.wait(), 20)
        await _wait_status(stack.queue, job.id, JobStatus.CANCELLED.value)

        assert stack.outcomes[0].error_code == "CANCELLED"
        assert stack.bot.calls == []

    async def test_send_failure_does_not_kill_worker(self, stack):
        stack.bot._audio_errors.append(
            TelegramBadRequest(method=None, message="bad request"))
        first = await stack.queue.enqueue(5, _candidate())
        await asyncio.wait_for(stack.done.wait(), 60)
        await _wait_status(stack.queue, first.id, JobStatus.COMPLETED.value)

        stack.done.clear()
        second = await stack.queue.enqueue(5, _candidate(external_id="track-2",
                                                         title="Second Song"))
        await asyncio.wait_for(stack.done.wait(), 60)
        await _wait_status(stack.queue, second.id, JobStatus.COMPLETED.value)

        assert stack.outcomes[-1].ok is True
        assert stack.bot.calls[-1][0] == "audio"
        assert stack.bot.calls[-1][1]["title"] == "Second Song"


class TestQueueActiveLookup:
    def test_find_active_returns_only_live_job_of_same_user_and_track(self):
        queue = queue_module.DownloadQueue(manager=SimpleNamespace(),
                                           settings=MusicSettings(workers=1))
        candidate = _candidate()
        running = queue_module.QueueJob(id="a", user_id=1, candidate=candidate)
        running.status = JobStatus.RUNNING.value
        other_user = queue_module.QueueJob(id="b", user_id=2, candidate=candidate)
        other_track = queue_module.QueueJob(id="c", user_id=1,
                                            candidate=_candidate(external_id="x"))
        finished = queue_module.QueueJob(id="d", user_id=1, candidate=candidate)
        finished.status = JobStatus.COMPLETED.value
        queue._jobs.update({job.id: job for job in
                            (running, other_user, other_track, finished)})

        found = queue.find_active(1, candidate)
        assert found is running
        assert queue.find_active(3, candidate) is None
        assert queue.find_active(1, _candidate(external_id="missing")) is None
