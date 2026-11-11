"""User Taste Engine (MODULE 1): долгосрочный профиль вкуса с затуханием.

Три уровня интересов (§3.2):
  LEVEL A — сильные предпочтения (регулярно слушает, лайки, коллекция);
  LEVEL B — ситуационные (привязка занятия/контекста к жанру/настроению);
  LEVEL C — экспериментальные (открыли недавно, реакции ещё мало).

Разделение профилей (§13):
  LONG-TERM  — таблица taste_profiles, затухание по полураспаду;
  SESSION    — session_taste(): окно активной сессии (30 мин), не хранится;
  CONTEXT    — context_affinity: занятие → жанры/настроения (устойчивое).

Аудио-параметры не выдумываются: жанр/настроение — эвристики по словам
каталога (единый словарь с own_engine), язык — кириллица.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import (ArtistStats, PlayEvent, Playlist, PlaylistTrack,
                         TasteProfile, Track, TrackFeedback, WaveEvent, utcnow)
from .config import load_config
from .events import artist_key

LOG = logging.getLogger("wave.intel.taste")

# полураспад интереса: через N дней вклад события вдвое слабее
HALF_LIFE_DAYS = 45.0
HISTORY_LIMIT = 500          # глубина истории для пересчёта
STRONG_SCORE = 5.0           # порог LEVEL A
SITUATIONAL_SCORE = 2.0      # порог LEVEL B в context_affinity

# вклад сигналов (до затухания)
W_LIKE = 4.0
W_LIBRARY = 5.0
W_COMPLETED = 2.5
W_PLAYED60 = 1.5
W_SKIP_SOFT = -0.4
W_SKIP_HARD = -1.4
W_DISLIKE = -6.0


def _decay(age_days: float) -> float:
    if age_days < 0:
        age_days = 0.0
    return 0.5 ** (age_days / HALF_LIFE_DAYS)


def detect_genres(t: Track) -> set[str]:
    """Жанры трека по словам (эвристика, единый словарь с движком B)."""
    from services.wave.own_engine import GENRE_WORDS
    text = " ".join((t.title or "", t.artist or "", t.album or "")).lower()
    return {g for g, words in GENRE_WORDS.items() if any(w in text for w in words)}


def detect_moods(t: Track) -> set[str]:
    from services.wave.own_engine import MOOD_WORDS
    text = " ".join((t.title or "", t.artist or "", t.album or "")).lower()
    return {m for m, words in MOOD_WORDS.items() if any(w in text for w in words)}


def is_ru(t: Track) -> bool:
    text = " ".join((t.artist or "", t.title or ""))
    return any(0x0400 <= ord(c) <= 0x04FF for c in text)


@dataclass
class TasteLevels:
    """Срез профиля для ranker/карты вкуса."""
    strong_artists: list[str] = field(default_factory=list)      # LEVEL A
    situational: dict[str, list[str]] = field(default_factory=dict)  # LEVEL B: activity → genres
    experimental_artists: list[str] = field(default_factory=list)  # LEVEL C


@dataclass
class SessionTaste:
    """SESSION TASTE: что звучит и как реагирует пользователь прямо сейчас."""
    artists: dict[str, float] = field(default_factory=dict)   # artist_key → вес
    genres: dict[str, float] = field(default_factory=dict)
    moods: dict[str, float] = field(default_factory=dict)
    skip_streak_same: int = 0        # серия скипов похожих (усталость)
    last_skip_genre: str | None = None
    reactions: dict[str, int] = field(default_factory=dict)   # like/dislike/skip счётчики


@dataclass
class TasteSnapshot:
    """Единый интерфейс вкуса для ranker и context (0..1 нормализация)."""
    artist_scores: dict[str, float] = field(default_factory=dict)
    genre_scores: dict[str, float] = field(default_factory=dict)
    mood_scores: dict[str, float] = field(default_factory=dict)
    lang_scores: dict[str, float] = field(default_factory=dict)
    liked_track_ids: set[int] = field(default_factory=set)
    disliked_track_ids: set[int] = field(default_factory=set)
    disliked_artist_keys: set[str] = field(default_factory=set)
    excluded_artists: set[str] = field(default_factory=set)
    excluded_genres: set[str] = field(default_factory=set)
    explore_share: float = 0.20
    levels: TasteLevels = field(default_factory=TasteLevels)
    max_artist: float = 0.0
    max_genre: float = 0.0
    max_mood: float = 0.0

    def artist_fit(self, ak: str | None) -> float:
        if not ak or self.max_artist <= 0:
            return 0.0
        return min(1.0, self.artist_scores.get(ak, 0.0) / self.max_artist)

    def genre_fit(self, genres: set[str]) -> float:
        if not genres or self.max_genre <= 0:
            return 0.0
        best = max((self.genre_scores.get(g, 0.0) for g in genres), default=0.0)
        return min(1.0, best / self.max_genre)

    def mood_fit(self, moods: set[str]) -> float:
        if not moods or self.max_mood <= 0:
            return 0.0
        best = max((self.mood_scores.get(m, 0.0) for m in moods), default=0.0)
        return min(1.0, best / self.max_mood)

    def lang_fit(self, ru: bool) -> float:
        key = "ru" if ru else "non_ru"
        return min(1.0, self.lang_scores.get(key, 0.0))


# ------------------------------ пересчёт профиля ------------------------------

async def refresh_profile(s: AsyncSession, user_id: int) -> TasteProfile:
    """Полный пересчёт долгосрочного профиля из истории (фоново/после событий)."""
    now = utcnow()
    artist_raw: dict[str, float] = {}
    artist_meta: dict[str, dict] = {}
    genre_raw: dict[str, float] = {}
    mood_raw: dict[str, float] = {}
    lang_raw: dict[str, float] = {"ru": 0.0, "non_ru": 0.0}
    liked_ids: set[int] = set()
    disliked_ids: set[int] = set()

    def _add_artist(ak: str, w: float, name: str = ""):
        if not ak:
            return
        artist_raw[ak] = artist_raw.get(ak, 0.0) + w
        meta = artist_meta.setdefault(ak, {"plays": 0, "likes": 0, "last_at": None})
        if w > 0:
            meta["plays"] += 1
        if name:
            meta.setdefault("name", name.split(",")[0].strip())

    def _add_track_words(t: Track, w: float):
        for g in detect_genres(t):
            genre_raw[g] = genre_raw.get(g, 0.0) + max(0.0, w) * 0.8
        for m in detect_moods(t):
            mood_raw[m] = mood_raw.get(m, 0.0) + max(0.0, w) * 0.8
        lang_raw["ru" if is_ru(t) else "non_ru"] += max(0.0, w) * 0.6

    # история прослушиваний
    q = (select(PlayEvent, Track)
         .join(Track, Track.id == PlayEvent.track_id)
         .where(PlayEvent.user_id == user_id)
         .order_by(PlayEvent.started_at.desc())
         .limit(HISTORY_LIMIT))
    for ev, t in (await s.execute(q)).all():
        ak = artist_key(t.artist or "")
        age = (now - (ev.started_at or now)).total_seconds() / 86400.0
        d = _decay(age)
        w = 0.0
        if ev.finished or float(ev.played_seconds or 0) >= 60.0:
            w = W_COMPLETED if ev.finished else W_PLAYED60
        elif ev.skipped:
            w = W_SKIP_HARD if float(ev.played_seconds or 0) < 10 else W_SKIP_SOFT
        if w:
            _add_artist(ak, w * d, t.artist)
            if w > 0:
                _add_track_words(t, w * d)

    # лайки = плейлист «❤%» (сильный сигнал + повторные прослушивания усиливают)
    q = (select(PlaylistTrack.track_id)
         .join(Playlist, Playlist.id == PlaylistTrack.playlist_id)
         .where(Playlist.owner_id == user_id, Playlist.title.like("❤%")))
    liked_ids = {int(x) for x in (await s.execute(q)).scalars()}
    if liked_ids:
        q = select(Track).where(Track.id.in_(list(liked_ids)[:400]))
        for t in (await s.execute(q)).scalars():
            ak = artist_key(t.artist or "")
            _add_artist(ak, W_LIKE, t.artist)
            _add_track_words(t, W_LIKE)
            meta = artist_meta.get(ak)
            if meta is not None:
                meta["likes"] += 1

    # добавления в коллекцию через волну (WaveEvent) — очень сильный сигнал
    q = (select(Track)
         .join(WaveEvent, WaveEvent.track_id == Track.id)
         .where(WaveEvent.user_id == user_id,
                WaveEvent.kind.in_(("added_to_library", "liked")))
         .order_by(WaveEvent.created_at.desc())
         .limit(200))
    for t in (await s.execute(q)).scalars():
        ak = artist_key(t.artist or "")
        if t.id not in liked_ids:      # без двойного счёта с плейлистом
            _add_artist(ak, W_LIBRARY * 0.8, t.artist)
            _add_track_words(t, W_LIBRARY * 0.8)

    # дисклайки: минусуем и трек, и артист
    q = (select(TrackFeedback, Track)
         .join(Track, Track.id == TrackFeedback.track_id)
         .where(TrackFeedback.user_id == user_id, TrackFeedback.kind == -1))
    for fb, t in (await s.execute(q)).all():
        disliked_ids.add(int(t.id))
        ak = artist_key(t.artist or "")
        _add_artist(ak, W_DISLIKE, t.artist)
        for g in detect_genres(t):
            genre_raw[g] = genre_raw.get(g, 0.0) + W_DISLIKE * 0.3

    profile = (await s.execute(select(TasteProfile).where(
        TasteProfile.user_id == user_id))).scalar_one_or_none()
    if profile is None:
        profile = TasteProfile(user_id=user_id)
        s.add(profile)

    profile.artist_affinity = {
        ak: {"score": round(v, 3), "plays": artist_meta.get(ak, {}).get("plays", 0),
             "likes": artist_meta.get(ak, {}).get("likes", 0),
             "name": artist_meta.get(ak, {}).get("name", "")}
        for ak, v in sorted(artist_raw.items(), key=lambda kv: -kv[1])
        if v > 0.05
    }
    profile.genre_affinity = {g: round(v, 3) for g, v in genre_raw.items() if v > 0.05}
    profile.mood_affinity = {m: round(v, 3) for m, v in mood_raw.items() if v > 0.05}
    profile.language_affinity = {k: round(v, 3) for k, v in lang_raw.items()}

    # устойчивые контекстные привычки (LEVEL B): занятие → жанры/настроения
    profile.context_affinity = await _context_affinity(s, user_id, genre_raw, mood_raw, now)

    # адаптивная доля открытий: реакция на discovery-треки (§5.4)
    profile.explore_share = await _adaptive_explore(s, user_id, cfg_limits=None)
    profile.refreshed_at = now
    return profile


async def _context_affinity(s: AsyncSession, user_id: int,
                            genre_raw: dict, mood_raw: dict, now) -> dict:
    """LEVEL B: в каком занятии какие жанры/настроения живут (затухание)."""
    q = (select(WaveEvent.kind, WaveEvent.context, Track)
         .join(Track, Track.id == WaveEvent.track_id)
         .where(WaveEvent.user_id == user_id,
                WaveEvent.kind.in_(("completed", "liked", "added_to_library",
                                    "track_started", "skipped", "disliked")))
         .order_by(WaveEvent.created_at.desc())
         .limit(300))
    buckets: dict[str, dict[str, dict[str, float]]] = {}
    positive = {"completed", "liked", "added_to_library"}
    for kind, ctx, t in (await s.execute(q)).all():
        activity = (ctx or {}).get("activity")
        if not activity:
            continue
        ak_b = buckets.setdefault(str(activity), {"genres": {}, "moods": {}})
        w = 1.0 if kind in positive else -0.5
        for g in detect_genres(t):
            b = ak_b["genres"]
            b[g] = round(b.get(g, 0.0) + w, 2)
        for m in detect_moods(t):
            b = ak_b["moods"]
            b[m] = round(b.get(m, 0.0) + w, 2)
    # нормируем и оставляем значимое
    out = {}
    for activity, b in buckets.items():
        g = {k: round(v, 2) for k, v in sorted(b["genres"].items(), key=lambda kv: -kv[1])[:6]
             if v >= SITUATIONAL_SCORE * 0.5}
        m = {k: round(v, 2) for k, v in sorted(b["moods"].items(), key=lambda kv: -kv[1])[:4]
             if v >= SITUATIONAL_SCORE * 0.5}
        if g or m:
            out[activity] = {"genres": g, "moods": m}
    return out


async def _adaptive_explore(s: AsyncSession, user_id: int, cfg_limits) -> float:
    """Доля открытий по реакции (лайк/дослушивание discovery → больше, скип → меньше)."""
    cfg = await load_config(s)
    base = float(cfg.explore.get("default", 0.20))
    lo = float(cfg.explore.get("min", 0.10))
    hi = float(cfg.explore.get("max", 0.35))
    q = (select(ArtistStats)
         .where(ArtistStats.user_id == user_id,
                ArtistStats.discovered_via.isnot(None))
         .order_by(ArtistStats.id.desc()).limit(40))
    rows = (await s.execute(q)).scalars().all()
    if len(rows) < 4:
        return base
    pos = sum(1 for r in rows if r.likes > 0 or r.completions > 0)
    neg = sum(1 for r in rows if r.skips >= 2 or r.dislikes > 0)
    rate = (pos - neg) / len(rows)
    return min(hi, max(lo, base + 0.08 * rate))


# ------------------------------ доступ к профилю ------------------------------

async def get_profile(s: AsyncSession, user_id: int, *,
                      refresh: bool = False) -> TasteProfile:
    """Профиль из БД; при устаревании (или впервые) — фоновый пересчёт."""
    cfg = await load_config(s)
    stale_minutes = float(cfg.limits.get("profile_refresh_minutes", 360))
    profile = (await s.execute(select(TasteProfile).where(
        TasteProfile.user_id == user_id))).scalar_one_or_none()
    age_ok = profile and profile.refreshed_at and \
        (utcnow() - profile.refreshed_at) < timedelta(minutes=stale_minutes)
    if profile is None or refresh or not age_ok:
        try:
            profile = await refresh_profile(s, user_id)
            await s.flush()
        except Exception:
            LOG.exception("пересчёт профиля вкуса не удался")
            if profile is None:
                profile = TasteProfile(user_id=user_id)
                s.add(profile)
                await s.flush()
    return profile


def snapshot_from_profile(profile: TasteProfile) -> TasteSnapshot:
    """Нормализованный срез для ranker (долгосрочный профиль)."""
    snap = TasteSnapshot()
    snap.explore_share = float(profile.explore_share or 0.20)
    snap.excluded_artists = {str(x) for x in (profile.excluded_artists or [])}
    snap.excluded_genres = {str(x) for x in (profile.excluded_genres or [])}

    artists = profile.artist_affinity or {}
    snap.max_artist = max((float(v.get("score", 0.0)) for v in artists.values()),
                          default=0.0)
    snap.artist_scores = {ak: float(v.get("score", 0.0))
                          for ak, v in artists.items() if float(v.get("score", 0.0)) > 0}
    snap.max_genre = max((float(v) for v in (profile.genre_affinity or {}).values()),
                         default=0.0)
    snap.genre_scores = dict(profile.genre_affinity or {})
    snap.max_mood = max((float(v) for v in (profile.mood_affinity or {}).values()),
                        default=0.0)
    snap.mood_scores = dict(profile.mood_affinity or {})
    snap.lang_scores = dict(profile.language_affinity or {})
    snap.levels = levels_of(profile)
    return snap


def levels_of(profile: TasteProfile) -> TasteLevels:
    """Срез уровней A/B/C для карты вкуса и объяснений."""
    lv = TasteLevels()
    for ak, v in (profile.artist_affinity or {}).items():
        score = float(v.get("score", 0.0))
        if score >= STRONG_SCORE:
            lv.strong_artists.append(ak)
        elif 0 < score < STRONG_SCORE:
            lv.experimental_artists.append(ak)
    for activity, b in (profile.context_affinity or {}).items():
        genres = [g for g, sc in (b.get("genres") or {}).items()
                  if sc >= SITUATIONAL_SCORE]
        if genres:
            lv.situational[activity] = genres
    return lv


async def liked_tracks(s: AsyncSession, user_id: int) -> set[int]:
    q = (select(PlaylistTrack.track_id)
         .join(Playlist, Playlist.id == PlaylistTrack.playlist_id)
         .where(Playlist.owner_id == user_id, Playlist.title.like("❤%")))
    return {int(x) for x in (await s.execute(q)).scalars()}


async def disliked(s: AsyncSession, user_id: int) -> tuple[set[int], set[str]]:
    """Дисклайкнутые треки (всегда) и артисты (мораторий в hydrate service.py)."""
    q = (select(Track.id, Track.artist)
         .join(TrackFeedback, TrackFeedback.track_id == Track.id)
         .where(TrackFeedback.user_id == user_id, TrackFeedback.kind == -1))
    ids: set[int] = set()
    artists: set[str] = set()
    for tid, artist in (await s.execute(q)).all():
        ids.add(int(tid))
        ak = artist_key(artist or "")
        if ak:
            artists.add(ak)
    return ids, artists


async def session_taste(s: AsyncSession, user_id: int, session_id: int | None,
                        *, window_minutes: float | None = None) -> SessionTaste:
    """SESSION TASTE: события текущей сессии (или последние 30 мин без неё)."""
    cfg = await load_config(s)
    minutes = float(window_minutes or cfg.limits.get("session_minutes", 30))
    since = utcnow() - timedelta(minutes=minutes)
    st = SessionTaste()

    if session_id is not None:
        q = (select(WaveEvent, Track)
             .join(Track, Track.id == WaveEvent.track_id)
             .where(WaveEvent.user_id == user_id,
                    WaveEvent.session_id == session_id,
                    WaveEvent.created_at >= since)
             .order_by(WaveEvent.created_at.desc()).limit(60))
    else:
        q = (select(WaveEvent, Track)
             .join(Track, Track.id == WaveEvent.track_id)
             .where(WaveEvent.user_id == user_id,
                    WaveEvent.created_at >= since)
             .order_by(WaveEvent.created_at.desc()).limit(60))

    streak_same = 0
    last_g = None
    for ev, t in (await s.execute(q)).all():
        ak = artist_key(t.artist or "")
        kind = ev.kind
        st.reactions[kind] = st.reactions.get(kind, 0) + 1
        weight = {"liked": 2.0, "completed": 1.6, "track_started": 0.6,
                  "skipped": -0.8, "disliked": -2.0}.get(kind)
        if weight:
            if weight > 0 and ak:
                st.artists[ak] = st.artists.get(ak, 0.0) + weight
            for g in detect_genres(t):
                st.genres[g] = st.genres.get(g, 0.0) + weight * 0.8
            for m in detect_moods(t):
                st.moods[m] = st.moods.get(m, 0.0) + weight * 0.8
        if kind == "skipped":
            gs = detect_genres(t) or set()
            g = next(iter(gs), None)
            streak_same = streak_same + 1 if (g and g == last_g) else 1
            last_g = g
        else:
            streak_same = 0
    st.skip_streak_same = streak_same
    st.last_skip_genre = last_g
    return st
