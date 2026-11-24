"""JioSaavn через публичный API saavn.sumit.co — полный трек, 320kbps, без токенов.

Проблема: часть треков не резолвится с RU-пираток (451 из ЕС) и ЯМ (нет
аудио), воркер отправляет 30-сек превью. JioSaavn — международный каталог
с полными фонограммами; хостинг API отдаёт прямые ссылки на CDN
(aac.saavncdn.com, audio/mp4), поддерживает Range. Домен saavn.dev
переехал на saavn.sumit.co — держим оба как фолбэк.
"""
import asyncio
import logging

import httpx

from services.providers.base import TrackMeta

log = logging.getLogger("providers.saavn")

# публичный инстанс Unofficial JioSaavn API (без ключей)
_HOSTS = ("https://saavn.sumit.co", "https://saavn.dev")
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}


def _dur(s) -> int:
    """API возвращает секунды строкой или 'mm:ss'."""
    d = str(s or "").strip()
    if not d:
        return 0
    try:
        return int(float(d))
    except ValueError:
        pass
    try:
        parts = [int(x) for x in d.split(":")]
        return parts[-1] + 60 * parts[-2] if len(parts) > 1 else parts[-1]
    except (ValueError, IndexError):
        return 0


def _pick_url(song: dict) -> str:
    """Самая жирная ссылка из downloadUrl (320 → 160 → 96…)."""
    urls = song.get("downloadUrl") or []
    best = ""
    for it in urls:
        u = (it or {}).get("url") or ""
        q = str((it or {}).get("quality") or "")
        if not u:
            continue
        if q.startswith("320") or not best:
            best = u
            if q.startswith("320"):
                break
    return best or (song.get("url") or "")


def _names(song: dict) -> str:
    """Основной артист через запятую."""
    pr = (song.get("artists") or {}).get("primary") or []
    got = ", ".join((a or {}).get("name") or "" for a in pr).strip(", ")
    return got or str(song.get("primaryArtists") or song.get("artists") or "").strip()


def _parse(song: dict) -> TrackMeta | None:
    sid = str(song.get("id") or "")
    title = str(song.get("name") or "").strip()
    if not sid or not title:
        return None
    return TrackMeta(source="saavn", external_id=sid, title=title,
                     artist=_names(song) or "", duration=_dur(song.get("duration")),
                     cover_url=str(song.get("image") or (song.get("image") or [{}])[-1].get("link")
                                   if isinstance(song.get("image"), list)
                                   else song.get("image")) or None)


def _search_sync(query: str, limit: int) -> list[TrackMeta]:
    """Поиск по всем зеркалам API; первый ответивший выигрывает."""
    last_err = ""
    for host in _HOSTS:
        try:
            with httpx.Client(headers=_UA, timeout=12, follow_redirects=True) as c:
                r = c.get(host + "/api/search/songs",
                          params={"query": query, "limit": max(1, limit), "page": 0})
                if r.status_code != 200:
                    last_err = f"HTTP {r.status_code}"
                    continue
                data = (r.json() or {}).get("data") or {}
                results = data.get("results") or []
                out = [m for m in (_parse(s) for s in results) if m]
                if out:
                    return out[:limit]
                last_err = "пусто"
        except Exception as e:  # noqa: BLE001
            last_err = type(e).__name__
    log.info("saavn search %s: %s", query[:40], last_err or "нет ответа")
    return []


async def search(query: str, limit: int = 6) -> list[TrackMeta]:
    try:
        return await asyncio.to_thread(_search_sync, query, limit)
    except Exception as e:  # noqa: BLE001
        log.info("saavn поиск не удался (%s)", type(e).__name__)
        return []


async def download_url(m: TrackMeta) -> str | None:
    """Прямая ссылка на файл по external_id (songMetadata endpoint)."""
    if not m.external_id:
        return None

    def fetch() -> str | None:
        for host in _HOSTS:
            try:
                with httpx.Client(headers=_UA, timeout=12, follow_redirects=True) as c:
                    r = c.get(f"{host}/api/songs/{m.external_id}")
                    if r.status_code != 200:
                        continue
                    data = (r.json() or {}).get("data") or []
                    if isinstance(data, dict):
                        data = [data]
                    for song in data:
                        u = _pick_url(song)
                        if u:
                            return u
            except Exception:  # noqa: BLE001
                continue
        return None

    try:
        return await asyncio.to_thread(fetch)
    except Exception:  # noqa: BLE001
        return None
