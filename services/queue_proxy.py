"""Шорткаты для очереди загрузок."""
from typing import Awaitable, Callable

from services.providers.base import TrackMeta
from services.queue import Job

def make_job(meta: TrackMeta, quality: str = "aac320",
             on_progress: Callable[[float], Awaitable[None]] | None = None) -> Job:
    return Job(meta=meta, quality=quality, on_progress=on_progress)
