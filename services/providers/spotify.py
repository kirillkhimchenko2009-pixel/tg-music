"""Spotify: только метаданные публичных плейлистов, без файлов.

Два пути без пользовательского токена:
1. client_credentials (бесплатный ключ: SPOTIPY_CLIENT_ID/SECRET) — основной,
   отдаёт isrc/год/обложки.
2. Fallback без ключей — SSR-данные embed-страницы (метод spotify-url-info,
   MIT): <script id="__NEXT_DATA__"> → props.pageProps.state.data.entity,
   либо base64-скрипты data-resource / initial-state.
   Embed треков/альбомов отдаёт данные SSR всегда; embed плейлистов —
   зависит от гео IP (из RU отдаёт 404-заглушку), поэтому оба пути в цепочке.

Если ни один путь не дал треки — SpotifyUnavailable с честным текстом.
"""
import asyncio
import base64
import json
import re
from typing import Any

import httpx

from services.providers.base import TrackMeta

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                     "AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
       "Accept-Language": "en-US,en;q=0.9"}

# Embed ходим НАПРЯМУЮ, без общего прокси: прод слушает Spotify из ФРГ,
# а прокси может резать гео-доступ к embed-данным.
_EMBED_TIMEOUT = 25


class SpotifyUnavailable(Exception):
    """Ни ключей, ни работающего embed-пути — честная ошибка для UI."""


def playlist_id_from_url(url: str) -> str | None:
    m = re.search(r"playlist[/:]([A-Za-z0-9]+)", url)
    return m.group(1) if m else None


def _track_from_embed(t: dict) -> TrackMeta | None:
    uri = t.get("uri") or ""
    if "spotify:track:" not in uri:
        return None
    ext = uri.rsplit(":", 1)[-1]
    dur = t.get("duration")
    dur_ms = dur.get("total") if isinstance(dur, dict) else dur
    return TrackMeta(
        source="spotify", external_id=ext,
        title=t.get("title") or t.get("name") or "",
        artist=t.get("subtitle") or "",
        album=None, year=None, isrc=None,
        duration=int(dur_ms or 0) // 1000,
        cover_url=None)


def _entity_from_html(html: str) -> dict | None:
    """entity по трём вариантам spotify-url-info: resource / initial-state / NEXT_DATA."""
    m = re.search(r'<script[^>]*\bdata-resource\b[^>]*type="application/json"[^>]*>'
                  r'(.*?)</script>', html, re.S)
    if m:
        try:
            j = json.loads(base64.b64decode(m.group(1)))
            return j.get("data")
        except Exception:
            pass
    m = re.search(r'<script[^>]*\bid="initial-state"[^>]*type="application/json"[^>]*>'
                  r'(.*?)</script>', html, re.S)
    if m:
        try:
            j = json.loads(base64.b64decode(m.group(1)))
            return j.get("data", {}).get("entity")
        except Exception:
            pass
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
                  html, re.S)
    if not m:
        return None
    try:
        j = json.loads(m.group(1))
    except Exception:
        return None
    pp = j.get("props", {}).get("pageProps", {})
    if pp.get("status") not in (None, 200):   # 404-заглушка без данных
        return None
    return ((pp.get("state") or {}).get("data") or {}).get("entity") or None


async def _embed_fetch(url: str) -> str | None:
    try:
        async with httpx.AsyncClient(headers=_UA, timeout=_EMBED_TIMEOUT,
                                     follow_redirects=True, proxy=None) as c:
            r = await c.get(url)
        return r.text if r.status_code == 200 else None
    except Exception:
        return None


async def _embed_entity(uri_path: str) -> dict | None:
    html = await _embed_fetch(f"https://open.spotify.com/embed/{uri_path}")
    if not html:
        return None
    return _entity_from_html(html)


class SpotifyProvider:
    def __init__(self, client_id: str | None = None, secret: str | None = None):
        import os
        self._client_id = client_id or os.getenv("SPOTIPY_CLIENT_ID") or None
        self._secret = secret or os.getenv("SPOTIPY_CLIENT_SECRET") or None
        self._sp: Any = None

    @property
    def has_credentials(self) -> bool:
        return bool(self._client_id and self._secret)

    def _sp_client(self) -> Any:
        if self._sp is None:
            import spotipy
            from spotipy.oauth2 import SpotifyClientCredentials
            from services.net import spotipy_session
            self._sp = spotipy.Spotify(
                requests_session=spotipy_session(),
                auth_manager=SpotifyClientCredentials(client_id=self._client_id,
                                                      client_secret=self._secret))
        return self._sp

    async def playlist(self, playlist_id: str) -> tuple[str, list[TrackMeta]]:
        if self.has_credentials:
            try:
                return await self._playlist_creds(playlist_id)
            except Exception:
                pass          # ключ есть, но отбит/протух — пробуем embed
        metas = await self._playlist_embed(playlist_id)
        if metas:
            return "Spotify Playlist", metas
        if not self.has_credentials:
            raise SpotifyUnavailable(
                "Spotify: плейлист недоступен без ключей. Добавь бесплатные "
                "SPOTIPY_CLIENT_ID / SPOTIPY_CLIENT_SECRET на сервере")
        raise SpotifyUnavailable("Spotify не отдал плейлист")

    async def _playlist_creds(self, playlist_id: str) -> tuple[str, list[TrackMeta]]:
        sp = self._sp_client()

        def fetch():
            pl = sp.playlist(playlist_id, fields="name")
            tracks, offsets_done = [], 0
            while True:
                page = sp.playlist_items(playlist_id, offset=offsets_done)
                for it in page.get("items") or []:
                    t = it.get("track") or {}
                    if not t.get("id"):
                        continue
                    artists = ", ".join(a.get("name", "") for a in t.get("artists") or [])
                    album = (t.get("album") or {}).get("name")
                    year = (t.get("album") or {}).get("release_date")
                    year = int(year[:4]) if year and year[:4].isdigit() else None
                    isrc = ((t.get("external_ids") or {}).get("isrc"))
                    imgs = ((t.get("album") or {}).get("images") or [])
                    tracks.append(TrackMeta(
                        source="spotify", external_id=t["id"], title=t.get("name") or "",
                        artist=artists, album=album, year=year,
                        isrc=isrc, duration=int(t.get("duration_ms") or 0) // 1000,
                        cover_url=imgs[0]["url"] if imgs else None))
                offsets_done += 100
                if page.get("next") is None:
                    break
            return pl.get("name") or "Spotify Playlist", tracks

        return await asyncio.to_thread(fetch)

    async def _playlist_embed(self, playlist_id: str) -> list[TrackMeta]:
        ent = await _embed_entity(f"playlist/{playlist_id}")
        if not ent:
            return []
        out = []
        for t in ent.get("trackList") or []:
            m = _track_from_embed(t)
            if m:
                out.append(m)
        return out

    async def track(self, track_id: str) -> TrackMeta:
        if self.has_credentials:
            try:
                sp = self._sp_client()
                t = await asyncio.to_thread(sp.track, track_id)
                artists = ", ".join(a.get("name", "") for a in t.get("artists") or [])
                album = t.get("album") or {}
                year = album.get("release_date") or ""
                imgs = album.get("images") or []
                return TrackMeta(
                    source="spotify", external_id=t["id"], title=t.get("name") or "",
                    artist=artists, album=album.get("name"),
                    year=int(year[:4]) if year[:4].isdigit() else None,
                    isrc=(t.get("external_ids") or {}).get("isrc"),
                    duration=int(t.get("duration_ms") or 0) // 1000,
                    cover_url=imgs[0]["url"] if imgs else None)
            except Exception:
                pass
        ent = await _embed_entity(f"track/{track_id}")
        m = _track_from_embed(ent) if ent else None
        if m is None:
            raise SpotifyUnavailable("Spotify не отдал трек")
        return m
