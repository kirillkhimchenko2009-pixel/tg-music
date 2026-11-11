"""Event Model (§7.1): запись гранулярных событий волны и артист-статистика.

События плеера стекаются в wave_events (телеметрия для метрик и обучения) и
обновляют artist_stats (интерес к артисту, память discovery). PlayEvent в
service.py остаётся ядром вкуса — тут телеметрия, там движок.

Здесь же — классификация скипа по моменту (§7.2): один скип ≠ дизлайк.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from core.models import ArtistStats, Track, WaveEvent, utcnow

LOG = logging.getLogger("wave.intel.events")

# типы событий (проверяются на входе ручки /api/wave/event)
EVENT_KINDS = {
    "impression", "track_started", "played_10s", "played_30s", "played_50p",
    "played_90p", "completed", "skipped", "replayed", "seeked", "liked",
    "unliked", "disliked", "added_to_library", "removed_from_library",
    "artist_opened", "search_selected", "context_selected",
    "wave_refreshed", "wave_shaken",
}

DISLIKE_REASONS = {
    "composition", "artist", "genre", "repeat", "mood", "too_energy", "too_calm",
}

SKIP_CLASSES = {
    "first_seconds",   # < soft_skip_seconds — резкое «нет»
    "few_seconds",     # < hard_skip_seconds — не зацепило
    "mid",             # середина — сменил настроение
    "near_end",        # почти дослушал — «было норм, но не тянуло»
    "after_similar",   # серия похожих — усталость от направления
}

# события, считающиеся прослушиванием (для artist_stats.plays)
_PLAY_KINDS = {"track_started", "completed"}


def artist_key(artist: str) -> str:
    """Нормализация артиста (единая с own_engine)."""
    head = (artist or "").split(",")[0].strip().lower()
    for junk in ("feat.", "ft.", "×", " x "):
        if junk in head:
            head = head.split(junk)[0].strip()
    return head


def classify_skip(played: float, duration: float,
                  after_similar: bool = False) -> str:
    """Момент скипа → класс. Лимиты придёт контекст (seconds), тут шкала долей."""
    if after_similar:
        return "after_similar"
    played = max(0.0, float(played or 0))
    if duration and duration > 0 and played / duration >= 0.85:
        return "near_end"
    if played < 10.0:
        return "first_seconds"
    if played < 25.0:
        return "few_seconds"
    return "mid"


@dataclass
class EventContext:
    time_of_day: str | None = None      # early_morning | morning | day | evening | late_night
    weekday: str | None = None          # workday | weekend
    device: str | None = None           # phone | desktop | tablet | tv
    activity: str | None = None         # явное занятие
    mood: str | None = None             # явное настроение

    def to_json(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v}


async def log_event(s: AsyncSession, user_id: int, kind: str, *,
                    track_id: int | None = None,
                    artist: str | None = None,
                    session_id: int | None = None,
                    reason: str | None = None,
                    context: EventContext | dict | None = None,
                    scenario: str | None = None,
                    played_seconds: float = 0.0,
                    track_duration: float = 0.0) -> WaveEvent | None:
    """Пишет событие + обновляет ArtistStats. Не валидацию трека — на ручке."""
    kind = (kind or "").strip()
    if kind not in EVENT_KINDS:
        LOG.warning("неизвестный event kind: %r", kind)
        return None
    ctx_json = context.to_json() if isinstance(context, EventContext) \
        else dict(context or {})
    ev = WaveEvent(
        user_id=int(user_id), track_id=track_id,
        artist_key=artist_key(artist) if artist else None,
        session_id=session_id, kind=kind,
        reason=reason or None, context=ctx_json,
        scenario=(scenario or None), played_seconds=float(played_seconds or 0),
        track_duration=float(track_duration or 0),
    )
    s.add(ev)
    if artist and kind in _PLAY_KINDS | {"skipped", "completed", "liked",
                                         "unliked", "disliked"}:
        await _bump_artist(s, user_id, artist, kind)
    return ev


async def _bump_artist(s: AsyncSession, user_id: int, artist: str, kind: str) -> None:
    ak = artist_key(artist)
    if not ak:
        return
    st = (await s.execute(select(ArtistStats).where(
        ArtistStats.user_id == user_id, ArtistStats.artist_key == ak)
    )).scalar_one_or_none()
    if st is None:
        st = ArtistStats(user_id=user_id, artist_key=ak,
                         display_name=(artist or "").split(",")[0].strip(),
                         first_seen_at=utcnow(),
                         plays=0, likes=0, dislikes=0, skips=0, completions=0)
        s.add(st)
    if kind in ("track_started", "completed"):
        st.plays = (st.plays or 0) + 1
        st.last_played_at = utcnow()
    if kind == "completed":
        st.completions = (st.completions or 0) + 1
    if kind == "skipped":
        st.skips = (st.skips or 0) + 1
    if kind == "liked":
        st.likes = (st.likes or 0) + 1
    if kind == "disliked":
        st.dislikes = (st.dislikes or 0) + 1
    if kind == "unliked" and (st.likes or 0) > 0:
        st.likes -= 1


async def note_discovered(s: AsyncSession, user_id: int, track: Track,
                          via: str) -> None:
    """Пометить артиста «открыт через волну» (для метрики discovery и карты)."""
    ak = artist_key(track.artist or "")
    if not ak:
        return
    st = (await s.execute(select(ArtistStats).where(
        ArtistStats.user_id == user_id, ArtistStats.artist_key == ak)
    )).scalar_one_or_none()
    if st is None:
        s.add(ArtistStats(
            user_id=user_id, artist_key=ak,
            display_name=(track.artist or "").split(",")[0].strip(),
            first_seen_at=utcnow(), discovered_via=via,
        ))
    elif not st.first_seen_at:
        st.first_seen_at = utcnow()
        st.discovered_via = st.discovered_via or via
