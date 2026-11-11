"""SoundCloud: поиск через публичный api-v2 (гостевой client_id без логина),
скачивание — yt-dlp (умеет HLS/AAC этого сайта).

- client_id ротируется: если api-v2 ответил 401/404 — свежий добирается
  из asset-скриптов главной страницы.
- 30-сек «превью» Sony-каталога отсекаются по full_duration != duration.
- external_id — полный permalink_url (yt-dlp качает по нему напрямую).
"""
import asyncio
import logging
import re
import time

import httpx

from services.providers.base import TrackMeta

log = logging.getLogger("providers.soundcloud")

_BASE = "https://soundcloud.com"
_API = "https://api-v2.soundcloud.com"
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
_ASSETS = re.compile(r'src="(https://a-v2\.sndcdn\.com/assets/[\w./-]+\.js)"')
_CID_RE = re.compile(r'(?:client_id|clientId)["\s:]{0,4}["\']([a-zA-Z0-9]{25,40})["\']')

# резервные client_id (c0lcDb60… добыт скрейпом 11.2026, проверен на поиске;
# swtEGNK… и OGA0P5R… проверены тем же днём — все три живы)
_FALLBACK_CIDS = (
    "c0lcDb60e4gNCrZY24mdmz9uWo1Ih29e",
    "swtEGNKrP2w6Z5B8DVvAurWCwNQBlgMd",
    "OGA0P5R5U4CpyooNe9OUcnjIMxao2hB2",
)


def _variants(q: str) -> list[str]:
    """Как есть → только трек (без первого слова = артиста) → только артист.
    Многословный запрос SC ищет неохотно; парные запросы работают лучше."""
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


class SoundCloudProvider:
    def __init__(self):
        self._client: httpx.Client | None = None
        self._cid: str | None = None
        self._cid_at: float = 0.0

    def _c(self) -> httpx.Client:
        if self._client is None:
            from core.config import settings
            self._client = httpx.Client(timeout=25, headers=_UA,
                                        follow_redirects=True,
                                        proxy=settings.PROXY_URL or None)
        return self._client

    def _client_id(self, force: bool = False) -> str | None:
        """Гостевой client_id: кеш 22 ч; force → ротация по списку резервных.

        Скрейп asset-JS в последнюю очередь: в 11.2026 в main.js cid не лежит
        (диагностика: 8 ассетов — ни одного совпадения), а известные резервы
        покрывают потребность."""
        if self._cid and not force and time.time() - self._cid_at < 22 * 3600:
            return self._cid
        if force:
            # ротация: forced-запрос даёт СЛЕДУЮЩИЙ после текущего cid
            # (текущая уже не сработала — повторять её бессмысленно), скрейп
            # удерживается последним резервом: в 11.2026 main.js cid не отдаёт
            self._rot = getattr(self, "_rot", -1)
            cands = list(_FALLBACK_CIDS)
            scraped = self._scrape_cid()
            if scraped and scraped not in cands:
                cands.append(scraped)
            if self._cid in cands and len(cands) > 1:
                i = cands.index(self._cid)
                cands = cands[i + 1:] + cands[:i + 1]
            self._cid = cands[0]
            self._rot += 1
            self._cid_at = time.time()
        elif self._cid is None:
            self._cid = _FALLBACK_CIDS[0]
            self._cid_at = time.time()
        return self._cid

    def _scrape_cid(self) -> str | None:
        try:
            r = self._c().get(_BASE)
            for js in _ASSETS.findall(r.text)[:12]:
                try:
                    rj = self._c().get(js)
                except Exception:
                    continue
                m = _CID_RE.search(rj.text)
                if m:
                    return m.group(1)
        except Exception as e:
            log.info("sc client_id не добыт (%s)", type(e).__name__)
        return None

    def _meta_of(self, it: dict) -> TrackMeta | None:
        dur = int(it.get("duration") or 0)
        full = int(it.get("full_duration") or dur)
        if not dur or (full > dur + 60_000 and dur <= 60_000):
            return None  # 30-сек «превью» лицензионного каталога
        perm = it.get("permalink_url") or ""
        if "soundcloud.com" not in perm:
            return None
        artist = ((it.get("publisher") or {}).get("artist")
                  or (it.get("user") or {}).get("username") or "")
        title = it.get("title") or ""
        # sc часто склеивает «Artist - Title» в одно поле
        if " - " in title:
            a, _, t2 = title.partition(" - ")
            if a.strip() and not artist:
                artist, title = a.strip(), t2.strip()
        return TrackMeta(
            source="soundcloud",
            external_id=perm,
            title=(title or "").replace('"', "").strip(),
            artist=(artist or "").strip(),
            duration=dur // 1000,
            cover_url=((it.get("artwork_url") or "").replace(
                "large", "t500x500") or None))

    def _search_sync(self, query: str, limit: int) -> list[TrackMeta]:
        out: list[TrackMeta] = []
        for attempt in (0, 1):
            cid = self._client_id(force=attempt > 0)
            if not cid:
                break
            r = self._c().get(_API + "/search/tracks",
                              params={"q": query, "limit": limit,
                                      "client_id": cid})
            if r.status_code == 200:
                for it in r.json().get("collection") or []:
                    m = self._meta_of(it)
                    if m:
                        out.append(m)
                break
            log.info("sc search %s: HTTP %s (cid=%s…)",
                     query[:40], r.status_code, cid[:8])
        return out

    async def search(self, query: str, limit: int = 6) -> list[TrackMeta]:
        pool: list[TrackMeta] = []
        seen: set[str] = set()
        try:
            for v in _variants(query):
                got = await asyncio.to_thread(self._search_sync, v, limit)
                for m in got:
                    if m.external_id in seen:
                        continue
                    seen.add(m.external_id)
                    pool.append(m)
                if len(pool) >= limit:
                    break
        except Exception as e:
            log.info("soundcloud поиск не удался (%s)", type(e).__name__)
        return pool[:limit]

    def _playlist_sync(self, permalink: str) -> tuple[str, list[TrackMeta]] | None:
        """Публичный плейлист SC: resolve → kind=playlist (+ треки без пагинации).

        Полностью анонимно (гостевой client_id) — токены не нужны."""
        for attempt in (0, 1):
            cid = self._client_id(force=attempt > 0)
            if not cid:
                break
            r = self._c().get(_API + "/resolve", params={
                "url": permalink, "client_id": cid})
            if r.status_code != 200:
                if attempt == 0:
                    continue
                log.info("sc playlist resolve %s: HTTP %s",
                         permalink[-30:], r.status_code)
                return None
            j = r.json()
            if (j.get("kind") or "") != "playlist":
                return None
            out: list[TrackMeta] = []
            for t in j.get("tracks") or []:
                m = self._meta_of(t)
                if m:
                    out.append(m)
            # со сдохшим cid resolve бывает 200, но отдаёт урезанный список
            # (тональности-заглушки) — ждём полноценный track_count, ретраим
            want = int(j.get("track_count") or 0)
            if want > len(out) and attempt == 0:
                continue
            return (j.get("title") or "SoundCloud плейлист"), out
        return None

    async def playlist_by_url(self, permalink: str) -> tuple[str, list[TrackMeta]] | None:
        if not (permalink or "").startswith("https://soundcloud.com/"):
            return None
        try:
            return await asyncio.to_thread(self._playlist_sync, permalink)
        except Exception as e:
            log.info("sc playlist не удался (%s)", type(e).__name__)
            return None

    def _trans_sync(self, permalink: str) -> dict:
        """Диагностика: список transcodings с текущим client_id."""
        for attempt in (0, 1):
            cid = self._client_id(force=attempt > 0)
            if not cid:
                return {"client_id": None}
            r = self._c().get(_API + "/resolve", params={
                "url": permalink, "client_id": cid})
            if r.status_code != 200:
                if attempt == 0:
                    continue
                return {"resolve_status": r.status_code, "client_id": cid}
            j = r.json()
            trans = ((j.get("media") or {}).get("transcodings")) or []
            return {"client_id": cid,
                    "transcodings": [{"preset": t.get("preset"),
                                      "protocol": (t.get("format") or {}).get("protocol"),
                                      "url": (t.get("url") or "")[:100]}
                                     for t in trans]}
        return {"error": "resolve не удался"}

    def _hls_sync(self, permalink: str) -> str | None:
        """m3u8-плейлист полного трека: resolve → transcodings → aac_160k."""
        for attempt in (0, 1):
            cid = self._client_id(force=attempt > 0)
            if not cid:
                break
            r = self._c().get(_API + "/resolve", params={
                "url": permalink, "client_id": cid})
            if r.status_code != 200:
                if attempt == 0:
                    continue
                log.info("sc hls resolve %s: HTTP %s", permalink[-20:],
                         r.status_code)
                return None
            j = r.json()
            trans = ((j.get("media") or {}).get("transcodings")) or []
            tr = next((t for t in trans if t.get("preset") == "aac_160k")
                      or next((t for t in trans
                               if (t.get("format") or {}).get("protocol") == "hls"),
                              None))
            if not tr:
                return None
            rm = self._c().get(tr["url"], params={"client_id": cid})
            if rm.status_code != 200:
                continue
            return (rm.json() or {}).get("url")
        return None

    async def hls_stream(self, external_id: str) -> str | None:
        """URL HLS-плейлиста (только для онлайн-плеера; ссылка подписана ~2-6 ч)."""
        if not (external_id or "").startswith("https://soundcloud.com/"):
            return None
        try:
            return await asyncio.to_thread(self._hls_sync, external_id)
        except Exception as e:
            log.info("soundcloud hls_stream не удался (%s)", type(e).__name__)
            return None


_default = SoundCloudProvider()


async def search(query: str, limit: int = 6) -> list[TrackMeta]:
    return await _default.search(query, limit)


async def hls_stream(external_id: str) -> str | None:
    return await _default.hls_stream(external_id)


async def playlist_by_url(permalink: str) -> tuple[str, list[TrackMeta]] | None:
    return await _default.playlist_by_url(permalink)
