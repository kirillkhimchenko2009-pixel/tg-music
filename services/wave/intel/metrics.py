"""Wave Optimization Engine (MODULE 8): метрики качества волны (§17).

Считается из wave_events per-user (и суммарно). Не оптимизируем только по
длительности прослушивания: completion — один из факторов, а не цель.
A/B — только с корректной рандомизацией (позже, при накоплении событий).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import ArtistStats, WaveEvent, utcnow


@dataclass
class WaveMetrics:
    window_days: int = 14
    impressions: int = 0
    plays: int = 0                      # track_started
    completions: int = 0
    skips: int = 0
    likes: int = 0
    dislikes: int = 0
    saves: int = 0                      # added_to_library
    shaken: int = 0
    discovery_plays: int = 0            # старты треков новых артистов
    unique_artists: int = 0
    unique_genres: int = 0
    context_matched: int = 0            # старты с context в событии
    avg_played_seconds: float = 0.0
    source_counts: dict[str, int] = field(default_factory=dict)

    def to_json(self) -> dict:
        plays = max(1, self.plays)
        return {
            "window_days": self.window_days,
            "impressions": self.impressions,
            "plays": self.plays,
            "completion_rate": round(self.completions / plays, 3),
            "skip_rate": round(self.skips / plays, 3),
            "like_rate": round(self.likes / plays, 3),
            "dislike_rate": round(self.dislikes / plays, 3),
            "save_rate": round(self.saves / plays, 3),
            "shaken": self.shaken,
            "discovery_rate": round(self.discovery_plays / plays, 3),
            "unique_artists": self.unique_artists,
            "unique_genres": self.unique_genres,
            "context_match_rate": round(self.context_matched / plays, 3)
            if self.context_matched else 0.0,
            "avg_played_seconds": round(self.avg_played_seconds, 1),
            "source_counts": self.source_counts,
        }


async def metrics(s: AsyncSession, user_id: int | None = None,
                  days: int = 14) -> WaveMetrics:
    since = utcnow() - timedelta(days=days)
    q = select(WaveEvent).where(WaveEvent.created_at >= since)
    if user_id is not None:
        q = q.where(WaveEvent.user_id == user_id)
    q = q.order_by(WaveEvent.created_at.desc()).limit(4000)
    rows = (await s.execute(q)).scalars().all()

    m = WaveMetrics(window_days=days)
    artists: set[str] = set()
    genres: set[str] = set()
    played_seconds_sum = 0.0
    played_seconds_n = 0
    discovered_keys = set()
    st_q = (select(ArtistStats).where(
        ArtistStats.discovered_via.isnot(None),
        ArtistStats.first_seen_at >= since))
    if user_id is not None:
        st_q = st_q.where(ArtistStats.user_id == user_id)
    for st in (await s.execute(st_q)).scalars():
        discovered_keys.add(st.artist_key)

    from services.wave.intel.taste import detect_genres
    from core.models import Track

    ids = [e.track_id for e in rows if e.track_id]
    tracks = {}
    if ids:
        trows = (await s.execute(select(Track).where(Track.id.in_(set(ids))))).scalars().all()
        tracks = {t.id: t for t in trows}

    for e in rows:
        if e.kind == "impression":
            m.impressions += 1
        elif e.kind == "track_started":
            m.plays += 1
            if e.track_id in tracks:
                ak = tracks[e.track_id].artist
                from services.wave.intel.events import artist_key as _ak
                key = _ak(ak or "")
                if key:
                    artists.add(key)
            if e.scenario or (e.context or {}).get("activity"):
                m.context_matched += 1
            t = tracks.get(e.track_id)
            if t is not None:
                from services.wave.intel.events import artist_key as _ak
                if _ak(t.artist or "") in discovered_keys:
                    m.discovery_plays += 1
                for g in detect_genres(t):
                    genres.add(g)
        elif e.kind == "completed":
            m.completions += 1
        elif e.kind == "skipped":
            m.skips += 1
        elif e.kind == "liked":
            m.likes += 1
        elif e.kind == "disliked":
            m.dislikes += 1
        elif e.kind == "added_to_library":
            m.saves += 1
        elif e.kind == "wave_shaken":
            m.shaken += 1
        if e.played_seconds:
            played_seconds_sum += float(e.played_seconds)
            played_seconds_n += 1

    m.unique_artists = len(artists)
    m.unique_genres = len(genres)
    m.avg_played_seconds = played_seconds_sum / played_seconds_n if played_seconds_n else 0.0
    return m
