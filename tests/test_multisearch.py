"""Комбинированный поиск по реестру (мастер-промпт §4): изоляция ошибок,
таймауты per-provider, области, round-robin слияние, дедуп, токены."""
from __future__ import annotations

import asyncio

import pytest

from services import multisearch as ms
from services import registry
from services.providers.base import TrackMeta


def _meta(source: str, ext: str, title: str = "Т", artist: str = "А") -> TrackMeta:
    return TrackMeta(source=source, external_id=ext, title=title, artist=artist)


@pytest.fixture
def adapters(monkeypatch):
    """Подмена поисковых адаптеров реестра на фейковые."""
    calls: dict[str, list] = {}

    def make(pid, results=None, error=None, delay=0.0, capture=True):
        async def fn(q, limit, token=None):
            if capture:
                calls.setdefault(pid, []).append((q, limit, token))
            if delay:
                await asyncio.sleep(delay)
            if error:
                raise error
            return list(results or [])
        return fn

    fake = {}
    for pid in ("zaycev", "promodj", "audius", "ytmusic"):
        fake[pid] = make(pid)
    monkeypatch.setattr(ms, "_ADAPTERS", fake)
    return make, calls


class TestParallelAndIsolation:
    async def test_sources_start_concurrently(self, adapters):
        """Медленный адаптер не должен блокировать старт остальных."""
        make, _ = adapters
        active = set()
        both_active = asyncio.Event()

        def waiting(pid):
            async def fn(q, limit, token=None):
                active.add(pid)
                if len(active) == 2:
                    both_active.set()
                try:
                    await asyncio.wait_for(both_active.wait(), timeout=0.2)
                    return [_meta(pid, pid)]
                finally:
                    active.discard(pid)
            return fn

        ms._ADAPTERS["zaycev"] = waiting("zaycev")
        ms._ADAPTERS["audius"] = waiting("audius")
        res = await ms.web_search("кино", limit=10)
        assert both_active.is_set()
        assert {m.source for m in res} == {"zaycev", "audius"}

    async def test_all_sources_queried(self, adapters):
        make, calls = adapters
        monkey = make("vk", [_meta("vk", "s:кино")])
        ms._ADAPTERS["vk"] = monkey
        res = await ms.web_search("кино", limit=10)
        assert set(calls) >= {"zaycev", "promodj", "audius", "vk"}
        assert any(m.source == "vk" for m in res)

    async def test_one_adapter_failure_does_not_break_search(self, adapters):
        """Ошибка одного адаптера не останавливает весь поиск (§4)."""
        make, calls = adapters
        ms._ADAPTERS["promodj"] = make("promodj", error=RuntimeError("api down"))
        ms._ADAPTERS["audius"] = make("audius", [_meta("audius", "a1")])
        res = await ms.web_search("кино", limit=10)
        assert any(m.source == "audius" for m in res)
        registry.health_mark("promodj", False, error="api down")

    async def test_timeout_isolated_per_provider(self, adapters, monkeypatch):
        make, calls = adapters
        ms._ADAPTERS["zaycev"] = make("zaycev", [_meta("zaycev", "z1")], delay=2.0)
        monkeypatch.setitem(ms._WAIT, "zaycev", 0.01)
        res = await ms.web_search("кино", limit=10)
        assert not any(m.source == "zaycev" for m in res)

    async def test_health_marked_ok_and_fail(self, adapters):
        make, calls = adapters
        ms._ADAPTERS["zaycev"] = make("zaycev", [_meta("zaycev", "z1")])
        ms._ADAPTERS["promodj"] = make("promodj", error=RuntimeError("x"))
        await ms.web_search("кино", limit=10)
        h = registry.health_all()
        assert h["zaycev"]["ok"] is True
        assert h["promodj"]["ok"] is False


class TestMerge:
    async def test_round_robin_interleave(self, adapters):
        """Первые результаты всех источников впереди (справедливая выдача)."""
        make, _ = adapters
        ms._ADAPTERS["zaycev"] = make("zaycev", [_meta("zaycev", f"z{i}") for i in range(3)])
        ms._ADAPTERS["audius"] = make("audius", [_meta("audius", f"a{i}") for i in range(3)])
        res = await ms.web_search("кино", limit=10)
        first_two = {res[0].source, res[1].source}
        assert first_two == {"zaycev", "audius"}

    async def test_dedupe_by_source_and_id(self, adapters):
        make, _ = adapters
        ms._ADAPTERS["zaycev"] = make("zaycev", [_meta("zaycev", "z1"), _meta("zaycev", "z1")])
        res = await ms.web_search("кино", limit=10)
        keys = [(m.source, m.external_id) for m in res]
        assert len(keys) == len(set(keys))

    async def test_empty_query_no_calls(self, adapters):
        _, calls = adapters
        res = await ms.web_search("   ", limit=10)
        assert res == [] and not calls

    async def test_limit_respected(self, adapters):
        make, _ = adapters
        ms._ADAPTERS["zaycev"] = make("zaycev", [_meta("zaycev", f"z{i}") for i in range(20)])
        res = await ms.web_search("кино", limit=5)
        assert len(res) <= 10           # limit*2 запас на дедуп, не 20


class TestScopes:
    async def test_scope_ru_only_russian_sources(self, adapters):
        make, calls = adapters
        ms._ADAPTERS["promodj"] = make("promodj", [_meta("promodj", "p1")])
        res = await ms.web_search("кино", limit=10, scope="ru")
        sources = {m.source for m in res}
        assert sources <= {"zaycev", "promodj", "mp3party", "hitmoz", "yandex",
                           "zvuk", "vk", "mts", "lisen"}
        assert "audius" not in sources

    async def test_scope_intl(self, adapters):
        make, _ = adapters
        ms._ADAPTERS["audius"] = make("audius", [_meta("audius", "a1")])
        res = await ms.web_search("кино", limit=10, scope="intl")
        sources = {m.source for m in res}
        assert "audius" in sources
        assert "zaycev" not in sources

    async def test_scope_downloadable_excludes_official_only(self, adapters):
        make, _ = adapters
        ms._ADAPTERS["audius"] = make("audius", [_meta("audius", "a1")])
        res = await ms.web_search("кино", limit=10, scope="downloadable")
        assert any(m.source == "audius" for m in res)
        # vk/mts/lisen/fma не участвуют
        assert not any(m.source in ("vk", "mts", "lisen", "fma") for m in res)

    async def test_scopes_for_mapping(self):
        assert "ru" in ms.scopes_for(_meta("zaycev", "1"))
        assert "intl" in ms.scopes_for(_meta("audius", "1"))
        assert "downloadable" in ms.scopes_for(_meta("zaycev", "1"))
        assert "official_links" in ms.scopes_for(_meta("vk", "s:x"))
        assert "downloadable" not in ms.scopes_for(_meta("vk", "s:x"))
        assert ms.scopes_for(_meta("unknown_src", "1")) == ["all"]


class TestTokens:
    async def test_tokens_passed_to_auth_required(self, adapters):
        """ЯМ/Звук работают по личным токенам пользователя (AUTH_REQUIRED)."""
        make, calls = adapters

        async def ya(q, limit, token=None):
            calls.setdefault("yandex", []).append((q, limit, token))
            return [_meta("yandex", "y1")] if token else []

        ms._ADAPTERS["yandex"] = ya
        res = await ms.web_search("кино", limit=10,
                                  tokens={"ym": "tok123", "zvuk": None})
        assert any(m.source == "yandex" for m in res)
        assert calls["yandex"][0][2] == "tok123"

    async def test_no_token_no_yandex_results(self, adapters):
        make, calls = adapters

        async def ya(q, limit, token=None):
            calls.setdefault("yandex", []).append((q, limit, token))
            return [_meta("yandex", "y1")] if token else []

        ms._ADAPTERS["yandex"] = ya
        res = await ms.web_search("кино", limit=10, tokens={"ym": None})
        assert not any(m.source == "yandex" for m in res)
