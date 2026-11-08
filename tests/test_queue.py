"""Очередь загрузок: лимиты, отмена, воркеры, восстановление и статистика."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import importlib

from music.config import MusicSettings
from music.domain import DownloadOutcome, TrackCandidate
from music.enums import JobStatus
from music.errors import DuplicateJobError, ProviderUnavailable, UserLimitError
from music.models import MusicDownloadHistory, MusicDownloadJob, utcnow
from music import repository

queue_module = importlib.import_module("music.downloader.queue")
DownloadQueue = queue_module.DownloadQueue
QueueJob = queue_module.QueueJob


def _candidate(**kwargs) -> TrackCandidate:
    base = dict(provider="jamendo", external_id="1", title="Song",
                artist="Artist", duration=200)
    base.update(kwargs)
    return TrackCandidate(**base)


def _settings(**kwargs) -> MusicSettings:
    base = dict(workers=1, user_active_jobs=2, user_daily_jobs=0,
                max_attempts=1, retry_backoff=0.0)
    base.update(kwargs)
    return MusicSettings(**base)


def _provider(enabled: bool = True):
    return SimpleNamespace(name="jamendo", enabled=enabled, display_name="Jamendo")


@pytest.fixture
def prepared_queue(monkeypatch, db_maker, temp_music_dir):
    """Очередь с тестовой БД, fake-провайдером и подменяемым исполнителем."""
    monkeypatch.setattr(queue_module, "AsyncSessionLocal", db_maker)
    monkeypatch.setattr(queue_module, "get_provider",
                        lambda name: _provider(True))
    monkeypatch.setattr(queue_module, "cleanup_temp", lambda *a, **k: 0)

    async def fake_execute(job, manager, settings):
        outcome = DownloadOutcome(ok=True, job_id=job.id)
        job.result = outcome
        if job.on_done is not None:
            await job.on_done(outcome)
        return outcome

    monkeypatch.setattr(queue_module, "execute_job", fake_execute)
    return DownloadQueue(manager=SimpleNamespace(), settings=_settings())


class TestEnqueueAndLimits:
    async def test_enqueue_creates_job_and_db_row(self, prepared_queue, db_maker):
        job = await prepared_queue.enqueue(1, _candidate())
        assert isinstance(job, QueueJob)
        assert job.status == JobStatus.QUEUED.value
        assert prepared_queue.status(job.id)["title"] == "Song"
        async with db_maker() as session:
            row = await session.get(MusicDownloadJob, job.id)
            assert row is not None
            assert row.user_id == 1
            assert row.status == JobStatus.QUEUED.value

    async def test_unknown_provider_is_rejected(self, monkeypatch, db_maker,
                                                temp_music_dir):
        monkeypatch.setattr(queue_module, "AsyncSessionLocal", db_maker)
        monkeypatch.setattr(queue_module, "get_provider", lambda name: None)
        queue = DownloadQueue(manager=SimpleNamespace(), settings=_settings())
        with pytest.raises(ProviderUnavailable):
            await queue.enqueue(1, _candidate())

    async def test_disabled_provider_is_rejected(self, monkeypatch, db_maker,
                                                 temp_music_dir):
        monkeypatch.setattr(queue_module, "AsyncSessionLocal", db_maker)
        monkeypatch.setattr(queue_module, "get_provider",
                            lambda name: _provider(False))
        queue = DownloadQueue(manager=SimpleNamespace(), settings=_settings())
        with pytest.raises(ProviderUnavailable):
            await queue.enqueue(1, _candidate())

    async def test_active_jobs_limit(self, monkeypatch, db_maker, temp_music_dir):
        monkeypatch.setattr(queue_module, "AsyncSessionLocal", db_maker)
        monkeypatch.setattr(queue_module, "get_provider",
                            lambda name: _provider(True))
        queue = DownloadQueue(manager=SimpleNamespace(),
                              settings=_settings(user_active_jobs=1))
        await queue.enqueue(1, _candidate())
        with pytest.raises(UserLimitError) as exc:
            await queue.enqueue(1, _candidate(external_id="2"))
        assert exc.value.code == "USER_ACTIVE_LIMIT"

    async def test_daily_jobs_limit(self, monkeypatch, db_maker, temp_music_dir):
        monkeypatch.setattr(queue_module, "AsyncSessionLocal", db_maker)
        monkeypatch.setattr(queue_module, "get_provider",
                            lambda name: _provider(True))
        async with db_maker() as session:
            session.add(MusicDownloadHistory(user_id=2, downloaded_at=utcnow()))
            await session.commit()
        queue = DownloadQueue(manager=SimpleNamespace(),
                              settings=_settings(user_daily_jobs=1))
        with pytest.raises(UserLimitError) as exc:
            await queue.enqueue(2, _candidate())
        assert exc.value.code == "USER_DAILY_LIMIT"

    async def test_daily_limit_zero_disables_check(self, monkeypatch, db_maker,
                                                   temp_music_dir):
        monkeypatch.setattr(queue_module, "AsyncSessionLocal", db_maker)
        monkeypatch.setattr(queue_module, "get_provider",
                            lambda name: _provider(True))
        queue = DownloadQueue(manager=SimpleNamespace(),
                              settings=_settings(user_daily_jobs=0))
        job = await queue.enqueue(3, _candidate())
        assert job.status == JobStatus.QUEUED.value

    async def test_user_guard_on_status_and_get(self, prepared_queue):
        job = await prepared_queue.enqueue(10, _candidate())
        assert prepared_queue.status(job.id, user_id=10) is not None
        assert prepared_queue.status(job.id, user_id=11) is None
        assert prepared_queue.get(job.id, user_id=10) is job
        assert prepared_queue.get(job.id, user_id=11) is None
        assert prepared_queue.status("missing") is None


class TestDuplicateEnqueue:
    async def test_second_enqueue_of_same_track_is_rejected(self, prepared_queue):
        job = await prepared_queue.enqueue(50, _candidate())
        with pytest.raises(DuplicateJobError) as exc:
            await prepared_queue.enqueue(50, _candidate())
        assert exc.value.job_id == job.id
        assert exc.value.code == "DUPLICATE_JOB"

    async def test_other_user_can_enqueue_same_track(self, prepared_queue):
        first = await prepared_queue.enqueue(51, _candidate())
        second = await prepared_queue.enqueue(52, _candidate())
        assert second.id != first.id

    async def test_different_external_id_is_not_duplicate(self, prepared_queue):
        first = await prepared_queue.enqueue(53, _candidate())
        second = await prepared_queue.enqueue(53, _candidate(external_id="2"))
        assert second.id != first.id

    async def test_enqueue_allowed_after_job_finished(self, prepared_queue):
        job = await prepared_queue.enqueue(54, _candidate())
        assert await prepared_queue.cancel(job.id, user_id=54) is True
        again = await prepared_queue.enqueue(54, _candidate())
        assert again.id != job.id

    async def test_concurrent_enqueue_creates_only_one_job(self, prepared_queue):
        results = await asyncio.gather(
            prepared_queue.enqueue(55, _candidate()),
            prepared_queue.enqueue(55, _candidate()),
            return_exceptions=True)
        jobs = [item for item in results if isinstance(item, QueueJob)]
        errors = [item for item in results
                  if isinstance(item, DuplicateJobError)]
        assert len(jobs) == 1
        assert len(errors) == 1
        assert errors[0].job_id == jobs[0].id


class TestCancel:
    async def test_cancel_queued_job(self, prepared_queue, db_maker):
        job = await prepared_queue.enqueue(20, _candidate())
        assert await prepared_queue.cancel(job.id, user_id=20) is True
        assert prepared_queue.status(job.id)["status"] == JobStatus.CANCELLED.value
        assert await prepared_queue.cancel(job.id, user_id=20) is False
        async with db_maker() as session:
            row = await session.get(MusicDownloadJob, job.id)
            assert row.status == JobStatus.CANCELLED.value
            assert row.finished_at is not None

    async def test_cancel_rejects_wrong_user(self, prepared_queue):
        job = await prepared_queue.enqueue(21, _candidate())
        assert await prepared_queue.cancel(job.id, user_id=22) is False
        assert prepared_queue.status(job.id)["status"] == JobStatus.QUEUED.value

    async def test_cancel_unknown_job(self, prepared_queue):
        assert await prepared_queue.cancel("missing") is False

    async def test_cancel_running_job(self, monkeypatch, db_maker, temp_music_dir):
        monkeypatch.setattr(queue_module, "AsyncSessionLocal", db_maker)
        monkeypatch.setattr(queue_module, "get_provider",
                            lambda name: _provider(True))
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def slow_execute(job, manager, settings):
            started.set()
            try:
                await asyncio.sleep(30)
            finally:
                pass
            return DownloadOutcome(ok=True, job_id=job.id)

        async def on_done(outcome):
            if outcome.error_code == "CANCELLED":
                cancelled.set()

        monkeypatch.setattr(queue_module, "execute_job", slow_execute)
        queue = DownloadQueue(manager=SimpleNamespace(), settings=_settings())
        await queue.start()
        try:
            job = await queue.enqueue(23, _candidate(), on_done=on_done)
            await asyncio.wait_for(started.wait(), 2)
            assert await queue.cancel(job.id, user_id=23) is True
            await asyncio.wait_for(cancelled.wait(), 2)
            assert queue.status(job.id)["status"] == JobStatus.CANCELLED.value
        finally:
            await queue.stop()


class TestWorker:
    async def test_worker_processes_job(self, prepared_queue):
        done = asyncio.Event()
        outcomes: list[DownloadOutcome] = []

        async def on_done(outcome):
            outcomes.append(outcome)
            done.set()

        await prepared_queue.start()
        try:
            job = await prepared_queue.enqueue(30, _candidate(), on_done=on_done)
            await asyncio.wait_for(done.wait(), 2)
            for _ in range(100):
                if prepared_queue.status(job.id)["status"] == JobStatus.COMPLETED.value:
                    break
                await asyncio.sleep(0.01)
            assert prepared_queue.status(job.id)["status"] == JobStatus.COMPLETED.value
            assert outcomes and outcomes[0].ok is True
        finally:
            await prepared_queue.stop()

    async def test_worker_marks_failure(self, monkeypatch, db_maker, temp_music_dir):
        monkeypatch.setattr(queue_module, "AsyncSessionLocal", db_maker)
        monkeypatch.setattr(queue_module, "get_provider",
                            lambda name: _provider(True))

        async def failing_execute(job, manager, settings):
            outcome = DownloadOutcome(ok=False, job_id=job.id,
                                      error_code="DAMAGED", error="broken",
                                      retryable=False)
            job.result = outcome
            job.error = outcome.error
            if job.on_done is not None:
                result = job.on_done(outcome)
                if hasattr(result, "__await__"):
                    await result
            return outcome

        monkeypatch.setattr(queue_module, "execute_job", failing_execute)
        queue = DownloadQueue(manager=SimpleNamespace(), settings=_settings())
        done = asyncio.Event()
        await queue.start()
        try:
            job = await queue.enqueue(31, _candidate(),
                                      on_done=lambda outcome: done.set())
            await asyncio.wait_for(done.wait(), 2)
            for _ in range(100):
                if queue.status(job.id)["status"] == JobStatus.FAILED.value:
                    break
                await asyncio.sleep(0.01)
            assert queue.status(job.id)["status"] == JobStatus.FAILED.value
            assert queue.status(job.id)["error"] == "broken"
        finally:
            await queue.stop()

    async def test_recover_stale_jobs(self, prepared_queue, db_maker):
        async with db_maker() as session:
            await repository.record_job(session, "stale-1", 40, _candidate(),
                                        status=JobStatus.QUEUED)
            await repository.record_job(session, "stale-2", 40, _candidate(),
                                        status=JobStatus.RUNNING)
            await repository.record_job(session, "done-1", 40, _candidate(),
                                        status=JobStatus.COMPLETED)
            await session.commit()
        assert await prepared_queue.recover_stale() == 2
        async with db_maker() as session:
            first = await session.get(MusicDownloadJob, "stale-1")
            second = await session.get(MusicDownloadJob, "stale-2")
            done = await session.get(MusicDownloadJob, "done-1")
            assert first.status == JobStatus.FAILED.value
            assert second.status == JobStatus.FAILED.value
            assert done.status == JobStatus.COMPLETED.value


class TestStats:
    def test_stats_and_job_dict(self):
        queue = DownloadQueue(manager=SimpleNamespace(), settings=_settings())
        stats = queue.stats()
        assert stats["queued"] == 0
        assert stats["active"] == 0
        assert stats["workers"] == 0
        assert stats["closing"] is False
        assert stats["jobs"] == 0

        job = QueueJob(id="abc", user_id=1, candidate=_candidate())
        data = job.as_dict()
        assert data["id"] == "abc"
        assert data["provider"] == "jamendo"
        assert data["title"] == "Song"
