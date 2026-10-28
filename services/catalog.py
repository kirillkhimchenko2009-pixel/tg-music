"""Каталог: поиск в локальной БД, upsert треков, лайки простым счётчиком."""
import json
from dataclasses import asdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import Track
from services.providers.base import TrackMeta

async def upsert_track(session: AsyncSession, meta: TrackMeta) -> Track:
    """Находит трек в каталоге по (source, external_id), иначе создаёт."""
    q = select(Track).where(Track.source == meta.source,
                            Track.external_id == meta.external_id)
    track = (await session.execute(q)).scalar_one_or_none()
    if track:
        # обновляем метаданные, файл_id остаётся кешем
        track.title = meta.title
        track.artist = meta.artist
        track.album = meta.album
        track.cover_url = meta.cover_url
        track.duration = meta.duration
        track.year = meta.year
        return track
    track = Track(**asdict(meta))
    session.add(track)
    await session.flush()
    return track

async def upsert_by_playlist_item(session: AsyncSession, meta: TrackMeta,
                                  fallback: TrackMeta | None = None) -> Track:
    """Для переноса плейлиста: сначала ищем существующий по (source, external_id),
    либо создаём заглушку (файл позже)."""
    return await upsert_track(session, meta if not fallback else meta)

async def local_search(session: AsyncSession, query: str, limit: int = 20) -> list[Track]:
    """Простой текстовый поиск по каталогу (title/artist LIKE)."""
    pat = f"%{query.strip()}%"
    q = (select(Track)
         .where((Track.title.ilike(pat)) | (Track.artist.ilike(pat)))
         .order_by(Track.downloads.desc(), Track.id.desc())
         .limit(limit))
    return list((await session.execute(q)).scalars())

async def get_track(session: AsyncSession, track_id: int) -> Track | None:
    return (await session.execute(
        select(Track).where(Track.id == track_id))).scalar_one_or_none()

def meta_of(track: Track) -> TrackMeta:
    return TrackMeta(source=track.source, external_id=track.external_id, title=track.title,
                     artist=track.artist, album=track.album, year=track.year,
                     isrc=track.isrc, duration=track.duration, cover_url=track.cover_url)
