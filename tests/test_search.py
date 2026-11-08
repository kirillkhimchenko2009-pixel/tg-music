"""Поиск: дедупликация, объединение, параллельный движок и circuit breaker."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from music.cache.metadata_cache import search_cache
from music.config import MusicSettings
from music.domain import (PermissionInfo, ProviderCapabilities, TrackCandidate)
from music.enums import PermissionStatus, SearchSort, TrackVersion
from music.providers.base import MusicProvider
from music.providers.registry import ProviderRegistry
from music.search.duplicate_detector import (dedupe_exact, file_cache_key,
                                             fingerprint, group_recordings,
                                             is_same_recording, same_file)
from music.search.engine import SearchEngine
from music.search.merger import ResultMerger, quality_rank


@pytest.fixture(autouse=True)
async def _clear_search_cache():
    await search_cache.clear()
    yield
    await search_cache.clear()


def _candidate(**kwargs) -> TrackCandidate:
    base = dict(provider="jamendo", external_id="1", title="One More Time",
                artist="Daft Punk", duration=320)
    base.update(kwargs)
    return TrackCandidate(**base)


class FakeProvider(MusicProvider):
    """Провайдер без сети: результаты/ошибка задаются в конструкторе."""

    def __init__(self, name: str, results: list[TrackCandidate] | None = None,
                 *, error: Exception | None = None,
                 direct_link: bool = False, download: bool = True,
                 delay: float = 0.0):
        super().__init__()
        self.name = name
        self.display_name = name.title()
        self.priority = 10
        self.capabilities = ProviderCapabilities(
            search=True, direct_link=direct_link, download=download)
        self._results = list(results or [])
        self._error = error
        self._delay = delay
        self.search_calls = 0
        self.track_calls = 0

    async def search(self, query: str, limit: int = 8) -> list[TrackCandidate]:
        self.search_calls += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return list(self._results[:limit])

    async def get_track(self, external_id: str) -> TrackCandidate | None:
        self.track_calls += 1
        if self._error is not None:
            raise self._error
        for item in self._results:
            if item.external_id == external_id:
                return item
        return self._results[0] if self._results else None


def _registry(*providers: FakeProvider) -> ProviderRegistry:
    registry = ProviderRegistry(failure_threshold=2, cooldown_sec=60)
    for provider in providers:
        registry.register(provider)
    return registry


def _settings(**kwargs) -> MusicSettings:
    base = dict(search_results=10, provider_results=5, provider_timeout=1.0,
                search_timeout=2.0)
    base.update(kwargs)
    return MusicSettings(**base)


class TestRecordingIdentity:
    def test_same_by_isrc_even_with_different_titles(self):
        a = _candidate(isrc="USRC17607839")
        b = _candidate(provider="archive", external_id="2", title="Other", isrc="usrc17607839")
        assert is_same_recording(a, b) is True

    def test_same_title_artist_duration(self):
        assert is_same_recording(_candidate(), _candidate(provider="x", external_id="2")) is True

    def test_duration_tolerance(self):
        assert is_same_recording(_candidate(duration=300),
                                 _candidate(duration=305)) is True
        assert is_same_recording(_candidate(duration=300),
                                 _candidate(duration=306)) is False

    def test_artist_mismatch(self):
        assert is_same_recording(_candidate(), _candidate(artist="Vivaldi")) is False

    def test_version_mismatch(self):
        assert is_same_recording(
            _candidate(), _candidate(version=TrackVersion.REMIX)) is False

    def test_different_title(self):
        assert is_same_recording(_candidate(), _candidate(title="Get Lucky")) is False

    def test_same_object(self):
        item = _candidate()
        assert is_same_recording(item, item) is True


class TestDeduplication:
    def test_dedupe_exact_merges_missing_fields(self):
        first = _candidate(album="", cover_url="")
        second = _candidate(album="Discovery", cover_url="https://x/cover.jpg",
                            score=0.9)
        result = dedupe_exact([first, second])
        assert len(result) == 1
        assert result[0].album == "Discovery"
        assert result[0].cover_url == "https://x/cover.jpg"
        assert result[0].score == 0.9

    def test_dedupe_exact_keeps_different_sources(self):
        result = dedupe_exact([
            _candidate(provider="jamendo", external_id="1"),
            _candidate(provider="archive", external_id="2"),
        ])
        assert len(result) == 2

    def test_group_recordings_keeps_versions_apart(self):
        groups = group_recordings([
            _candidate(), _candidate(provider="x", external_id="2"),
            _candidate(provider="y", external_id="3",
                       version=TrackVersion.LIVE),
        ])
        assert sorted(len(group) for group in groups) == [1, 2]

    def test_fingerprint_and_same_file(self, tmp_path: Path):
        one = tmp_path / "one.bin"
        two = tmp_path / "two.bin"
        three = tmp_path / "three.bin"
        one.write_bytes(b"music" * 100)
        two.write_bytes(b"music" * 100)
        three.write_bytes(b"other" * 100)
        assert fingerprint(one) == fingerprint(two)
        assert same_file(one, two) is True
        assert same_file(one, three) is False
        assert same_file(one, tmp_path / "missing.bin") is False

    def test_file_cache_key(self):
        assert file_cache_key("a" * 64) == "a" * 32
        assert file_cache_key("") == ""

    def test_quality_rank_prefers_lossless(self):
        mp3 = _candidate(file_format="mp3", bitrate_kbps=320)
        flac = _candidate(file_format="flac", bitrate_kbps=900)
        assert quality_rank(flac) > quality_rank(mp3)


class TestMerger:
    def test_merges_same_recording_from_two_sources(self):
        allowed = _candidate(provider="jamendo", external_id="1",
                             permission=PermissionStatus.DOWNLOAD_ALLOWED,
                             file_format="mp3", bitrate_kbps=320,
                             provider_label="Jamendo", album="Discovery")
        stream = _candidate(provider="archive", external_id="2",
                            permission=PermissionStatus.STREAM_ONLY,
                            file_format="flac", bitrate_kbps=900,
                            provider_label="Internet Archive")
        merged = ResultMerger().merge([allowed, stream], limit=10)
        assert len(merged) == 1
        best = merged[0]
        assert best.provider == "jamendo"
        assert best.permission is PermissionStatus.DOWNLOAD_ALLOWED
        assert best.alternatives
        assert best.alternatives[0].provider == "archive"
        assert "Internet Archive" in best.extra["also_on"]

    def test_download_allowed_wins_over_better_quality(self):
        allowed = _candidate(provider="jamendo", external_id="1",
                             permission=PermissionStatus.DOWNLOAD_ALLOWED,
                             file_format="mp3", bitrate_kbps=128)
        forbidden = _candidate(provider="archive", external_id="2",
                               permission=PermissionStatus.OFFICIAL_LINK_ONLY,
                               file_format="flac", bitrate_kbps=900)
        merged = ResultMerger().merge([allowed, forbidden], limit=10)
        assert merged[0].provider == "jamendo"

    def test_keeps_different_versions(self):
        original = _candidate(provider="a", external_id="1")
        remix = _candidate(provider="b", external_id="2",
                           title="One More Time (Remix)",
                           version=TrackVersion.REMIX)
        merged = ResultMerger().merge([original, remix], limit=10)
        assert len(merged) == 2
        assert {item.version for item in merged} == {TrackVersion.ORIGINAL,
                                                     TrackVersion.REMIX}

    def test_limit_is_respected(self):
        items = [_candidate(provider=f"p{i}", external_id=str(i),
                            title=f"Song {i}", duration=200 + i * 10)
                 for i in range(10)]
        merged = ResultMerger().merge(items, limit=3)
        assert len(merged) == 3

    def test_query_scores_are_recomputed(self):
        from music.search.normalizer import parse
        query = parse("Daft Punk - One More Time")
        item = _candidate(score=0.01)
        merged = ResultMerger().merge([item], query, limit=5)
        assert merged[0].score > 0.9


class TestSearchEngine:
    async def test_parallel_search_and_merge(self):
        one = _candidate(provider="jamendo", external_id="1",
                         permission=PermissionStatus.DOWNLOAD_ALLOWED)
        two = _candidate(provider="archive", external_id="2",
                         permission=PermissionStatus.STREAM_ONLY)
        engine = SearchEngine(_registry(
            FakeProvider("jamendo", [one]),
            FakeProvider("archive", [two])), _settings())
        response = await engine.search("Daft Punk - One More Time")
        assert response.results
        assert response.results[0].provider == "jamendo"
        assert "jamendo" in response.provider_status
        assert "archive" in response.provider_status
        assert response.errors == {}

    async def test_one_provider_failure_does_not_break_others(self):
        good = _candidate(provider="jamendo", external_id="1")
        bad = FakeProvider("bad", error=RuntimeError("api down"))
        engine = SearchEngine(_registry(FakeProvider("jamendo", [good]), bad),
                              _settings())
        response = await engine.search("Daft Punk - One More Time")
        assert len(response.results) == 1
        assert "bad" in response.errors
        assert "api down" in response.errors["bad"]

    async def test_timeout_is_isolated(self):
        good = _candidate(provider="jamendo", external_id="1")
        slow = FakeProvider("slow", [good], delay=0.2)
        engine = SearchEngine(_registry(FakeProvider("jamendo", [good]), slow),
                              _settings(provider_timeout=0.02))
        response = await engine.search("Daft Punk - One More Time")
        assert response.results
        assert response.errors.get("slow") == "таймаут источника"

    async def test_second_search_uses_cache(self):
        provider = FakeProvider("jamendo", [_candidate()])
        engine = SearchEngine(_registry(provider), _settings())
        first = await engine.search("Daft Punk - One More Time")
        second = await engine.search("Daft Punk - One More Time")
        assert first.cache_hit is False
        assert second.cache_hit is True
        assert provider.search_calls == 1

    async def test_empty_query_returns_empty_response(self):
        engine = SearchEngine(_registry(FakeProvider("jamendo", [])), _settings())
        response = await engine.search("   ")
        assert response.results == []
        assert response.elapsed == 0.0

    async def test_url_hint_uses_get_track(self):
        candidate = _candidate(provider="spotify", external_id="abc123",
                               title="Song", artist="Artist", duration=200)
        provider = FakeProvider("spotify", [candidate], direct_link=True)
        engine = SearchEngine(_registry(provider), _settings())
        response = await engine.search("https://open.spotify.com/track/abc123")
        assert len(response.results) == 1
        assert provider.track_calls == 1
        assert provider.search_calls == 0
        assert response.results[0].score == 1.0

    async def test_url_without_hint_queries_direct_link_providers(self):
        candidate = _candidate(provider="bandcamp", external_id="url",
                               title="Song", artist="Artist")
        provider = FakeProvider("bandcamp", [candidate], direct_link=True)
        engine = SearchEngine(_registry(provider), _settings())
        response = await engine.search("https://example.com/track/song")
        assert len(response.results) == 1
        assert provider.search_calls == 1

    async def test_lookup_delegates_to_provider(self):
        candidate = _candidate(provider="jamendo", external_id="42")
        provider = FakeProvider("jamendo", [candidate])
        engine = SearchEngine(_registry(provider), _settings())
        found = await engine.lookup("jamendo", "42")
        assert found is candidate
        assert provider.track_calls == 1
        assert await engine.lookup("missing", "1") is None

    async def test_sort_by_quality_keeps_allowed_first(self):
        allowed = _candidate(provider="jamendo", external_id="1",
                             title="Song A", duration=200,
                             permission=PermissionStatus.DOWNLOAD_ALLOWED,
                             file_format="mp3", bitrate_kbps=128)
        forbidden = _candidate(provider="archive", external_id="2",
                               title="Song B", duration=210,
                               permission=PermissionStatus.OFFICIAL_LINK_ONLY,
                               file_format="flac", bitrate_kbps=900)
        engine = SearchEngine(_registry(FakeProvider("jamendo", [allowed]),
                                        FakeProvider("archive", [forbidden])),
                              _settings())
        response = await engine.search("Daft Punk", sort=SearchSort.QUALITY)
        assert [item.title for item in response.results] == ["Song A", "Song B"]


class TestRegistryCircuitBreaker:
    def test_opens_after_failures_and_recovers(self):
        provider = FakeProvider("flaky", [_candidate()])
        registry = _registry(provider)
        assert registry.available("flaky") is True
        registry.mark_failure("flaky", "boom")
        registry.mark_failure("flaky", "boom")
        assert registry.available("flaky") is False
        assert registry.circuit("flaky").health.value == "down"
        assert provider not in registry.enabled()
        registry.mark_success("flaky")
        assert registry.available("flaky") is True
        assert registry.circuit("flaky").health.value == "ok"

    def test_snapshot_contains_capabilities(self):
        registry = _registry(FakeProvider("jamendo", [_candidate()]))
        snapshot = registry.snapshot()
        assert "jamendo" in snapshot
        assert snapshot["jamendo"]["capabilities"]["download"] is True
        assert "enabled" in snapshot["jamendo"]
