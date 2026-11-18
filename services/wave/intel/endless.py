"""Endless Wave Engine (MODULE 4): сборка партии волны из 8 источников.

Полная цепочка (§24): контекст → профиль → кандидаты → ранжирование →
баланс знакомого/нового → оптимизация последовательности → честность названия
сценария → выдача.

Отказоустойчивость (§19): падение pipeline не роняет волну — service.py
откатывается на прежний own_engine (движок B-legacy), тот имеет собственную
защиту от «тонкого» каталога.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import (Playlist, PlaylistTrack, Track, WaveSession, utcnow)
from . import taste as taste_mod
from .config import WaveConfig, load_config
from .context import (Context, Scenario, DIVERSITY_EXPLORE, FAMILIARITY_EXPLORE,
                      pick_scenario, scenario_from_formula, scenario_list,
                      mood_of_texts)
from .events import artist_key, note_discovered
from . import feedback as feedback_mod
from .ranker import rank
from .sequence import optimize_sequence
from .sources import SourceContext, collect_candidates
from .taste import SessionTaste, TasteSnapshot, snapshot_from_profile

LOG = logging.getLogger("wave.intel.endless")

DEVICE_KEYS = {"phone", "desktop", "tablet", "tv"}
ACTIVITY_KEYS = {"work", "study", "rest", "walk", "road", "sport",
                 "reading", "sleep", "party", "evening"}
MOOD_KEYS = {"fun", "active", "calm", "sad", "edge"}
FAMILIARITY_KEYS = {"favorite", "familiar", "similar", "new", "unknown",
                    "experimental"}


@dataclass
class BatchResult:
    pairs: list[tuple[Track, str]] = field(default_factory=list)
    scenario: Scenario | None = None
    context: Context | None = None
    sources_used: dict[str, int] = field(default_factory=dict)
    note: str = "intel"


def build_context(ws, request_ctx: dict, snap: TasteSnapshot,
                  st: SessionTaste) -> Context:
    """Сигналы запроса + формула → нормализованный контекст (с валидацией)."""
    rq = request_ctx or {}
    enabled = bool(getattr(ws, "context_enabled", True))

    hour = rq.get("local_hour")
    weekday = rq.get("local_weekday")
    from .context import time_of_day as _tod, weekday_kind
    tod = _tod(int(hour)) if isinstance(hour, (int, float)) and 0 <= hour <= 23 else _tod(utcnow().hour)
    wd = weekday_kind(int(weekday)) if isinstance(weekday, int) and 1 <= weekday <= 7 \
        else weekday_kind(utcnow().weekday() + 1)

    activity = rq.get("activity") if enabled else None
    mood = rq.get("mood") if enabled else None
    familiarity = rq.get("familiarity") if enabled else None
    device = rq.get("device") if enabled else None
    if activity not in ACTIVITY_KEYS:
        activity = None
    if mood not in MOOD_KEYS:
        mood = None
    if familiarity not in FAMILIARITY_KEYS:
        familiarity = None
    if device not in DEVICE_KEYS:
        device = None

    ctx = Context(
        time_of_day=tod, weekday=wd, device=device,
        activity=activity, mood=mood, familiarity=familiarity,
        enabled=enabled, session=st, snapshot=snap,
        engine_mood=getattr(ws, "mood", None),
        recent_skips=rq.get("_recent_skips") or [],
        recent_tracks=rq.get("_recent_tracks") or [],
        recently_played_ids=rq.get("_recently_played_ids") or set(),
        recent_48h_ids=rq.get("_recent_48h_ids") or set(),
    )
    return ctx


async def build_context_full(s: AsyncSession, user_id: int, ws, sess: WaveSession,
                             request_ctx: dict | None = None) -> Context:
    """Контекст с полной телеметрией (запросы в БД) — для start/next/shake."""
    cfg = await load_config(s, user_id)
    profile = await taste_mod.get_profile(s, user_id)
    snap = snapshot_from_profile(profile)
    st = await taste_mod.session_taste(s, user_id, sess.id if sess is not None else None)
    rq = dict(request_ctx or {})
    rq["_recent_skips"] = await feedback_mod.recent_skip_classes(
        s, user_id, float(cfg.limits.get("skip_freeze_days", 3)))
    rq["_recent_tracks"] = await feedback_mod.recent_played_tracks(s, user_id, 12)
    rq["_recently_played_ids"] = await feedback_mod.recently_played_ids(
        s, user_id, float(cfg.limits.get("days_no_repeat", 7)))
    rq["_recent_48h_ids"] = await feedback_mod.recently_played_ids(s, user_id, 2.0)
    return build_context(ws, rq, snap, st)


def _scenario_for(ctx: Context, ws, requested: str | None) -> Scenario:
    if not ctx.enabled:
        # контекстная персонализация выключена: только формула или «Для тебя»
        return scenario_from_formula(ws) or Scenario(
            id="foryou", title="Для тебя", description="личная волна под твой вкус")
    if requested:
        for sc in scenario_list(ctx, ctx.snapshot):
            if sc.id == requested:
                return sc
    # сохранённая формула (WaveSettings) — постоянный явный выбор юзера
    form = scenario_from_formula(ws)
    if form is not None:
        return form
    # v3: формула пуста, но «Разнообразие» выставлено — политика юзера важнее
    # контекстной карточки (эксплор/квоты переопределяем, название карточки честно)
    base = pick_scenario(ctx, ctx.snapshot)
    div = (getattr(ws, "diversity", None) or "").strip()
    if div in DIVERSITY_EXPLORE and div != "default":
        base.diversity = div
        base.explore = DIVERSITY_EXPLORE[div]
    return base


async def build_batch(s: AsyncSession, user_id: int, ws, sess: WaveSession, *,
                      request_ctx: dict | None = None,
                      n: int | None = None) -> BatchResult:
    """Партия волны движком B-v2 (полный pipeline)."""
    cfg = await load_config(s, user_id)
    n = int(n or cfg.limits.get("batch", 3))

    ctx = await build_context_full(s, user_id, ws, sess, request_ctx)
    snap: TasteSnapshot = ctx.snapshot
    # свежие дисклайки/исключения могли не попасть в профиль (не пересчитан)
    try:
        d_ids, d_artists = await taste_mod.disliked(s, user_id)
        snap.disliked_track_ids |= d_ids
        snap.disliked_artist_keys |= d_artists
    except Exception:
        LOG.exception("дисклайки не подмешаны в снапшот")
    scen = _scenario_for(ctx, ws, (request_ctx or {}).get("scenario"))
    ctx.scenario = scen

    liked = await taste_mod.liked_tracks(s, user_id)
    snap.liked_track_ids |= liked          # квоты партии отличают лайки
    pool = await _load_pool(s, user_id, liked, ws=ws, scen=scen)
    played = {int(x) for x in (sess.played_track_ids or [])}

    sc = SourceContext(s=s, user_id=user_id, ws=ws, sess=sess, ctx=ctx,
                       snap=snap, st=ctx.session, liked=liked,
                       pool=pool, played=played)

    candidates = await collect_candidates(sc)
    sources_used: dict[str, int] = {}
    for c in candidates:
        sources_used[c.source] = sources_used.get(c.source, 0) + 1

    ranked = rank(sc, candidates, cfg)

    picks = _balance_explore(ranked, snap, scen, n, cfg, ws=ws)

    tail = list(ctx.recent_tracks or [])
    seq = optimize_sequence(picks, n, tail, cfg)

    # честность названия (§4.3): «спокойная» обязана иметь спокойные треки
    if scen.needs_mood_check:
        hits = mood_of_texts([c.track for c in seq.picks], scen.needs_mood_check)
        if hits * 2 < max(1, len(seq.picks)):
            scen = Scenario(id="foryou", title="Для тебя",
                            description="личная волна под твой вкус")
            ctx.scenario = scen

    # память discovery: «открыли артиста» (для метрик и карты вкуса)
    for c in seq.picks:
        if c.candidate.source == "discovery":
            await note_discovered(s, user_id, c.track, "discovery")

    return BatchResult(pairs=[(c.track, c.why) for c in seq.picks],
                       scenario=scen, context=ctx, sources_used=sources_used)


def _balance_explore(ranked, snap: TasteSnapshot, scen: Scenario,
                     n: int, cfg: WaveConfig, ws=None):
    """Баланс знакомого/нового (§5.4) + квоты партии (v3).

    Квоты решают жалобу «в основном любимое»: доля лайков в партии
    ограничена разнообразием (favorite → можно всё, default → 1 из 3,
    discover → 0), а режимы «new/unknown» требуют минимум незнакомых
    артистов. Открытия (discovery-источник) идут первыми, дальше лайки
    в пределах квоты, остальное — основным потоком. Язык формулы при
    явном выборе (ru / non-ru) не расходует квоту на противоречащие
    треки — иначе «Разнообразие» съедало языковую настройку."""
    if not ranked:
        return []
    target = 0.20
    if scen is not None and scen.explore is not None:
        target = float(scen.explore)
    elif snap.explore_share:
        target = float(snap.explore_share)
    target = min(float(cfg.explore.get("max", 0.35)),
                 max(float(cfg.explore.get("min", 0.10)), target))
    n_disc = round(n * target)

    liked_set = set(snap.liked_track_ids or set())
    disc = [c for c in ranked if c.candidate.source == "discovery"]
    main = [c for c in ranked if c.candidate.source != "discovery"]

    # язык формулы: противоречащие треки откладываем до добора
    lang = (getattr(ws, "language", "any") or "any") if ws is not None else "any"
    if lang in ("ru", "non-ru"):
        from .taste import is_ru
        want_ru = lang == "ru"

        def _lang_bad(c) -> bool:
            return is_ru(c.track) != want_ru
        lang_defer = [c for c in main if _lang_bad(c)]
        main = [c for c in main if not _lang_bad(c)]
    else:
        lang_defer = []

    liked_main = [c for c in main if c.track.id in liked_set]
    rest_main = [c for c in main if c.track.id not in liked_set]

    fam = (getattr(scen, "familiarity", None) or "") if scen is not None else ""
    liked_cap = {"favorite": n, "familiar": 2}.get(fam, 1)
    if fam in ("new", "unknown", "experimental"):
        liked_cap = 0
    if scen is not None and getattr(scen, "diversity", None):
        liked_cap = {"favorite": n, "popular": 1, "discover": 0}.get(
            scen.diversity, liked_cap)

    picks: list = []
    if disc and n_disc > 0:
        picks.extend(disc[:n_disc])
        drop = {p.track.id for p in picks}
        liked_main = [c for c in liked_main if c.track.id not in drop]
        rest_main = [c for c in rest_main if c.track.id not in drop]

    # незнакомые артисты добирают «новизну», если discovery-источник был худ
    new_min = 2 if (fam in ("new", "unknown", "experimental")
                    or (scen is not None and scen.diversity == "discover")) else 0
    if new_min and len(picks) < new_min:
        fresh = [c for c in rest_main
                 if (artist_key(c.track.artist or "")
                     not in (snap.artist_scores or {}))]
        picks.extend(fresh[: new_min - len(picks)])
        drop = {p.track.id for p in picks}
        rest_main = [c for c in rest_main if c.track.id not in drop]

    picks.extend(liked_main[: max(0, min(liked_cap, n - len(picks)))])
    picks.extend(rest_main[: n - len(picks)])

    # если открытий/квот не хватило — добираем основным потоком, затем
    # отложенными по языку (пустая партия хуже несовпадения языка)
    if len(picks) < n:
        rest = [c for c in ranked if c.track.id not in {p.track.id for p in picks}]
        picks.extend(rest[: n - len(picks)])
    if len(picks) < n and lang_defer:
        drop = {p.track.id for p in picks}
        picks.extend([c for c in lang_defer if c.track.id not in drop]
                     [: n - len(picks)])
    return picks[:n]


def _formula_words(scen: Scenario | None) -> list[str]:
    """Слова жанров/настроений/занятий формулы — для вытягивания тематических
    треков в пул (иначе формула действует только на треки, уже попавшие
    в топ-420). Слова ≥3 символов, не более 24 условий (размер SQL)."""
    if scen is None:
        return []
    from services.wave.own_engine import ACTIVITY_WORDS, GENRE_WORDS, MOOD_WORDS
    out: list[str] = []
    for g in (scen.genres or frozenset()):
        out.extend(GENRE_WORDS.get(str(g), (str(g),)))
    for m in (scen.moods or frozenset()):
        out.extend(MOOD_WORDS.get(str(m), (str(m),)))
    for a in (scen.activities or frozenset()):
        out.extend(ACTIVITY_WORDS.get(str(a), (str(a),)))
    seen: set[str] = set()
    uniq: list[str] = []
    for w in out:
        w = (w or "").strip().lower()
        if len(w) >= 3 and w not in seen:
            seen.add(w)
            uniq.append(w)
    return uniq[:24]


async def _load_pool(s: AsyncSession, user_id: int, liked: set[int],
                     limit: int = 420, ws=None, scen=None) -> dict[int, Track]:
    """Общий пул кандидатов: популярное каталога + плейлисты юзера + лайки.
    v3: жанры/настроения/занятия формулы дотащат тематические треки
    вне топа — смена настроек реально меняет материал волны."""
    out: dict[int, Track] = {}
    q = select(Track).order_by(Track.downloads.desc(), Track.id.desc()).limit(limit)
    for t in (await s.execute(q)).scalars():
        out[t.id] = t
    q = (select(Track)
         .join(PlaylistTrack, PlaylistTrack.track_id == Track.id)
         .join(Playlist, Playlist.id == PlaylistTrack.playlist_id)
         .where(Playlist.owner_id == user_id).limit(limit))
    for t in (await s.execute(q)).scalars():
        out.setdefault(t.id, t)
    if liked:
        q = select(Track).where(Track.id.in_(list(liked)[:400]))
        for t in (await s.execute(q)).scalars():
            out.setdefault(t.id, t)
    words = _formula_words(scen)
    if words:
        from sqlalchemy import or_
        conds = [Track.title.ilike(f"%{w}%") | Track.artist.ilike(f"%{w}%")
                 | Track.album.ilike(f"%{w}%") for w in words]
        q = (select(Track).where(or_(*conds))
             .order_by(Track.downloads.desc(), Track.id.desc()).limit(250))
        for t in (await s.execute(q)).scalars():
            out.setdefault(t.id, t)
    return out
