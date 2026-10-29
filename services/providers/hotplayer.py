"""hotplayer.ru: поиск через JSON-LD ItemList выдачи, прямая mp3 через box.hitpleer.

Выдача содержит `"name":"Баста — Сансара (DFM Mix)","url":"https://hotpleer.ru/?dl=<id>"`.
Страница ?dl=<id> (box.hitplayer.ru) отдаёт подписанную ссылку на полный mp3.
"""
import asyncio
import logging
import re

import httpx

from services.providers.base import TrackMeta

log = logging.getLogger("providers.hotplayer")
_BASE = "https://hotplayer.ru"
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
       "Accept-Language": "ru-RU,ru;q=0.9"}
_ITEM = re.compile(r'"name":"([^"]+?)"\s*,\s*"url":"https://hotpleer\.ru/\?dl=(\d+)"')
_DL = re.compile(r'href="(https://[a-z0-9.]*hotplayer[^"]*/download[^"]*)"')


def _search_sync(query: str, limit: int) -> list[TrackMeta]:
    r = httpx.get(_BASE + "/", params={"s": query}, headers=_UA, timeout=20,
                  follow_redirects=True)
    r.raise_for_status()
    out: list[TrackMeta] = []
    for name, dl_id in _ITEM.findall(r.text):
        name = name.replace("\\u0026", "&").replace("&amp;", "&").strip()
        artist, _, title = name.partition(" — ")
        if not title:
            artist, title = "", name
        out.append(TrackMeta(source="hotplayer", external_id=dl_id,
                             title=title.strip(), artist=artist.strip()))
        if len(out) >= limit:
            break
    return out


async def search(query: str, limit: int = 6) -> list[TrackMeta]:
    try:
        return await asyncio.to_thread(_search_sync, query, limit)
    except Exception as e:
        log.info("hotplayer поиск не удался (%s)", type(e).__name__)
        return []


def _download_url_sync(dl_id: str) -> str | None:
    r = httpx.get(_BASE + "/", params={"dl": dl_id}, headers=_UA, timeout=25,
                  follow_redirects=True)
    r.raise_for_status()
    m = _DL.search(r.text)
    return m.group(1) if m else None


async def download_url(meta: TrackMeta) -> str | None:
    try:
        return await asyncio.to_thread(_download_url_sync, meta.external_id)
    except Exception:
        return None
