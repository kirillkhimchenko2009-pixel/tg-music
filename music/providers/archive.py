"""Internet Archive: поиск по метаданным, скачивание только при явной лицензии."""
from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import quote

from ..domain import AudioSource, PermissionInfo, ProviderCapabilities, TrackCandidate
from ..enums import PermissionStatus, ProviderHealth, TrackVersion
from ..errors import ProviderError, ProviderUnavailable
from .base import MusicProvider

log = logging.getLogger("music.provider.archive")

_SEARCH = "https://archive.org/advancedsearch.php"
_META = "https://archive.org/metadata/{identifier}"
_DETAILS = "https://archive.org/details/{identifier}"
_DOWNLOAD = "https://archive.org/download/{identifier}/{name}"
_COVER = "https://archive.org/services/img/{identifier}"

_AUDIO_FORMATS = {
    "flac": (120, "flac", None),
    "wave": (110, "wav", None),
    "wav": (110, "wav", None),
    "320kbps mp3": (100, "mp3", 320),
    "vbr mp3": (90, "mp3", 220),
    "mp3": (85, "mp3", 192),
    "ogg vorbis": (80, "ogg", 192),
    "128kbps mp3": (55, "mp3", 128),
    "64kbps mp3": (35, "mp3", 64),
    "24bit flac": (125, "flac", None),
    "shorten": (40, "shn", None),
}
_EXCLUDE = re.compile(
    r"(spectrogram|waveform|thumbnail|sample|preview|\.jpg$|\.png$|\.gif$|"
    r"columbia peaks|essentia|metadata|item tile|__ia_thumb)", re.I)
_CC_RE = re.compile(r"(creativecommons\.org|publicdomain/zero|publicdomain/mark)", re.I)
_PD_RE = re.compile(r"(public domain|publicdomain|cc0|pdm)", re.I)


def _duration(value) -> int:
    if value is None or value == "":
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    text = str(value).strip()
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return int(float(text))
    parts = text.split(":")
    if len(parts) in (2, 3) and all(p.isdigit() for p in parts):
        total = 0
        for p in parts:
            total = total * 60 + int(p)
        return total
    return 0


def _as_list(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    return [str(value)]


class ArchiveProvider(MusicProvider):
    name = "archive"
    display_name = "Internet Archive"
    priority = 20
    capabilities = ProviderCapabilities(search=True, direct_link=False,
                                        metadata=True, cover=True,
                                        download=True, stream=True,
                                        requires_auth=False,
                                        notes="только явные CC/PD лицензии")
    api_hosts = ("archive.org",)

    @property
    def enabled(self) -> bool:
        return self.settings.archive_enabled

    def status(self) -> ProviderHealth:
        return ProviderHealth.UNKNOWN if self.enabled else ProviderHealth.DISABLED

    async def search(self, query: str, limit: int = 8) -> list[TrackCandidate]:
        if not self.enabled:
            return []
        q = f"mediatype:(audio) AND ({self.clean_query(query)})"
        params = [
            ("q", q), ("output", "json"),
            ("rows", str(max(1, min(limit * 3, 40)))), ("page", "1"),
            ("fl[]", "identifier"), ("fl[]", "title"), ("fl[]", "creator"),
            ("fl[]", "year"), ("fl[]", "date"), ("fl[]", "licenseurl"),
            ("fl[]", "collection"), ("fl[]", "format"), ("fl[]", "downloads"),
        ]
        async with self.http() as client:
            data = await client.get_json(_SEARCH, params=params)
        docs = ((data.get("response") or {}).get("docs") or [])
        candidates: list[TrackCandidate] = []
        sem = asyncio.Semaphore(3)

        async def enrich(doc: dict) -> TrackCandidate | None:
            identifier = str(doc.get("identifier") or "")
            if not identifier:
                return None
            async with sem:
                try:
                    async with self.http() as client:
                        meta = await asyncio.wait_for(
                            client.get_json(_META.format(identifier=quote(identifier, safe=""))),
                            timeout=self.settings.provider_timeout)
                except Exception as exc:  # noqa: BLE001
                    log.debug("archive: metadata %s: %s", identifier, exc)
                    return None
            return self._parse(doc, meta)

        results = await asyncio.gather(*(enrich(d) for d in docs[:max(1, limit * 2)]))
        for item in results:
            if item is not None:
                candidates.append(item)
            if len(candidates) >= limit:
                break
        return candidates

    async def get_track(self, external_id: str) -> TrackCandidate | None:
        identifier = str(external_id)
        async with self.http() as client:
            meta = await client.get_json(_META.format(identifier=quote(identifier, safe="")))
        return self._parse({"identifier": identifier}, meta)

    def _parse(self, doc: dict, meta: dict) -> TrackCandidate | None:
        metadata = meta.get("metadata") or {}
        if isinstance(metadata, list):  # некоторые ответы отдают список словарей
            merged: dict = {}
            for part in metadata:
                if isinstance(part, dict):
                    merged.update(part)
            metadata = merged
        identifier = str(metadata.get("identifier") or "")
        if not identifier:
            identifier = str(doc.get("identifier") or "")
        if not identifier:
            return None
        files = meta.get("files") or []
        best = self._pick_file(files)
        if not best:
            return None
        file_name = str(best.get("name") or "")
        fmt = str(best.get("format") or "").lower()
        rank, ext, bitrate = _AUDIO_FORMATS.get(fmt, (20, _ext_from_name(file_name), None))
        if ext == "bin":
            return None
        title = (str(best.get("title") or "").strip()
                 or str(metadata.get("title") or "").strip() or "Без названия")
        artist = ", ".join(_as_list(metadata.get("creator"))[:3])
        album = str(metadata.get("album") or metadata.get("title") or "")
        year = self._year(metadata)
        license_url = str(metadata.get("licenseurl") or "").strip()
        rights = str(metadata.get("rights") or metadata.get("license") or "").strip()
        permission, license_name = self._permission(license_url, rights, metadata)
        duration = _duration(best.get("length") or metadata.get("length"))
        size = best.get("size")
        try:
            size_int = int(size) if size is not None and str(size).isdigit() else None
        except (TypeError, ValueError):
            size_int = None
        return self.make_candidate(
            external_id=identifier,
            title=title,
            artist=artist,
            album=album,
            duration=duration,
            year=year,
            cover_url=_COVER.format(identifier=quote(identifier, safe="")),
            source_url=_DETAILS.format(identifier=quote(identifier, safe="")),
            permission=permission,
            license_name=license_name,
            license_url=license_url,
            version=self._version(title),
            bitrate_kbps=bitrate,
            file_format=ext,
            file_size=size_int,
            downloadable=permission is PermissionStatus.DOWNLOAD_ALLOWED,
            streamable=True,
            extra={
                "identifier": identifier,
                "file_name": file_name,
                "archive_format": best.get("format"),
                "collection": _as_list(metadata.get("collection")),
                "rights": rights,
                "all_files": [str(f.get("name")) for f in files
                              if f.get("name") and not _EXCLUDE.search(str(f.get("name")))][:30],
            },
        )

    @staticmethod
    def _pick_file(files: list[dict]) -> dict | None:
        best: tuple[int, dict] | None = None
        for f in files:
            name = str(f.get("name") or "")
            fmt = str(f.get("format") or "").lower()
            if not name or _EXCLUDE.search(name):
                continue
            rank = _AUDIO_FORMATS.get(fmt, (None, None, None))[0]
            if rank is None:
                continue
            if best is None or rank > best[0]:
                best = (rank, f)
        return best[1] if best else None

    @staticmethod
    def _year(metadata: dict) -> int | None:
        for key in ("year", "date", "publicdate", "addeddate"):
            raw = str(metadata.get(key) or "")
            m = re.search(r"(19|20)\d{2}", raw)
            if m:
                return int(m.group(0))
        return None

    @staticmethod
    def _permission(license_url: str, rights: str, metadata: dict) -> tuple[PermissionStatus, str]:
        blob = f"{license_url} {rights}"
        if _CC_RE.search(blob):
            return PermissionStatus.DOWNLOAD_ALLOWED, "Creative Commons"
        if _PD_RE.search(blob):
            return PermissionStatus.DOWNLOAD_ALLOWED, "Public Domain"
        return (PermissionStatus.OFFICIAL_LINK_ONLY,
                "Лицензия не указана явно — доступна только страница источника")

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

    async def get_download_permissions(self, track: TrackCandidate) -> PermissionInfo:
        if track.permission is PermissionStatus.DOWNLOAD_ALLOWED and track.extra.get("file_name"):
            return PermissionInfo(
                status=track.permission,
                reason="Internet Archive: явная свободная лицензия",
                source_url=self.get_source_url(track),
                license_name=track.license_name, license_url=track.license_url)
        fresh = await self.get_track(track.external_id)
        if fresh and fresh.permission is PermissionStatus.DOWNLOAD_ALLOWED:
            track.extra.update(fresh.extra)
            return PermissionInfo(status=fresh.permission,
                                  reason="Internet Archive: явная свободная лицензия",
                                  source_url=fresh.source_url,
                                  license_name=fresh.license_name,
                                  license_url=fresh.license_url)
        return PermissionInfo(
            status=(fresh.permission if fresh else PermissionStatus.LICENSE_UNKNOWN),
            reason="Internet Archive: лицензия не подтверждена",
            source_url=self.get_source_url(track),
            license_name=track.license_name, license_url=track.license_url)

    async def get_audio_stream(self, track: TrackCandidate) -> AudioSource:
        perm = await self.get_download_permissions(track)
        if not perm.can_download:
            raise ProviderError(perm.reason, code="PERMISSION_DENIED")
        identifier = str(track.extra.get("identifier") or track.external_id)
        file_name = str(track.extra.get("file_name") or "")
        if not file_name:
            fresh = await self.get_track(identifier)
            file_name = str((fresh.extra or {}).get("file_name") or "") if fresh else ""
        if not file_name:
            raise ProviderUnavailable("Archive: аудиофайл не найден", code="ARCHIVE_NO_FILE")
        url = _DOWNLOAD.format(identifier=quote(identifier, safe=""),
                               name=quote(file_name, safe="/"))
        ext = _ext_from_name(file_name)
        return AudioSource(url=url, mime=self._mime(ext),
                           filename=f"archive-{identifier}{'.' + ext if ext else ''}",
                           expected_format=ext)

    @staticmethod
    def _mime(ext: str) -> str:
        return {"mp3": "audio/mpeg", "flac": "audio/flac", "wav": "audio/wav",
                "ogg": "audio/ogg", "opus": "audio/opus", "m4a": "audio/mp4"}.get(
            ext, "application/octet-stream")

    async def health_check(self) -> ProviderHealth:
        if not self.enabled:
            return ProviderHealth.DISABLED
        try:
            async with self.http() as client:
                await client.get_json(_SEARCH, params={
                    "q": "mediatype:(audio)", "output": "json", "rows": "1",
                    "fl[]": "identifier"})
            return ProviderHealth.OK
        except Exception as exc:  # noqa: BLE001
            log.info("archive health: %s", exc)
            return ProviderHealth.DOWN


def _ext_from_name(name: str) -> str:
    name = (name or "").lower().split("?")[0]
    if "." not in name:
        return "bin"
    ext = name.rsplit(".", 1)[-1]
    return {"mpeg": "mp3", "mp4": "m4a", "oga": "ogg"}.get(ext, ext)
