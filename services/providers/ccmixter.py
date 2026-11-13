"""ccMixter: официальный JSON API (ccmixter.org/api/query?f=json).

Все материалы — Creative Commons; лицензия конкретной композиции приходит
в ответе (license_url / license_name) и сохраняется в TrackMeta.license.
Файлы отдаются площадкой напрямую (download_url в files[]): mp3 — основной,
FLAC-стемы доступны там же (original_quality). Обложек в API нет.
"""
import asyncio
import logging
import re

import httpx

from services.net import httpx_sync_client
from services.providers.base import TrackMeta, parse_duration

log = logging.getLogger("providers.ccmixter")
_API = "https://ccmixter.org/api/query"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
}

_LIC_MAP = {
    "by": "CC-BY", "by-sa": "CC-BY-SA", "by-nd": "CC-BY-ND",
    "by-nc": "CC-BY-NC", "by-nc-sa": "CC-BY-NC-SA", "by-nc-nd": "CC-BY-NC-ND",
}


def _license_code(up: dict) -> str | None:
    """https://creativecommons.org/licenses/by/4.0/ → 'CC-BY 4.0'."""
    url = up.get("license_url") or ""
    m = re.search(r"/licenses/([a-z\-]+)/([\d.]+)/?", url)
    if m:
        base = _LIC_MAP.get(m.group(1))
        if base:
            return f"{base} {m.group(2)}"
    name = up.get("license_name")
    return (str(name)[:60] if name else None)


def _pick_file(files: list) -> dict | None:
    """Основной mp3 (file_nicname='mp3'), иначе первый файл."""
    for f in files or []:
        if (f or {}).get("file_nicname") == "mp3":
            return f
    for f in files or []:
        if str((f or {}).get("file_name", "")).lower().endswith(".mp3"):
            return f
    return (files or [None])[0]


class CCMixterProvider:
    def __init__(self):
        self._client: httpx.Client | None = None
        self._dl_urls: dict[str, str] = {}     # upload_id → прямая ссылка mp3

    def _c(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx_sync_client(headers=_HEADERS)
        return self._client

    # --- сеть синхронная; наружу async через to_thread ---

    def _search_sync(self, query: str, limit: int) -> list[TrackMeta]:
        r = self._c().get(_API, params={"f": "json", "limit": limit, "q": query})
        r.raise_for_status()
        out: list[TrackMeta] = []
        for up in (r.json() or []):
            if not isinstance(up, dict):
                continue
            uid = str(up.get("upload_id") or "")
            title = (up.get("upload_name") or "").strip()
            if not uid or not title:
                continue
            f = _pick_file(up.get("files") or [])
            if f:
                self._dl_urls[uid] = f.get("download_url") or ""
            ps = ((f or {}).get("file_format_info") or {}).get("ps")
            page = (up.get("file_page_url")
                    or f"https://ccmixter.org/files/{up.get('user_name')}/{uid}")
            out.append(TrackMeta(
                source="ccmixter",
                external_id=uid,
                title=title,
                artist=(up.get("user_name") or "").strip(),
                duration=parse_duration(ps),
                cover_url=None,                      # обложек в API нет (честно)
                page_url=page,
                license=_license_code(up),
                downloadable=bool(f and f.get("download_url")),
            ))
            if len(out) >= limit:
                break
        return out

    async def search(self, query: str, limit: int = 6) -> list[TrackMeta]:
        if not (query or "").strip():
            return []
        try:
            got = await asyncio.to_thread(self._search_sync, query, limit)
            log.info("ccmixter search «%s»: %d рез.", query, len(got))
            return got
        except Exception as e:
            log.info("ccmixter поиск не удался (%s)", type(e).__name__)
            return []

    def _download_url_sync(self, meta: TrackMeta) -> str | None:
        """Прямая ссылка mp3, отданная самим API (только для CC-материала)."""
        if not meta.downloadable:
            return None
        url = self._dl_urls.get(meta.external_id)
        if url:
            return url
        # запасной путь: перезапрашиваем API этой композиции
        try:
            r = self._c().get(_API, params={"f": "json", "limit": 1,
                                            "ids": meta.external_id})
            r.raise_for_status()
            for up in (r.json() or []):
                if str(up.get("upload_id")) == meta.external_id:
                    f = _pick_file(up.get("files") or [])
                    if f and f.get("download_url"):
                        self._dl_urls[meta.external_id] = f["download_url"]
                        return f["download_url"]
        except Exception:
            pass
        return None

    async def download_url(self, meta: TrackMeta) -> str | None:
        return await asyncio.to_thread(self._download_url_sync, meta)


# модульный синглтон (как у zaycev)
_default = CCMixterProvider()


async def search(query: str, limit: int = 6) -> list[TrackMeta]:
    return await _default.search(query, limit)


async def download_url(meta: TrackMeta) -> str | None:
    return await _default.download_url(meta)
