"""SoundCloud: только официальный API и только треки с флагом downloadable."""
from __future__ import annotations

import logging
import time
from urllib.parse import quote

from ..domain import AudioSource, PermissionInfo, ProviderCapabilities, TrackCandidate
from ..enums import PermissionStatus, ProviderHealth, TrackVersion
from ..errors import ProviderAuthError, ProviderError, ProviderRateLimited
from .base import MusicProvider

log = logging.getLogger("music.provider.soundcloud")

_API = "https://api.soundcloud.com"
_TOKEN_URL = "https://secure.soundcloud.com/oauth/token"
_LICENSE = {
    "cc-by": "CC BY", "cc-by-sa": "CC BY-SA", "cc-by-nd": "CC BY-ND",
    "cc-by-nc": "CC BY-NC", "cc-by-nc-sa": "CC BY-NC-SA",
    "cc-by-nc-nd": "CC BY-NC-ND", "to-share": "SoundCloud To Share",
}


class SoundCloudProvider(MusicProvider):
    name = "soundcloud"
    display_name = "SoundCloud"
    priority = 30
    capabilities = ProviderCapabilities(search=True, direct_link=False,
                                        metadata=True, cover=True,
                                        download=True, stream=True,
                                        requires_auth=True,
                                        notes="скачивание только downloadable-треков")
    api_hosts = ("api.soundcloud.com", "secure.soundcloud.com",
                 "soundcloud.com", "www.soundcloud.com", "sndcdn.com")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._token: str = ""
        self._token_expires: float = 0.0

    @property
    def configured(self) -> bool:
        return bool(self.settings.soundcloud_client_id)

    def status(self) -> ProviderHealth:
        return ProviderHealth.UNKNOWN if self.configured else ProviderHealth.UNCONFIGURED

    async def _auth(self) -> tuple[dict[str, str], dict[str, str]]:
        if not self.configured:
            raise ProviderAuthError("SoundCloud: не задан SOUNDCLOUD_CLIENT_ID")
        params: dict[str, str] = {}
        auth_header: dict[str, str] = {}
        if self._token and time.time() < self._token_expires - 30:
            auth_header = {"Authorization": f"OAuth {self._token}"}
            return params, auth_header
        if self.settings.soundcloud_client_secret:
            body = ("grant_type=client_credentials"
                    f"&client_id={quote(self.settings.soundcloud_client_id)}"
                    f"&client_secret={quote(self.settings.soundcloud_client_secret)}")
            try:
                async with self.http() as client:
                    data = await client.post_json(
                        _TOKEN_URL, data=body,
                        headers={"Content-Type": "application/x-www-form-urlencoded"})
                token = str(data.get("access_token") or "")
                if token:
                    self._token = token
                    self._token_expires = time.time() + int(data.get("expires_in") or 3600)
                    return {}, {"Authorization": f"OAuth {token}"}
            except Exception as exc:  # noqa: BLE001
                log.info("soundcloud oauth недоступен, пробуем client_id: %s", exc)
        params["client_id"] = self.settings.soundcloud_client_id
        return params, auth_header

    async def _api_get(self, path: str, params: dict | None = None) -> dict:
        auth_params, headers = await self._auth()
        merged = {**(params or {}), **auth_params}
        url = path if path.startswith("http") else f"{_API}{path}"
        async with self.http() as client:
            try:
                return await client.get_json(url, params=merged, headers=headers)
            except ProviderError as exc:
                if "HTTP_401" in exc.code or "HTTP_403" in exc.code:
                    raise ProviderAuthError("SoundCloud: ключ отклонён",
                                            code="SC_AUTH") from exc
                if "HTTP_429" in exc.code:
                    raise ProviderRateLimited("SoundCloud: лимит API") from exc
                raise

    async def search(self, query: str, limit: int = 8) -> list[TrackCandidate]:
        data = await self._api_get("/tracks", {
            "q": self.clean_query(query), "limit": max(1, min(limit, 50)),
            "linked_partitioning": "1", "access": "playable",
        })
        rows = data.get("collection") if isinstance(data.get("collection"), list) else [data]
        out: list[TrackCandidate] = []
        for row in rows:
            if isinstance(row, dict) and row.get("id"):
                try:
                    out.append(self._parse(row))
                except Exception as exc:  # noqa: BLE001
                    log.debug("soundcloud: пропуск записи: %s", exc)
        return out[:limit]

    async def get_track(self, external_id: str) -> TrackCandidate | None:
        value = str(external_id or "").strip()
        if value.startswith(("http://", "https://")):
            data = await self._api_get("/resolve", {"url": value})
        else:
            data = await self._api_get(f"/tracks/{quote(value, safe='')}")
        return self._parse(data) if data.get("id") else None

    def _parse(self, row: dict) -> TrackCandidate:
        track_id = str(row.get("id") or "")
        if not track_id:
            raise ProviderError("SoundCloud: запись без id", code="SC_PARSE")
        downloadable = bool(row.get("downloadable"))
        download_url = str(row.get("download_url") or "")
        stream_url = str(row.get("stream_url") or "")
        if downloadable and not download_url:
            download_url = f"{_API}/tracks/{track_id}/download"
        permission = (PermissionStatus.DOWNLOAD_ALLOWED if downloadable
                      else PermissionStatus.STREAM_ONLY)
        artwork = row.get("artwork_url") or (row.get("user") or {}).get("avatar_url")
        if artwork:
            artwork = str(artwork).replace("-large", "-t500x500")
        user = row.get("user") or {}
        created = str(row.get("created_at") or "")
        license_code = str(row.get("license") or "")
        title = str(row.get("title") or "Без названия")
        return self.make_candidate(
            external_id=track_id,
            title=title,
            artist=str(user.get("username") or row.get("artist") or ""),
            album=str(row.get("album") or ""),
            duration=int(round((row.get("duration") or 0) / 1000)),
            year=int(created[:4]) if created[:4].isdigit() else None,
            cover_url=artwork,
            source_url=str(row.get("permalink_url") or f"https://soundcloud.com/tracks/{track_id}"),
            permission=permission,
            license_name=_LICENSE.get(license_code, "SoundCloud"),
            license_url="",
            version=self._version(title),
            bitrate_kbps=None,
            file_format="mp3" if downloadable else None,
            downloadable=downloadable,
            streamable=bool(stream_url) and bool(row.get("streamable", True)),
            extra={"download_url": download_url, "stream_url": stream_url,
                   "genre": row.get("genre") or "", "downloadable": downloadable},
        )

    async def get_download_permissions(self, track: TrackCandidate) -> PermissionInfo:
        if (track.extra or {}).get("downloadable"):
            return PermissionInfo(
                status=PermissionStatus.DOWNLOAD_ALLOWED,
                reason="SoundCloud: автор включил официальное скачивание",
                source_url=self.get_source_url(track),
                license_name=track.license_name, license_url=track.license_url)
        fresh = await self.get_track(track.external_id)
        if fresh and fresh.permission is PermissionStatus.DOWNLOAD_ALLOWED:
            track.extra.update(fresh.extra)
            return PermissionInfo(status=fresh.permission,
                                  reason="SoundCloud: автор включил скачивание",
                                  source_url=fresh.source_url,
                                  license_name=fresh.license_name)
        return PermissionInfo(
            status=PermissionStatus.STREAM_ONLY,
            reason="SoundCloud: скачивание автором не разрешено",
            source_url=self.get_source_url(track))

    async def get_audio_stream(self, track: TrackCandidate) -> AudioSource:
        perm = await self.get_download_permissions(track)
        if not perm.can_download:
            raise ProviderError(perm.reason, code="PERMISSION_DENIED")
        auth_params, headers = await self._auth()
        url = str((track.extra or {}).get("download_url") or f"{_API}/tracks/{track.external_id}/download")
        if "?" not in url:
            url += "?" + "&".join(f"{k}={quote(v)}" for k, v in auth_params.items())
        return AudioSource(url=url, headers=headers, mime="audio/mpeg",
                           filename=f"soundcloud-{track.external_id}.mp3",
                           expected_format="mp3")

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
            await self._api_get("/tracks", {"q": "music", "limit": 1})
            return ProviderHealth.OK
        except Exception as exc:  # noqa: BLE001
            log.info("soundcloud health: %s", exc)
            return ProviderHealth.DOWN
