"""Artist Discovery Engine (MODULE 6): артисты в центре внимания.

Профиль артиста собирается ИСКЛЮЧИТЕЛЬНО из проверяемых метаданных каталога
(треки, альбомы, годы, плейлисты, статистика юзера). Реплики — шаблонные
факты из этих же данных; никаких выдуманных биографий и «концертов».
Если данных мало — реплики нет (§9.2).
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import (ArtistStats, Playlist, PlaylistTrack, Track,
                         WaveSession)
from .events import artist_key


@dataclass
class ArtistProfile:
    artist_key: str
    name: str
    cover_url: str | None = None
    tracks_count: int = 0
    albums: list[str] = None
    years: list[int] = None
    top_tracks: list[dict] = None      # {id, title, duration, cover_url}
    genres: list[str] = None           # эвристика по словам каталога
    user_plays: int = 0
    user_likes: int = 0
    first_seen_at: str | None = None   # когда открыл через волну
    is_excluded: bool = False
    insight: str | None = None         # реплика — только из фактов выше

    def to_json(self) -> dict:
        return {
            "artist_key": self.artist_key, "name": self.name,
            "cover_url": self.cover_url, "tracks_count": self.tracks_count,
            "albums": self.albums or [], "years": self.years or [],
            "top_tracks": self.top_tracks or [], "genres": self.genres or [],
            "user_plays": self.user_plays, "user_likes": self.user_likes,
            "first_seen_at": self.first_seen_at, "is_excluded": self.is_excluded,
            "insight": self.insight,
        }


def _insight(p: ArtistProfile) -> str | None:
    """Короткая реплика из проверяемых фактов. Недостаточно данных → None."""
    facts = []
    if p.tracks_count >= 3:
        facts.append(f"в каталоге {p.tracks_count} треков")
    if p.albums and len(p.albums) >= 2:
        facts.append(f"альбомы: {', '.join(p.albums[:2])}")
    elif p.albums:
        facts.append(f"альбом «{p.albums[0]}»")
    if p.years:
        facts.append(f"годы релизов: {min(p.years)}–{max(p.years)}")
    if p.genres:
        facts.append("звучание: " + ", ".join(p.genres[:2]))
    if not facts:
        return None
    lead = f"{p.name}: " if p.name else ""
    return lead + "; ".join(facts[:3]) + "."


async def artist_profile(s: AsyncSession, user_id: int, name_or_key: str) -> ArtistProfile | None:
    """Профиль по имени артиста или ключу (search по каталогу)."""
    key = artist_key(name_or_key)
    name_part = name_or_key.strip()
    q = select(Track).where((Track.artist.ilike(f"%{name_part}%"))).limit(60)
    tracks = list((await s.execute(q)).scalars())
    # уточняем выборку тем же ключом, что и движок
    tracks = [t for t in tracks if artist_key(t.artist or "") == key] or tracks
    if not tracks:
        return None

    st = (await s.execute(select(ArtistStats).where(
        ArtistStats.user_id == user_id, ArtistStats.artist_key == key)
    )).scalar_one_or_none()

    albums: list[str] = []
    for t in tracks:
        if t.album and t.album not in albums:
            albums.append(t.album)
    years = sorted({t.year for t in tracks if t.year})
    top = sorted(tracks, key=lambda t: (t.downloads or 0), reverse=True)[:5]
    best_cover = next((t.cover_url for t in top if t.cover_url), None)

    from .taste import detect_genres
    genres: set[str] = set()
    for t in tracks[:10]:
        genres |= detect_genres(t)

    p = ArtistProfile(
        artist_key=key,
        name=(tracks[0].artist or "").split(",")[0].strip(),
        cover_url=best_cover,
        tracks_count=len(tracks),
        albums=albums[:6], years=years,
        top_tracks=[{"id": t.id, "title": t.title, "duration": int(t.duration or 0),
                     "cover_url": t.cover_url} for t in top],
        genres=sorted(genres),
        user_plays=st.plays if st else 0,
        user_likes=st.likes if st else 0,
        first_seen_at=st.first_seen_at.isoformat() if (st and st.first_seen_at) else None,
        is_excluded=st.is_excluded if st else False,
    )
    p.insight = _insight(p)
    return p


async def discovery_cards(s: AsyncSession, user_id: int, limit: int = 8) -> list[dict]:
    """Карточки «открытия» для экрана волны: новые артисты рядом с вкусом.

    Порядок: артисты, отмеченные discovery недавно, затем незнакомые из
    популярного каталога (не случайные — по совпадению жанров с профилем).
    """
    from .taste import detect_genres, TasteProfile, snapshot_from_profile, get_profile
    profile = await get_profile(s, user_id)
    snap = snapshot_from_profile(profile)

    known_q = (select(ArtistStats.artist_key)
               .where(ArtistStats.user_id == user_id,
                      ArtistStats.plays > 0))
    known = {str(x) for x in (await s.execute(known_q)).scalars()}
    # лайкнутые артисты — тоже знакомые: в карточках открытий им не место
    liked_q = (select(Track.artist)
               .join(PlaylistTrack, PlaylistTrack.track_id == Track.id)
               .join(Playlist, Playlist.id == PlaylistTrack.playlist_id)
               .where(Playlist.owner_id == user_id, Playlist.title.like("❤%")))
    known |= {artist_key(a or "") for (a,) in (await s.execute(liked_q)).all()
              if artist_key(a or "")}

    q = (select(Track)
         .order_by(Track.downloads.desc(), Track.id.desc()).limit(200))
    seen: dict[str, dict] = {}
    for t in (await s.execute(q)).scalars():
        ak = artist_key(t.artist or "")
        if not ak or ak in known or ak in seen:
            continue
        gs = detect_genres(t)
        near = len(gs & set(snap.genre_scores)) if snap.genre_scores else 0
        if not gs and not near:
            continue                     # без жанровой опоры открытия не даём
        seen[ak] = {"artist_key": ak, "name": (t.artist or "").split(",")[0].strip(),
                    "cover_url": t.cover_url, "genres": sorted(gs),
                    "near": near,
                    "track_id": t.id, "title": t.title}
        if len(seen) >= limit * 2:
            break
    cards = sorted(seen.values(), key=lambda c: -c["near"])[:limit]
    for c in cards:
        c.pop("near", None)
    return cards
