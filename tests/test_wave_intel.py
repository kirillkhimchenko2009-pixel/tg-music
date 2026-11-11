"""Тесты интеллектуального слоя волны (этапы 4–8, 10): вкус, контекст,
источники+ранжирование, последовательность, фидбек, shake, метрики, discovery.

БД — изолированная in-memory SQLite (core.Base), каталог синтетический:
жанры/настроения живут только словами каталога (аудио-фичи не выдумываем).
"""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (AsyncSession, async_sessionmaker,
                                    create_async_engine)
from sqlalchemy.pool import StaticPool

from core.models import (ArtistStats, Base, PlayEvent, Playlist,
                         PlaylistTrack, TasteProfile, Track, TrackFeedback,
                         User, WaveEvent, WaveKV, WaveSession, WaveSettings,
                         utcnow)

# ------------------------------------------------------------------ fixtures


@pytest.fixture
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        s.add(User(id=111, first_name="tester"))
        s.add(User(id=222, first_name="other"))
        await s.flush()

        async def track(tid, title, artist, downloads=5, explicit=False,
                        album=None, year=None, quality=None, size=None, dur=200):
            t = Track(source="test", external_id=tid, title=title, artist=artist,
                      downloads=downloads, explicit=explicit, album=album,
                      year=year, quality=quality, size_bytes=size or 0,
                      duration=dur)
            s.add(t)
            return t

        await track("c1", "Спокойный lo-fi рассвет", "Lofi Dream", downloads=20)
        await track("c2", "тихий ambient вечер", "Lofi Dream", downloads=15)
        await track("c3", "мягкий фокус concentr", "Lofi Dream", downloads=10)
        await track("a1", "жёсткий метал хаос", "Steel Fury", downloads=30)
        await track("a2", "агрессивный панк гром", "Steel Fury", downloads=25)
        await track("a3", "explicit панк стена", "Steel Fury", explicit=True)
        await track("p1", "весёлая поп вечеринка", "Pop Star", downloads=40)
        await track("p2", "танцевальный клуб хит", "Pop Star", downloads=35)
        await track("e1", "энергичная электроника дорога", "Neon Wave", downloads=18)
        await track("e2", "танцевальный synth драйв", "Neon Wave", downloads=12)
        await track("u1", "Трек номер один", "Unknown X", downloads=3)
        await track("u2", "Track Two", "Yan Unknown", downloads=2)
        await s.flush()
        # Lofi Dream с альбомом и годами (для insight)
        rows = (await s.execute(select(Track).where(Track.artist == "Lofi Dream"))).scalars().all()
        rows[0].album = "Quiet Mornings"
        rows[0].year = 2021
        rows[1].album = "Quiet Mornings"
        rows[1].year = 2022
        rows[2].album = "Night Focus"
        rows[2].year = 2023
        await s.commit()
    yield maker
    await engine.dispose()


@pytest.fixture
async def likes(db):
    """Юзер 111 любит Lofi Dream + Neon Wave (плейлист «❤ Любимое»)."""
    async with db() as s:
        pl = Playlist(owner_id=111, title="❤ Любимое")
        s.add(pl)
        await s.flush()
        ids = (await s.execute(
            select(Track.id).where(Track.artist.in_(("Lofi Dream", "Neon Wave")))
        )).scalars().all()
        for pos, tid in enumerate(ids):
            s.add(PlaylistTrack(playlist_id=pl.id, track_id=tid, position=pos))
        await s.commit()
    return likes


def wave_settings_of(s, user_id=111, **kw):
    ws = WaveSettings(user_id=user_id)
    for k, v in kw.items():
        setattr(ws, k, v)
    s.add(ws)
    return ws


async def make_session(db, user_id=111, engine="own"):
    async with db() as s:
        sess = WaveSession(user_id=user_id, engine=engine, seeds=[],
                           played_ids=[], played_track_ids=[], prefetch=[],
                           scenario={}, context={})
        s.add(sess)
        await s.commit()
        return sess.id


# ------------------------------------------------------------------ конфиг


class TestConfig:
    async def test_weights_override_from_wavekv(self, db):
        async with db() as s:
            s.add(WaveKV(key="weights", value={"context_fit": 0.9, "bogus": 5}))
            await s.commit()
        from services.wave.intel.config import load_config
        cfg = await load_config(s)
        assert cfg.weights["context_fit"] == 0.9
        assert cfg.weights["taste_fit"] == 0.30          # остальные — дефолт
        assert "bogus" not in cfg.weights


# ------------------------------------------------------------------ вкус


class TestTaste:
    async def test_profile_strong_artists_and_levels(self, db, likes):
        async with db() as s:
            # история: юзер дослушал Lofi Dream, скипнул Steel Fury
            for t in (await s.execute(
                    select(Track).where(Track.artist == "Lofi Dream"))).scalars():
                s.add(PlayEvent(user_id=111, track_id=t.id, played_seconds=120,
                                finished=True))
            fury = (await s.execute(
                select(Track).where(Track.artist == "Steel Fury"))).scalars().first()
            s.add(PlayEvent(user_id=111, track_id=fury.id, played_seconds=5,
                            skipped=True))
            await s.commit()

            from services.wave.intel import taste as T
            profile = await T.refresh_profile(s, 111)
            snap = T.snapshot_from_profile(profile)
            assert "lofi dream" in snap.levels.strong_artists
            assert snap.artist_fit("lofi dream") > 0.5
            assert snap.artist_fit("steel fury") <= snap.artist_fit("lofi dream")
            assert snap.genre_fit({"classical"}) <= snap.genre_fit({"electronic"})

    async def test_dislike_moratorium_in_snapshot(self, db, likes):
        async with db() as s:
            fury = (await s.execute(
                select(Track).where(Track.artist == "Steel Fury"))).scalars().first()
            s.add(TrackFeedback(user_id=111, track_id=fury.id, kind=-1))
            await s.commit()
            from services.wave.intel import taste as T
            d_ids, d_artists = await T.disliked(s, 111)
            assert fury.id in d_ids
            assert "steel fury" in d_artists

    async def test_touch_profile_stale_triggers_recalc(self, db, likes):
        async with db() as s:
            from services.wave.intel import taste as T
            from services.wave.intel.feedback import touch_profile_stale
            p1 = await T.get_profile(s, 111)
            before = p1.refreshed_at
            await touch_profile_stale(s, 111)
            p2 = await T.get_profile(s, 111)
            assert p2.refreshed_at > before


# ------------------------------------------------------------------ контекст


class TestContext:
    def test_time_of_day_mapping(self):
        from services.wave.intel.context import time_of_day
        assert time_of_day(6) == "early_morning"
        assert time_of_day(9) == "morning"
        assert time_of_day(14) == "day"
        assert time_of_day(20) == "evening"
        assert time_of_day(2) == "late_night"

    async def test_build_context_validates_and_prioritizes(self, db):
        async with db() as s:
            ws = wave_settings_of(s, mood="active")
            await s.flush()
            from services.wave.intel import endless as E
            ctx = E.build_context(ws, {
                "local_hour": 21, "local_weekday": 6,
                "activity": "нет такое", "mood": "edge", "device": "fridge",
                "familiarity": "new",
            }, snap=None, st=None)
            assert ctx.time_of_day == "evening"
            assert ctx.weekday == "weekend"
            assert ctx.activity is None            # невалидное занятие откинуто
            assert ctx.device is None
            assert ctx.mood == "edge" and ctx.familiarity == "new"
            assert ctx.effective_mood() == "edge"  # явный выбор выше формулы

    async def test_context_disabled_falls_back_to_formula(self, db):
        async with db() as s:
            ws = wave_settings_of(s, mood="calm", context_enabled=False)
            await s.flush()
            from services.wave.intel import endless as E
            ctx = E.build_context(ws, {"local_hour": 9, "activity": "sport",
                                       "mood": "active"}, snap=None, st=None)
            assert ctx.activity is None and ctx.mood is None
            assert ctx.effective_mood() == "calm"  # только формула, без времени суток

    async def test_scenario_cards_dynamic(self, db, likes):
        async with db() as s:
            wave_settings_of(s)
            await s.flush()
            from services.wave.intel import context as C
            from services.wave.intel.taste import TasteSnapshot
            snap = TasteSnapshot()
            cards = C.scenario_list(C.Context(time_of_day="day"), snap)
            ids = [c.id for c in cards]
            assert "favorites" not in ids          # нет LEVEL A — карточки нет
            assert ids[0] == "foryou"
            snap.levels.strong_artists = ["lofi dream"]
            cards2 = C.scenario_list(C.Context(time_of_day="day"), snap)
            assert "favorites" in [c.id for c in cards2]
            sc = C.pick_scenario(C.Context(time_of_day="day", mood="active"),
                                 snap, requested="energy")
            assert sc.id == "energy"


# ------------------------------------------------------------------ партия (endless)


class TestEndlessBatch:
    async def test_service_start_own_engine_returns_tracks_and_scenario(self, db, likes):
        from services.wave import service
        async with db() as s:
            wave_settings_of(s)
            await s.flush()
            resp = await service.start(s, 111, "own", ctx_request={"local_hour": 14})
            await s.commit()
        assert resp["engine"] == "own"
        assert len(resp["tracks"]) == 3
        assert resp["scenario"] is not None
        ids = [t["id"] for t in resp["tracks"]]
        assert len(ids) == len(set(ids))

    async def test_service_start_with_requested_scenario(self, db, likes):
        """Карточка сценария: движок B, сценарий эхом, rotor не вызывается."""
        from services.wave import service
        async with db() as s:
            wave_settings_of(s)
            await s.flush()
            resp = await service.start(s, 111, "yandex",
                                       ctx_request={"local_hour": 14,
                                                    "scenario": "discovery"})
            await s.commit()
        assert resp["engine"] == "own"          # сценарий форсит наш движок
        assert resp["scenario"]["id"] == "discovery"
        assert resp["scenario"]["title"]
        assert len(resp["tracks"]) >= 1

    async def test_disliked_and_explicit_never_in_batch(self, db, likes):
        from services.wave import service
        async with db() as s:
            fury = (await s.execute(
                select(Track).where(Track.artist == "Steel Fury"))).scalars().first()
            s.add(TrackFeedback(user_id=111, track_id=fury.id, kind=-1))
            wave_settings_of(s, allow_explicit=False)
            await s.commit()
            for i in range(2):                     # несколько партий
                resp = await service.start(s, 111, "own", ctx_request={})
                await s.commit()
                ids = {t["id"] for t in resp["tracks"]}
                artists = {t["artist"] for t in resp["tracks"]}
                assert fury.id not in ids
                assert "Steel Fury" not in artists  # мораторий артиста
                for t in resp["tracks"]:
                    assert not t["explicit"]

    async def test_next_batch_no_repeats_of_played(self, db, likes):
        from services.wave import service
        async with db() as s:
            wave_settings_of(s)
            r1 = await service.start(s, 111, "own", ctx_request={})
            await s.commit()
            sid = r1["session_id"]
            seen = {t["id"] for t in r1["tracks"]}
            last = {"track_id": r1["tracks"][0]["id"], "played_seconds": 190,
                    "event": "finished"}
            r2 = await service.next_batch(s, 111, sid, last, ctx_request={})
            await s.commit()
            got = {t["id"] for t in r2["tracks"]}
            assert not (got & seen), "уже отыгранные не возвращаются"

    async def test_calm_formula_never_titled_calm_with_aggressive_pool(self, db):
        """§4.3: формула «спокойное», а весь подходящий пул агрессивный →
        название обязано откатиться на нейтральное «Для тебя»."""
        from services.wave import service
        async with db() as s:
            # юзер без лайков: пул — популярное каталога (жёсткое сверху)
            wave_settings_of(s, mood="calm")
            await s.commit()
            resp = await service.start(s, 111, "own", ctx_request={"local_hour": 21})
            await s.commit()
            scen = resp["scenario"] or {}
            artists = {t["artist"] for t in resp["tracks"]}
            if artists == {"Steel Fury"}:
                assert scen.get("id") != "formula" or scen.get("title") == "Для тебя"

    async def test_context_card_scenario_selected_on_next(self, db, likes):
        from services.wave import service
        async with db() as s:
            wave_settings_of(s)
            r1 = await service.start(s, 111, "own", ctx_request={})
            await s.commit()
            sid = r1["session_id"]
            r2 = await service.next_batch(s, 111, sid, None,
                                          ctx_request={"scenario": "discovery",
                                                       "local_hour": 14})
            await s.commit()
            assert r2["scenario"] is not None
            assert r2["scenario"]["id"] in ("discovery", "foryou")


# ------------------------------------------------------------------ фидбек


class TestFeedback:
    async def test_skip_classification_by_moment(self):
        from services.wave.intel.events import classify_skip
        assert classify_skip(5, 200) == "first_seconds"
        assert classify_skip(15, 200) == "few_seconds"
        assert classify_skip(100, 200) == "mid"
        assert classify_skip(190, 200) == "near_end"
        assert classify_skip(30, 200, after_similar=True) == "after_similar"

    async def test_dislike_reason_artist_excludes(self, db, likes):
        from services.wave.intel import feedback as F
        async with db() as s:
            fury = (await s.execute(
                select(Track).where(Track.artist == "Steel Fury"))).scalars().first()
            res = await F.process_event(s, 111, kind="disliked", track=fury,
                                        session_id=None, reason="artist")
            await s.commit()
            assert res["policy"]["scope"] == "artist"
            from core.models import TasteProfile
            p = (await s.execute(select(TasteProfile))).scalar_one()
            assert "steel fury" in (p.excluded_artists or [])

    async def test_dislike_reason_genre_excludes(self, db, likes):
        from services.wave.intel import feedback as F
        async with db() as s:
            rock = (await s.execute(
                select(Track).where(Track.title.like("%панк%")))).scalars().first()
            res = await F.process_event(s, 111, kind="disliked", track=rock,
                                        session_id=None, reason="genre")
            await s.commit()
            assert res["policy"]["scope"] == "genre"
            from core.models import TasteProfile
            p = (await s.execute(select(TasteProfile))).scalar_one()
            assert "rock" in (p.excluded_genres or [])

    async def test_dislike_mood_keeps_taste(self, db, likes):
        from services.wave.intel import feedback as F
        async with db() as s:
            from services.wave.intel import taste as T
            await T.get_profile(s, 111)        # профиль уже существует
            pop = (await s.execute(
                select(Track).where(Track.artist == "Pop Star"))).scalars().first()
            res = await F.process_event(s, 111, kind="disliked", track=pop,
                                        session_id=None, reason="too_energy")
            await s.commit()
            assert res["policy"]["scope"] == "session"
            from core.models import TasteProfile
            p = (await s.execute(select(TasteProfile))).scalar_one()
            assert not (p.excluded_artists or [])   # вкус не тронут
            assert not (p.excluded_genres or [])

    async def test_log_transition_writes_wave_event(self, db, likes):
        from services.wave.intel import feedback as F
        sess_id = await make_session(db)
        async with db() as s:
            lofi = (await s.execute(
                select(Track).where(Track.artist == "Lofi Dream"))).scalars().first()
            await F.log_transition(s, 111, sess_id, lofi.id, "skip", 4.0)
            await s.commit()
            ev = (await s.execute(select(WaveEvent).where(
                WaveEvent.kind == "skipped"))).scalar_one()
            assert ev.reason == "first_seconds"
            assert ev.session_id == sess_id

    async def test_skip_after_similar_detected(self, db, likes):
        from services.wave.intel import feedback as F
        async with db() as s:
            t1 = (await s.execute(select(Track).where(Track.external_id == "a1"))).scalar_one()
            t2 = (await s.execute(select(Track).where(Track.external_id == "a2"))).scalar_one()
            for t in (t1, t2):
                s.add(WaveEvent(user_id=111, track_id=t.id, kind="skipped",
                                reason="mid"))
            await s.commit()
            assert await F.skip_after_similar(s, 111, None) is True


# ------------------------------------------------------------------ shake


class TestShake:
    async def test_cooldown_blocks_parallel_shakes(self, db):
        from services.wave.intel import shake as S
        S._last_shake.clear()
        assert S.shake_allowed(999) is True
        assert S.shake_allowed(999) is False
        assert S.shake_allowed(998) is True       # другой юзер — ок

    async def test_alternate_changes_mood_not_taste(self, db, likes):
        async with db() as s:
            ws = wave_settings_of(s)
            await s.flush()
            from services.wave.intel import endless as E, shake as S
            ctx = await E.build_context_full(s, 111, ws, None, {"local_hour": 15})
            ctx.scenario = None
            ctx.mood = "calm"
            res = S.alternate_scenario(ctx, user_id=111)
            assert res.scenario.id.startswith("shake:")
            assert res.changed
            assert res.scenario.moods
            assert "calm" not in res.scenario.moods  # настроение сменилось
            assert res.scenario.familiarity in (None, "favorite", "similar",
                                                "new", "unknown", "experimental")


# ------------------------------------------------------------------ метрики


class TestMetrics:
    async def test_rates_computed(self, db, likes):
        async with db() as s:
            t1 = (await s.execute(select(Track).where(Track.external_id == "c1"))).scalar_one()
            t2 = (await s.execute(select(Track).where(Track.external_id == "a1"))).scalar_one()
            for tid, played in ((t1.id, 200), (t2.id, 8)):
                s.add(WaveEvent(user_id=111, track_id=tid, kind="track_started",
                                played_seconds=played))
            s.add(WaveEvent(user_id=111, track_id=t1.id, kind="completed",
                            played_seconds=200))
            s.add(WaveEvent(user_id=111, track_id=t2.id, kind="skipped",
                            reason="first_seconds", played_seconds=8))
            s.add(WaveEvent(user_id=111, track_id=t1.id, kind="liked"))
            await s.commit()
            from services.wave.intel import metrics as M
            m = await M.metrics(s, user_id=111, days=7)
            j = m.to_json()
            assert j["plays"] == 2
            assert j["completion_rate"] == 0.5
            assert j["skip_rate"] == 0.5
            assert j["like_rate"] == 0.5
            assert j["unique_artists"] >= 2


# ------------------------------------------------------------------ discovery


class TestDiscovery:
    async def test_cards_only_unknown_artists(self, db, likes):
        async with db() as s:
            from services.wave.intel import discovery as D
            cards = await D.discovery_cards(s, 111, limit=8)
            names = {c["artist_key"] for c in cards}
            assert "lofi dream" not in names        # лайкнут — не открытие
            assert "neon wave" not in names
            assert names                            # что-то новое найдено
            for c in cards:
                assert c["genres"]                  # открытие опирается на жанры


    async def test_artist_profile_insight_from_facts(self, db):
        async with db() as s:
            from services.wave.intel import discovery as D
            prof = await D.artist_profile(s, 111, "Lofi Dream")
            assert prof is not None
            assert prof.tracks_count == 3
            assert "Quiet Mornings" in prof.albums
            assert prof.insight and "2021" in prof.insight
            # у бедного на метаданные артиста реплики нет (не выдумываем)
            poor = await D.artist_profile(s, 111, "Unknown X")
            assert poor is not None
            assert poor.insight is None


# ------------------------------------------------------------------ последовательность


class TestSequence:
    def _mk(self, tid, artist, title):
        return Track(id=tid, source="test", external_id=f"x{tid}",
                     title=title, artist=artist, duration=180)

    def test_no_artist_twice_in_batch(self):
        from services.wave.intel.ranker import ScoredCandidate
        from services.wave.intel.sources import Candidate
        from services.wave.intel.sequence import optimize_sequence
        from services.wave.intel.config import WaveConfig

        ranked = []
        for i in range(4):
            t = self._mk(i, "Steel Fury", f"жёсткий метал {i}")
            ranked.append(ScoredCandidate(Candidate(t, "artist", 1.0, "why"),
                                          score=2.0 - i * 0.1))
        calm = self._mk(9, "Lofi Dream", "спокойный lo-fi")
        ranked.append(ScoredCandidate(Candidate(calm, "content", 0.5, "why"),
                                      score=1.0))
        neon = self._mk(10, "Neon Wave", "энергичный synth")
        ranked.append(ScoredCandidate(Candidate(neon, "session", 0.8, "why"),
                                      score=1.5))
        cfg = WaveConfig()
        res = optimize_sequence(ranked, 3, tail=[], cfg=cfg)
        artists = [c.track.artist for c in res.picks]
        assert len(artists) == len(set(artists)), "артист не звучит дважды в партии"

    def test_thin_pool_allows_repeat(self):
        """Пул из одного артиста: после исчерпания альтернатив повтор разрешён."""
        from services.wave.intel.ranker import ScoredCandidate
        from services.wave.intel.sources import Candidate
        from services.wave.intel.sequence import optimize_sequence
        from services.wave.intel.config import WaveConfig

        ranked = []
        for i in range(4):
            t = self._mk(i, "Steel Fury", f"жёсткий метал {i}")
            ranked.append(ScoredCandidate(Candidate(t, "artist", 1.0, "why"),
                                          score=2.0 - i * 0.1))
        cfg = WaveConfig()
        res = optimize_sequence(ranked, 3, tail=[], cfg=cfg)
        assert len(res.picks) == 3                 # партия всё равно собрана
        assert all(c.track.artist == "Steel Fury" for c in res.picks)

    def test_smooth_transition_prefers_matching_mood(self):
        from services.wave.intel.ranker import ScoredCandidate
        from services.wave.intel.sources import Candidate
        from services.wave.intel.sequence import optimize_sequence
        from services.wave.intel.config import WaveConfig

        calm = self._mk(1, "Lofi Dream", "спокойный lo-fi вечер")
        metal = self._mk(2, "Steel Fury", "жёсткий метал хаос")
        calm2 = self._mk(3, "Chill Age", "тихий ambient полёт")
        ranked = [
            ScoredCandidate(Candidate(metal, "content", 0.6, "w"), score=1.5),
            ScoredCandidate(Candidate(calm2, "content", 0.6, "w"), score=1.4),
        ]
        cfg = WaveConfig()
        res = optimize_sequence(ranked, 2, tail=[calm], cfg=cfg)
        # после спокойного хвоста металл уходит в конец партии
        assert res.picks[0].track.id == 3


# ------------------------------------------------------------------ адаптация


class TestAdaptive:
    """Этапы 11–12: веса подстраиваются только при достаточных данных."""

    @staticmethod
    def _unlock_ml(s, min_events=10, min_slice=4, step=0.02):
        """Опускаем пороги в WaveKV и сбрасываем кэш конфига."""
        s.add(WaveKV(key="ml", value={"min_events": min_events,
                                      "min_slice": min_slice, "step": step,
                                      "cooldown_hours": 24.0,
                                      "window_days": 30.0,
                                      "min_weight": 0.03}))
        from services.wave.intel.config import invalidate
        invalidate()

    async def _seed_events(self, s, user_id=111):
        ids = dict((await s.execute(
            select(Track.external_id, Track.id))).all())
        lofi = ids["c1"]

        def ev(kind, ext=None, scenario=None, activity=None, artist=None):
            s.add(WaveEvent(
                user_id=user_id, track_id=ids.get(ext) if ext else lofi,
                artist_key=artist,
                kind=kind, scenario=scenario,
                context={"activity": activity} if activity else {}))

        # обычные плеи: 4 старта, все скипнуты
        for _ in range(4):
            ev("track_started", "a1", artist="steel fury")
            ev("skipped", "a1")
        # контекстные плеи (scenario=focus): 4 старта, все дослушаны
        for _ in range(4):
            ev("track_started", "c1", scenario="focus", artist="lofi dream")
            ev("completed", "c1", scenario="focus")
        # открытия: 4 старта, все дослушаны
        for _ in range(4):
            ev("track_started", "u1", artist="unknown x")
            ev("completed", "u1")
        s.add(ArtistStats(user_id=user_id, artist_key="unknown x",
                          discovered_via="discovery"))
        await s.commit()

    async def test_adapt_noop_below_threshold(self, db):
        from services.wave.intel.adaptive import maybe_adapt
        async with db() as s:
            self._unlock_ml(s, min_events=100)     # события не дотягивают
            await self._seed_events(s)             # 24 события < 100
            res = await maybe_adapt(s, 111)
            assert res is None
            row = (await s.execute(select(WaveKV).where(
                WaveKV.key == "ml:u111"))).scalar_one()
            assert row.value["events"] == 24       # проверка запомнена
            assert not row.value["changes"]
            assert (await s.execute(select(WaveKV).where(
                WaveKV.key == "weights:u111"))).scalar_one_or_none() is None

    async def test_adapt_context_fit_falls_when_ctx_skips_more(self, db):
        from services.wave.intel.adaptive import maybe_adapt
        async with db() as s:
            self._unlock_ml(s)
            await self._seed_events(s)
            # подменяем картину: контекстные скипаются, обычные дослушиваются
            from core.models import WaveEvent as WE
            await s.execute(
                WE.__table__.update()
                .where(WE.kind == "completed", WE.scenario == "focus")
                .values(kind="skipped"))
            await s.commit()
            rep = await maybe_adapt(s, 111)
            assert rep and "context_fit" in rep["changes"]
            assert rep["changes"]["context_fit"] < 0
            w = (await s.execute(select(WaveKV).where(
                WaveKV.key == "weights:u111"))).scalar_one().value
            assert w["context_fit"] < 0.25
            assert abs(sum(w.values()) - 1.0) < 1e-6     # нормировка сохранена

    async def test_adapt_discovery_raises_on_completions(self, db):
        from services.wave.intel.adaptive import maybe_adapt
        async with db() as s:
            self._unlock_ml(s)
            await self._seed_events(s)
            rep = await maybe_adapt(s, 111)
            assert rep and "discovery" in rep["changes"]
            assert rep["changes"]["discovery"] > 0    # открытия дослушивают
            w = (await s.execute(select(WaveKV).where(
                WaveKV.key == "weights:u111"))).scalar_one().value
            assert w["discovery"] > 0.12

    async def test_adapt_cooldown_blocks_immediate_second_run(self, db):
        from services.wave.intel.adaptive import maybe_adapt
        async with db() as s:
            self._unlock_ml(s)
            await self._seed_events(s)
            first = await maybe_adapt(s, 111)
            assert first is not None
            assert await maybe_adapt(s, 111) is None   # кулдаун

    async def test_personal_weights_overlay_in_load_config(self, db):
        async with db() as s:
            s.add(WaveKV(key="weights:u111", value={"context_fit": 0.4}))
            await s.commit()
        from services.wave.intel.config import load_config
        personal = await load_config(s, 111)
        assert personal.weights["context_fit"] == 0.4
        global_cfg = await load_config(s)
        assert global_cfg.weights["context_fit"] == 0.25   # глобальные целы

    async def test_reset_clears_personal_ml(self, db):
        from services.wave.intel.adaptive import maybe_adapt, reset
        async with db() as s:
            self._unlock_ml(s)
            await self._seed_events(s)
            await maybe_adapt(s, 111)
            await reset(s, 111)
            keys = [k for (k,) in (await s.execute(select(WaveKV.key))).all()
                    if k.endswith(":u111")]
            assert keys == []


class TestPrivacyRoute:
    """Приватность §20: /clear чистит телеметрию/вкус, не трогая глобальный
    конфиг и чужих пользователей."""

    async def test_clear_all_wipes_user_data_keeps_global_config(
            self, db, monkeypatch):
        from bot import runtime as rt
        from server.routes import wave as wr
        monkeypatch.setattr(rt, "get_session", lambda: db())

        async with db() as s:
            s.add(WaveSettings(user_id=111))
            s.add(WaveEvent(user_id=111, kind="track_started",
                            context={}))
            s.add(WaveEvent(user_id=222, kind="track_started",
                            context={}))                  # чужие данные
            s.add(ArtistStats(user_id=111, artist_key="lofi dream"))
            s.add(TasteProfile(user_id=111, artist_affinity={"x": 1}))
            s.add(WaveKV(key="weights", value={"context_fit": 0.9}))
            s.add(WaveKV(key="weights:u111", value={"discovery": 0.2}))
            s.add(WaveKV(key="ml:u111", value={"at": "2026-01-01T00:00:00",
                                               "events": 5, "changes": {}}))
            await s.commit()

        class _U:
            id = 111

        resp = await wr.wave_clear(wr.ClearBody(scope="all"), _U())
        assert resp == {"ok": True, "scope": "all"}

        async with db() as s:
            ev111 = (await s.execute(select(WaveEvent)
                                     .where(WaveEvent.user_id == 111))).scalars().all()
            ev222 = (await s.execute(select(WaveEvent)
                                     .where(WaveEvent.user_id == 222))).scalars().all()
            assert ev111 == [] and len(ev222) == 1        # чужие не тронуты
            assert (await s.execute(select(ArtistStats))).scalars().all() == []
            assert (await s.execute(select(TasteProfile))).scalars().all() == []
            kv = {k: v for k, v in (await s.execute(
                select(WaveKV.key, WaveKV.value))).all()}
            assert "weights" in kv                        # глобальный конфиг цел
            assert "weights:u111" not in kv               # личный вес сброшен
            assert "ml:u111" not in kv
