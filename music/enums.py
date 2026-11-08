"""Перечисления музыкальной системы: разрешения, качество, версии, статусы."""
from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    """Строковый enum, совместимый с JSON/SQLite без value-магии."""

    def __str__(self) -> str:  # pragma: no cover - удобство логов
        return str(self.value)


class PermissionStatus(StrEnum):
    DOWNLOAD_ALLOWED = "DOWNLOAD_ALLOWED"
    STREAM_ONLY = "STREAM_ONLY"
    OFFICIAL_LINK_ONLY = "OFFICIAL_LINK_ONLY"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    PURCHASE_REQUIRED = "PURCHASE_REQUIRED"
    LICENSE_UNKNOWN = "LICENSE_UNKNOWN"
    BLOCKED = "BLOCKED"

    @property
    def can_download(self) -> bool:
        return self is PermissionStatus.DOWNLOAD_ALLOWED

    @property
    def label(self) -> str:
        return {
            PermissionStatus.DOWNLOAD_ALLOWED: "Скачивание разрешено",
            PermissionStatus.STREAM_ONLY: "Только прослушивание",
            PermissionStatus.OFFICIAL_LINK_ONLY: "Только официальная ссылка",
            PermissionStatus.AUTH_REQUIRED: "Нужна авторизация",
            PermissionStatus.PURCHASE_REQUIRED: "Нужна покупка",
            PermissionStatus.LICENSE_UNKNOWN: "Лицензия не определена",
            PermissionStatus.BLOCKED: "Запрещено источником",
        }[self]


class QualityCategory(StrEnum):
    LOSSLESS_SOURCE = "LOSSLESS_SOURCE"
    HIGH_QUALITY = "HIGH_QUALITY"
    STANDARD_QUALITY = "STANDARD_QUALITY"
    LOW_QUALITY = "LOW_QUALITY"
    DAMAGED = "DAMAGED"
    UNKNOWN = "UNKNOWN"

    @property
    def label(self) -> str:
        return {
            QualityCategory.LOSSLESS_SOURCE: "Lossless (без потерь)",
            QualityCategory.HIGH_QUALITY: "Высокое качество",
            QualityCategory.STANDARD_QUALITY: "Стандартное качество",
            QualityCategory.LOW_QUALITY: "Низкое качество",
            QualityCategory.DAMAGED: "Повреждённый файл",
            QualityCategory.UNKNOWN: "Качество не определено",
        }[self]

    @property
    def rank(self) -> int:
        return {
            QualityCategory.LOSSLESS_SOURCE: 4,
            QualityCategory.HIGH_QUALITY: 3,
            QualityCategory.STANDARD_QUALITY: 2,
            QualityCategory.LOW_QUALITY: 1,
            QualityCategory.UNKNOWN: 0,
            QualityCategory.DAMAGED: -1,
        }[self]


class TrackVersion(StrEnum):
    ORIGINAL = "original"
    REMIX = "remix"
    LIVE = "live"
    ACOUSTIC = "acoustic"
    INSTRUMENTAL = "instrumental"
    COVER = "cover"
    EXTENDED = "extended"
    RADIO_EDIT = "radio_edit"
    DEMO = "demo"
    SPEED = "speed"
    OTHER = "other"

    @property
    def label(self) -> str:
        return {
            TrackVersion.ORIGINAL: "Оригинал",
            TrackVersion.REMIX: "Ремикс",
            TrackVersion.LIVE: "Live",
            TrackVersion.ACOUSTIC: "Acoustic",
            TrackVersion.INSTRUMENTAL: "Инструментал",
            TrackVersion.COVER: "Кавер",
            TrackVersion.EXTENDED: "Extended",
            TrackVersion.RADIO_EDIT: "Radio edit",
            TrackVersion.DEMO: "Демо",
            TrackVersion.SPEED: "Ускоренная версия",
            TrackVersion.OTHER: "Другая версия",
        }[self]


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class MediaKind(StrEnum):
    AUDIO = "audio"
    DOCUMENT = "document"


class ProviderHealth(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"
    DISABLED = "disabled"
    UNCONFIGURED = "unconfigured"
    UNKNOWN = "unknown"


class SearchSort(StrEnum):
    RELEVANCE = "relevance"
    QUALITY = "quality"
    DURATION = "duration"
