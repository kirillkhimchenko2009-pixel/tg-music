"""Spotify: только метаданные и официальная ссылка (никаких файлов/DRM)."""
from __future__ import annotations

import base64
import logging
import time
from urllib.parse import quote

from ..domain import AudioSource, PermissionInfo, ProviderCapabilities, TrackCandidate
from ..enums import PermissionStatus, ProviderHealth, TrackVersion
from ..errors import NotSupportedError, ProviderAuthError, ProviderRateLimited
from .base import MusicProvider

log = logging.getLogger("music.provider.spotify")

_TOKEN_URL = "https://accounts.spotify.com/api/token"
_API = "https://api.spotify.com/v1"


class SpotifyProvider(MusicProvider):
    name = "spotify"
    display_name = "Spotify"
    priority = 40
    capabilities = ProviderCapabilities(search=True, direct_link=True,
                                        metadata=True, cover=True,
                                        download=False, stream=False,
                                        requires_auth=True,
                                        notes="только метаданные и официальная ссылка")
    api_hosts = ("api.spotify.com", "accounts.spotify.com", "open.spotify.com",
                 "i.scdn.co", "mosaic.scdn.co", "image-cdn-ak.spotifycdn.com",
                 "image-cdn-fa.spotifycdn.com")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._token = ""
        self._expires = 0.0

    @property
    def configured(self) -> bool:
        return bool(self.settings.spotify_client_id and self.settings.spotify_client_secret)

    def status(self) -> ProviderHealth:
        return ProviderHealth.UNKNOWN if self.configured else ProviderHealth.UNCONFIGURED

    async def _get_token(self) -> str:
        if not self.configured:
            raise ProviderAuthError("Spotify: не заданы SPOTIFY_CLIENT_ID/SECRET")
        if self._token and time.time() < self._expires - 60:
            return self._token
        raw = f"{self.settings.spotify_client_id}:{self.settings.spotify_client_secret}"
        basic = base64.b64encode(raw.encode()).decode()
        async with self.http() as client:
            data = await client.post_json(
                _TOKEN_URL, data="grant_type=client_credentials",
                headers={"Authorization": f"Basic {basic}",
                         "Content-Type": "application/x-www-form-urlencoded"})
        token = str(data.get("access_token") or "")
        if not token:
            raise ProviderAuthError("Spotify: токен не получен")
        self._token = token
        self._expires = time.time() + int(data.get("expires_in") or 3600)
        return token

    async def _api(self, path: str, params: dict | None = None) -> dict:
        token = await self._get_token()
        try:
            async with self.http() as client:
                return await client.get_json(f"{_API}{path}", params=params,
                                             headers={"Authorization": f"Bearer {token}"})
        except Exception as exc:  # noqa: BLE001
            code = getattr(exc, "code", "")
            if code in ("HTTP_401", "HTTP_403"):
                self._token = ""
                raise ProviderAuthError("Spotify: токен отклонён", code="SPOTIFY_AUTH") from exc
            if code == "HTTP_429":
                raise ProviderRateLimited("Spotify: лимит API") from exc
            raise

    async def search(self, query: str, limit: int = 8) -> list[TrackCandidate]:
        data = await self._api("/search", {
            "q": self.clean_query(query), "type": "track",
            "limit": max(1, min(limit, 50)), "market": "from_token",
        })
        items = ((data.get("tracks") or {}).get("items") or [])
        out = []
        for row in items:
            try:
                out.append(self._parse(row))
            except Exception as exc:  # noqa: BLE001
                log.debug("spotify: пропуск записи: %s", exc)
        return out

    async def get_track(self, external_id: str) -> TrackCandidate | None:
        track_id = self.normalize_id(external_id)
        if not track_id:
            return None
        data = await self._api(f"/tracks/{quote(track_id, safe='')}")
        return self._parse(data) if data.get("id") else None

    def _parse(self, row: dict) -> TrackCandidate:
        images = ((row.get("album") or {}).get("images") or [])
        cover = ""
        if images:
            best = max(images, key=lambda i: int(i.get("width") or 0) * int(i.get("height") or 0))
            cover = str(best.get("url") or "")
        artists = row.get("artists") or []
        artist = ", ".join(str(a.get("name") or "") for a in artists if a.get("name"))
        release = str((row.get("album") or {}).get("release_date") or "")
        return self.make_candidate(
            external_id=str(row.get("id") or ""),
            title=str(row.get("name") or "Без названия"),
            artist=artist,
            album=str((row.get("album") or {}).get("name") or ""),
            album_artist=artist,
            duration=int(round((row.get("duration_ms") or 0) / 1000)),
            year=int(release[:4]) if release[:4].isdigit() else None,
            cover_url=cover or None,
            source_url=str((row.get("external_urls") or {}).get("spotify")
                           or f"https://open.spotify.com/track/{row.get('id')}"),
            permission=PermissionStatus.OFFICIAL_LINK_ONLY,
            license_name="Spotify (стриминг)",
            license_url="",
            isrc=((row.get("external_ids") or {}).get("isrc") or None),
            version=self._version(str(row.get("name") or "")),
            explicit=bool(row.get("explicit")),
            file_format=None,
            downloadable=False,
            streamable=False,
            extra={"preview_url": row.get("preview_url") or "",
                   "popularity": row.get("popularity") or 0},
        )

    async def get_download_permissions(self, track: TrackCandidate) -> PermissionInfo:
        return PermissionInfo(
            status=PermissionStatus.OFFICIAL_LINK_ONLY,
            reason="Spotify не предоставляет аудиофайлы; доступна официальная ссылка",
            source_url=self.get_source_url(track), license_name=track.license_name)

    async def get_audio_stream(self, track: TrackCandidate) -> AudioSource:
        raise NotSupportedError("Spotify: скачивание запрещено (только метаданные)",
                                code="SPOTIFY_NO_DOWNLOAD")

    @staticmethod
    def normalize_id(value: str) -> str:
        value = (value or "").strip()
        if "open.spotify.com" in value:
            part = value.split("/track/", 1)[-1].split("?", 1)[0]
            return part.split("/", 1)[0]
        return value.removeprefix("spotify:track:")

    @staticmethod
    def _version(title: str) -> TrackVersion:
        low = title.lower()
        if "remix" in low:
            return TrackVersion.REMIX
        if "live" in low:
            return TrackVersion.LIVE
        if "acoustic" in low:
            return TrackVersion.ACOUSTIC
        if "instrumental" in low:
            return TrackVersion.INSTRUMENTAL
        return TrackVersion.ORIGINAL

    async def health_check(self) -> ProviderHealth:
        if not self.configured:
            return ProviderHealth.UNCONFIGURED
        try:
            await self._api("/search", {"q": "music", "type": "track", "limit": 1})
            return ProviderHealth.OK
        except Exception as exc:  # noqa: BLE001
            log.info("spotify health: %s", exc)
            return ProviderHealth.DOWN
