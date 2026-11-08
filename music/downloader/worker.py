"""Исполнение одной задачи очереди: статусы, попытки, таймауты, отмена."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..config import MusicSettings, music_settings
from ..db import AsyncSessionLocal
from ..domain import DownloadOutcome
from ..enums import JobStatus
from ..models import MusicDownloadJob, utcnow
from ..repository import update_job

log = logging.getLogger("music.worker")


async def execute_job(job: Any, manager, settings: MusicSettings | None = None) -> DownloadOutcome:
    settings = settings or music_settings
    last: DownloadOutcome | None = None
    for attempt in range(1, settings.max_attempts + 1):
        if job.cancel.is_set():
            return await _finish(job, DownloadOutcome(
                ok=False, job_id=job.id, error="Отменено пользователем",
                error_code="CANCELLED"), JobStatus.CANCELLED)
        job.attempts = attempt
        await _mark(job.id, JobStatus.RUNNING, attempts=attempt,
                    started_at=utcnow(), error=None)
        try:
            async with AsyncSessionLocal() as session:
                outcome = await asyncio.wait_for(
                    manager.prepare(
                        job.id, job.user_id, job.candidate,
                        provider=job.provider, session=session,
                        progress=job.on_progress, cancel=job.cancel),
                    timeout=settings.job_timeout)
        except asyncio.TimeoutError:
            outcome = DownloadOutcome(
                ok=False, job_id=job.id, error_code="TIMEOUT",
                error="Превышено время обработки задачи", retryable=True)
        except asyncio.CancelledError:
            if job.cancel.is_set():
                outcome = DownloadOutcome(
                    ok=False, job_id=job.id, error="Отменено пользователем",
                    error_code="CANCELLED")
            else:
                raise
        except Exception as exc:  # noqa: BLE001
            log.exception("job %s: сбой воркера", job.id)
            outcome = DownloadOutcome(
                ok=False, job_id=job.id, error_code="WORKER_ERROR",
                error=f"{type(exc).__name__}: {exc}", retryable=True)
        last = outcome
        if outcome.ok:
            return await _finish(job, outcome, JobStatus.COMPLETED)
        if outcome.error_code == "CANCELLED" or job.cancel.is_set():
            return await _finish(job, outcome, JobStatus.CANCELLED)
        if outcome.retryable and attempt < settings.max_attempts:
            await _mark(job.id, JobStatus.QUEUED, error=outcome.error)
            await asyncio.sleep(settings.retry_backoff * attempt)
            continue
        return await _finish(job, outcome, JobStatus.FAILED)
    return await _finish(job, last or DownloadOutcome(
        ok=False, job_id=job.id, error="Не удалось выполнить задачу"),
        JobStatus.FAILED)


async def _mark(job_id: str, status: JobStatus, **fields) -> None:
    try:
        async with AsyncSessionLocal() as session:
            await update_job(session, job_id, status=status, **fields)
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("job %s: не удалось обновить статус: %s", job_id, exc)


async def _finish(job: Any, outcome: DownloadOutcome, status: JobStatus) -> DownloadOutcome:
    job.result = outcome
    job.error = outcome.error
    progress = 100 if outcome.ok else 0
    await _mark(job.id, status, error=outcome.error or None,
                progress=progress,
                permission=(outcome.permission.status.value
                            if outcome.permission else "LICENSE_UNKNOWN"),
                finished_at=utcnow())
    try:
        if job.on_done is not None:
            await job.on_done(outcome)
    except Exception as exc:  # noqa: BLE001
        log.warning("job %s: on_done: %s", job.id, exc)
    return outcome


__all__ = ["execute_job"]
