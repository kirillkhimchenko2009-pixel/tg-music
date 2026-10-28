"""zaycev.net: поиск и прямые mp3-320 через внутренний API (анонимно).
Цепочка: cookies с главной → GET /api/external/pages/search/tracks?q=
→ POST /api/external/track/filezmeta → GET /api/external/track/download/<hex>.
"""
import asyncio
import logging
import re

import httpx

from services.providers.base import TrackMeta, parse_duration

log = logging.getLogger("providers.zaycev")
_BASE = "https://zaycev.net"
_API = _BASE + "/api/external"
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}


def _variants(q: str) -> list[str]:
    """Как есть → только трек (без первого слова = артиста) → только артист.
    Поиск zaycev часто «теряет» совпадение при артист+трек одним словом."""
    q0 = re.sub(r"\s+", " ", q or "").strip()
    out = [q0]
    parts = re.split(r"\s+-\s+|\s+—\s+|\s+", q0, maxsplit=1)
    if len(parts) == 2 and parts[1]:
        out.append(parts[1].strip())
        out.append(parts[0].strip())
    seen, uniq = set(), []
    for x in out:
        if x and x.lower() not in seen:
            seen.add(x.lower())
            uniq.append(x)
    return uniq[:3]


class ZaycevProvider:
    def __init__(self):
        self._client: httpx.Client | None = None

    def _c(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=20, headers=_UA, follow_redirects=True)
            # куки user_id/isMobile, которые ждёт API
            try:
                self._client.get(_BASE)
            except Exception:
                pass
        return self._client

    # --- сеть синхронная; наружу async через to_thread ---

    def _search_sync(self, query: str, limit: int) -> list[TrackMeta]:
        r = self._c().get(_API + "/pages/search/tracks", params={"q": query, "page": 1})
        r.raise_for_status()
        d = r.json()
        info = d.get("tracksInfo") or {}
        out: list[TrackMeta] = []
        for tid in d.get("trackIds", []):
            t = info.get(str(tid)) or {}
            if not t.get("track") or not t.get("playbackEnabled", True):
                continue
            out.append(TrackMeta(
                source="zaycev", external_id=str(tid),
                title=t.get("track") or "",
                artist=t.get("artistName") or "",
                duration=parse_duration(t.get("duration")),
                cover_url=None))
            if len(out) >= limit:
                break
        return out

    async def search(self, query: str, limit: int = 6) -> list[TrackMeta]:
        """Собирает кандидатов со всеми вариантами запроса до лимита."""
        pool: list[TrackMeta] = []
        seen: set[str] = set()
        try:
            for v in _variants(query):
                got = await asyncio.to_thread(self._search_sync, v, limit)
                for c in got:
                    if c.external_id in seen:
                        continue
                    seen.add(c.external_id)
                    pool.append(c)
                if len(pool) >= limit:
                    break
        except Exception as e:
            log.info("zaycev поиск не удался (%s)", type(e).__name__)
        await asyncio.sleep(0.15)
        return pool[:limit]

    def _download_url_sync(self, track_id: str) -> str | None:
        c = self._c()
        # 1) hex-токен на полноценное скачивание (прямые 320)
        try:
            r = c.post(_API + "/track/filezmeta",
                       json={"trackIds": [str(track_id)], "subscription": False})
            r.raise_for_status()
            for t in r.json().get("tracks") or []:
                if str(t.get("id")) != str(track_id):
                    continue
                hex_dl = t.get("download")
                if hex_dl:
                    r2 = c.get(f"{_API}/track/download/{hex_dl}",
                               headers={"Referer": _BASE + "/"})
                    if r2.status_code == 200 and r2.text.strip().startswith("http"):
                        return r2.text.strip()
                hex_st = t.get("streaming")
                if hex_st:
                    r3 = c.get(f"{_API}/track/play/{hex_st}")
                    if r3.status_code == 200:
                        u = (r3.json() or {}).get("url")
                        if u:
                            return u
        except Exception as e:
            log.info("zaycev url не получен для %s (%s)", track_id, type(e).__name__)
        return None

    async def download_url(self, meta: TrackMeta) -> str | None:
        return await asyncio.to_thread(self._download_url_sync, meta.external_id)


# модульный синглтон (как у hitmoz): один httpx-клиент и куки на весь процесс
_default = ZaycevProvider()


async def search(query: str, limit: int = 6) -> list[TrackMeta]:
    return await _default.search(query, limit)


async def download_url(meta: TrackMeta) -> str | None:
    return await _default.download_url(meta)
