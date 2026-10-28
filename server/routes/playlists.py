"""REST API плейлистов для Mini App."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from bot import runtime
from server.auth import CurrentUser, tg_user
from server.routes.tracks import track_dict

router = APIRouter(prefix="/api/playlists", tags=["playlists"])

class PlaylistIn(BaseModel):
    title: str

class TrackIn(BaseModel):
    track_id: int

async def _owned_playlist(user: CurrentUser, playlist_id, session):
    from core.models import Playlist
    pl = (await session.execute(
        select(Playlist).where(Playlist.id == playlist_id,
                               Playlist.owner_id == int(user.id)))).scalar_one_or_none()
    if not pl:
        raise HTTPException(404, "нет такого плейлиста")
    return pl

@router.get("")
async def my_playlists(user: CurrentUser = Depends(tg_user)):
    async with runtime.get_session() as s:
        from core.models import Playlist, PlaylistTrack
        q = (select(Playlist).where(Playlist.owner_id == int(user.id))
             .order_by(Playlist.id.desc()))
        rows = list((await s.execute(q)).scalars())
        out = []
        for pl in rows:
            items = list((await s.execute(
                select(PlaylistTrack).where(PlaylistTrack.playlist_id == pl.id))).scalars())
            first = next(iter(items), None)
            cover = None
            if first:
                from services.catalog import get_track
                t = await get_track(s, first.track_id)
                cover = t.cover_url if t else None
            out.append({"id": pl.id, "title": pl.title, "count": len(items),
                        "cover_url": cover})
    return {"playlists": out}

@router.post("")
async def create(body: PlaylistIn, user: CurrentUser = Depends(tg_user)):
    title = (body.title or "").strip()[:120]
    if not title:
        raise HTTPException(400, "нужен title")
    async with runtime.get_session() as s:
        await runtime.ensure_user(s, int(user.id), user.username, "MiniApp")
        pl = await runtime.get_or_create_playlist(s, int(user.id), title)
        await s.commit()
        return {"id": pl.id, "title": pl.title, "count": 0}

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
