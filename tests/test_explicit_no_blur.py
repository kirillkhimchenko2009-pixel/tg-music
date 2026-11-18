"""18+/«блюр»: запиканное не отдаётся нигде — плеер, бот, поиск.

По решению юзера цензурные заливки ЯМ (слова замазаны/запиканы) полностью
исключены: стрим 18+ идёт только с прямых сайтов/YouTube, скачивание
не отдаёт даже «наименее замученную» версию, поиск (веб + каталог +
инлайн) explicit/censored не показывает, кеш замученных заливок игнорируется.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import (AsyncSession, async_sessionmaker,
                                    create_async_engine)
from sqlalchemy.pool import StaticPool

from core.models import Base, Track
from services import ingest, multisearch
from services.catalog import local_search
from services.providers.base import TrackMeta


def _meta(source="yandex", title="Я тебя люблю", artist="Anacondaz",
          explicit=False, eid="y1") -> TrackMeta:
    return TrackMeta(source=source, external_id=eid, title=title,
                     artist=artist, duration=200, explicit=explicit)


@pytest.fixture
async def core_session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False,
                               class_=AsyncSession)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest.fixture(autouse=True)
def _quiet_chain(monkeypatch):
    """Тишина всех провайдеров: поиск везде пуст (нужные оверхеды —
    в конкретных тестах точечно включаются)."""
    async def _empty(*a, **k):
        return []
    for mod in ("services.providers.zaycev", "services.providers.soundcloud",
                "services.providers.promodj", "services.providers.ccmixter",
                "services.providers.audius", "services.providers.hotplayer",
                "services.providers.mp3party", "services.providers.hitmoz",
                "services.providers.zvuk"):
        monkeypatch.setattr(f"{mod}.search", _empty)
    monkeypatch.setattr("services.providers.yandex.YandexProvider.search",
                        _empty)
    monkeypatch.setattr(ingest, "_yt_candidates", _empty)
    async def _tok(session, user_id, kind):
        return "tok"
    monkeypatch.setattr(ingest, "get_token", _tok)


class TestChain18Plus:
    async def test_explicit_yandex_has_no_yandex_variant(self, monkeypatch):
        """18+ от ЯМ: цепочка не содержит ЯМ-варианта вовсе (запиканное
        не скачивается даже как запасное)."""
        async def _no_zc(q, m):
            return None
        monkeypatch.setattr(ingest, "_zaycev_original", _no_zc)
        chain = await ingest._candidates(None, 1, _meta(explicit=True), "mp3")
        assert all(c.source != "yandex" for c, _t in chain)

    async def test_explicit_yandex_zaycev_original_first(self, monkeypatch):
        """Если zaycev знает оригинал — он первый и единственный кандидат
        из пары «оригинал vs ЯМ»."""
        zc = _meta(source="zaycev", title="Я тебя люблю", eid="z1")

        async def _zc(q, m):
            return zc
        monkeypatch.setattr(ingest, "_zaycev_original", _zc)
        chain = await ingest._candidates(None, 1, _meta(explicit=True), "mp3")
        assert [(c.source, c.external_id) for c, _t in chain] == [("zaycev", "z1")]

    async def test_non_explicit_yandex_keeps_ym(self):
        """Регресс: обычный (не 18+) трек по-прежнему идёт с ЯМ-токеном."""
        chain = await ingest._candidates(None, 1, _meta(explicit=False), "mp3")
        assert chain and chain[0][0].source == "yandex" and chain[0][1] == "tok"

    async def test_generic_ym_block_explicit_skipped(self, monkeypatch):
        """18+ кандидат найден ЯМом у НЕ-яндекс источника — тоже без ЯМ."""
        from services import matching
        monkeypatch.setattr(matching, "best_match",
                            lambda meta, cands, min_score=0.5: cands[0])
        async def _no_zc(q, m):
            return None
        monkeypatch.setattr(ingest, "_zaycev_original", _no_zc)
        chain = await ingest._candidates(
            None, 1, _meta(source="audius", explicit=False, eid="a1"), "mp3")
        assert all(c.source != "yandex" for c, _t in chain)


class TestSearchClean:
    async def test_web_search_drops_explicit(self, monkeypatch):
        explicit = _meta(explicit=True, eid="e1")
        normal = _meta(explicit=False, eid="n1")

        async def fake(q, limit, token):
            return [explicit, normal]
        monkeypatch.setattr(multisearch, "_ADAPTERS", {"yandex": fake})
        monkeypatch.setattr(multisearch.registry, "specs",
                            lambda scope: [SimpleNamespace(provider_id="yandex")])
        monkeypatch.setattr(multisearch.registry, "health_mark",
                            lambda *a, **k: None)
        out = await multisearch.web_search("люблю", limit=10,
                                           tokens={"ym": "t"})
        assert [m.external_id for m in out] == ["n1"]


class TestLocalSearchClean:
    async def test_local_search_hides_explicit_and_censored(self, core_session):
        core_session.add_all([
            Track(source="yandex", external_id="1", title="Я тебя люблю",
                  artist="Anacondaz", explicit=False, censored=False),
            Track(source="yandex", external_id="2", title="Я тебя тебя",  # explicit
                  artist="Anacondaz", explicit=True, censored=False),
            Track(source="zaycev", external_id="3", title="Я тебя жду",
                  artist="Anacondaz", explicit=False, censored=True),
        ])
        await core_session.commit()
        rows = await local_search(core_session, "тебя", limit=10)
        # показан только чистый трек: explicit и censored скрыты
        assert [r.external_id for r in rows] == ["1"]


class TestCacheSkipsCensored:
    async def test_resolve_ignores_censored_cache(self, core_session, monkeypatch, tmp_path):
        track = Track(source="yandex", external_id="y1", title="Я тебя люблю",
                      artist="Anacondaz", explicit=True, censored=True,
                      file_id="f1")
        core_session.add(track)
        await core_session.commit()
        async def _chain(session, user_id, meta, quality):
            return [(meta, "tok")]
        monkeypatch.setattr(ingest, "_candidates", _chain)
        # «скачали» файл, детектор сказал: заливка с бипами/мутами
        f = tmp_path / "fill.mp3"
        f.write_bytes(b"x" * 16)
        from types import SimpleNamespace as NS
        async def _fake_dl(*a, **k):
            return NS(path=f, quality="mp3", size_bytes=16)
        monkeypatch.setattr(ingest.pipeline, "download", _fake_dl)
        async def _censored(p):
            return {"censored": True, "beeps": 18, "mutes": 2}
        monkeypatch.setattr(ingest.censor_mod, "check", _censored)
        res = await ingest.resolve(core_session, _meta(explicit=True), "mp3")
        # кеш с file_id НЕ отдан (там запиканная заливка), и «наименее
        # замученная» тоже не отдаётся — честный not_found
        assert res.status == "not_found"
        assert res.file_path is None
        assert "цензур" in (res.error or "")
        await core_session.refresh(track)
        assert track.censored is True
