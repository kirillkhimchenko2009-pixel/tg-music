"""Реестр провайдеров, health-check и circuit breaker."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Iterable

from ..enums import ProviderHealth
from .base import MusicProvider

log = logging.getLogger("music.providers")


@dataclass
class CircuitState:
    failures: int = 0
    cooldown_until: float = 0.0
    last_error: str = ""
    last_ok: float = 0.0
    last_check: float = 0.0
    health: ProviderHealth = ProviderHealth.UNKNOWN
    latency_ms: int = 0

    def as_dict(self) -> dict:
        return {
            "failures": self.failures,
            "cooldown_until": self.cooldown_until,
            "last_error": self.last_error,
            "last_ok": self.last_ok,
            "health": self.health.value,
            "latency_ms": self.latency_ms,
        }


class ProviderRegistry:
    def __init__(self, failure_threshold: int = 3, cooldown_sec: float = 90.0):
        self._providers: dict[str, MusicProvider] = {}
        self._circuits: dict[str, CircuitState] = {}
        self.failure_threshold = failure_threshold
        self.cooldown_sec = cooldown_sec

    def register(self, provider: MusicProvider) -> None:
        self._providers[provider.name] = provider
        self._circuits.setdefault(provider.name, CircuitState(health=provider.status()))

    def get(self, name: str) -> MusicProvider | None:
        return self._providers.get(name)

    def all(self) -> list[MusicProvider]:
        return sorted(self._providers.values(), key=lambda p: (p.priority, p.name))

    def enabled(self) -> list[MusicProvider]:
        return [p for p in self.all() if p.enabled and self.available(p.name)]

    def available(self, name: str) -> bool:
        state = self._circuits.get(name)
        return not state or state.cooldown_until <= time.monotonic()

    def circuit(self, name: str) -> CircuitState:
        return self._circuits.setdefault(name, CircuitState())

    def mark_success(self, name: str) -> None:
        state = self.circuit(name)
        state.failures = 0
        state.cooldown_until = 0.0
        state.last_ok = time.time()
        state.health = ProviderHealth.OK

    def mark_failure(self, name: str, error: str) -> None:
        state = self.circuit(name)
        state.failures += 1
        state.last_error = error[:300]
        if state.failures >= self.failure_threshold:
            state.cooldown_until = time.monotonic() + self.cooldown_sec
            state.health = ProviderHealth.DOWN
            log.warning("provider %s: circuit open на %.0f c (%s)",
                        name, self.cooldown_sec, state.last_error)
        else:
            state.health = ProviderHealth.DEGRADED

    def mark_disabled(self, name: str, reason: str = "") -> None:
        state = self.circuit(name)
        state.health = ProviderHealth.DISABLED
        if reason:
            state.last_error = reason[:300]

    def snapshot(self) -> dict[str, dict]:
        out = {}
        for p in self.all():
            state = self.circuit(p.name)
            out[p.name] = {
                "display_name": p.display_name,
                "enabled": p.enabled,
                "configured": p.configured,
                "capabilities": p.capabilities.as_dict(),
                **state.as_dict(),
            }
        return out

    async def health_check(self, timeout: float = 8.0) -> dict[str, dict]:
        async def check(p: MusicProvider) -> tuple[str, ProviderHealth, int, str]:
            started = time.monotonic()
            try:
                health = await asyncio.wait_for(p.health_check(), timeout=timeout)
                latency = int((time.monotonic() - started) * 1000)
                return p.name, health, latency, ""
            except asyncio.TimeoutError:
                return p.name, ProviderHealth.DOWN, timeout and int(timeout * 1000), "timeout"
            except Exception as exc:  # noqa: BLE001
                return p.name, ProviderHealth.DOWN, 0, f"{type(exc).__name__}: {exc}"[:200]

        results = await asyncio.gather(*(check(p) for p in self.all()))
        for name, health, latency, error in results:
            state = self.circuit(name)
            state.health = health
            state.last_check = time.time()
            state.latency_ms = latency
            if error:
                state.last_error = error
            else:
                state.last_error = ""
                state.last_ok = time.time()
                state.failures = 0
                state.cooldown_until = 0.0
        return self.snapshot()


def build_default_registry() -> ProviderRegistry:
    from .jamendo import JamendoProvider
    from .archive import ArchiveProvider
    from .soundcloud import SoundCloudProvider
    from .spotify import SpotifyProvider
    from .bandcamp import BandcampProvider
    from .user_upload import UserUploadProvider

    registry = ProviderRegistry()
    for provider in (JamendoProvider(), ArchiveProvider(), SoundCloudProvider(),
                     SpotifyProvider(), BandcampProvider(), UserUploadProvider()):
        registry.register(provider)
    return registry


registry = build_default_registry()


def provider_specs() -> list[dict]:
    return [p.as_spec() for p in registry.all()]


def get_provider(name: str) -> MusicProvider | None:
    return registry.get(name)


def enabled_providers() -> list[MusicProvider]:
    return registry.enabled()


__all__ = ["ProviderRegistry", "CircuitState", "registry", "provider_specs",
           "get_provider", "enabled_providers", "build_default_registry"]
