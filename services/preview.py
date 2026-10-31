"""30-секундные фрагменты треков из открытых каталогов (Deezer / iTunes).
Без зависимостей от FastAPI: используется и веб-роутами, и бот-рантаймом."""
import asyncio

import httpx


async def deezer_preview(artist: str, title: str) -> str | None:
    """Анонимное превью (30 сек) через публичный API Deezer — без токенов и VPN."""
    def _sync() -> str | None:
        from services.matching import best_match
        from services.providers.base import TrackMeta as TM
        q = f"{artist or ''} {title or ''}".strip()
        if not q:
            return None
        r = httpx.get("https://api.deezer.com/search",
                      params={"q": q, "limit": 8}, timeout=12)
        data = r.json().get("data") or []
        cands, previews = [], {}
        for d in data:
            tid = str(d.get("id") or "")
            cands.append(TM(source="deezer", external_id=tid,
                            title=d.get("title") or "",
                            artist=(d.get("artist") or {}).get("name") or "",
                            duration=int(d.get("duration") or 0)))
            previews[tid] = d.get("preview")
        m = best_match(TM(source="deezer", external_id="", title=title or "",
                          artist=artist or ""), cands, min_score=0.5)
        if m:
            return previews.get(m.external_id)
        # матчинг не сработал (транслит/фиты) — доверяем релевантности Deezer
        return (data[0].get("preview") if data else None)
    try:
        return await asyncio.wait_for(asyncio.to_thread(_sync), timeout=20)
    except Exception:
        return None


async def itunes_preview(artist: str, title: str) -> str | None:
    """30-сек превью из iTunes Search API (CDN Apple, без токенов)."""
    def _sync() -> str | None:
        from services.matching import best_match
        from services.providers.base import TrackMeta as TM
        q = f"{artist or ''} {title or ''}".strip()
        if not q:
            return None
        r = httpx.get("https://itunes.apple.com/search",
                      params={"term": q, "media": "music", "entity": "song",
                              "limit": 8}, timeout=12)
        data = r.json().get("results") or []
        cands, previews = [], {}
        for d in data:
            tid = str(d.get("trackId") or "")
            cands.append(TM(source="itunes", external_id=tid,
                            title=d.get("trackName") or "",
                            artist=d.get("artistName") or "",
                            duration=int((d.get("trackTimeMillis") or 0) / 1000)))
            previews[tid] = d.get("previewUrl")
        m = best_match(TM(source="itunes", external_id="", title=title or "",
                          artist=artist or ""), cands, min_score=0.5)
        if m:
            return previews.get(m.external_id)
        return (data[0].get("previewUrl") if data else None)
    try:
        return await asyncio.wait_for(asyncio.to_thread(_sync), timeout=15)
    except Exception:
        return None


async def best_preview(artist: str, title: str) -> str | None:
    """Первый найденный URL 30-сек фрагмента: Deezer → iTunes."""
    dz, it = await asyncio.gather(deezer_preview(artist, title),
                                  itunes_preview(artist, title))
    return (dz if isinstance(dz, str) and dz else
            (it if isinstance(it, str) and it else None))
