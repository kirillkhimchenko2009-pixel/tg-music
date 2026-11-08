"""Health check музыкальной подсистемы: БД, FFmpeg, диск, очередь, провайдеры."""
from __future__ import annotations

import asyncio
import logging
import shutil
import time
from pathlib import Path

from .config import music_settings
from .db import db_healthy
from .domain import HealthReport
from .downloader.queue import queue
from .ffmpeg import ffmpeg_path, ffprobe_path
from .providers.registry import registry

log = logging.getLogger("music.health")


def _tool_ok(path: str) -> bool:
    try:
        return bool(shutil.which(path) or Path(path).exists())
    except OSError:
        return False


def _disk_free_mb() -> int:
    try:
        return int(shutil.disk_usage(music_settings.data_dir).free / (1024 * 1024))
    except OSError:
        return 0


async def collect_health(*, deep: bool = False, timeout: float = 8.0) -> HealthReport:
    """`deep=True` дополнительно пингует внешние API (медленно)."""
    checked = time.time()
    database = "ok" if await db_healthy() else "down"
    ffmpeg = "ok" if _tool_ok(ffmpeg_path()) and _tool_ok(ffprobe_path()) else "missing"
    providers: dict[str, dict] = {}
    if deep:
        try:
            providers = await asyncio.wait_for(registry.health_check(timeout=timeout),
                                               timeout=timeout + 2)
        except Exception as exc:  # noqa: BLE001
            log.info("health providers: %s", exc)
            providers = registry.snapshot()
    else:
        providers = registry.snapshot()

    report = HealthReport(
        ok=(database == "ok" and ffmpeg == "ok"),
        checked_at=checked,
        database=database,
        ffmpeg=ffmpeg,
        disk_free_mb=_disk_free_mb(),
        queue=queue.stats(),
        providers=providers,
    )
    return report


async def health_dict(*, deep: bool = False) -> dict:
    return (await collect_health(deep=deep)).as_dict()


__all__ = ["collect_health", "health_dict"]
