"""Скачивание итогового трека с выбранного источника (с приоритетом качества)."""
import asyncio
import logging

from core.config import settings
from services import downloader
from services.providers import yandex as ymx
from services.providers import zvuk as zv
from services.providers import soundcloud as scc
from services.providers import zaycev as zay
from services.providers import hitmoz as hzm
from services.providers import hotplayer as hpl
from services.providers import mp3party as mpp
from services.providers import promodj as pdj
from services.providers import audius as aud
from services.providers import ccmixter as ccm
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
    "mp3party": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0",
        "Referer": "https://mp3party.net/",
    },
    "promodj": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0",
        "Referer": "https://promodj.com/",
    },
    "audius": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0",
    },
    "ccmixter": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0",
        "Referer": "https://ccmixter.org/",
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
            log.info("скачивание %s сорвалось (%s: %s) — ретрай через 2 с",
                     dl_meta.display(), type(e1).__name__, str(e1)[:180])
            await asyncio.sleep(2)
            try:
                return await _download_inner(dl_meta, quality, token, on_progress)
            except Exception:
                return None

async def _download_inner(dl_meta, quality, token, on_progress):
    if dl_meta.source in ("zaycev", "hitmoz", "hotplayer", "mp3party"):
        prov = {"zaycev": zay, "hitmoz": hzm, "hotplayer": hpl,
                "mp3party": mpp}[dl_meta.source]
        try:
            url = await prov.download_url(dl_meta)
        except Exception:
            url = None
        if url:
            return await downloader.download_direct(
                url, dl_meta, "aac320", on_progress=on_progress,
                headers=SOURCE_HEADERS[dl_meta.source], use_proxy=False)
        return None  # не переводим на yt-dlp: id не yt-совместим
    if dl_meta.source == "promodj":
        # официальная ссылка скачивания конкретной композиции (per-track):
        # download_url() сам вернёт None, если у трека её нет (downloadable=False)
        try:
            url = await pdj.download_url(dl_meta)
        except Exception:
            url = None
        if url:
            return await downloader.download_direct(
                url, dl_meta, "aac320", on_progress=on_progress,
                headers=SOURCE_HEADERS["promodj"], use_proxy=False)
        return None
    if dl_meta.source == "audius":
        # только официальные эндпоинты: download (если негейтован), иначе
        # публичный stream — оба mp3; платные (usdc) не обходим
        url = None
        try:
            if dl_meta.downloadable:
                url = await aud.download_url(dl_meta)
        except Exception:
            url = None
        if not url:
            try:
                url = await aud.stream_url(dl_meta)
            except Exception:
                url = None
        if url:
            return await downloader.download_direct(
                url, dl_meta, "aac320", on_progress=on_progress,
                headers=SOURCE_HEADERS["audius"])
        return None
    if dl_meta.source == "ccmixter":
        # CC-материал: API отдаёт прямую ссылку mp3 (+ лицензия в meta)
        try:
            url = await ccm.download_url(dl_meta)
        except Exception:
            url = None
        if url:
            return await downloader.download_direct(
                url, dl_meta, "aac320", on_progress=on_progress,
                headers=SOURCE_HEADERS["ccmixter"], use_proxy=False)
        return None
    if dl_meta.source == "zvuk":
        try:
            url = await zv.stream_url(dl_meta.external_id, token or None)
        except Exception as e:
            log.info("zvuk stream_url %s fail: %s: %s",
                     dl_meta.external_id, type(e).__name__, str(e)[:120])
            url = None
        if url:
            is_flac = ".flac" in (url or "").lower() or "flac" in (url or "").lower()
            q = "flac" if (quality == "flac" and is_flac) else ("aac320" if not is_flac else "flac")
            return await downloader.download_direct(
                url, dl_meta, q, on_progress=on_progress,
                headers=SOURCE_HEADERS["zvuk"])
        # нет стрима — yt-dlp с чужеродным zvuk-id тут бесполезен, идём к следующему
        # кандидату цепочки
        log.info("zvuk %s: стрима нет — следующий кандидат", dl_meta.external_id)
        return None
    if dl_meta.source == "yandex":
        prov = ymx.YandexProvider(token)
        try:
            url = await prov.track_url(dl_meta, quality)
        except Exception as e:
            log.info("yandex track_url %s fail: %s: %s",
                     dl_meta.external_id, type(e).__name__, str(e)[:120])
            url = None
        if url:
            is_flac = url.lower().endswith(".flac") or "flac" in url.lower()
            return await downloader.download_direct(
                url, dl_meta, "flac" if is_flac else "aac320",
                on_progress=on_progress, headers=SOURCE_HEADERS["yandex"])
        # ссылки нет — yt-dlp с яндекс-id бесполезен; дальше работает цепочка
        log.info("yandex %s: ссылки нет — следующий кандидат", dl_meta.external_id)
        return None
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
    if dl_meta.source == "soundcloud":
        # yt-dlp у SC на датацентровых IP регулярно падает — берём HLS-плейлист
        # и транскодим ffmpeg'ом (тот же путь, что у стрим-роута)
        try:
            playlist = await scc.hls_stream(dl_meta.external_id)
        except Exception:
            playlist = None
        if playlist:
            res = await downloader.download_ffmpeg_hls(
                playlist, dl_meta, on_progress=on_progress)
            if res:
                return res
    return await downloader.download_track(dl_meta, quality, on_progress=on_progress)
