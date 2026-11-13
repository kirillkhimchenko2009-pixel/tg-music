"""Каталог: поиск в локальной БД, upsert треков, лайки простым счётчиком."""
import json
from dataclasses import asdict

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import Track
from services.providers.base import TrackMeta

async def upsert_track(session: AsyncSession, meta: TrackMeta) -> Track:
    """Находит трек в каталоге по (source, external_id), иначе создаёт."""
    q = select(Track).where(Track.source == meta.source,
                            Track.external_id == meta.external_id)
    track = (await session.execute(q)).scalar_one_or_none()
    if track:
        # обновляем метаданные, файл_id остаётся кешем; строки без изменений
        # не грязним — иначе каждый поиск переписывает весь каталог
        # (на удалённой БД это лишние UPDATE-запросы на каждый результат)
        new_page = getattr(meta, "page_url", None)
        new_lic = getattr(meta, "license", None)
        if (track.title != meta.title or track.artist != meta.artist
                or track.album != meta.album or track.cover_url != meta.cover_url
                or track.duration != meta.duration or track.year != meta.year
                or track.explicit != bool(getattr(meta, "explicit", False))
                or track.page_url != new_page or track.license != new_lic):
            track.title = meta.title
            track.artist = meta.artist
            track.album = meta.album
            track.cover_url = meta.cover_url
            track.duration = meta.duration
            track.year = meta.year
            track.explicit = bool(getattr(meta, "explicit", False))
            track.page_url = new_page
            track.license = new_lic
        return track
    # INSERT тоже в SAVEPOINT: гонка двух джобов на один (source, external_id)
    # валит flush IntegrityError — откатываем точку и перечитываем, а не
    # отравляем сессию (иначе любой следующий доступ к атрибутам строк
    # этого каталога даёт PendingRollbackError, и джоба умирает целиком)
    # whitelist: TrackMeta шире модели (downloadable — режим реестра, не колонка)
    data = asdict(meta)
    track = Track(**{k: data[k] for k in (
        "source", "external_id", "title", "artist", "album", "year",
        "isrc", "duration", "explicit", "cover_url", "page_url", "license")
        if k in data})
    session.add(track)
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError:
        existing = (await session.execute(q)).scalar_one_or_none()
        if existing:
            return existing
        raise
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
                     isrc=track.isrc, duration=track.duration, cover_url=track.cover_url,
                     explicit=bool(getattr(track, "explicit", False)),
                     page_url=getattr(track, "page_url", None),
                     license=getattr(track, "license", None))
