"""PromoDJ: поиск и метаданные через ОФИЦИАЛЬНЫЕ механизмы площадки.

- шаблон OpenSearch (promodj.com/opensearch.xml): /search?searchfor={q}
- AJAX той же страницы поиска: GET /search c параметрами CORE.Search
  (mode=audio → JSON results[] с kind/kindID/html)
- официальные страницы треков: promodj.com/<user>/<type>/<id>/<slug>
- официальные ссылки скачивания: promodj.com/download/<id>/… — их наличие
  в результате означает разрешение скачивания КОНКРЕТНОЙ композиции
  (per-track, не «всему сайту»).

Никаких обходов авторизации/подписки: если у композиции нет бесплатной
официальной ссылки скачивания — downloadable=False (только ссылка на страницу).
"""
import asyncio
import logging
import re

import httpx

from services.net import httpx_sync_client
from services.providers.base import TrackMeta, parse_duration

log = logging.getLogger("providers.promodj")
_BASE = "https://promodj.com"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
}

# якорь заголовка результата (class="invert"; href до или после class)
_A1 = re.compile(r'<a[^>]*\bclass="invert"[^>]*\bhref="([^"]+)"[^>]*>(.*?)</a>', re.S)
_A2 = re.compile(r'<a[^>]*\bhref="(https://promodj\.com/[^"]+)"[^>]*\bclass="invert"'
                 r'[^>]*>(.*?)</a>', re.S)
_HREF = re.compile(r'href="(https://promodj\.com/[^"]+)"')
_TIME = re.compile(r'player_standard_tool__time[^>]*>([0-9:]+)<')
_DL = re.compile(r'href="(https://promodj\.com/download/\d+[^"]*)"')
_BIT = re.compile(r'player_standard_tool__bitrate[^>]*>\s*(\d+)\s*<')
_AVATAR = re.compile(r'background-image:url\((https://cdn\.promodj\.com/[^)]+)\)')
_TAG = re.compile(r"<[^>]+>")
_NAME = re.compile(r'class="user"[^>]*>([^<]+)<')


def _strip_tags(s: str) -> str:
    return re.sub(r"\s+", " ", _TAG.sub("", s)).strip()


def _artist_of(url: str) -> str:
    """promodj.com/<user>/<type>/<id>/<slug> → <user> (автор сохраняется всегда)."""
    tail = url.split("promodj.com/", 1)[-1]
    return (tail.split("/", 1)[0] or "").strip()


class PromoDJProvider:
    def __init__(self):
        self._client: httpx.Client | None = None
        self._dl_urls: dict[str, str] = {}     # kindID → официальная ссылка скачивания

    def _c(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx_sync_client(headers=_HEADERS)
        return self._client

    # --- сеть синхронная; наружу async через to_thread ---

    def _search_sync(self, query: str, limit: int) -> list[TrackMeta]:
        r = self._c().get(_BASE + "/search", params={
            "searchfor": query, "mode": "audio", "sortby": "relevance",
            "period": "all", "page": 1, "results": 1, "clear": 1})
        r.raise_for_status()
        data = r.json()
        out: list[TrackMeta] = []
        for item in data.get("results") or []:
            if item.get("kind") != "file":
                continue
            html = item.get("html") or ""
            tid = str(item.get("kindID") or "")
            m = _A1.search(html) or _A2.search(html)
            url = (m.group(1) if m else "")
            title = _strip_tags((m.group(2) if m else ""))
            if not tid or not title:
                continue
            dl = _DL.search(html)
            if dl:
                self._dl_urls[tid] = dl.group(1)
            dur = _TIME.search(html)
            av = _AVATAR.search(html)
            out.append(TrackMeta(
                source="promodj",
                external_id=tid,
                title=title,
                artist=_artist_of(url),
                duration=parse_duration(dur.group(1)) if dur else 0,
                cover_url=av.group(1) if av else None,
                page_url=url or None,
                downloadable=bool(dl),
            ))
            if len(out) >= limit:
                break
        return out

    async def search(self, query: str, limit: int = 6) -> list[TrackMeta]:
        if not (query or "").strip():
            return []
        try:
            got = await asyncio.to_thread(self._search_sync, query, limit)
            log.info("promodj search «%s»: %d рез.", query, len(got))
            return got
        except Exception as e:
            log.info("promodj поиск не удался (%s)", type(e).__name__)
            return []

    def _search_artists_sync(self, query: str, limit: int) -> list[dict]:
        """Поиск DJ/артистов (официальный раздел «people» той же страницы поиска)."""
        r = self._c().get(_BASE + "/search", params={
            "searchfor": query, "mode": "user", "sortby": "relevance",
            "period": "all", "page": 1, "results": 1, "clear": 1})
        r.raise_for_status()
        out = []
        for item in (r.json().get("results") or []):
            if item.get("kind") != "user":
                continue
            html = item.get("html") or ""
            m = _HREF.search(html)
            nm = _NAME.search(html)
            av = _AVATAR.search(html)
            if m:
                out.append({"id": str(item.get("kindID") or ""),
                            "name": (nm.group(1).strip() if nm else ""),
                            "url": m.group(1), "avatar": av.group(1) if av else None})
            if len(out) >= limit:
                break
        return out

    async def search_artists(self, query: str, limit: int = 8) -> list[dict]:
        if not (query or "").strip():
            return []
        try:
            return await asyncio.to_thread(self._search_artists_sync, query, limit)
        except Exception as e:
            log.info("promodj поиск DJ не удался (%s)", type(e).__name__)
            return []

    def _download_url_sync(self, meta: TrackMeta) -> str | None:
        """Официальная ссылка скачивания конкретной композиции (или None)."""
        tid = meta.external_id
        if not meta.downloadable:
            return None
        url = self._dl_urls.get(tid)
        if url:
            return url
        # запасной путь: на официальной странице трека ищем /download/<id>
        try:
            r = self._c().get(meta.page_url or "")
            if r.status_code == 200:
                m = _DL.search(r.text)
                if m:
                    self._dl_urls[tid] = m.group(1)
                    return m.group(1)
        except Exception:
            pass
        return None

    async def download_url(self, meta: TrackMeta) -> str | None:
        return await asyncio.to_thread(self._download_url_sync, meta)


# модульный синглтон (как у zaycev): один httpx-клиент на весь процесс
_default = PromoDJProvider()


async def search(query: str, limit: int = 6) -> list[TrackMeta]:
    return await _default.search(query, limit)


async def search_artists(query: str, limit: int = 8) -> list[dict]:
    return await _default.search_artists(query, limit)


async def download_url(meta: TrackMeta) -> str | None:
    return await _default.download_url(meta)
