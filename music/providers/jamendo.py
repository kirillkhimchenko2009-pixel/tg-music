"""Jamendo: официальный API, скачивание только при `audiodownload_allowed`."""
from __future__ import annotations

import logging
from urllib.parse import quote

from ..domain import AudioSource, PermissionInfo, ProviderCapabilities, TrackCandidate
from ..enums import PermissionStatus, ProviderHealth, TrackVersion
from ..errors import ProviderAuthError, ProviderError, ProviderUnavailable
from .base import MusicProvider

log = logging.getLogger("music.provider.jamendo")

_API = "https://api.jamendo.com/v3.0"
_LICENSE_NAMES = {
    "by": "CC BY", "by-sa": "CC BY-SA", "by-nc": "CC BY-NC",
    "by-nc-sa": "CC BY-NC-SA", "by-nd": "CC BY-ND", "by-nc-nd": "CC BY-NC-ND",
    "zero": "CC0", "publicdomain": "Public Domain",
}


class JamendoProvider(MusicProvider):
    name = "jamendo"
    display_name = "Jamendo"
    priority = 10
    capabilities = ProviderCapabilities(search=True, direct_link=False,
                                        metadata=True, cover=True,
                                        download=True, stream=True,
                                        requires_auth=True,
                                        notes="Creative Commons каталог; MP3 320")
    api_hosts = ("api.jamendo.com", "prod-1.storage.jamendo.com",
                 "storage.jamendo.com", "jamendo.com", "www.jamendo.com",
                 "imgjam.com")

    @property
    def configured(self) -> bool:
        return bool(self.settings.jamendo_client_id)

    def status(self) -> ProviderHealth:
        return ProviderHealth.UNKNOWN if self.configured else ProviderHealth.UNCONFIGURED

    def _base_params(self) -> dict:
        return {"client_id": self.settings.jamendo_client_id,
                "format": "json", "audioformat": "mp32"}

    async def _get(self, path: str, params: dict) -> dict:
        if not self.configured:
            raise ProviderAuthError("Jamendo: не задан JAMENDO_CLIENT_ID")
        url = f"{_API}/{path}"
        async with self.http() as client:
            data = await client.get_json(url, params={**self._base_params(), **params})
        headers = data.get("headers") or {}
        if headers.get("status") not in (None, "success"):
            raise ProviderError(f"Jamendo: {headers.get('error_message') or 'ошибка API'}",
                                code="JAMENDO_API")
        return data

    async def search(self, query: str, limit: int = 8) -> list[TrackCandidate]:
        data = await self._get("tracks/", {
            "search": self.clean_query(query),
            "limit": max(1, min(limit, 50)),
            "include": "musicinfo+licenses",
            "order": "relevance",
        })
        out: list[TrackCandidate] = []
        for row in data.get("results") or []:
            try:
                out.append(self._parse(row))
            except Exception as exc:  # noqa: BLE001
                log.debug("jamendo: пропуск битой записи: %s", exc)
        return out

    async def get_track(self, external_id: str) -> TrackCandidate | None:
        data = await self._get("tracks/", {
            "id": str(external_id), "limit": 1, "include": "musicinfo+licenses",
        })
        rows = data.get("results") or []
        return self._parse(rows[0]) if rows else None

    def _parse(self, row: dict) -> TrackCandidate:
        track_id = str(row.get("id") or "")
        if not track_id:
            raise ProviderError("Jamendo: запись без id", code="JAMENDO_PARSE")
        allowed = bool(row.get("audiodownload_allowed"))
        download_url = row.get("audiodownload") or ""
        stream_url = row.get("audio") or ""
        license_url = row.get("license_ccurl") or ""
        license_name = self._license_name(license_url)
        if allowed and not download_url:
            # API иногда отдаёт ссылку только в audio; не выдаём stream за download
            allowed = False
        permission = (PermissionStatus.DOWNLOAD_ALLOWED if allowed
                      else PermissionStatus.STREAM_ONLY)
        duration = int(row.get("duration") or 0)
        released = str(row.get("releasedate") or "")
        year = int(released[:4]) if released[:4].isdigit() else None
        cover = row.get("image") or row.get("album_image") or None
        return self.make_candidate(
            external_id=track_id,
            title=str(row.get("name") or "Без названия"),
            artist=str(row.get("artist_name") or ""),
            album=str(row.get("album_name") or ""),
            duration=duration,
            year=year,
            cover_url=cover,
            source_url=(row.get("shareurl") or row.get("shorturl")
                        or f"https://www.jamendo.com/track/{track_id}"),
            permission=permission,
            license_name=license_name,
            license_url=license_url,
            version=self._detect_version(str(row.get("name") or "")),
            explicit=False,
            bitrate_kbps=320 if allowed else None,
            file_format="mp3",
            downloadable=allowed,
            streamable=bool(stream_url),
            extra={"download_url": download_url, "stream_url": stream_url,
                   "audiodownload_allowed": allowed},
        )

    async def get_download_permissions(self, track: TrackCandidate) -> PermissionInfo:
        extra = track.extra or {}
        if extra.get("audiodownload_allowed") and extra.get("download_url"):
            return PermissionInfo(
                status=PermissionStatus.DOWNLOAD_ALLOWED,
                reason="Jamendo: автор разрешил скачивание",
                source_url=self.get_source_url(track),
                license_name=track.license_name, license_url=track.license_url)
        fresh = await self.get_track(track.external_id)
        if fresh and fresh.permission is PermissionStatus.DOWNLOAD_ALLOWED:
            track.extra.update(fresh.extra)
            return PermissionInfo(status=fresh.permission,
                                  reason="Jamendo: скачивание разрешено",
                                  source_url=fresh.source_url,
                                  license_name=fresh.license_name,
                                  license_url=fresh.license_url)
        return PermissionInfo(
            status=PermissionStatus.STREAM_ONLY,
            reason="Jamendo: автор не разрешил скачивание файла",
            source_url=self.get_source_url(track),
            license_name=track.license_name, license_url=track.license_url)

    async def get_audio_stream(self, track: TrackCandidate) -> AudioSource:
        perm = await self.get_download_permissions(track)
        if not perm.can_download:
            raise ProviderError(perm.reason or "Jamendo: скачивание запрещено",
                                code="PERMISSION_DENIED")
        url = (track.extra or {}).get("download_url") or ""
        if not url:
            fresh = await self.get_track(track.external_id)
            url = (fresh.extra or {}).get("download_url", "") if fresh else ""
        if not url:
            raise ProviderUnavailable("Jamendo: ссылка на файл не выдана",
                                      code="JAMENDO_NO_FILE")
        return AudioSource(url=url, mime="audio/mpeg", filename=f"jamendo-{track.external_id}.mp3",
                           expected_format="mp3")

    @staticmethod
    def _license_name(url: str) -> str:
        if not url:
            return ""
        tail = url.rstrip("/").split("/")[-1].lower()
        return _LICENSE_NAMES.get(tail, "Creative Commons" if "creativecommons" in url else url)

    @staticmethod
    def _detect_version(title: str) -> TrackVersion:
        low = title.lower()
        if "remix" in low or "rmx" in low:
            return TrackVersion.REMIX
        if "acoustic" in low:
            return TrackVersion.ACOUSTIC
        if "live" in low:
            return TrackVersion.LIVE
        if "instrumental" in low:
            return TrackVersion.INSTRUMENTAL
        return TrackVersion.ORIGINAL

    async def health_check(self) -> ProviderHealth:
        if not self.configured:
            return ProviderHealth.UNCONFIGURED
        try:
            await self._get("tracks/", {"limit": 1})
            return ProviderHealth.OK
        except Exception as exc:  # noqa: BLE001
            log.info("jamendo health: %s", exc)
            return ProviderHealth.DOWN
