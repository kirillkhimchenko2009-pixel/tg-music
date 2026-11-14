"""REST API импорта плейлистов по ссылке (v2): ЯМ/Звук/Spotify/YT → плейлист пользователя.

Аналог /link-флоу из бота, но без отправки в чат: треки попадают в плейлист
Mini App, откуда их можно слушать и скачивать чанками.

Импорт фоновый: POST /api/import быстро отвечает {"job": id} (202), а
GET /api/import/{job} отдаёт статус/результат. Так рыхлые публичные прокси
(русский выход для Яндекс.Музыки) не подвешивают HTTP: задача терпеливо
ретраит в фоне, UI поллит статус. Повторный клик по той же ссылке не
скачивает плейлист заново: живая задача дедуплицируется, недавний успех
отдаётся сразу.
"""
import asyncio
import logging
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
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
log = logging.getLogger("import")

_COOLDOWN = 60
_LAST_IMPORT: dict[int, float] = {}
_INFLIGHT: dict[tuple[int, str], str] = {}        # (uid, url) -> job_id
_JOBS: dict[str, dict] = {}
_TASKS: set[asyncio.Task] = set()
# (uid, url) -> (ts, playlist_id, title, added) — недавний успех для дедупа
_RECENT_OK: dict[tuple[int, str], tuple] = {}
_RECENT_TTL = 15 * 60
_JOB_TTL = 15 * 60
_MAX_JOBS = 300


class ImportIn(BaseModel):
    url: str


def _prune(now: float) -> None:
    """Чистка памяти: старые завершённые задачи и устаревшие дедупы."""
    for jid, j in list(_JOBS.items()):
        if j["status"] in ("done", "error") and now - j["updated_at"] > _JOB_TTL:
            _JOBS.pop(jid, None)
            if _INFLIGHT.get(j["key"]) == jid:
                _INFLIGHT.pop(j["key"], None)
    for k, v in list(_RECENT_OK.items()):
        if now - v[0] > _RECENT_TTL:
            _RECENT_OK.pop(k, None)
    while len(_JOBS) > _MAX_JOBS:
        for jid, j in sorted(_JOBS.items(), key=lambda kv: kv[1]["created_at"]):
            if j["status"] in ("done", "error"):
                _JOBS.pop(jid, None)
                if _INFLIGHT.get(j["key"]) == jid:
                    _INFLIGHT.pop(j["key"], None)
                break
        else:
            break


async def _pl_payload(s, playlist_id: int) -> list[dict]:
    """Треки плейлиста в API-формате (общий для задачи и дедуп-ответа)."""
    from core.models import Track
    out: list[dict] = []
    pts = (await s.execute(
        select(PlaylistTrack).where(PlaylistTrack.playlist_id == playlist_id)
        .order_by(PlaylistTrack.position))).scalars().all()
    for pt in pts:
        t = await s.get(Track, pt.track_id)
        if t:
            out.append(track_dict(t))
    return out


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


async def _run_import(job_id: str, uid: int, url: str, username: str | None,
                      user: CurrentUser) -> None:
    """Фоновая часть импорта: сетевой забор + запись в БД. Ошибки — в статус."""
    j = _JOBS[job_id]
    try:
        title, metas = await _fetch_playlist(url, user)
        if not metas:
            raise ValueError("в плейлисте нет треков")
        async with runtime.get_session() as s:
            await runtime.ensure_user(s, uid, username, "MiniApp")
            pl = await runtime.get_or_create_playlist(s, uid, (title or "Импорт")[:120])
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
            payload = await _pl_payload(s, pl.id)
        j.update(status="done", playlist_id=pl.id, title=title, added=added,
                 tracks=payload)
        _RECENT_OK[(uid, url)] = (time.time(), pl.id, title, added)
        log.info("import job %s: ok playlist=%s added=%s (%d треков)",
                 job_id, pl.id, added, len(payload))
    except Exception as e:
        msg = str(e) or type(e).__name__
        j.update(status="error", error=msg)
        log.warning("import job %s failed: %s: %s", job_id, type(e).__name__,
                    str(e)[:250])
    finally:
        j["updated_at"] = time.time()
        if _INFLIGHT.get(j["key"]) == job_id:
            _INFLIGHT.pop(j["key"], None)


@router.post("/import")
async def import_playlist(body: ImportIn, user: CurrentUser = Depends(tg_user)):
    url = (body.url or "").strip()
    if not url:
        raise HTTPException(400, "нужна ссылка на плейлист")
    uid = int(user.id)
    now = time.time()

    link = parse_link(url)
    if link is None:
        raise HTTPException(400, "не удалось разобрать ссылку "
                                 "(ЯМ / Spotify / Звук / SoundCloud / YouTube)")
    if link.kind != "playlist":
        raise HTTPException(400, "пока поддерживаются ссылки на плейлисты")

    _prune(now)

    # Дедуп: та же ссылка у того же юзера недавно — не качаем заново.
    hit = _RECENT_OK.get((uid, url))
    if hit:
        _RECENT_OK[(uid, url)] = (now, hit[1], hit[2], hit[3])
        async with runtime.get_session() as s:
            payload = await _pl_payload(s, int(hit[1]))
        return {"playlist_id": hit[1], "id": hit[1], "title": hit[2],
                "added": hit[3], "tracks": payload, "deduplicated": True}

    # Живая задача по той же ссылке — вернём её же, а не клон.
    infl = _INFLIGHT.get((uid, url))
    if infl:
        j = _JOBS.get(infl)
        if j and j["status"] == "running":
            return JSONResponse(status_code=202,
                                content={"job": infl, "deduplicated": True})
        _INFLIGHT.pop((uid, url), None)

    last = _LAST_IMPORT.get(uid, 0)
    if now - last < _COOLDOWN:
        raise HTTPException(429, f"подожди {int(_COOLDOWN - (now - last))} с "
                                 f"до следующего импорта")

    job_id = uuid.uuid4().hex[:12]
    _JOBS[job_id] = {"status": "running", "user": uid, "url": url,
                     "key": (uid, url), "error": "", "created_at": now,
                     "updated_at": now, "playlist_id": None, "title": None,
                     "added": 0, "tracks": []}
    _INFLIGHT[(uid, url)] = job_id
    _LAST_IMPORT[uid] = now            # кулдаун с момента старта, не успеха
    task = asyncio.create_task(_run_import(job_id, uid, url, user.username, user))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return JSONResponse(status_code=202, content={"job": job_id})


@router.get("/import/{job_id}")
async def import_status(job_id: str, user: CurrentUser = Depends(tg_user)):
    j = _JOBS.get(job_id)
    if not j or j["user"] != int(user.id):
        raise HTTPException(404, "задача не найдена")
    j["updated_at"] = max(j["updated_at"], time.time()) if j["status"] == "running" \
        else j["updated_at"]
    return {"status": j["status"], "title": j["title"], "added": j["added"],
            "playlist_id": j["playlist_id"], "id": j["playlist_id"],
            "tracks": j["tracks"], "error": j["error"]}
