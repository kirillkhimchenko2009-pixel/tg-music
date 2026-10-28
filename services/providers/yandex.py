"""Яндекс Музыка: поиск/плейлисты/прямые ссылки (нужен токен).

Публичные плейлисты по ссылке читаются АНОНИМНО через
api.music.yandex.net (подтверждено 2026-10: /users/<login>/playlists/<kind>
и /playlist/<uuid> отдают полный JSON без OAuth)."""
import asyncio
from typing import Any

from core.config import settings
from services.providers.base import TrackMeta

URL = "https://api.music.yandex.net"

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
        cover_url=cover_url)


async def anon_playlist_by_login(owner: str, kind: str) -> tuple[str, list[TrackMeta]]:
    """Публичный плейлист по ссылкам вида /users/<login>/playlists/<kind> — без токена."""
    import httpx
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
        r = await c.get(f"{URL}/users/{owner}/playlists/{kind}")
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
    import httpx
    candidates = [uuid]
    if "." in uuid:
        bare = uuid.split(".", 1)[1]
        if bare not in candidates:
            candidates.append(bare)
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
        last_err = None
        for u in candidates:
            r = await c.get(f"{URL}/playlist/{u}")
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
    pl = await asyncio.to_thread(client.users_playlists, int(kind), int(owner_s))
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
            cover_url=cover_url))
    return (getattr(pl, "title", None) or "Плейлист Яндекс"), metas

async def _client(token: str) -> Any:
    from yandex_music import Client
    if not token:
        raise RuntimeError("нужен токен Яндекс Музыки (/YM_TOKEN или /token)")
    return await asyncio.to_thread(lambda: Client(token).init())

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
        except Exception:
            return []
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
                         cover_url=cover)

    async def track_url(self, meta: TrackMeta, quality: str) -> str | None:
        """Прямая ссылка на файл (flac при Plus; 320 иначе)."""
        client = await self._ym()
        try:
            infos = await asyncio.to_thread(client.tracks_download_info, meta.external_id)
        except Exception:
            return None
        if not infos:
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
        except Exception:
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
