"""mp3party.net: поиск (структурированные data-js-атрибуты) + прямые mp3-ссылки.
CDN в некоторых сетях отвечает текстом «fail» вместо аудио (бэкенд file-info),
поэтому download_url() перед выдачей ссылки проверяет первые байты файла:
если не mp3 — возвращает None, и цепочка переходит к следующему источнику.
ВАЖНО (прод, Render/DE): RU-пиратки отдают 451 «Unavailable For Legal Reasons»
на запросы из ЕС — провайдер деградирует в [] без шума. Полезен только при
RU-хостинге бота (локальная отладка).
"""
import asyncio
import logging
import re

import httpx

from services.providers.base import TrackMeta

log = logging.getLogger("providers.mp3party")
_BASE = "https://mp3party.net"
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}

_TAG = re.compile(r'<[a-z]+[^>]*data-js-id="\d+"[^>]*>')


def _nz(s: str | None) -> str:
    from html import unescape
    return re.sub(r"\s+", " ", unescape(s or "")).strip()


def _search_sync(query: str, limit: int) -> list[TrackMeta]:
    c = httpx.Client(headers=_UA, timeout=20, follow_redirects=True)
    try:
        r = c.get(_BASE + "/search", params={"q": query})
        if r.status_code != 200:
            log.info("mp3party search %s: HTTP %s", query[:40], r.status_code)
            return []
        out: list[TrackMeta] = []
        for tag in _TAG.findall(r.text):
            a = dict(re.findall(r'(data-js-[\w-]+)="([^"]*)"', tag))
            iid = a.get("data-js-id") or ""
            title_n, artist_n = _nz(a.get("data-js-song-title")), \
                _nz(a.get("data-js-artist-name"))
            if not iid or not title_n:
                continue
            out.append(TrackMeta(source="mp3party", external_id=iid,
                                 title=title_n, artist=artist_n,
                                 duration=0, cover_url=None))
            if len(out) >= limit:
                break
        return out
    finally:
        c.close()


async def search(query: str, limit: int = 6) -> list[TrackMeta]:
    try:
        got = await asyncio.to_thread(_search_sync, query, limit)
    except Exception as e:
        log.info("mp3party поиск не удался (%s)", type(e).__name__)
        got = []
    return got[:limit]


async def download_url(meta: TrackMeta) -> str | None:
    """Прямая ссылка + префлайт первых байт (CDN умеет отвечать «fail»)."""
    if not re.fullmatch(r"\d+", meta.external_id or ""):
        return None

    def _check() -> bool:
        url = f"https://dl2.mp3party.net/online/{meta.external_id}.mp3"
        try:
            with httpx.Client(headers={
                    **_UA, "Referer": f"{_BASE}/music/{meta.external_id}",
                    "Range": "bytes=0-511"}, timeout=20) as c:
                r = c.get(url, follow_redirects=True)
            body = r.content[:512]
            if r.status_code >= 400 or len(body) < 512:
                return False
            # mp3-магия: ID3-тег или кадр (0xFF Ex/0xFx)
            if body[:3] == b"ID3" or (body[:1] == b"\xff" and
                                      (body[1] & 0xE0) == 0xE0):
                return True
            log.info("mp3party %s: CDN отдал не-mp3 (%r…)",
                     meta.external_id, bytes(body[:12]))
            return False
        except Exception as e:
            log.info("mp3party %s: префлайт не удался (%s)",
                     meta.external_id, type(e).__name__)
            return False

    try:
        if await asyncio.to_thread(_check):
            return f"https://dl2.mp3party.net/online/{meta.external_id}.mp3"
    except Exception as e:
        log.info("mp3party download_url %s: %s", meta.external_id,
                 type(e).__name__)
    return None
