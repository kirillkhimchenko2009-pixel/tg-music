"""Доменные структуры музыкальной системы (без ORM и без сети)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .enums import (MediaKind, PermissionStatus, QualityCategory,
                    TrackVersion)


def fmt_duration(seconds: float | int | None) -> str:
    sec = max(0, int(seconds or 0))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def human_size(size: int | float | None) -> str:
    n = float(size or 0)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024 or unit == "ГБ":
            return f"{n:.0f} {unit}" if unit == "Б" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} ГБ"


def normalize_key(value: str) -> str:
    s = (value or "").lower().replace("ё", "е")
    s = re.sub(r"[^\w]+", " ", s, flags=re.UNICODE)
    return re.sub(r"\s+", " ", s).strip()


@dataclass(slots=True)
class ProviderCapabilities:
    search: bool = True
    direct_link: bool = False
    metadata: bool = True
    cover: bool = True
    download: bool = False
    stream: bool = False
    requires_auth: bool = False
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "search": self.search, "direct_link": self.direct_link,
            "metadata": self.metadata, "cover": self.cover,
            "download": self.download, "stream": self.stream,
            "requires_auth": self.requires_auth, "notes": self.notes,
        }


@dataclass(slots=True)
class PermissionInfo:
    status: PermissionStatus = PermissionStatus.LICENSE_UNKNOWN
    reason: str = ""
    source_url: str = ""
    license_name: str = ""
    license_url: str = ""
    purchase_url: str = ""

    @property
    def can_download(self) -> bool:
        return self.status.can_download

    @property
    def label(self) -> str:
        return self.status.label

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value, "reason": self.reason,
            "source_url": self.source_url, "license_name": self.license_name,
            "license_url": self.license_url, "purchase_url": self.purchase_url,
        }


@dataclass(slots=True)
class TrackCandidate:
    provider: str
    external_id: str
    title: str
    artist: str = ""
    album: str = ""
    album_artist: str = ""
    duration: int = 0
    year: int | None = None
    cover_url: str | None = None
    source_url: str = ""
    permission: PermissionStatus = PermissionStatus.LICENSE_UNKNOWN
    license_name: str = ""
    license_url: str = ""
    isrc: str | None = None
    version: TrackVersion = TrackVersion.ORIGINAL
    explicit: bool = False
    bitrate_kbps: int | None = None
    sample_rate: int | None = None
    bit_depth: int | None = None
    file_format: str | None = None
    file_size: int | None = None
    downloadable: bool = False
    streamable: bool = False
    score: float = 0.0
    provider_label: str = ""
    track_id: int | None = None
    alternatives: list["TrackCandidate"] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def display(self) -> str:
        return f"{self.artist} — {self.title}" if self.artist else self.title

    @property
    def duration_label(self) -> str:
        return fmt_duration(self.duration) if self.duration else "—"

    @property
    def permission_label(self) -> str:
        return self.permission.label

    @property
    def quality_hint(self) -> str:
        if self.file_format and self.bitrate_kbps:
            return f"{self.file_format.upper()} · {self.bitrate_kbps} kbps"
        if self.file_format:
            return self.file_format.upper()
        if self.bitrate_kbps:
            return f"{self.bitrate_kbps} kbps"
        return "—"

    @property
    def identity_key(self) -> str:
        bucket = int(round((self.duration or 0) / 5.0)) * 5
        return "|".join((normalize_key(self.artist), normalize_key(self.title),
                         self.version.value, str(bucket)))

    @property
    def dedupe_key(self) -> tuple[str, str]:
        return (self.provider, str(self.external_id))

    def merge_missing(self, other: "TrackCandidate") -> None:
        """Дополняет пустые поля, не перетирая уже известные значения."""
        for name in ("artist", "album", "album_artist", "cover_url", "source_url",
                     "license_name", "license_url", "isrc", "file_format"):
            if not getattr(self, name) and getattr(other, name):
                setattr(self, name, getattr(other, name))
        for name in ("duration", "year", "bitrate_kbps", "sample_rate",
                     "bit_depth", "file_size"):
            if not getattr(self, name) and getattr(other, name):
                setattr(self, name, getattr(other, name))

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider, "external_id": self.external_id,
            "title": self.title, "artist": self.artist, "album": self.album,
            "album_artist": self.album_artist, "duration": self.duration,
            "year": self.year, "cover_url": self.cover_url,
            "source_url": self.source_url, "permission": self.permission.value,
            "license_name": self.license_name, "license_url": self.license_url,
            "isrc": self.isrc, "version": self.version.value,
            "explicit": self.explicit, "bitrate_kbps": self.bitrate_kbps,
            "sample_rate": self.sample_rate, "bit_depth": self.bit_depth,
            "file_format": self.file_format, "file_size": self.file_size,
            "downloadable": self.downloadable, "streamable": self.streamable,
            "score": round(self.score, 4), "provider_label": self.provider_label,
        }


@dataclass(slots=True)
class AudioSource:
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    mime: str = ""
    filename: str = ""
    expected_format: str = ""
    size: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AudioProbe:
    codec: str = ""
    container: str = ""
    format_name: str = ""
    bitrate_kbps: int | None = None
    sample_rate: int | None = None
    bit_depth: int | None = None
    channels: int | None = None
    duration: float = 0.0
    size: int = 0
    tags: dict[str, str] = field(default_factory=dict)
    has_audio: bool = False
    has_video: bool = False
    ffprobe_error: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def is_lossless(self) -> bool:
        codec = (self.codec or "").lower()
        return codec in ("flac", "alac", "pcm_s16le", "pcm_s24le", "pcm_s32le",
                         "pcm_f32le", "wavpack", "ape", "truehd") or (
            self.container in ("flac", "wav") and codec.startswith("pcm"))

    @property
    def format_label(self) -> str:
        if self.container:
            return self.container.upper()
        return (self.codec or "?").upper()


@dataclass(slots=True)
class AudioIssue:
    code: str
    message: str
    severity: str = "warning"  # info | warning | error

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "severity": self.severity}


@dataclass(slots=True)
class AudioAnalysis:
    probe: AudioProbe = field(default_factory=AudioProbe)
    category: QualityCategory = QualityCategory.UNKNOWN
    score: int = 0
    clipping: bool = False
    clipped_samples: int = 0
    clipped_ratio: float = 0.0
    peak_dbfs: float | None = None
    rms_dbfs: float | None = None
    true_peak_dbfs: float | None = None
    silence_leading: float = 0.0
    silence_trailing: float = 0.0
    silence_total: float = 0.0
    bandwidth_hz: int | None = None
    likely_transcoded: bool = False
    issues: list[AudioIssue] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add_issue(self, code: str, message: str, severity: str = "warning") -> None:
        self.issues.append(AudioIssue(code, message, severity))

    @property
    def is_sendable(self) -> bool:
        return (self.category is not QualityCategory.DAMAGED
                and self.probe.has_audio and self.probe.duration > 0)

    @property
    def issue_codes(self) -> list[str]:
        return [i.code for i in self.issues]


@dataclass(slots=True)
class CoverCandidate:
    url: str = ""
    data: bytes = b""
    width: int = 0
    height: int = 0
    format: str = ""
    mime: str = ""
    size: int = 0
    source: str = ""

    @property
    def pixels(self) -> int:
        return max(0, self.width) * max(0, self.height)

    @property
    def is_valid(self) -> bool:
        return bool(self.data) and self.width > 0 and self.height > 0


@dataclass(slots=True)
class CoverAnalysis:
    candidate: CoverCandidate = field(default_factory=CoverCandidate)
    accepted: bool = False
    sharpness: float = 0.0
    blockiness: float = 0.0
    score: int = 0
    is_upscaled: bool = False
    issues: list[AudioIssue] = field(default_factory=list)

    def add_issue(self, code: str, message: str, severity: str = "warning") -> None:
        self.issues.append(AudioIssue(code, message, severity))


@dataclass(slots=True)
class SearchResponse:
    query: str
    results: list[TrackCandidate] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    provider_status: dict[str, str] = field(default_factory=dict)
    elapsed: float = 0.0
    cache_hit: bool = False


@dataclass(slots=True)
class PreparedTrack:
    path: str
    media_kind: MediaKind
    mime_type: str
    file_format: str
    size: int
    duration: float
    title: str
    artist: str
    album: str
    candidate: TrackCandidate
    audio: AudioAnalysis
    cover: CoverAnalysis | None = None
    cover_path: str | None = None
    cache_key: str = ""
    sha256: str = ""
    history_id: int | None = None
    telegram_file_id: str | None = None
    telegram_file_unique_id: str | None = None
    cache_hit: bool = False
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DownloadOutcome:
    ok: bool
    job_id: str = ""
    prepared: PreparedTrack | None = None
    error: str = ""
    error_code: str = ""
    retryable: bool = False
    permission: PermissionInfo | None = None
    official_url: str = ""


@dataclass(slots=True)
class HealthReport:
    ok: bool
    checked_at: float
    database: str = "unknown"
    ffmpeg: str = "unknown"
    disk_free_mb: int = 0
    queue: dict[str, Any] = field(default_factory=dict)
    providers: dict[str, dict[str, Any]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok, "checked_at": self.checked_at,
            "database": self.database, "ffmpeg": self.ffmpeg,
            "disk_free_mb": self.disk_free_mb, "queue": self.queue,
            "providers": self.providers,
        }
