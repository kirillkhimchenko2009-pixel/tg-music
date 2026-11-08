"""Единый интерфейс музыкального источника."""
from __future__ import annotations

from abc import ABC
from typing import Any, Iterable

from ..config import MusicSettings, music_settings
from ..domain import (AudioSource, PermissionInfo, ProviderCapabilities,
                      TrackCandidate)
from ..enums import PermissionStatus, ProviderHealth
from ..errors import NotSupportedError, ProviderError
from ..net import SafeHttpClient


class MusicProvider(ABC):
    """Все адаптеры независимы: падение одного не влияет на остальные."""

    name: str = "base"
    display_name: str = "Base"
    priority: int = 100
    capabilities: ProviderCapabilities = ProviderCapabilities()
    api_hosts: tuple[str, ...] = ()

    def __init__(self, settings: MusicSettings | None = None, *,
                 transport: Any = None, resolver: Any = None):
        self.settings = settings or music_settings
        self._transport = transport
        self._resolver = resolver

    # ---- конфигурация/здоровье -------------------------------------------------
    @property
    def configured(self) -> bool:
        return True

    @property
    def enabled(self) -> bool:
        return self.configured

    def status(self) -> ProviderHealth:
        if not self.enabled:
            return ProviderHealth.UNCONFIGURED
        return ProviderHealth.UNKNOWN

    def http(self, allowed_hosts: Iterable[str] | None = None) -> SafeHttpClient:
        hosts = tuple(allowed_hosts or self.api_hosts) or None
        return SafeHttpClient(self.settings, allowed_hosts=hosts,
                              transport=self._transport, resolver=self._resolver)

    async def health_check(self) -> ProviderHealth:
        return self.status()

    # ---- обязательный поиск -----------------------------------------------------
    async def search(self, query: str, limit: int = 8) -> list[TrackCandidate]:
        raise NotSupportedError(f"{self.name}: поиск не поддерживается")

    async def get_track(self, external_id: str) -> TrackCandidate | None:
        raise NotSupportedError(f"{self.name}: карточка трека не поддерживается")

    async def get_metadata(self, external_id: str) -> TrackCandidate | None:
        return await self.get_track(external_id)

    async def get_cover(self, track: TrackCandidate) -> str | None:
        return track.cover_url

    # ---- разрешения/аудио -------------------------------------------------------
    async def get_download_permissions(self, track: TrackCandidate) -> PermissionInfo:
        status = track.permission if track.permission else PermissionStatus.LICENSE_UNKNOWN
        return PermissionInfo(status=status, source_url=self.get_source_url(track),
                              license_name=track.license_name,
                              license_url=track.license_url)

    async def get_audio_stream(self, track: TrackCandidate) -> AudioSource:
        raise NotSupportedError(f"{self.name}: загрузка аудио не поддерживается")

    def get_source_url(self, track: TrackCandidate) -> str:
        return track.source_url or ""

    # ---- утилиты ----------------------------------------------------------------
    def make_candidate(self, **kwargs: Any) -> TrackCandidate:
        return TrackCandidate(provider=self.name,
                              provider_label=self.display_name, **kwargs)

    def supports(self, capability: str) -> bool:
        return bool(getattr(self.capabilities, capability, False))

    def as_spec(self) -> dict[str, Any]:
        status = self.status()
        return {
            "provider": self.name,
            "display_name": self.display_name,
            "enabled": status not in (ProviderHealth.UNCONFIGURED, ProviderHealth.DISABLED),
            "capabilities": self.capabilities.as_dict(),
            "health": status.value,
        }

    @staticmethod
    def clean_query(query: str) -> str:
        return " ".join((query or "").split())[:300]


class UnsupportedProvider(MusicProvider):
    """Выключенный источник: ничего не делает, но не ломает реестр."""

    async def search(self, query: str, limit: int = 8) -> list[TrackCandidate]:
        return []
