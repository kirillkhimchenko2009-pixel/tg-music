"""User Feedback Engine (MODULE 5): обучение на действиях пользователя.

Прямые сигналы (лайк/дизлайк с причиной, добавление в коллекцию) и косвенное
поведение (гранулярные события плеера §7.1). Ключевые правила:

  • один скип ≠ дизлайк: класс скипа по моменту задаёт силу сигнала;
  • дизлайк с причиной: композиция/артист/жанр/повтор/настроение/энергичность —
    разные последствия (трек/артист/жанр → разные горизонты);
  • сильные сигналы мгновенно делают профиль «протухшим» — он пересчитается
    при следующей партии (touch_profile_stale);
  • разовые реакции не ломают профиль (пересчёт с затуханием в taste.py).
"""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import (ArtistStats, PlayEvent, TasteProfile, Track,
                         TrackFeedback, WaveEvent, WaveSession, utcnow)
from .config import load_config
from .events import (EventContext, artist_key, classify_skip, log_event)

LOG = logging.getLogger("wave.intel.feedback")


async def touch_profile_stale(s: AsyncSession, user_id: int) -> None:
    """Сильный сигнал → профиль пересчитать при следующем запуске волны."""
    row = (await s.execute(select(TasteProfile).where(
        TasteProfile.user_id == user_id))).scalar_one_or_none()
    if row is not None:
        row.refreshed_at = utcnow() - timedelta(days=365)


async def recent_skip_classes(s: AsyncSession, user_id: int,
                              days: float = 3.0) -> list[dict]:
    """Скипы последнего окна: [{track_id, class}] (свежие первыми)."""
    since = utcnow() - timedelta(days=days)
    q = (select(WaveEvent.track_id, WaveEvent.reason)
         .where(WaveEvent.user_id == user_id, WaveEvent.kind == "skipped",
                WaveEvent.track_id.isnot(None), WaveEvent.created_at >= since)
         .order_by(WaveEvent.created_at.desc()).limit(80))
    out = []
    for tid, reason in (await s.execute(q)).all():
        out.append({"track_id": int(tid),
                    "class": reason if reason in ("first_seconds", "few_seconds",
                                                  "mid", "near_end", "after_similar")
                    else "few_seconds"})
    return out


async def recent_played_tracks(s: AsyncSession, user_id: int,
                               limit: int = 12) -> list[Track]:
    """Хвост потока для artist_fatigue и sequence-оптимизатора."""
    q = (select(Track)
         .join(PlayEvent, PlayEvent.track_id == Track.id)
         .where(PlayEvent.user_id == user_id)
         .order_by(PlayEvent.started_at.desc()).limit(limit))
    return list((await s.execute(q)).scalars())


async def recently_played_ids(s: AsyncSession, user_id: int,
                              days: float = 7.0) -> set[int]:
    since = utcnow() - timedelta(days=days)
    q = (select(PlayEvent.track_id)
         .where(PlayEvent.user_id == user_id, PlayEvent.track_id.isnot(None),
                PlayEvent.started_at >= since))
    return {int(x) for x in (await s.execute(q)).scalars()}


async def dislike_reason_to_policy(s: AsyncSession, user_id: int, track: Track,
                                   reason: str | None) -> dict:
    """Причина дизлайка → политика (§7.3): отказ от трека ≠ отказ от направления.

    Возвращает человекочитаемое описание того, что изменилось.
    """
    policy = {"scope": "track", "note": "трек больше не появится в волне"}
    ak = artist_key(track.artist or "")

    from .taste import get_profile
    async def _profile():
        return await get_profile(s, user_id)

    if reason == "artist" and ak:
        profile = await _profile()
        excluded = list(profile.excluded_artists or [])
        if ak not in excluded:
            excluded.append(ak)
            profile.excluded_artists = excluded[:50]
        st = (await s.execute(select(ArtistStats).where(
            ArtistStats.user_id == user_id, ArtistStats.artist_key == ak)
        )).scalar_one_or_none()
        if st is not None:
            st.is_excluded = True
        policy.update(scope="artist", note="артист скрыт из волны (вернуть можно в профиле вкуса)")
    elif reason == "genre":
        genres = detect_track_genres(track)
        if genres:
            profile = await _profile()
            excluded = list(profile.excluded_genres or [])
            for g in list(genres)[:2]:
                if g not in excluded:
                    excluded.append(g)
            profile.excluded_genres = excluded[:20]
            policy.update(scope="genre", note="жанр «" + "», «".join(sorted(genres)) + "» скрыт из волны")
    elif reason == "repeat":
        policy.update(scope="track", note="трек отдохнёт подольше — учтён повтор")
    elif reason in ("mood", "too_energy", "too_calm"):
        policy.update(scope="session", note="учтём в настроении сессии, вкус не трогаем")
    return policy


def detect_track_genres(track: Track) -> set[str]:
    from .taste import detect_genres
    return detect_genres(track)


async def process_event(s: AsyncSession, user_id: int, *, kind: str,
                        track: Track | None, session_id: int | None,
                        reason: str | None = None,
                        context: EventContext | None = None,
                        scenario: str | None = None,
                        played_seconds: float = 0.0,
                        duration: float = 0.0,
                        after_similar: bool = False) -> dict:
    """Главная точка входа /api/wave/event: сохранить + интерпретировать."""
    track_id = track.id if track is not None else None
    artist = (track.artist if track is not None else None)

    # классификация скипа по моменту (один скип ≠ дизлайк)
    if kind == "skipped":
        reason = classify_skip(played_seconds, duration, after_similar)

    ev = await log_event(s, user_id, kind, track_id=track_id, artist=artist,
                         session_id=session_id, reason=reason,
                         context=context, scenario=scenario,
                         played_seconds=played_seconds,
                         track_duration=duration)

    strong = kind in ("liked", "disliked", "added_to_library",
                      "removed_from_library")
    if strong:
        await touch_profile_stale(s, user_id)

    result: dict = {"ok": True, "event_id": ev.id if ev else None}
    if kind == "disliked" and track is not None:
        result["policy"] = await dislike_reason_to_policy(s, user_id, track, reason)
    return result


async def log_transition(s: AsyncSession, user_id: int, session_id: int | None,
                         track_id: int, event: str, played_seconds: float,
                         context: dict | None = None,
                         scenario: str | None = None) -> dict:
    """Переход трека (skip | trackFinished) из /next.last и /feedback.

    Логирует WaveEvent с классом скипа; PlayEvent закрывает service.py.
    """
    kind = "skipped" if event == "skip" else "completed"
    track = (await s.execute(select(Track).where(Track.id == int(track_id))
                             )).scalar_one_or_none()
    if track is None:
        return {"ok": False}
    duration = float(track.duration or 0)
    after = False
    if kind == "skipped":
        after = await skip_after_similar(s, user_id, session_id)
    ctx = EventContext(**{k: context.get(k) for k in
                          ("time_of_day", "weekday", "device", "activity", "mood")
                          if context and context.get(k)}) if context else None
    return await process_event(
        s, user_id, kind=kind, track=track, session_id=session_id,
        context=ctx, scenario=scenario,
        played_seconds=max(0.0, float(played_seconds or 0)),
        duration=duration, after_similar=after)


async def skip_after_similar(s: AsyncSession, user_id: int,
                             session_id: int | None) -> bool:
    """Последние 2 скипа подряд в одной жанровой семье? (усталость от направления)"""
    q = (select(WaveEvent.track_id)
         .where(WaveEvent.user_id == user_id, WaveEvent.kind == "skipped",
                WaveEvent.track_id.isnot(None))
         .order_by(WaveEvent.created_at.desc()).limit(2))
    ids = [int(x) for x in (await s.execute(q)).scalars()]
    if len(ids) < 2:
        return False
    tracks = (await s.execute(select(Track).where(Track.id.in_(ids)))).scalars().all()
    if len(tracks) < 2:
        return False
    from .taste import detect_genres
    ga, gb = detect_genres(tracks[0]), detect_genres(tracks[1])
    return bool(ga and gb and (ga & gb))
