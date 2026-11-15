"""REST API плейлистов для Mini App."""
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from bot import runtime
from server.auth import CurrentUser, tg_user
from server.routes.tracks import track_dict

router = APIRouter(prefix="/api/playlists", tags=["playlists"])
likes_router = APIRouter(prefix="/api/likes", tags=["likes"])

_LIKES_TITLE = "❤ Любимое"          # личный плейлист лайков (как «Мне нравится» в ЯМ)

class PlaylistIn(BaseModel):
    title: str

class TrackIn(BaseModel):
    track_id: int


class LikesBulkIn(BaseModel):
    track_ids: list[int]

async def _owned_playlist(user: CurrentUser, playlist_id, session):
    from core.models import Playlist
    pl = (await session.execute(
        select(Playlist).where(Playlist.id == playlist_id,
                               Playlist.owner_id == int(user.id)))).scalar_one_or_none()
    if not pl:
        raise HTTPException(404, "нет такого плейлиста")
    return pl

async def _likes_playlist(user: CurrentUser, s):
    """Личный плейлист «❤ Любимое» — создаётся всем и всегда идёт первым."""
    from bot import runtime
    await runtime.ensure_user(s, int(user.id), user.username, "MiniApp")
    return await runtime.get_or_create_playlist(s, int(user.id), _LIKES_TITLE)


@router.get("")
async def my_playlists(user: CurrentUser = Depends(tg_user)):
    async with runtime.get_session() as s:
        from core.models import Playlist, PlaylistTrack, Track
        likes = await _likes_playlist(user, s)
        await s.commit()                      # «Любимое» живёт в БД с первого открытия
        q = (select(Playlist).where(Playlist.owner_id == int(user.id),
                                    Playlist.id != likes.id)
             .order_by(Playlist.id.desc()))
        rows = [likes] + list((await s.execute(q)).scalars())
        ids = [pl.id for pl in rows]
        counts: dict[int, int] = {i: 0 for i in ids}
        covers: dict[int, list[str]] = {i: [] for i in ids}
        if ids:
            # одна выборка на все плейлисты: количество + до 4 разных обложек (мозаика)
            pt_rows = (await s.execute(
                select(PlaylistTrack.playlist_id, Track.cover_url)
                .join(Track, PlaylistTrack.track_id == Track.id)
                .where(PlaylistTrack.playlist_id.in_(ids))
                .order_by(PlaylistTrack.playlist_id, PlaylistTrack.position))).all()
            for pid, cover in pt_rows:
                counts[pid] += 1
                if cover and len(covers[pid]) < 4 and cover not in covers[pid]:
                    covers[pid].append(cover)
        out = []
        for pl in rows:
            cv = covers[pl.id]
            out.append({"id": pl.id, "title": pl.title, "count": counts[pl.id],
                        "cover_url": cv[0] if cv else None, "covers": cv,
                        "source": getattr(pl, "source", "") or ""})
    return {"playlists": out}

@router.post("")
async def create(body: PlaylistIn, user: CurrentUser = Depends(tg_user)):
    title = (body.title or "").strip()[:120]
    if not title:
        raise HTTPException(400, "нужен title")
    async with runtime.get_session() as s:
        await runtime.ensure_user(s, int(user.id), user.username, "MiniApp")
        pl = await runtime.get_or_create_playlist(s, int(user.id), title)
        pl.source = "manual"
        await s.commit()
        return {"id": pl.id, "title": pl.title, "count": 0, "source": "manual"}


# ---------------- любимые исполнители (топ по плейлистам) ----------------

artists_router = APIRouter(prefix="/api/artists", tags=["artists"])


@artists_router.get("/top")
async def top_artists(limit: int = 8, user: CurrentUser = Depends(tg_user)):
    """Топ исполнителей юзера: частота треков в его плейлистах (лайки дают
    доп. вес — трек из «❤ Любимое» и другого плейлиста считается дважды)."""
    from sqlalchemy import func

    async with runtime.get_session() as s:
        from core.models import Playlist, PlaylistTrack, Track
        await _likes_playlist(user, s)          # гарантируем «❤ Любимое»
        await s.commit()
        rows = (await s.execute(
            select(Track.artist, func.count().label("cnt"),
                   func.max(Track.cover_url).label("cover"))
            .join(PlaylistTrack, PlaylistTrack.track_id == Track.id)
            .join(Playlist, Playlist.id == PlaylistTrack.playlist_id)
            .where(Playlist.owner_id == int(user.id),
                   Track.artist.isnot(None), Track.artist != "")
            .group_by(Track.artist)
            .order_by(func.count().desc(), Track.artist)
            .limit(max(1, min(limit, 12))))).all()
    return {"artists": [{"name": r.artist, "count": r.cnt, "cover_url": r.cover}
                        for r in rows]}

@router.get("/{playlist_id}")
async def playlist_items(playlist_id: int, user: CurrentUser = Depends(tg_user)):
    async with runtime.get_session() as s:
        from core.models import Playlist, PlaylistTrack, Track
        await _owned_playlist(user, playlist_id, s)
        rows = list((await s.execute(
            select(PlaylistTrack, Track)
            .join(Track, PlaylistTrack.track_id == Track.id)
            .where(PlaylistTrack.playlist_id == playlist_id)
            .order_by(PlaylistTrack.position))).all())
    return {"id": playlist_id,
            "tracks": [track_dict(t) | {"pt_id": pt.id} for pt, t in rows]}

@router.get("/{playlist_id}/tracks")
async def playlist_tracks(playlist_id: int, user: CurrentUser = Depends(tg_user)):
    """Треки плейлиста (экран детального списка estos шлёт тут)."""
    async with runtime.get_session() as s:
        from core.models import PlaylistTrack, Track
        await _owned_playlist(user, playlist_id, s)
        rows = list((await s.execute(
            select(PlaylistTrack, Track)
            .join(Track, PlaylistTrack.track_id == Track.id)
            .where(PlaylistTrack.playlist_id == playlist_id)
            .order_by(PlaylistTrack.position))).all())
    return {"tracks": [track_dict(t) | {"pt_id": pt.id} for pt, t in rows]}

@router.post("/{playlist_id}/tracks")
async def add_track(playlist_id: int, body: TrackIn, user: CurrentUser = Depends(tg_user)):
    async with runtime.get_session() as s:
        await _owned_playlist(user, playlist_id, s)
        from services import catalog
        t = await catalog.get_track(s, body.track_id)
        if not t:
            raise HTTPException(404, "нет такого трека")
        ok = await runtime.playlist_add_track(s, playlist_id, body.track_id)
    return {"added": ok}

@router.delete("/{playlist_id}/tracks/{track_id}")
async def del_track(playlist_id: int, track_id: int, user: CurrentUser = Depends(tg_user)):
    async with runtime.get_session() as s:
        await _owned_playlist(user, playlist_id, s)
        from core.models import PlaylistTrack
        from sqlalchemy import delete as _del
        await s.execute(_del(PlaylistTrack).where(
            PlaylistTrack.playlist_id == playlist_id,
            PlaylistTrack.track_id == track_id))
        await s.commit()
    return {"deleted": True}

@router.delete("/{playlist_id}")
async def delete_playlist(playlist_id: int, user: CurrentUser = Depends(tg_user)):
    async with runtime.get_session() as s:
        from core.models import Playlist, PlaylistTrack
        await _owned_playlist(user, playlist_id, s)
        from sqlalchemy import delete as _del
        await s.execute(_del(PlaylistTrack).where(PlaylistTrack.playlist_id == playlist_id))
        await s.execute(_del(Playlist).where(Playlist.id == playlist_id))
        await s.commit()
    return {"deleted": True}


# ---------------- лайки («❤ Любимое») ----------------

@likes_router.get("")
async def liked_ids(user: CurrentUser = Depends(tg_user)):
    """Множество лайкнутых track_id — для состояний сердечек."""
    async with runtime.get_session() as s:
        from core.models import PlaylistTrack
        pl = await _likes_playlist(user, s)
        rows = (await s.execute(
            select(PlaylistTrack.track_id).where(PlaylistTrack.playlist_id == pl.id)
        )).scalars().all()
    return {"ids": list(rows)}


_LIKES_BULK_MAX = max(1, int(os.getenv("WEBAPP_LIKES_BULK_MAX", "500")))


@likes_router.post("/bulk")
async def likes_bulk(body: LikesBulkIn, user: CurrentUser = Depends(tg_user)):
    """«Всё в любимое»: весь плейлист → «❤ Любимое» одним запросом.

    Дубликаты списка и уже лайкнутые пропускаются, неизвестные каталогу
    — мимо. Один INSERT + один коммит: N транзакций по треку на Neon
    медленны и плодят «database is locked»."""
    from sqlalchemy import func

    from core.models import PlaylistTrack, Track
    ids = list(dict.fromkeys(int(t) for t in body.track_ids))[:_LIKES_BULK_MAX]
    if not ids:
        return {"added": 0, "ids": [], "existing": []}
    async with runtime.get_session() as s:
        pl = await _likes_playlist(user, s)
        have = set((await s.execute(
            select(PlaylistTrack.track_id).where(
                PlaylistTrack.playlist_id == pl.id,
                PlaylistTrack.track_id.in_(ids)))).scalars())
        known = set((await s.execute(
            select(Track.id).where(Track.id.in_(ids)))).scalars())
        pos = ((await s.execute(
            select(func.count()).select_from(PlaylistTrack)
            .where(PlaylistTrack.playlist_id == pl.id))).scalar() or 0)
        added: list[int] = []
        existing: list[int] = []
        for tid in ids:
            if tid in have:
                existing.append(tid)
                continue
            if tid not in known:
                continue
            s.add(PlaylistTrack(playlist_id=pl.id, track_id=tid, position=pos))
            pos += 1
            added.append(tid)
        await s.commit()
    return {"added": len(added), "ids": added, "existing": existing}


@likes_router.post("/{track_id}")
async def toggle_like(track_id: int, user: CurrentUser = Depends(tg_user)):
    """Сердечко-переключатель: добавил/убрал из «❤ Любимое»."""
    async with runtime.get_session() as s:
        from core.models import PlaylistTrack, Track
        from sqlalchemy import delete as _del
        pl = await _likes_playlist(user, s)
        exists = (await s.execute(
            select(PlaylistTrack).where(PlaylistTrack.playlist_id == pl.id,
                                        PlaylistTrack.track_id == track_id)
        )).scalar_one_or_none()
        if exists:
            await s.execute(_del(PlaylistTrack).where(PlaylistTrack.id == exists.id))
            liked = False
        else:
            t = await s.get(Track, track_id)
            if not t:
                raise HTTPException(404, "нет такого трека")
            n = len((await s.execute(
                select(PlaylistTrack).where(PlaylistTrack.playlist_id == pl.id))).scalars().all())
            s.add(PlaylistTrack(playlist_id=pl.id, track_id=track_id, position=n))
            liked = True
        await s.commit()
    return {"liked": liked}
