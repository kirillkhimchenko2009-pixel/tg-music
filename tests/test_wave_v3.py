"""Wave v3: движок анализирует лучше, а не выдаёт в основном «Любимое»,
и настройки формулы реально меняют партию (жалобы юзера 18.11).

Проверяются:
  • квоты лайков в партии по «Разнообразию» (favorite/default/discover);
  • «Открытия» требуют незнакомых артистов, «Любимое звучание» — лайки;
  • язык формулы (ru / non-ru) вытесняет чужой язык;
  • жанр/настроение формулы поднимают совпадающие треки (и тянут их в пул);
  • лайк больше не автоматический максимум taste_fit.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (AsyncSession, async_sessionmaker,
                                    create_async_engine)
from sqlalchemy.pool import StaticPool

from core.models import (Base, Playlist, PlaylistTrack, Track, User,
                         WaveSettings)


@pytest.fixture
async def db():
    from services.wave.intel import config as _cfg
    _cfg.invalidate()               # кеш конфига не течёт между тестами
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        s.add(User(id=111, first_name="tester"))
        await s.flush()

        async def track(tid, title, artist, downloads=5):
            t = Track(source="test", external_id=tid, title=title, artist=artist,
                      downloads=downloads, duration=200)
            s.add(t)
            return t

        # два «любимых» артиста (по 3 трека) + два посторонних
        await track("l1", "lofi закат", "Lofi Dream", downloads=20)
        await track("l2", "спокойный lofi", "Lofi Dream", downloads=18)
        await track("l3", "мягкий lofi вечер", "Lofi Dream", downloads=16)
        await track("n1", "synth волна", "Neon Wave", downloads=15)
        await track("n2", "танцевальный synth", "Neon Wave", downloads=14)
        await track("n3", "synth ночной", "Neon Wave", downloads=13)
        # посторонние: жанр «шансон» с малым downloads (в топ-420 пула из
        # 2 треков НЕ попадёт — проверка расширения пула формулой)
        await track("s1", "шансон у костра", "Bard Old", downloads=1)
        await track("s2", "бард река", "Bard Old", downloads=1)
        await track("s3", "шансон трактир", "Bard Two", downloads=1)
        # английский трек для языковых тестов
        await track("e1", "english love song", "UK Singer", downloads=12)
        await track("e2", "party all night", "UK Singer", downloads=11)
        await track("e3", "summer dance", "UK Singer", downloads=10)
        await s.flush()
        pl = Playlist(owner_id=111, title="❤ Любимое")
        s.add(pl)
        await s.flush()
        liked = (await s.execute(select(Track.id).where(
            Track.artist.in_(("Lofi Dream", "Neon Wave"))))).scalars().all()
        for pos, tid in enumerate(liked):
            s.add(PlaylistTrack(playlist_id=pl.id, track_id=tid, position=pos))
        await s.commit()
    yield maker
    await engine.dispose()


def ws_of(s, **kw) -> WaveSettings:
    ws = WaveSettings(user_id=111)
    for k, v in kw.items():
        setattr(ws, k, v)
    s.add(ws)
    return ws


async def batch_titles(db, **settings) -> list[tuple[str, str]]:
    """Партия движка B-v2: [(title, artist), ...]."""
    from services.wave import service
    async with db() as s:
        ws_of(s, **settings)
        await s.flush()
        resp = await service.start(s, 111, "own", ctx_request={})
        await s.commit()
    return [(t["title"], t["artist"]) for t in resp["tracks"]]


# ------------------------------------------------------------- разнообразие


class TestDiversityQuotas:
    async def test_favorite_batch_mostly_likes(self, db):
        pairs = await batch_titles(db, diversity="favorite")
        liked = [a for _, a in pairs if a in ("Lofi Dream", "Neon Wave")]
        assert len(liked) >= 2, f"favorite: лайков мало — {pairs}"

    async def test_default_batch_at_most_one_liked_track(self, db):
        pairs = await batch_titles(db, diversity="default")
        liked = [a for _, a in pairs if a in ("Lofi Dream", "Neon Wave")]
        assert len(liked) <= 1, f"default: лайки залили партию — {pairs}"

    async def test_discover_batch_no_liked_unknown_artists(self, db):
        pairs = await batch_titles(db, diversity="discover")
        liked = [a for _, a in pairs if a in ("Lofi Dream", "Neon Wave")]
        assert not liked, f"discover: лайки просочились — {pairs}"
        others = {a for _, a in pairs}
        assert others & {"Bard Old", "Bard Two", "UK Singer"}, \
            f"discover: партия не из незнакомых — {pairs}"

    async def test_scenario_explore_follows_diversity(self, db):
        from services.wave.intel.context import scenario_from_formula
        async with db() as s:
            ws = ws_of(s, mood="calm", diversity="discover")
            await s.flush()
            scen = scenario_from_formula(ws)
            assert scen.diversity == "discover"
            assert scen.explore == 0.55
        async with db() as s:               # отдельная сессия: без SAWarning
            ws2 = ws_of(s, diversity="favorite")
            await s.flush()
            scen2 = scenario_from_formula(ws2)
            assert scen2.title == "Любимое звучание"
            assert scen2.explore == 0.06

    async def test_diversity_without_formula_still_applies(self, db):
        """Формула пуста, но «Разнообразие» выставлено — политика применяется."""
        async with db() as s:
            from services.wave.intel import endless as E
            from services.wave.intel.context import Context
            ws = ws_of(s, diversity="discover")
            await s.flush()
            scen = E._scenario_for(Context(time_of_day="day"), ws, None)
            assert scen.diversity == "discover"
            assert scen.explore == 0.55


# ------------------------------------------------------------------- язык


class TestLanguageFormula:
    async def test_ru_language_pushes_out_english(self, db):
        pairs = await batch_titles(db, language="ru", diversity="favorite")
        ru = [t for t, _ in pairs if any("а" <= c.lower() <= "я" for c in t)]
        assert len(ru) >= 2, f"ru: английские залили партию — {pairs}"

    async def test_non_ru_language_pushes_out_cyrillic(self, db):
        pairs = await batch_titles(db, language="non-ru", diversity="favorite")
        en = [t for t, _ in pairs
              if not any("а" <= c.lower() <= "я" for c in t)]
        assert en, f"non-ru: английский не пробился — {pairs}"


# ------------------------------------------------- формула: жанры/настроения


class TestFormulaFit:
    async def test_shanson_formula_pulls_outside_top_pool(self, db):
        """Жанр «шансон» тянет в пул треки вне топа (downloads=1) — и они
        выигрывают у лайков, которые к шансону отношения не имеют."""
        async with db() as s:
            ws = ws_of(s, genres=["shanson"], diversity="favorite")
            await s.flush()
            from services.wave.intel import endless as E
            pool = await E._load_pool(s, 111, set(), limit=2, ws=ws,
                                      scen=E.scenario_from_formula(ws))
            ids = {t.external_id for t in pool.values()}
            assert {"s1", "s2", "s3"} & ids, \
                f"формула не дотащила тематические треки: {ids}"

    async def test_ranked_formula_promotes_matching_track(self, db):
        """При формуле «спокойное» спокойный lofi выше вечеринки UK Singer."""
        async with db() as s:
            from services.wave.intel import endless as E
            from services.wave.intel.ranker import _context_fit
            from services.wave.intel.sources import SourceContext
            from services.wave.intel.taste import SessionTaste, TasteSnapshot
            ws = ws_of(s, mood="calm")
            await s.flush()
            scen = E.scenario_from_formula(ws)
            ctx = E.build_context(ws, {"local_hour": 14}, TasteSnapshot(),
                                  SessionTaste())
            ctx.scenario = scen
            calm = (await s.execute(select(Track).where(
                Track.title.like("%спокойн%")))).scalar_one()
            party = (await s.execute(select(Track).where(
                Track.title.like("%party%")))).scalar_one()
            sc = SourceContext(s=s, user_id=111, ws=ws, sess=None, ctx=ctx,
                               snap=TasteSnapshot(), st=ctx.session,
                               liked=set(), pool={}, played=set())
            assert _context_fit(calm, sc, scen) > _context_fit(party, sc, scen)

    async def test_mood_mismatch_penalized(self, db):
        """Настроение трека определено и противоречит формуле → штраф."""
        async with db() as s:
            from services.wave.intel import endless as E
            from services.wave.intel.ranker import _context_fit
            from services.wave.intel.sources import SourceContext
            from services.wave.intel.taste import SessionTaste, TasteSnapshot
            ws = ws_of(s, mood="calm")
            await s.flush()
            scen = E.scenario_from_formula(ws)
            ctx = E.build_context(ws, {"local_hour": 14}, TasteSnapshot(),
                                  SessionTaste())
            ctx.scenario = scen
            dance = (await s.execute(select(Track).where(
                Track.title.like("%танцеваль%")))).scalar_one()
            calm = (await s.execute(select(Track).where(
                Track.title.like("%спокойн%")))).scalar_one()
            sc = SourceContext(s=s, user_id=111, ws=ws, sess=None, ctx=ctx,
                               snap=TasteSnapshot(), st=ctx.session,
                               liked=set(), pool={}, played=set())
            assert _context_fit(calm, sc, scen) > _context_fit(dance, sc, scen)


# ------------------------------------------------------- лайк не авто-максимум


class TestLikedNotAutowin:
    async def test_taste_fit_liked_cap(self, db):
        from services.wave.intel.ranker import _taste_fit
        from services.wave.intel.sources import SourceContext
        from services.wave.intel.taste import SessionTaste, TasteSnapshot
        import types
        snap = TasteSnapshot()
        sc = SourceContext(s=None, user_id=111, ws=types.SimpleNamespace(),
                           sess=None, ctx=None, snap=snap, st=SessionTaste(),
                           liked={42}, pool={}, played=set())
        track = types.SimpleNamespace(id=42, artist="Nobody Known", title="x",
                                      album=None)
        assert _taste_fit(track, sc) == pytest.approx(0.88)
