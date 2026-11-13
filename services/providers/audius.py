"""Audius: официальный публичный API (api.audius.co → discovery-хост).

- GET /v1/tracks/search?query=&app_name= — поиск треков
- is_downloadable — разрешение скачивания КОНКРЕТНОЙ композиции (per-track)
- artwork[480x480] — обложка, permalink — официальная страница
- файлы: только то, что площадка сама отдаёт (download/stream эндпоинты);
  никаких защищённых потоков и обходов.
"""
import asyncio
import logging

import httpx

from services.net import httpx_sync_client
from services.providers.base import TrackMeta

log = logging.getLogger("providers.audius")
_DISCOVERY = "https://api.audius.co"
_APP = "vibe-music"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
}


class AudiusProvider:
    def __init__(self):
        self._client: httpx.Client | None = None
        self._host: str | None = None

    def _c(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx_sync_client(headers=_HEADERS)
        return self._client

    def _host_sync(self) -> str:
        """Хост из официального дискавери-эндпоинта (кэшируется)."""
        if not self._host:
            r = self._c().get(_DISCOVERY)
            r.raise_for_status()
            hosts = r.json().get("data") or []
            if not hosts:
                raise RuntimeError("audius: discovery пуст")
            self._host = hosts[0].rstrip("/")
        return self._host

    # --- сеть синхронная; наружу async через to_thread ---

    def _search_sync(self, query: str, limit: int) -> list[TrackMeta]:
        host = self._host_sync()
        r = self._c().get(f"{host}/v1/tracks/search",
                          params={"query": query, "app_name": _APP})
        r.raise_for_status()
        out: list[TrackMeta] = []
        for t in (r.json().get("data") or []):
            tid = str(t.get("id") or "")
            title = (t.get("title") or "").strip()
            if not tid or not title:
                continue
            art = t.get("artwork") or {}
            user = t.get("user") or {}
            rel = str(t.get("release_date") or "")
            # разрешение скачивания конкретной композиции (мастер-промпт §3):
            # бесплатное негейтованное скачивание; gated (в т.ч. платные
            # usdc_purchase) НЕ считаем доступными — платное не обходим
            dl = bool(t.get("is_downloadable")) and not bool(t.get("is_download_gated"))
            out.append(TrackMeta(
                source="audius",
                external_id=tid,
                title=title,
                artist=(user.get("name") or user.get("handle") or "").strip(),
                duration=int(t.get("duration") or 0),
                cover_url=art.get("480x480") or art.get("150x150"),
                year=int(rel[:4]) if rel[:4].isdigit() else None,
                page_url=("https://audius.co" + (t.get("permalink") or "")
                          if t.get("permalink") else None),
                downloadable=dl,
            ))
            if len(out) >= limit:
                break
        return out

    async def search(self, query: str, limit: int = 6) -> list[TrackMeta]:
        if not (query or "").strip():
            return []
        try:
            got = await asyncio.to_thread(self._search_sync, query, limit)
            log.info("audius search «%s»: %d рез.", query, len(got))
            return got
        except Exception as e:
            log.info("audius поиск не удался (%s)", type(e).__name__)
            return []

    def _download_url_sync(self, meta: TrackMeta) -> str | None:
        """Официальный download-эндпоинт (только если is_downloadable=True)."""
        if not meta.downloadable:
            return None
        try:
            host = self._host_sync()
            r = self._c().get(f"{host}/v1/tracks/{meta.external_id}/download",
                              params={"app_name": _APP}, follow_redirects=False)
            if r.status_code in (301, 302, 303, 307, 308):
                return r.headers.get("location")
            if r.status_code == 200 and "audio" in (r.headers.get("content-type") or ""):
                return str(r.url)
        except Exception as e:
            log.info("audius url не получен для %s (%s)", meta.external_id, type(e).__name__)
        return None

    async def download_url(self, meta: TrackMeta) -> str | None:
        return await asyncio.to_thread(self._download_url_sync, meta)

    def _stream_url_sync(self, meta: TrackMeta) -> str | None:
        """Официальный stream-эндпоинт (публичный; без обхода защиты)."""
        try:
            host = self._host_sync()
            r = self._c().get(f"{host}/v1/tracks/{meta.external_id}/stream",
                              params={"app_name": _APP}, follow_redirects=False)
            if r.status_code in (301, 302, 303, 307, 308):
                return r.headers.get("location")
            if r.status_code == 200 and "audio" in (r.headers.get("content-type") or ""):
                return str(r.url)
        except Exception:
            pass
        return None

    async def stream_url(self, meta: TrackMeta) -> str | None:
        return await asyncio.to_thread(self._stream_url_sync, meta)


# модульный синглтон (как у zaycev)
_default = AudiusProvider()


async def search(query: str, limit: int = 6) -> list[TrackMeta]:
    return await _default.search(query, limit)


async def download_url(meta: TrackMeta) -> str | None:
    return await _default.download_url(meta)
