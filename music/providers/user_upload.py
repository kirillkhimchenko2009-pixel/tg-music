"""Пользовательский файл: единственный источник с безусловным разрешением.

Файл уже принадлежит пользователю; мы не скачиваем его из сети. Путь обязан
находиться внутри каталогов музыкальной системы (защита от path traversal).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from ..config import music_settings
from ..domain import AudioSource, PermissionInfo, ProviderCapabilities, TrackCandidate
from ..enums import PermissionStatus, ProviderHealth, TrackVersion
from ..errors import PermissionDenied, ProviderError
from ..storage import sha256_file
from .base import MusicProvider

log = logging.getLogger("music.provider.user")

_AUDIO_EXT = {".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".opus", ".webm"}
_TAG_KEYS = {
    "title": ("title", "\xa9nam", "TIT2"),
    "artist": ("artist", "\xa9ART", "TPE1"),
    "album": ("album", "\xa9alb", "TALB"),
    "album_artist": ("albumartist", "aART", "TPE2"),
    "date": ("date", "\xa9day", "TDRC"),
    "genre": ("genre", "\xa9gen", "TCON"),
    "isrc": ("isrc", "TSRC"),
}


class UserUploadProvider(MusicProvider):
    name = "user"
    display_name = "Мой файл"
    priority = 0
    capabilities = ProviderCapabilities(search=False, direct_link=True,
                                        metadata=True, cover=True,
                                        download=True, stream=True,
                                        requires_auth=False,
                                        notes="файл загружен самим пользователем")
    api_hosts = ()

    def status(self) -> ProviderHealth:
        return ProviderHealth.OK

    async def search(self, query: str, limit: int = 8) -> list[TrackCandidate]:
        return []

    def _safe_local(self, path: str) -> Path:
        p = Path(path).resolve()
        roots = [music_settings.tmp_dir.resolve(), music_settings.cache_dir.resolve(),
                 music_settings.covers_dir.resolve()]
        if not any(p == root or root in p.parents for root in roots):
            raise PermissionDenied("Путь вне каталога музыкальной системы",
                                   code="USER_PATH")
        if not p.exists() or not p.is_file():
            raise ProviderError("Файл не найден", code="USER_FILE_MISSING")
        if p.suffix.lower() not in _AUDIO_EXT:
            raise PermissionDenied("Формат не поддерживается", code="USER_FORMAT")
        return p

    def describe(self, path: str, *, original_name: str = "",
                 title: str = "", artist: str = "", duration: int = 0) -> TrackCandidate:
        p = self._safe_local(path)
        tags = self._read_tags(p)
        clean_title = title or tags.get("title") or ""
        clean_artist = artist or tags.get("artist") or ""
        if not clean_title:
            stem = Path(original_name or p.name).stem
            stem = re.sub(r"[_]+", " ", stem).strip()
            if " - " in stem and not clean_artist:
                clean_artist, clean_title = (x.strip() for x in stem.split(" - ", 1))
            else:
                clean_title = stem
        year = None
        date = tags.get("date") or ""
        if date[:4].isdigit():
            year = int(date[:4])
        return self.make_candidate(
            external_id=f"upload:{sha256_file(p)[:24]}",
            title=clean_title or "Загруженный файл",
            artist=clean_artist,
            album=tags.get("album") or "",
            album_artist=tags.get("album_artist") or clean_artist,
            duration=duration,
            year=year,
            cover_url=None,
            source_url="",
            permission=PermissionStatus.DOWNLOAD_ALLOWED,
            license_name="Файл предоставлен пользователем",
            isrc=tags.get("isrc") or None,
            version=self._version(clean_title),
            file_format=p.suffix.lstrip(".").lower(),
            file_size=p.stat().st_size,
            downloadable=True,
            streamable=True,
            extra={"local_path": str(p), "original_name": original_name or p.name,
                   "tags": tags},
        )

    @staticmethod
    def _read_tags(path: Path) -> dict[str, str]:
        try:
            from mutagen import File as MutagenFile
            audio = MutagenFile(path)
            if not audio or not getattr(audio, "tags", None):
                return {}
            tags = audio.tags
            out: dict[str, str] = {}
            for field, keys in _TAG_KEYS.items():
                for key in keys:
                    try:
                        value = tags.get(key)
                    except Exception:  # noqa: BLE001
                        value = None
                    if value:
                        if isinstance(value, list):
                            value = value[0]
                        out[field] = str(value).strip()
                        break
            return out
        except Exception as exc:  # noqa: BLE001
            log.debug("user upload tags: %s", exc)
            return {}

    async def get_download_permissions(self, track: TrackCandidate) -> PermissionInfo:
        path = str((track.extra or {}).get("local_path") or "")
        if not path:
            return PermissionInfo(status=PermissionStatus.BLOCKED,
                                  reason="Файл не приложен")
        self._safe_local(path)
        return PermissionInfo(
            status=PermissionStatus.DOWNLOAD_ALLOWED,
            reason="Файл загружен пользователем",
            source_url="", license_name="Файл предоставлен пользователем")

    async def get_audio_stream(self, track: TrackCandidate) -> AudioSource:
        path = str((track.extra or {}).get("local_path") or "")
        p = self._safe_local(path)
        return AudioSource(url="", filename=p.name,
                           expected_format=p.suffix.lstrip(".").lower(),
                           size=p.stat().st_size,
                           extra={"local_path": str(p)})

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth.OK

    @staticmethod
    def _version(title: str) -> TrackVersion:
        low = (title or "").lower()
        if "remix" in low:
            return TrackVersion.REMIX
        if "live" in low:
            return TrackVersion.LIVE
        if "acoustic" in low:
            return TrackVersion.ACOUSTIC
        if "instrumental" in low:
            return TrackVersion.INSTRUMENTAL
        return TrackVersion.ORIGINAL
