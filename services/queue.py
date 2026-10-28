"""Фоновая очередь загрузок (асинхронные воркеры): поиск → скачивание → теги."""
import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

from services.providers.base import TrackMeta
from services import downloader, tags

log = logging.getLogger(__name__)
CONCURRENCY = 2

ProgressSync = Callable[[float], None]

@dataclass
class Job:
    meta: TrackMeta
    quality: str = "aac320"
    on_done: Callable[..., Awaitable[None]] | None = None       # DownloadResult
    on_error: Callable[[str], Awaitable[None]] | None = None
    on_progress: Callable[[float], Awaitable[None]] | None = None

class DownloadQueue:
    def __init__(self):
        self.queue: asyncio.Queue[Job] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []

    async def start(self, n: int = CONCURRENCY) -> None:
        if self._workers:
            return
        self._workers = [asyncio.create_task(self._worker()) for _ in range(n)]

    async def stop(self) -> None:
        for w in self._workers:
            w.cancel()
        self._workers = []

    def enqueue(self, job: Job) -> None:
        self.queue.put_nowait(job)

    async def run_now(self, job: Job):
        """Выполнить не через очередь, а сразу (для интерактивных запросов)."""
        await self.start()
        return await self._process(job)

    async def _worker(self) -> None:
        while True:
            job = await self.queue.get()
            try:
                await self._process(job)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("download failed: %s", e)
                if job.on_error:
                    try:
                        await job.on_error(str(e))
                    except Exception:  # noqa: BLE001
                        pass
            finally:
                self.queue.task_done()

    async def _process(self, job: Job) -> "downloader.DownloadResult":
        meta, quality = job.meta, job.quality
        _loop = asyncio.get_running_loop()
        last_v = {"p": 0.0}

        def step(p: float) -> None:
            if p - last_v["p"] >= 2.0 or p >= 100.0:
                last_v["p"] = p
                if job.on_progress:
                    _loop.create_task(_call(job.on_progress, p))

        res = None
        if meta.source == "zvuk" and quality == "flac":
            from services.providers import zvuk as zv
            url = await zv.stream_url(meta.external_id)
            if url:
                res = await downloader.download_direct(url, meta, quality, on_progress=step)
        if res is None:
            res = await downloader.download_track(meta, quality, on_progress=step)
        await tags.tag_file(res.path, meta)
        if job.on_done:
            try:
                await job.on_done(res)
            except Exception:  # noqa: BLE001
                pass
        return res

async def _call(fn: Callable[..., Awaitable[None]], *args) -> None:
    try:
        await fn(*args)
    except Exception:  # noqa: BLE001
        pass

queue = DownloadQueue()
