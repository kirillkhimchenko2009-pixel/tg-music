"""Асинхронная очередь загрузок: уникальные job id, лимиты, отмена, ретраи."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy import update

from ..cache.music_cache import cleanup_cache
from ..config import MusicSettings, music_settings
from ..db import AsyncSessionLocal
from ..domain import DownloadOutcome, TrackCandidate
from ..enums import JobStatus
from ..errors import (DuplicateJobError, ProviderUnavailable, QueueFullError,
                      UserLimitError)
from ..models import MusicDownloadJob, utcnow
from ..providers import get_provider
from ..repository import count_active_jobs, count_daily_downloads, record_job, update_job
from ..storage import cleanup_temp
from .manager import DownloadManager, manager as default_manager
from .worker import execute_job

log = logging.getLogger("music.queue")

ProgressCB = Callable[[str, int, str], Any]
DoneCB = Callable[[DownloadOutcome], Any]


@dataclass
class QueueJob:
    id: str
    user_id: int
    candidate: TrackCandidate
    provider: Any = None
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    status: str = JobStatus.QUEUED.value
    attempts: int = 0
    progress: int = 0
    step: str = "queued"
    error: str = ""
    result: DownloadOutcome | None = None
    created_at: float = field(default_factory=time.time)
    on_progress: ProgressCB | None = None
    on_done: DoneCB | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "user_id": self.user_id, "status": self.status,
            "attempts": self.attempts, "progress": self.progress,
            "step": self.step, "error": self.error,
            "provider": self.candidate.provider,
            "title": self.candidate.title, "artist": self.candidate.artist,
        }


class DownloadQueue:
    def __init__(self, manager: DownloadManager | None = None,
                 settings: MusicSettings | None = None, maxsize: int = 200):
        self.manager = manager or default_manager
        self.settings = settings or music_settings
        self._queue: asyncio.Queue[QueueJob] = asyncio.Queue(maxsize=maxsize)
        self._jobs: dict[str, QueueJob] = {}
        self._running: dict[str, asyncio.Task] = {}
        self._workers: list[asyncio.Task] = []
        self._maintenance: asyncio.Task | None = None
        self._enqueue_lock = asyncio.Lock()
        self._closing = False
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._closing = False
        await self.recover_stale()
        self._workers = [asyncio.create_task(self._worker(i), name=f"music-worker-{i}")
                         for i in range(self.settings.workers)]
        self._maintenance = asyncio.create_task(self._maintenance_loop(),
                                                name="music-maintenance")
        log.info("music queue: %d воркеров запущено", len(self._workers))

    async def stop(self) -> None:
        self._closing = True
        if self._maintenance:
            self._maintenance.cancel()
        for task in self._workers:
            task.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        self._running.clear()
        self._started = False

    async def enqueue(self, user_id: int, candidate: TrackCandidate, *,
                      provider_name: str | None = None,
                      on_progress: ProgressCB | None = None,
                      on_done: DoneCB | None = None,
                      payload: dict | None = None) -> QueueJob:
        provider = get_provider(provider_name or candidate.provider)
        if provider is None or not provider.enabled:
            raise ProviderUnavailable(
                f"Источник {provider_name or candidate.provider} недоступен",
                code="PROVIDER_DISABLED")

        # Проверка + вставка под одним lock: два одновременных нажатия не
        # создадут две одинаковые задачи (find_active снаружи — только UX).
        async with self._enqueue_lock:
            existing = self.find_active(user_id, candidate)
            if existing is not None:
                raise DuplicateJobError(
                    f"Загрузка уже выполняется: {candidate.display}",
                    job_id=existing.id)

            async with AsyncSessionLocal() as session:
                active = await count_active_jobs(session, user_id)
                if active >= self.settings.user_active_jobs:
                    raise UserLimitError(
                        f"У тебя уже {active} активных загрузок. Дождись завершения "
                        f"или отмени задачу.", code="USER_ACTIVE_LIMIT")
                if self.settings.user_daily_jobs:
                    daily = await count_daily_downloads(session, user_id)
                    tier_limit = None
                    try:
                        # тарифный лимит: 25 бесплатно / 1000 Lite / безлимит Pro
                        from bot.runtime import get_session as _core_session
                        from services import billing as _billing
                        async with _core_session() as _cs:
                            tier_limit = await _billing.download_limit(_cs, user_id)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("job: тарифный лимит недоступен (%r) — базовый", exc)
                        tier_limit = self.settings.user_daily_jobs
                    if tier_limit is not None:
                        tier_limit = min(tier_limit, self.settings.user_daily_jobs) \
                            if self.settings.user_daily_jobs else tier_limit
                    if tier_limit is not None and daily >= tier_limit:
                        raise UserLimitError(
                            f"Дневной лимит: {tier_limit} треков в чат. "
                            f"Vibe Lite — 1000/день, Pro — безлимит (раздел Vibe Pro).",
                            code="USER_DAILY_LIMIT")
                job_id = str(uuid.uuid4())
                await record_job(session, job_id, user_id, candidate,
                                 status=JobStatus.QUEUED, payload=payload)
                await session.commit()

            job = QueueJob(id=job_id, user_id=int(user_id), candidate=candidate,
                           provider=provider)
            if on_progress is not None:
                async def wrapped(step: str, percent: int, detail: str = "") -> None:
                    job.step = step
                    job.progress = max(0, min(100, int(percent)))
                    try:
                        result = on_progress(step, percent, detail)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as exc:  # noqa: BLE001
                        log.debug("job %s: progress: %s", job.id, exc)
                job.on_progress = wrapped
            job.on_done = on_done
            self._jobs[job.id] = job
            try:
                self._queue.put_nowait(job)
            except asyncio.QueueFull as exc:
                job.status = JobStatus.FAILED.value
                async with AsyncSessionLocal() as session:
                    await update_job(session, job.id, status=JobStatus.FAILED,
                                     error="Очередь переполнена", finished_at=utcnow())
                    await session.commit()
                raise QueueFullError("Очередь загрузок переполнена") from exc
            return job

    async def cancel(self, job_id: str, user_id: int | None = None) -> bool:
        job = self._jobs.get(job_id)
        if not job or (user_id is not None and job.user_id != int(user_id)):
            return False
        if job.status in (JobStatus.COMPLETED.value, JobStatus.FAILED.value,
                          JobStatus.CANCELLED.value):
            return False
        job.cancel.set()
        job.status = JobStatus.CANCELLED.value
        task = self._running.get(job_id)
        if task is not None and not task.done():
            task.cancel()
        async with AsyncSessionLocal() as session:
            await update_job(session, job_id, status=JobStatus.CANCELLED,
                             error="Отменено пользователем", finished_at=utcnow())
            await session.commit()
        log.info("job %s: отменена пользователем %s", job_id, user_id)
        return True

    def status(self, job_id: str, user_id: int | None = None) -> dict[str, Any] | None:
        job = self._jobs.get(job_id)
        if not job or (user_id is not None and job.user_id != int(user_id)):
            return None
        return job.as_dict()

    def get(self, job_id: str, user_id: int | None = None) -> QueueJob | None:
        job = self._jobs.get(job_id)
        if not job or (user_id is not None and job.user_id != int(user_id)):
            return None
        return job

    def find_active(self, user_id: int, candidate: TrackCandidate) -> QueueJob | None:
        """Живая задача того же пользователя и трека.

        Нужна для защиты от двойного нажатия: повторный клик не должен
        скачивать и отправлять один и тот же файл дважды.
        """
        key = candidate.dedupe_key
        for job in self._jobs.values():
            if job.user_id != int(user_id):
                continue
            if job.status not in (JobStatus.QUEUED.value, JobStatus.RUNNING.value):
                continue
            if job.candidate.dedupe_key == key:
                return job
        return None

    def stats(self) -> dict[str, Any]:
        return {"queued": self._queue.qsize(), "active": len(self._running),
                "workers": len(self._workers), "jobs": len(self._jobs),
                "closing": self._closing}

    async def recover_stale(self) -> int:
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    update(MusicDownloadJob)
                    .where(MusicDownloadJob.status.in_([JobStatus.QUEUED.value,
                                                        JobStatus.RUNNING.value]))
                    .values(status=JobStatus.FAILED.value,
                            error="Прервано перезапуском бота",
                            finished_at=utcnow()))
                await session.commit()
                count = result.rowcount or 0
                if count:
                    log.info("music queue: восстановлено %d прерванных задач", count)
                return count
        except Exception as exc:  # noqa: BLE001
            log.warning("recover_stale: %s", exc)
            return 0

    async def _worker(self, index: int) -> None:
        while not self._closing:
            try:
                job = await self._queue.get()
            except asyncio.CancelledError:
                return
            task = asyncio.create_task(
                self._process_job(job, index), name=f"music-job-{job.id[:8]}")
            self._running[job.id] = task
            try:
                # shield: отмена пользователем гасит task задачи, а не воркер
                await asyncio.shield(task)
            except asyncio.CancelledError:
                if self._closing:
                    task.cancel()
                    try:
                        await task
                    except BaseException:  # noqa: BLE001
                        pass
                    raise
                # задача отменена пользователем — даём ей завершить cleanup
                try:
                    await task
                except BaseException:  # noqa: BLE001
                    pass
            except Exception:  # noqa: BLE001
                pass
            finally:
                self._running.pop(job.id, None)
                try:
                    self._queue.task_done()
                except ValueError:
                    pass

    async def _process_job(self, job: QueueJob, index: int) -> None:
        try:
            if job.cancel.is_set():
                job.status = JobStatus.CANCELLED.value
                await self._notify_cancelled(job)
                return
            job.status = JobStatus.RUNNING.value
            outcome = await execute_job(job, self.manager, self.settings)
            if outcome.ok:
                job.status = JobStatus.COMPLETED.value
                job.progress = 100
            elif outcome.error_code == "CANCELLED":
                job.status = JobStatus.CANCELLED.value
            else:
                job.status = JobStatus.FAILED.value
            job.error = outcome.error
        except asyncio.CancelledError:
            job.cancel.set()
            job.status = JobStatus.CANCELLED.value
            await self._notify_cancelled(job)
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("worker %d: job %s", index, job.id)
            job.status = JobStatus.FAILED.value
            job.error = f"{type(exc).__name__}: {exc}"
            try:
                async with AsyncSessionLocal() as session:
                    await update_job(session, job.id, status=JobStatus.FAILED,
                                     error=job.error, finished_at=utcnow())
                    await session.commit()
            except Exception:  # noqa: BLE001
                pass

    async def _notify_cancelled(self, job: QueueJob) -> None:
        outcome = DownloadOutcome(ok=False, job_id=job.id,
                                  error="Отменено пользователем",
                                  error_code="CANCELLED")
        job.result = outcome
        try:
            if job.on_done is not None:
                await job.on_done(outcome)
        except Exception as exc:  # noqa: BLE001
            log.debug("cancel on_done: %s", exc)

    async def _maintenance_loop(self) -> None:
        while not self._closing:
            try:
                removed = cleanup_temp()
                if removed:
                    log.info("music: очищено %d временных объектов", removed)
                async with AsyncSessionLocal() as session:
                    await cleanup_cache(session)
                self._prune_jobs()
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001
                log.debug("maintenance: %s", exc)
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                return

    def _prune_jobs(self) -> None:
        cutoff = time.time() - 3600
        done = [job_id for job_id, job in self._jobs.items()
                if job.status in (JobStatus.COMPLETED.value, JobStatus.FAILED.value,
                                  JobStatus.CANCELLED.value)
                and job.created_at < cutoff]
        for job_id in done[:200]:
            self._jobs.pop(job_id, None)
        if len(self._jobs) > 1000:
            for job_id in list(self._jobs)[:len(self._jobs) - 1000]:
                if job_id not in self._running:
                    self._jobs.pop(job_id, None)


queue = DownloadQueue()

__all__ = ["DownloadQueue", "QueueJob", "queue", "ProgressCB", "DoneCB"]
