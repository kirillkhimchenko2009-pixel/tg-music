"""REST API импорта плейлистов по ссылке (v2): ЯМ/Звук/Spotify/YT → плейлист пользователя.

Аналог /link-флоу из бота, но без отправки в чат: треки попадают в плейлист
Mini App, откуда их можно слушать и скачивать чанками.
"""
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from bot import runtime
from core.config import settings
from core.models import PlaylistTrack
from server.routes.tracks import track_dict
from server.auth import CurrentUser, tg_user
from services import catalog
from services.links import parse as parse_link

router = APIRouter(prefix="/api", tags=["import"])

_LAST_IMPORT: dict[int, float] = {}
_COOLDOWN = 60


class ImportIn(BaseModel):
    url: str


async def _fetch_playlist(url: str, user: CurrentUser) -> tuple[str, list]:
    """Плейлист по ссылке: (title, [TrackMeta]). Адаптировано из bot/links.py."""
    from fastapi import HTTPException as _exc
    from services.authstore import get_token
    from services.providers import yandex as ymx
    from services.providers import zvuk as zv
    from services.providers import soundcloud as scx
    from services.providers.spotify import SpotifyProvider

    link = parse_link(url)
    if link is None:
        raise _exc(400, "не удалось разобрать ссылку "
                        "(ЯМ / Spotify / Звук / SoundCloud / YouTube)")
    if link.kind != "playlist":
        raise _exc(400, "пока поддерживаются ссылки на плейлисты")

    async with runtime.get_session() as s:
        ym_tok = await get_token(s, int(user.id), "ym")
        zv_tok = await get_token(s, int(user.id), "zvuk")

    if link.service == "yandex":
        try:
            if link.extras.get("uuid_mode"):
                try:
                    return await ymx.anon_playlist_by_uuid(link.id)
                except Exception:
                    # lk.<uuid> («Мне нравится») и приватные списки анонимно
                    # не отдаются (а с зарубежного IP анонимное API геофильтруется)
                    raise _exc(
                        400, "Яндекс: такие ссылки (в т.ч. «Мне нравится») "
                             "работают только с токеном ЯМ или с российского IP — "
                             "подключи токен командой /token")
            owner = link.extras.get("owner")
            if owner:
                tok = ym_tok or (settings.YM_TOKEN or None)
                try:
                    return await ymx.anon_playlist_by_login(owner, link.id)
                except Exception:
                    if not tok:
                        raise _exc(
                            400, "Яндекс не отдал плейлист анонимно. Подключи "
                                 "токен Яндекс.Музыки командой /token")
                    try:
                        return await ymx.get_public_playlist(owner, link.id, tok)
                    except Exception:
                        raise _exc(
                            400, "Яндекс не отдал плейлист (возможно, он приватный)")
            raise _exc(400, "не распознал формат ссылки на плейлист ЯМ — "
                            "нужен адрес вида users/<владелец>/playlists/<номер>")
        except _exc:
            raise
        except Exception:
            raise _exc(400, "Яндекс не отдал плейлист — проверь ссылку или добавь /token")

    if link.service == "spotify":
        from services.providers.spotify import SpotifyUnavailable
        try:
            res = await SpotifyProvider().playlist(link.id)
        except SpotifyUnavailable as e:
            raise _exc(400, str(e))
        if res:
            return res
        raise _exc(400, "Spotify не отдал плейлист")

    if link.service == "soundcloud":
        res = await scx.playlist_by_url(link.id)
        if res:
            return res
        raise _exc(404, "SoundCloud не отдал плейлист "
                        "(приватные списки недоступны)")

    if link.service == "zvuk":
        if not zv_tok and not settings.ZVUK_TOKEN:
            raise _exc(400, "Звук: плейлисты доступны только с токеном — "
                            "подключи его командой /token")
        res = await zv.get_playlist(link.id, token=zv_tok)
        if res:
            return res

    if link.service == "youtube":
        from services.providers.youtube import YouTubeProvider
        metas = await YouTubeProvider().playlist(link.id)
        if metas:
            return ("YouTube playlist", metas)

    raise HTTPException(404, "плейлист не найден")


@router.post("/import")
async def import_playlist(body: ImportIn, user: CurrentUser = Depends(tg_user)):
    url = (body.url or "").strip()
    if not url:
        raise HTTPException(400, "нужна ссылка на плейлист")
    now = time.time()
    last = _LAST_IMPORT.get(int(user.id), 0)
    if now - last < _COOLDOWN:
        raise HTTPException(429, f"подожди {int(_COOLDOWN - (now - last))} с до следующего импорта")

    title, metas = await _fetch_playlist(url, user)
    if not metas:
        raise HTTPException(404, "в плейлисте нет треков")

    async with runtime.get_session() as s:
        await runtime.ensure_user(s, int(user.id), user.username, "MiniApp")
        pl = await runtime.get_or_create_playlist(s, int(user.id), (title or "Импорт")[:120])
        pl.source = "import"

        count = len(list((await s.execute(
            select(PlaylistTrack).where(PlaylistTrack.playlist_id == pl.id))).scalars()))

        added = 0
        for m in metas:
            t = await catalog.upsert_track(s, m)
            if t is None:
                continue
            exists = (await s.execute(
                select(PlaylistTrack).where(
                    PlaylistTrack.playlist_id == pl.id, PlaylistTrack.track_id == t.id))
            ).scalar_one_or_none()
            if exists is None:
                s.add(PlaylistTrack(playlist_id=pl.id, track_id=t.id, position=count + added))
                added += 1
        await s.commit()

        from core.models import Track
        out: list[dict] = []
        pts = (await s.execute(
            select(PlaylistTrack).where(PlaylistTrack.playlist_id == pl.id)
            .order_by(PlaylistTrack.position))).scalars().all()
        for pt in pts:
            t = await s.get(Track, pt.track_id)
            if t:
                out.append(track_dict(t))
    _LAST_IMPORT[int(user.id)] = now

    return {"playlist_id": pl.id, "title": title, "added": added, "tracks": out}

