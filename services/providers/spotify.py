"""Spotify: только метаданные публичных плейлистов (client_credentials), без файлов."""
import asyncio
import re
from typing import Any

from services.providers.base import TrackMeta

def playlist_id_from_url(url: str) -> str | None:
    m = re.search(r"playlist[/:]([A-Za-z0-9]+)", url)
    return m.group(1) if m else None

class SpotifyProvider:
    def __init__(self, client_id: str | None = None, secret: str | None = None):
        self._client_id = client_id
        self._secret = secret
        self._sp: Any = None

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
        sp = self._sp_client()
        def fetch():
            pl = sp.playlist(playlist_id, fields="name")
            tracks, total, offsets_done = [], None, 0
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

    async def track(self, track_id: str) -> TrackMeta:
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
