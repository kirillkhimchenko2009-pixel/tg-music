"""Яндекс Музыка: поиск/плейлисты/прямые ссылки (нужен токен).

Публичные плейлисты по ссылке читаются АНОНИМНО через
api.music.yandex.net (подтверждено 2026-10: /users/<login>/playlists/<kind>
и /playlist/<uuid> отдают полный JSON без OAuth)."""
import asyncio
import logging
from typing import Any

import httpx

from core.config import settings
from services.providers.base import TrackMeta

log = logging.getLogger("providers.yandex")
URL = "https://api.music.yandex.net"
# Анонимное API геофильтруется (с ФРГ-IP .net отдаёт не-200), пробуем зеркальный
# хост .ru вторым шагом; с RU-IP оба хоста отдают 200.
URL_ALT = "https://api.music.yandex.ru"


def _explicit(o: Any) -> bool:
    """Метка 18+ с фолбэком: в bulk-ответах ЯМ поле explicit=null,
    но contentWarning="explicit" проставлен. Фолбэк нужен именно тут."""
    if bool(getattr(o, "explicit", False)):
        return True
    cw = getattr(o, "content_warning", None) or getattr(o, "contentWarning", None)
    return str(cw or "").lower() == "explicit"

def _web_meta(t) -> TrackMeta:
    """TrackMeta из JSON track api.music.yandex.net (works и для nested)."""
    real = t.get("track") or t
    artists = ", ".join(a.get("name", "") for a in (real.get("artists") or [])
                        if a.get("name"))
    albums = real.get("albums") or []
    album = albums[0].get("title") if albums else None
    year = albums[0].get("year") if albums else None
    cover_uri = real.get("coverUri")
    cover_url = ("https://" + cover_uri.replace("%%", "400x400").lstrip("/")
                 if cover_uri else None)
    return TrackMeta(
        source="yandex",
        external_id=str(real.get("id") or ""),
        title=real.get("title") or "",
        artist=artists, album=album, year=year,
        duration=int(real.get("durationMs") or 0) // 1000,
        cover_url=cover_url,
        explicit=bool(real.get("explicit"))
        or str(real.get("contentWarning") or "").lower() == "explicit")


async def _anon_get(path: str) -> httpx.Response:
    """GET к анонимному API ЯМ с fallback-доменом (.net → .ru).

    Ходим через общий httpx_client (учитывает PROXY_URL: с зарубежного IP
    анонимное API отдаёт HTTP 451, нужен российский выход). Исключение:
    YM_PROXY_URL — отдельный русский прокси ТОЛЬКО для ЯМ. Публичные
    прокси рыхлые (большой ответ может обрываться/тянуться долго), поэтому
    4 попытки (две длинные, две короткие) — импорт теперь фоновый и может
    себе позволить терпение; затем ретрай напрямую, чтобы отдать честную
    гео-ошибку 451, а не сетевой облом."""
    from services.net import httpx_client
    ym_proxy = settings.YM_PROXY_URL
    if ym_proxy:
        for i, tmo in enumerate((httpx.Timeout(60.0, connect=10.0),
                                 httpx.Timeout(25.0, connect=5.0),
                                 httpx.Timeout(60.0, connect=10.0),
                                 httpx.Timeout(25.0, connect=5.0)), 1):
            try:
                async with httpx_client(proxy=ym_proxy, timeout=tmo,
                                        follow_redirects=True) as c:
                    return await _anon_hosts(c, path)
            except httpx.TransportError as e:
                log.warning("ym proxy попытка %d не удалась (%s: %s)",
                            i, type(e).__name__, str(e)[:140])
        log.warning("ym proxy не помог — ретрай напрямую (честная гео-ошибка)")
    async with httpx_client(timeout=20, follow_redirects=True) as c:
        return await _anon_hosts(c, path)


async def _anon_hosts(c: httpx.AsyncClient, path: str) -> httpx.Response:
    async def ok(r: httpx.Response) -> bool:
        if r.status_code != 200:
            return False
        try:
            r.json()
            return True
        except ValueError:
            log.warning("ym anon: %s: 200 с битым телом (обрыв через прокси?)",
                        path)
            return False

    r = await c.get(f"{URL}{path}")
    if await ok(r):
        return r
    r2 = await c.get(f"{URL_ALT}{path}")
    if await ok(r2):
        log.info("ym anon: %s отдал только зеркальный .ru (первичный %s)",
                 path, r.status_code)
        return r2
    if r.status_code == 200 or r2.status_code == 200:
        # оба 200, но тела битые — честная ошибка вместо падения на json()
        raise RuntimeError(
            "Яндекс.Музыка отдала повреждённый ответ (обрыв через прокси) "
            "— попробуй ещё раз")
    return r if r.status_code != 404 else r2 if r2.status_code != 404 else r


async def anon_playlist_by_login(owner: str, kind: str) -> tuple[str, list[TrackMeta]]:
    """Публичный плейлист по ссылкам вида /users/<login>/playlists/<kind> — без токена."""
    r = await _anon_get(f"/users/{owner}/playlists/{kind}")
    if r.status_code != 200:
        raise RuntimeError(
            "Яндекс не отдал плейлист (код %s). Если он приватный — нужен токен /token"
            % r.status_code)
    res = r.json().get("result") or {}
    title = res.get("title") or "Плейлист Яндекс"
    metas = [_web_meta(t) for t in (res.get("tracks") or [])]
    metas = [m for m in metas if m.title]
    return title, metas


async def anon_playlist_by_uuid(uuid: str) -> tuple[str, list[TrackMeta]]:
    """Публичный плейлист по ссылкам нового вида /playlists/<uuid> — без токена.

    uuid передаётся как в ссылке (ps.<…> и lk.<…> — значимые префиксы);
    если API не знает префиксную форму — ретраим со снятым префиксом."""
    candidates = [uuid]
    if "." in uuid:
        bare = uuid.split(".", 1)[1]
        if bare not in candidates:
            candidates.append(bare)
    last_err = None
    for u in candidates:
        r = await _anon_get(f"/playlist/{u}")
        if r.status_code == 200:
            res = r.json().get("result") or {}
            title = res.get("title") or "Плейлист Яндекс"
            metas = [_web_meta(t) for t in (res.get("tracks") or [])]
            return title, [m for m in metas if m.title]
        last_err = r.status_code
    raise RuntimeError(
        "Яндекс не отдал плейлист по uuid (код %s). Если он приватный — нужен токен /token"
        % last_err)


async def get_public_playlist(owner: str, kind: str, token: str) -> tuple[str, list[TrackMeta]]:
    """Плейлист по ссылке через официальный API (нужен токен)."""
    client = await _client(token)
    owner_s = str(owner or "").strip()
    if not owner_s.isdigit():
        raise RuntimeError(
            "нужен числовой id владельца Яндекс-плейлиста; попробуй ссылку вида "
            "music.yandex.ru/users/<число>/playlists/<kind> или укажи свой плейлист через /token-on-app")
    pl = await asyncio.to_thread(
        client.users_playlists, int(kind),
        int(owner_s) if owner_s.isdigit() else owner_s)
    if not pl:
        return "Плейлист Яндекс", []
    metas = []
    for pt in (pl.tracks or []):
        t = getattr(pt, "track", None)
        if not t:
            continue
        artists = ", ".join(a.name for a in (t.artists or []) if getattr(a, "name", None))
        albums = t.albums or []
        album = albums[0].title if albums else None
        year = albums[0].year if albums else None
        cover_url = None
        cu = getattr(t, "cover_uri", None)
        if cu:
            cover_url = "https://" + cu.replace("%%", "400x400").lstrip("/")
        metas.append(TrackMeta(
            source="yandex", external_id=str(t.id), title=t.title or "",
            artist=artists, album=album, year=year,
            duration=int(getattr(t, "duration_ms", 0) or 0) // 1000,
            cover_url=cover_url,
            explicit=_explicit(t)))
    return (getattr(pl, "title", None) or "Плейлист Яндекс"), metas

async def _client(token: str) -> Any:
    from yandex_music import Client
    if not token:
        raise RuntimeError("нужен токен Яндекс Музыки (/YM_TOKEN или /token)")
    try:
        client = await asyncio.to_thread(lambda: Client(token).init())
    except Exception as e:
        log.warning("ym init fail: %s: %s", type(e).__name__, str(e)[:180])
        raise
    return client

class YandexProvider:
    def __init__(self, token: str, user_id: int | None = None):
        self.token = token
        self.user_id = user_id
        self._client: Any = None

    async def _ym(self) -> Any:
        if self._client is None:
            self._client = await _client(self.token)
        return self._client

    async def search(self, query: str, limit: int = 8) -> list[TrackMeta]:
        if not self.token:
            return []
        try:
            client = await self._ym()
            res = await asyncio.to_thread(client.search, query, "track")
            tracks = (getattr(res, "tracks", None) and res.tracks.results) or []
        except Exception as e:
            log.warning("ym search «%s» fail: %s: %s", query, type(e).__name__, str(e)[:180])
            return []
        log.info("ym search «%s»: %d рез.", query, len(tracks))
        return [self._from_library(t) for t in tracks[:limit]]

    @staticmethod
    def _from_library(t: Any) -> TrackMeta:
        artists = ", ".join(a.name for a in (t.artists or []) if getattr(a, "name", None))
        albums = t.albums or []
        album = albums[0].title if albums else None
        year = albums[0].year if albums else None
        cover = None
        if getattr(t, "cover_uri", None):
            cover = "https://" + t.cover_uri.replace("%%", "400x400").lstrip("/")
        return TrackMeta(source="yandex", external_id=str(t.id), title=t.title or "",
                         artist=artists, album=album, year=year,
                         duration=int(getattr(t, "duration_ms", 0) or 0) // 1000,
                         cover_url=cover,
                         explicit=_explicit(t))

    async def track_url(self, meta: TrackMeta, quality: str) -> str | None:
        """Прямая ссылка на файл (flac при Plus; 320 иначе)."""
        client = await self._ym()
        try:
            infos = await asyncio.to_thread(client.tracks_download_info, meta.external_id)
        except Exception as e:
            log.warning("ym download_info %s fail: %s: %s",
                        meta.external_id, type(e).__name__, str(e)[:180])
            return None
        if not infos:
            log.warning("ym download_info %s: пусто (для этого трека нет аудио на ЯМ)",
                        meta.external_id)
            return None
        want = None
        if quality == "flac":
            for i in infos:
                if getattr(i, "codec", "") == "flac":
                    want = i
                    break
        if want is None:
            for prec in (320, 192, 128):
                for i in infos:
                    if getattr(i, "bitrate_in_kbps", 0) == prec:
                        want = i
                        break
                if want:
                    break
        want = want or infos[0]
        try:
            link = await asyncio.to_thread(want.get_direct_link)
            return link
        except Exception as e:
            log.warning("ym get_direct_link %s fail: %s: %s",
                        meta.external_id, type(e).__name__, str(e)[:180])
            return None

async def track_by_id(token: str, external_id: str) -> TrackMeta | None:
    if not token:
        return None
    client = await _client(token)
    try:
        tracks = await asyncio.to_thread(client.tracks, [external_id])
    except Exception:
        return None
    if not tracks:
        return None
    return YandexProvider._from_library(tracks[0])
