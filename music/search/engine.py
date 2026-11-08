"""Параллельный поиск по независимым провайдерам с изоляцией ошибок."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Iterable

from ..cache.metadata_cache import search_cache
from ..config import MusicSettings, music_settings
from ..domain import SearchResponse, TrackCandidate
from ..enums import PermissionStatus, SearchSort
from ..providers.registry import ProviderRegistry, registry as default_registry
from .merger import ResultMerger
from .normalizer import NormalizedQuery, parse

log = logging.getLogger("music.search")


class SearchEngine:
    def __init__(self, registry: ProviderRegistry | None = None,
                 settings: MusicSettings | None = None,
                 merger: ResultMerger | None = None):
        self.registry = registry or default_registry
        self.settings = settings or music_settings
        self.merger = merger or ResultMerger()

    def _targets(self, providers: Iterable[str] | None, *, url: bool = False):
        if providers:
            names = set(providers)
            return [p for name in names if (p := self.registry.get(name))]
        if url:
            return self.registry.all()
        return [p for p in self.registry.enabled()
                if p.supports("search") and p.name != "user"]

    async def search(self, query: str, limit: int | None = None, *,
                     providers: Iterable[str] | None = None,
                     sort: SearchSort = SearchSort.RELEVANCE) -> SearchResponse:
        started = time.monotonic()
        normalized = parse(query)
        limit = limit or self.settings.search_results
        if not normalized.raw:
            return SearchResponse(query=query, elapsed=0.0)
        if normalized.is_url:
            response = await self._search_url(normalized, limit)
            response.elapsed = round(time.monotonic() - started, 3)
            return response

        key = ("search", normalized.search_text, normalized.version.value,
               limit, tuple(sorted(providers or ())), sort.value)
        cached = await search_cache.get(key)
        if isinstance(cached, SearchResponse):
            return SearchResponse(query=cached.query, results=cached.results,
                                  errors=dict(cached.errors),
                                  provider_status=dict(cached.provider_status),
                                  elapsed=round(time.monotonic() - started, 3),
                                  cache_hit=True)

        targets = self._targets(providers)
        response = SearchResponse(query=normalized.search_text)
        response.provider_status = {
            p.name: self.registry.circuit(p.name).health.value for p in targets}

        async def run(provider) -> tuple[str, list[TrackCandidate], str | None]:
            try:
                found = await asyncio.wait_for(
                    provider.search(response.query, limit=self.settings.provider_results),
                    timeout=self.settings.provider_timeout)
                self.registry.mark_success(provider.name)
                return provider.name, list(found or []), None
            except asyncio.TimeoutError:
                self.registry.mark_failure(provider.name, "timeout")
                return provider.name, [], "таймаут источника"
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.registry.mark_failure(provider.name, f"{type(exc).__name__}: {exc}")
                log.info("provider %s: поиск не удался: %s", provider.name, exc)
                return provider.name, [], self._short_error(exc)

        gathered = await asyncio.gather(*(run(p) for p in targets))
        raw: list[TrackCandidate] = []
        for name, found, error in gathered:
            if error:
                response.errors[name] = error
            raw.extend(found)

        merged = self.merger.merge(raw, normalized, limit=limit)
        merged = [c for c in merged if c.score >= self._threshold(normalized)]
        if sort is SearchSort.QUALITY:
            from .merger import quality_rank
            merged.sort(key=lambda c: (1 if c.permission is PermissionStatus.DOWNLOAD_ALLOWED else 0,
                                       quality_rank(c), c.score), reverse=True)
        elif sort is SearchSort.DURATION:
            merged.sort(key=lambda c: (c.duration or 10**6, -c.score))
        response.results = merged
        response.elapsed = round(time.monotonic() - started, 3)
        response.provider_status.update(
            {p.name: self.registry.circuit(p.name).health.value for p in targets})
        await search_cache.set(key, SearchResponse(
            query=response.query, results=response.results,
            errors=response.errors, provider_status=response.provider_status))
        return response

    async def _search_url(self, normalized: NormalizedQuery, limit: int) -> SearchResponse:
        response = SearchResponse(query=normalized.source_url)
        provider = self.registry.get(normalized.provider_hint) if normalized.provider_hint else None
        if provider is None:
            # Bandcamp-подобные источники умеют искать по прямой ссылке
            for candidate_provider in self.registry.enabled():
                if not candidate_provider.capabilities.direct_link:
                    continue
                try:
                    found = await asyncio.wait_for(
                        candidate_provider.search(normalized.source_url, limit=1),
                        timeout=self.settings.provider_timeout)
                except Exception as exc:  # noqa: BLE001
                    response.errors[candidate_provider.name] = self._short_error(exc)
                    continue
                if found:
                    response.results.extend(found)
            response.results = self.merger.merge(response.results, None, limit=limit)
            response.provider_status = {p.name: self.registry.circuit(p.name).health.value
                                        for p in self.registry.all()}
            return response

        response.provider_status = {provider.name: self.registry.circuit(provider.name).health.value}
        if not provider.enabled:
            response.errors[provider.name] = "источник не настроен"
            return response
        try:
            if normalized.external_id_hint:
                candidate = await asyncio.wait_for(
                    provider.get_track(normalized.external_id_hint),
                    timeout=self.settings.provider_timeout)
                if candidate:
                    candidate.score = 1.0
                    response.results = [candidate]
                else:
                    response.errors[provider.name] = "композиция не найдена по ссылке"
            else:
                found = await asyncio.wait_for(
                    provider.search(normalized.source_url, limit=limit),
                    timeout=self.settings.provider_timeout)
                response.results = self.merger.merge(found, None, limit=limit)
                if not found:
                    response.errors[provider.name] = (
                        "для этой ссылки нужен официальный API источника; "
                        "попробуй поиск по названию")
            self.registry.mark_success(provider.name)
        except asyncio.TimeoutError:
            self.registry.mark_failure(provider.name, "timeout")
            response.errors[provider.name] = "таймаут источника"
        except Exception as exc:  # noqa: BLE001
            self.registry.mark_failure(provider.name, f"{type(exc).__name__}: {exc}")
            response.errors[provider.name] = self._short_error(exc)
        return response

    async def lookup(self, provider_name: str, external_id: str) -> TrackCandidate | None:
        provider = self.registry.get(provider_name)
        if not provider or not provider.enabled:
            return None
        return await provider.get_track(external_id)

    @staticmethod
    def _threshold(query: NormalizedQuery) -> float:
        return 0.25 if (query.artist and query.title) else 0.2

    @staticmethod
    def _short_error(exc: Exception) -> str:
        text = str(exc) or type(exc).__name__
        return text[:180]


engine = SearchEngine()

__all__ = ["SearchEngine", "engine"]
