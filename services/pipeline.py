"""Скачивание итогового трека с выбранного источника (с приоритетом качества)."""
import asyncio
import logging

from core.config import settings
from services import downloader
from services.providers import yandex as ymx
from services.providers import zvuk as zv
from services.providers import zaycev as zay
from services.providers import hitmoz as hzm
from services.providers import hotplayer as hpl
from services.providers.base import TrackMeta

log = logging.getLogger("pipeline")

SEM_SIZE = 2
_sem = asyncio.Semaphore(SEM_SIZE)

SOURCE_HEADERS = {
    "yandex": {
        "User-Agent": "Yandex-Music-API",
        "Referer": "https://music.yandex.ru/",
        "X-Yandex-Music-Client": "YandexMusicAndroid/2023.12.1",
    },
    "zvuk": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://zvuk.com/",
    },
    "zaycev": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0",
        "Referer": "https://zaycev.net/",
    },
    "hitmoz": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0",
        "Referer": "https://ru.hitmoz.org/",
    },
    "hotplayer": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0",
        "Referer": "https://hotplayer.ru/",
    },
}

async def download(dl_meta: TrackMeta, quality: str, token: str = "",
                   on_progress=None) -> "downloader.DownloadResult | None":
    """Скачивает конкретный вариант трека. dl_meta.source определяет путь.
    Один ретрай на сетевой/временный сбой (yt-dlp и прокси иногда спотыкаются)."""
    async with _sem:
        try:
            return await _download_inner(dl_meta, quality, token, on_progress)
        except Exception as e1:
            log.info("скачивание %s сорвалось (%s) — ретрай через 2 с",
                     dl_meta.display(), type(e1).__name__)
            await asyncio.sleep(2)
            try:
                return await _download_inner(dl_meta, quality, token, on_progress)
            except Exception:
                return None

async def _download_inner(dl_meta, quality, token, on_progress):
    if dl_meta.source in ("zaycev", "hitmoz", "hotplayer"):
        prov = {"zaycev": zay, "hitmoz": hzm, "hotplayer": hpl}[dl_meta.source]
        try:
            url = await prov.download_url(dl_meta)
        except Exception:
            url = None
        if url:
            return await downloader.download_direct(
                url, dl_meta, "aac320", on_progress=on_progress,
                headers=SOURCE_HEADERS[dl_meta.source], use_proxy=False)
        return None  # не переводим на yt-dlp: id не yt-совместим
    if dl_meta.source == "zvuk":
        try:
            url = await zv.stream_url(dl_meta.external_id, token or None)
        except Exception:
            url = None
        if url:
            is_flac = ".flac" in (url or "").lower() or "flac" in (url or "").lower()
            q = "flac" if (quality == "flac" and is_flac) else ("aac320" if not is_flac else "flac")
            return await downloader.download_direct(
                url, dl_meta, q, on_progress=on_progress,
                headers=SOURCE_HEADERS["zvuk"])
        # нет стрима — фолбэк на yt-dlp ниже
    if dl_meta.source == "yandex":
        prov = ymx.YandexProvider(token)
        try:
            url = await prov.track_url(dl_meta, quality)
        except Exception:
            url = None
        if url:
            is_flac = url.lower().endswith(".flac") or "flac" in url.lower()
            return await downloader.download_direct(
                url, dl_meta, "flac" if is_flac else "aac320",
                on_progress=on_progress, headers=SOURCE_HEADERS["yandex"])
        # нет ссылки — yt-fallback ниже
    if dl_meta.source == "ytmusic":
        # InnerTube напрямую (без yt-dlp): быстрее и иногда проходит бот-чек
        try:
            res = await downloader.download_innertube(dl_meta, quality,
                                                      on_progress=on_progress)
        except Exception:
            res = None
        if res:
            return res
        # yt-dlp как запасной путь ниже
    return await downloader.download_track(dl_meta, quality, on_progress=on_progress)
