"""REST API для Mini App: поиск, каталог, загрузки, стримы."""
import asyncio
import base64
import hashlib
import hmac
import httpx
import json as _json
import os
import time as _time
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Header, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select

from bot import runtime
from core.config import settings
from core.models import Track, PlayEvent
from music import mediajobs
from music.config import music_settings
from server.auth import CurrentUser, is_guest_id, tg_user
from services import catalog
from services.providers.base import TrackMeta

router = APIRouter(prefix="/api", tags=["tracks"])
JOB_STATUS: dict[str, dict] = {}
_TERMINAL_STATUSES = ("done", "error")
_JOB_TTL_SEC = float(os.getenv("WEBAPP_JOB_TTL_SEC", "1800"))
_JOB_LIMIT = max(100, int(os.getenv("WEBAPP_JOB_CACHE_LIMIT", "2000")))
_MAX_ACTIVE_DOWNLOADS = max(1, int(os.getenv("WEBAPP_MAX_ACTIVE_DOWNLOADS", "3")))
# strong references: иначе event loop может собрать задачу до завершения
_ACTIVE_TASKS: set[asyncio.Task] = set()
_INFLIGHT: dict[tuple[int, int, str], str] = {}
log = __import__("logging").getLogger("routes.tracks")


# ---------------- короткоживущие playback-токены ----------------
# <audio src=...> не умеет заголовки — отдаём подписанную ссылку /api/stream/{id}?t=...
_KEY = hashlib.sha256(("streamv2|" + settings.BOT_TOKEN).encode()).digest()


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64u_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_stream_token(uid: int, track_id: int, ttl: int = 6 * 3600) -> str:
    payload = _b64u(_json.dumps({"u": uid, "tr": track_id,
                                 "e": int(_time.time()) + ttl}).encode())
    sig = _b64u(hmac.new(_KEY, payload.encode(), hashlib.sha256).digest())
    return payload + "." + sig


def check_stream_token(t: str, expected_track_id: int | None = None):
    try:
        payload, sig = t.split(".", 1)
        ok = hmac.compare_digest(sig, _b64u(hmac.new(_KEY, payload.encode(), hashlib.sha256).digest()))
        if not ok:
            return None
        d = _json.loads(_b64u_dec(payload))
        if int(d.get("e", 0)) < _time.time():
            return None
        track_id = int(d["tr"])
        if expected_track_id is not None and track_id != int(expected_track_id):
            return None
        return int(d.get("u", 0))
    except Exception:
        return None

def track_dict(t: Track) -> dict:
    return {
        "id": t.id, "title": t.title, "artist": t.artist, "album": t.album,
        "year": t.year, "duration": t.duration, "cover_url": t.cover_url,
        "quality": t.quality, "ready": bool(t.file_id),
        "downloads": t.downloads,
        "source": t.source,
        "page_url": t.page_url, "license": t.license,
    }

@router.get("/search")
async def search(q: str = "", limit: int = 24, scope: str = "all",
                 user: CurrentUser = Depends(tg_user)):
    """Комбинированный поиск (мастер-промпт §4): локальный каталог, затем
    параллельно ВСЕ источники единого реестра (12 канонических + доп.)
    выбранной области (all/ru/intl/downloadable/official_links). Ошибка
    одного адаптера не влияет на остальные; результаты сливаются round-robin.
    """
    q = (q or "").strip()
    if not q:
        return {"local": [], "web": []}
    from services.authstore import get_token
    from services import multisearch

    scope = scope if scope in ("all", "ru", "intl", "downloadable",
                               "official_links") else "all"
    uid = int(user.id)
    async with runtime.get_session() as s:
        local = await catalog.local_search(s, q, limit=limit)
        tokens = {"ym": await get_token(s, uid, "ym") or None,
                  "zvuk": await get_token(s, uid, "zvuk") or None}

    web = await multisearch.web_search(q, limit=limit, scope=scope, tokens=tokens)

    out = []
    async with runtime.get_session() as s:
        for m in web:
            t = await catalog.upsert_track(s, m)
            out.append(track_dict(t))
        # ОДИН коммит на батч: Neon — удалённая БД, commit-на-трек давал
        # ~N сетевых round-trip'ов и подвешивал поиск на десятки секунд
        # (локально на SQLite незаметно, в облаке — «треки не ищутся»).
        await s.commit()
    return {"local": [track_dict(t) for t in local],
            "web": out}

@router.get("/library")
async def library(limit: int = 100, offset: int = 0,
                  user: CurrentUser = Depends(tg_user)):
    """Треки, когда-либо скачивавшиеся (есть file_id)."""
    async with runtime.get_session() as s:
        q = (select(Track).where(Track.file_id.isnot(None), Track.file_id != "")
             .order_by(Track.downloads.desc(), Track.id.desc())
             .limit(min(limit, 500)).offset(offset))
        rows = list((await s.execute(q)).scalars())
    return {"tracks": [track_dict(t) for t in rows]}


# ---------------- история прослушивания (продолжить слушать / недавнее) ----------------

class HistoryIn(BaseModel):
    track_id: int
    finished: bool = False
    played_seconds: float = 0.0


@router.get("/history")
async def history(limit: int = 24, user: CurrentUser = Depends(tg_user)):
    """Недавно прослушанное: последний факт по каждому треку, свежие сверху."""
    limit = max(1, min(limit, 50))
    from sqlalchemy import func
    async with runtime.get_session() as s:
        latest = (select(PlayEvent.track_id,
                         func.max(PlayEvent.id).label("lid"))
                  .where(PlayEvent.user_id == int(user.id),
                         PlayEvent.track_id.isnot(None))
                  .group_by(PlayEvent.track_id).subquery())
        rows = list((await s.execute(
            select(Track)
            .join(latest, latest.c.track_id == Track.id)
            .order_by(latest.c.lid.desc())
            .limit(limit))).scalars())
    return {"tracks": [track_dict(t) for t in rows]}


@router.post("/history")
async def history_add(body: HistoryIn, user: CurrentUser = Depends(tg_user)):
    """Beacon веб-плеера: старт/окончание трека вне волны (origin=web).

    Волна пишет свои события сама (по session) — здесь только обычный список."""
    if body.played_seconds and body.played_seconds > 4 * 3600:
        raise HTTPException(400, "недопустимая длительность")
    async with runtime.get_session() as s:
        t = await s.get(Track, body.track_id)
        if not t:
            raise HTTPException(404, "нет такого трека")
        s.add(PlayEvent(user_id=int(user.id), track_id=body.track_id,
                        origin="web", finished=bool(body.finished),
                        played_seconds=max(0.0, float(body.played_seconds or 0))))
        await s.commit()
    return {"ok": True}


# ---------------- офлайн-события плеера (local-first §17) ----------------

class OfflineEventIn(BaseModel):
    event_id: str
    track_id: int | None = None
    kind: str = "started"
    played_seconds: float = 0.0
    finished: bool = False
    skipped: bool = False
    at: int | None = None                      # epoch ms устройства


class HistorySyncIn(BaseModel):
    events: list[OfflineEventIn]


def _ev_ts(ms: int | None):
    """epoch ms → aware datetime; ничего внятного → None (дефолт utcnow)."""
    if not ms:
        return None
    try:
        v = float(ms)
        if v > 1e12:                            # миллисекунды → секунды
            v /= 1000.0
        from datetime import datetime, timezone
        return datetime.fromtimestamp(v, timezone.utc)
    except Exception:
        return None


@router.post("/history/sync")
async def history_sync(body: HistorySyncIn, user: CurrentUser = Depends(tg_user)):
    """Идемпотентная выгрузка офлайн-событий плеера (§17).

    Дедуп по уникальной паре (user_id, event_id) — двойной POST добавляет
    одну строку. События с неизвестным track_id, личные действия (лайки,
    добавления в плейлист — они синхронизируются своими API) и капнутые по
    длительности (>4 ч) ack-аются без записи, чтобы клиент не гонял их снова.
    """
    if len(body.events) > 200:
        raise HTTPException(400, "слишком много событий за раз")
    from sqlalchemy.dialects import postgresql, sqlite

    acked: list[str] = []
    async with runtime.get_session() as s:
        ids = {int(e.track_id) for e in body.events if e.track_id is not None}
        known: set[int] = set()
        if ids:
            rows = await s.execute(select(Track.id).where(Track.id.in_(ids)))
            known = {int(r[0]) for r in rows.all()}

        dialect = s.bind.dialect.name
        maker = {"postgresql": postgresql.insert,
                 "sqlite": sqlite.insert}.get(dialect)

        for e in body.events:
            ev_id = (e.event_id or "").strip()[:48]
            if not ev_id:
                continue
            if e.track_id is None or int(e.track_id) not in known \
                    or e.kind in ("liked", "added_to_playlist") \
                    or (e.played_seconds and e.played_seconds > 4 * 3600):
                acked.append(ev_id)             # ack без записи
                continue
            values = {
                "user_id": int(user.id), "track_id": int(e.track_id),
                "origin": "web", "event_id": ev_id,
                "played_seconds": max(0.0, float(e.played_seconds or 0)),
                "finished": bool(e.finished), "skipped": bool(e.skipped),
            }
            ts = _ev_ts(e.at)
            if ts is not None:
                values["started_at"] = ts
            if maker is not None:
                stmt = maker(PlayEvent).values(**values).on_conflict_do_nothing(
                    index_elements=["user_id", "event_id"])
                await s.execute(stmt)
            else:
                dup = await s.execute(select(PlayEvent.id).where(
                    PlayEvent.user_id == int(user.id),
                    PlayEvent.event_id == ev_id))
                if dup.scalar_one_or_none() is None:
                    s.add(PlayEvent(**values))
            acked.append(ev_id)
        await s.commit()
    return {"acked": acked}


# ---------------- пачка треков по id (восстановление очереди §10) ----------------

class TracksBatchIn(BaseModel):
    ids: list[int]


@router.post("/tracks/batch")
async def tracks_batch(body: TracksBatchIn, user: CurrentUser = Depends(tg_user)):
    ids = list(dict.fromkeys(int(i) for i in body.ids if i))[:300]
    if not ids:
        return {"tracks": []}
    async with runtime.get_session() as s:
        rows = list((await s.execute(select(Track).where(Track.id.in_(ids)))).scalars())
    by_id = {t.id: t for t in rows}
    return {"tracks": [track_dict(by_id[i]) for i in ids if i in by_id]}


@router.get("/tracks/{track_id}")
async def get_track(track_id: int, user: CurrentUser = Depends(tg_user)):
    async with runtime.get_session() as s:
        t = await catalog.get_track(s, track_id)
        if not t:
            raise HTTPException(404, "нет такого трека")
        return track_dict(t)

def _now() -> float:
    return _time.time()


def _is_terminal(status: str) -> bool:
    return status in _TERMINAL_STATUSES


def _touch(payload: dict, **fields) -> None:
    payload.update(fields)
    payload["updated_at"] = _now()


def _public_job(status: dict) -> dict:
    """Наружу не отдаём владельца и служебные метки времени."""
    return {k: v for k, v in status.items()
            if k not in ("user", "created_at", "updated_at")}


def _prune_jobs(now: float | None = None) -> None:
    """Не даём JOB_STATUS расти бесконечно (и чистим завершённые дубли)."""
    now = _now() if now is None else now
    for job_id, status in list(JOB_STATUS.items()):
        updated = status.get("updated_at") or status.get("created_at") or now
        if _is_terminal(status.get("status", "")) and now - updated > _JOB_TTL_SEC:
            JOB_STATUS.pop(job_id, None)
    if len(JOB_STATUS) > _JOB_LIMIT:
        ordered = sorted(JOB_STATUS.items(),
                         key=lambda item: item[1].get("created_at", 0))
        for job_id, status in ordered:
            if len(JOB_STATUS) <= _JOB_LIMIT:
                break
            if _is_terminal(status.get("status", "")):
                JOB_STATUS.pop(job_id, None)
    for key, job_id in list(_INFLIGHT.items()):
        status = JOB_STATUS.get(job_id)
        if status is None or _is_terminal(status.get("status", "")):
            _INFLIGHT.pop(key, None)


@router.post("/download")
async def download(track_id: int, quality: str = "",
                   user: CurrentUser = Depends(tg_user)):
    """Фоновая доставка трека в чат пользователя (с шагом прогресса).

    Повторное нажатие, пока задача жива, возвращает тот же job: файл не
    скачивается и не отправляется дважды. Лимит активных задач на юзера
    защищает Telegram и диск от лавины параллельных загрузок.
    """
    _prune_jobs()
    quality = quality or settings.DEFAULT_QUALITY
    uid = int(user.id)
    if is_guest_id(uid):
        # Гостевой id — не Telegram chat_id: send_audio/send_document уйдут
        # в никуда и юзер будет ждать вечную загрузку. Честно просим войти.
        raise HTTPException(409, "telegram_required")

    if music_settings.media_role == "app":
        # Скачивание выполняют воркеры (Botkeep → serv00 → локальный
        # резерв Render): APP-роль только ставит джобу в очередь Neon.
        return await _remote_download(uid, int(track_id), quality)

    key = (uid, int(track_id), quality)

    existing = _INFLIGHT.get(key)
    if existing:
        status = JOB_STATUS.get(existing)
        if status and not _is_terminal(status.get("status", "")):
            return {"job": existing, "deduplicated": True}
        _INFLIGHT.pop(key, None)

    active = 0
    for job_id in _INFLIGHT.values():
        status = JOB_STATUS.get(job_id) or {}
        if status.get("user") == uid and not _is_terminal(status.get("status", "")):
            active += 1
    if active >= _MAX_ACTIVE_DOWNLOADS:
        raise HTTPException(429, "Слишком много одновременных загрузок. "
                                 "Дождись завершения текущих.")

    job_id = uuid.uuid4().hex[:12]
    now = _now()
    JOB_STATUS[job_id] = {"status": "queued", "progress": 0.0, "user": uid,
                          "created_at": now, "updated_at": now}
    _INFLIGHT[key] = job_id
    task = asyncio.create_task(_deliver_job(job_id, uid, track_id, quality))
    _ACTIVE_TASKS.add(task)
    task.add_done_callback(_ACTIVE_TASKS.discard)
    return {"job": job_id}


async def _remote_download(uid: int, track_id: int, quality: str) -> dict:
    """APP-роль: скачивание выполняют воркеры, поэтому дедуп и лимит —
    по очереди в Neon, а не по памяти процесса. Контракт ответов тот же,
    что у локального пути ({"job": id} / {"job": id, "deduplicated": True}),
    прогресс веб-апп опрашивает тот же GET /api/jobs/{id}."""
    async with runtime.get_session() as s:
        t = await catalog.get_track(s, track_id)
        if not t:
            raise HTTPException(404, "нет такого трека")
        active = await mediajobs.find_active(s, track_id, uid)
        if active is not None:
            return {"job": active.id, "deduplicated": True}
        if await mediajobs.count_active(s, uid) >= _MAX_ACTIVE_DOWNLOADS:
            raise HTTPException(429, "Слишком много одновременных загрузок. "
                                     "Дождись завершения текущих.")
        job = await mediajobs.create_job(s, track_id=track_id, uid=uid,
                                         quality=quality, source=t.source)
        await s.commit()
    return {"job": job.id}


class BulkDownloadBody(BaseModel):
    track_ids: list[int]


_BULK_MAX = max(1, int(os.getenv("WEBAPP_BULK_MAX", "200")))


@router.post("/download-bulk")
async def download_bulk(body: BulkDownloadBody,
                        user: CurrentUser = Depends(tg_user)):
    """Пачка треков («Скачать всё») — одним запросом в очередь заданной роли.

    Каждый трек → джоба (дедуп по активной джобе юзера/трека).
    Уже скачанные (кеш file_id) в очередь НЕ ставятся — повторно не качаем:
    их id уходят в ответ как skipped. Лимит одиночных загрузок сюда не
    применяется: работу сериализует сама очередь, а не счётчик процесса.
    В APP- и local-ролях задачу исполняет mediajobs poller;
    прогресс — GET /api/jobs-bulk?ids=..."""
    uid = int(user.id)
    if is_guest_id(uid):
        # Гостевой id — не Telegram chat_id: файлы уйдут в никуда.
        raise HTTPException(409, "telegram_required")
    ids = list(dict.fromkeys(int(t) for t in body.track_ids))[:_BULK_MAX]
    if not ids:
        return {"jobs": [], "total": 0, "skipped": 0}
    quality = settings.DEFAULT_QUALITY
    out: list[dict] = []
    skipped: list[int] = []
    async with runtime.get_session() as s:
        tracks = (await s.execute(
            select(Track).where(Track.id.in_(ids)))).scalars().all()
        by_id = {t.id: t for t in tracks}
        for tid in ids:
            t = by_id.get(tid)
            if t is None:
                continue
            # уже скачан ранее (file_id в кеше) — не качаем повторно
            if getattr(t, "file_id", None):
                skipped.append(tid)
                continue
            active = await mediajobs.find_active(s, tid, uid)
            if active is not None:
                out.append({"track_id": tid, "job": active.id})
                continue
            job = await mediajobs.create_job(s, track_id=tid, uid=uid,
                                             quality=quality, source=t.source)
            await s.commit()
            out.append({"track_id": tid, "job": job.id})
    return {"jobs": out, "total": len(out), "skipped": skipped}


@router.get("/jobs-bulk")
async def jobs_bulk(ids: str = "", user: CurrentUser = Depends(tg_user)):
    """Статусы списка СВОИХ джоб одним запросом: {id: статус}.
    Фильтр по uid — чужие джобы не раскрываются даже по угаданному id."""
    uid = int(user.id)
    want = [x for x in (p.strip() for p in ids.split(",")) if x][:400]
    if not want:
        return {"jobs": {}}
    from music.models import MediaDownloadJob
    async with runtime.get_session() as s:
        rows = (await s.execute(
            select(MediaDownloadJob.id, MediaDownloadJob.status)
            .where(MediaDownloadJob.id.in_(want),
                   MediaDownloadJob.uid == uid))).all()
    return {"jobs": {jid: st for jid, st in rows}}


_BOT = None

async def _bot():
    global _BOT
    if _BOT is None:
        from bot.main import create_bot
        _BOT = create_bot()
    return _BOT

async def _deliver_job(job_id: str, user_id: int, track_id: int, quality: str):
    from services import catalog, ingest
    st = JOB_STATUS.get(job_id)
    if st is None:
        return
    key = (int(user_id), int(track_id), quality)
    bot = None
    t = None

    def update(**fields) -> None:
        _touch(st, **fields)

    async def send_fallback() -> bool:
        """В чат должен прийти хоть какой-то файл: 30-сек фрагмент.

        Сторонние ТГ-боты больше не используются: Telegram запрещает
        обмен сообщениями между ботами (USER_BOT_TO_BOT_DISABLED)."""
        nonlocal t
        if bot is None or t is None:
            return False
        from bot.runtime import send_preview_file
        ok = await send_preview_file(bot, user_id, t)
        if ok:
            update(status="done", progress=100.0, telegram=True,
                   preview=True)
        return ok

    try:
        bot = await _bot()
        from services.catalog import meta_of
        async with runtime.get_session() as s:
            t = await catalog.get_track(s, track_id)
            if not t:
                update(status="error", error="нет такого трека")
                return

            async def on_progress(p):
                if p - st.get("progress", 0.0) >= 2.0 or p >= 100:
                    update(progress=p, status="downloading")

            res = await ingest.resolve(s, meta_of(t), quality, user_id=user_id,
                                       on_progress=on_progress)
            if res.status == "not_found" or not (res.file_path or res.track):
                if await send_fallback():
                    return
                update(status="error", error=res.error or "не удалось скачать")
                return
            target = res.track or (await catalog.get_track(s, track_id))

            async def deliver_file(target_track, path, q) -> bool:
                """Отправить полный файл и закешировать новый file_id."""
                from bot.runtime import save_file_id_retry, send_audio_file
                fid = await send_audio_file(bot, user_id, target_track, path)
                if not fid:
                    return False
                update(status="done", progress=100.0, telegram=True)
                await save_file_id_retry(
                    target_track.id, fid, q,
                    size=path.stat().st_size,
                    local_path=path.as_posix())
                return True

            if res.file_path and target and res.file_path.exists():
                if await deliver_file(target, res.file_path, res.quality):
                    return
                if await send_fallback():
                    return
                # Нельзя писать done, если в чат реально ничего не ушло.
                update(status="error",
                       error="Telegram не принял файл (возможно, больше лимита)")
                return
            if target and target.file_id:
                from bot.runtime import save_file_id_retry, send_track_cached
                if await send_track_cached(bot, user_id, target):
                    update(status="done", progress=100.0, telegram=True)
                    return
                # file_id мог устареть (старый токен/другой бот). Не отдаём
                # 30-секундный фрагмент, пока не попробовали полный файл:
                # сначала локальная копия, затем честная перекачка.
                local = Path(target.local_path) if target.local_path else None
                if local and local.exists():
                    if await deliver_file(target, local, target.quality):
                        return
                try:
                    await save_file_id_retry(target.id, "")
                    target.file_id = ""
                    await s.flush()
                except Exception:  # noqa: BLE001
                    log.warning("не удалось сбросить file_id трека %s", target.id)
                res2 = await ingest.resolve(s, meta_of(target), quality,
                                            user_id=user_id,
                                            on_progress=on_progress,
                                            allow_cached=False)
                fresh = (res2.track or target) if res2 else None
                if (res2 and res2.file_path and fresh
                        and res2.file_path.exists()
                        and await deliver_file(fresh, res2.file_path, res2.quality)):
                    return
                if await send_fallback():
                    return
                update(status="error", error="файл не доступен")
                return
            if await send_fallback():
                return
            update(status="error", error="файл не доступен")
    except Exception as e:  # noqa: BLE001
        # полный трейс в логи: str(e) в job-статусе часто лишь «rolled back»,
        # а исходное место падения видно только здесь
        log.exception("deliver job %s упал (track %s):", job_id, track_id)
        # часто тут TelegramEntityTooLarge (>50 MB) — превью-файл крошечный,
        # его отправка всё равно реально сработает
        try:
            if bot is not None and await send_fallback():
                return
        except Exception:
            pass
        update(status="error", error=str(e)[:200])
    finally:
        _INFLIGHT.pop(key, None)
        _touch(st)
        _prune_jobs()

@router.get("/jobs/{job_id}")
async def job_status(job_id: str, user: CurrentUser = Depends(tg_user)):
    _prune_jobs()
    status = JOB_STATUS.get(job_id)
    if status is not None:
        if int(status.get("user", -1)) != int(user.id):
            # Чужой job неотличим от несуществующего: не раскрываем id.
            raise HTTPException(404, "нет такой задачи")
        return _public_job(status)
    # APP-роль: джобы очереди живут в Neon (созданы этим же/другим
    # инстансом, исполняются воркерами или локальным резервом).
    if music_settings.media_role == "app":
        async with runtime.get_session() as s:
            job = await mediajobs.get_job(s, job_id)
        if job and job.uid == int(user.id):
            return mediajobs.public_view(job)
    raise HTTPException(404, "нет такой задачи")

# ---------------- потоковый плеер ----------------
_FILE_PATH_CACHE: dict[int, tuple[str, float]] = {}    # track_id -> (tg file_path, expire)
_AUDIO_URL_CACHE: dict[tuple[int, int], tuple[tuple[str, str, bool], float]] = {}  # (uid, track_id) -> (audio, expire)


async def stream_user(
        request: Request,
        track_id: int,
        t: str = "",
        x_init_data: str = Header(default=""),
        authorization: str = Header(default="")):
    """Для <audio>: либо токен именно для этого трека, либо обычные заголовки."""
    if t:
        uid = check_stream_token(t, expected_track_id=track_id)
        if uid is not None:
            return CurrentUser(id=uid, username=None, first_name="player")
    # tg_user синхронный (def, не async) — вызов без await; await здесь ломал
    # каждый запрос с просроченным/невалидным ?t= в 500 вместо 401
    return tg_user(init_data=x_init_data, authorization=authorization)


@router.get("/player/{track_id}")
async def player_src(track_id: int, user: CurrentUser = Depends(tg_user)):
    """Лёгкий JSON со ссылкой на поток (нативное воспроизведение, без blob).
    kind подсказывает UI, что играть: file (скачано/ТГ) | online (полный)
    | preview (30-сек превью — не нашлось полной версии)."""
    async with runtime.get_session() as s:
        t = await catalog.get_track(s, track_id)
        if not t:
            raise HTTPException(404, "нет такого трека")
        kind = "file" if (t.file_id or t.local_path) else "online"
        resolved = None
        if kind == "online":
            # резолвим ЗДЕСЬ (сессия нужна ym_source): прогрев кеша до первого
            # Range от <audio> + бейдж в UI
            resolved = await _resolve_online_audio(track_id, t, s, int(user.id))
            if resolved and resolved[1] == "preview":
                kind = "preview"
    return {"url": f"/api/stream/{track_id}?t={make_stream_token(int(user.id), track_id)}",
            "kind": kind}


async def _tg_file_path(track_id: int, file_id: str) -> str | None:
    """file_path из Telegram с коротким кэшем (не дёргаем get_file на каждом Range)."""
    hit = _FILE_PATH_CACHE.get(track_id)
    if hit and hit[1] > _time.time():
        return hit[0]
    from bot.main import create_bot
    bot = create_bot()
    try:
        f = await bot.get_file(file_id)
        fp = f.file_path
    finally:
        await bot.session.close()
    if fp:
        _FILE_PATH_CACHE[track_id] = (fp, _time.time() + 45 * 60)
    return fp


async def _resolve_online_audio(track_id: int, t: Track,
                                session=None, user_id: int | None = None) -> tuple[str, str] | None:
    """Аудио для нескачанных треков. ПРИОРИТЕТ — ПОЛНЫЙ звук:
    Яндекс.Музыка (если юзер дал /token — весь каталог)
    → zaycev.net (mp3-320, Range) → mp3party → hitmoz → hotplayer
    → YouTube (InnerTube → yt-dlp). Ничего не нашлось — быстрый фолбэк
    на 30-сек превью (Deezer → iTunes, ~1 с). Возвращает (url, kind)."""
    # Яндекс-ссылки могут зависеть от личного токена; кеш разделён по
    # пользователю, чтобы один аккаунт не получил URL, найденный для другого.
    cache_key = (int(user_id or 0), int(track_id))
    hit = _AUDIO_URL_CACHE.get(cache_key)
    if hit and hit[1] > _time.time():
        return hit[0]

    async def ym_source(query: str) -> tuple[str, str, bool] | None:
        """Полный трек с Яндекс.Музыки. Третий элемент — флаг 18+ найденного
        кандидата: такие фонограммы у ЯМ бывают «чистыми» (слова замазаны)."""
        if session is None or user_id is None:
            return None
        try:
            from services.authstore import get_token
            tok = await get_token(session, int(user_id), "ym")
            if not tok:
                return None
            from services.providers import yandex as _ya
            # Быстрый путь треков волны (движок A): source=yandex + известный
            # external_id — резолвим сразу по ID, без поиска и best_match.
            if (t.source or "") == "yandex" and t.external_id:
                try:
                    m = await _ya.track_by_id(tok, t.external_id)
                    if m:
                        # ЯМ подтвердил 18+ у строки без флага — пометим БД:
                        # и скачивание, и будущие проигрыши пойдут мимо ЯМ
                        expl_id = bool(getattr(m, "explicit", False))
                        if expl_id and not bool(getattr(t, "explicit", False)):
                            try:
                                t.explicit = True
                                await session.commit()
                            except Exception:
                                pass
                            # 18+ появился после того, как кеш уже выдал
                            # URL «чистой» версии — сбрасываем, чтобы
                            # следующий резолв пошёл в прямые источники
                            _AUDIO_URL_CACHE.pop(cache_key, None)
                        url = await _ya.YandexProvider(tok).track_url(m, "")
                        if url:
                            log.info("онлайн-звук «%s» → yandex-ID %s (точный, 18+: %s)",
                                     query, t.external_id, expl_id)
                            return url, "yandex", expl_id
                except Exception as e:
                    log.info("онлайн-звук yandex-ID %s не удался: %s",
                             t.external_id, type(e).__name__)
            from services.matching import best_match
            from services.ingest import _clean_query
            meta = TrackMeta(source="yandex", external_id="", title=t.title or "",
                             artist=t.artist or "", duration=int(t.duration or 0))
            cands = await _ya.YandexProvider(tok).search(_clean_query(query), limit=8)
            m = best_match(meta, cands, min_score=0.5)
            if not m:
                return None
            expl18 = bool(getattr(m, "explicit", False))
            if expl18 and not bool(getattr(t, "explicit", False)):
                # старая строка БД без флага — пометим, чтобы и скачивание,
                # и последующие проигрыши получали приоритет оригиналов
                try:
                    t.explicit = True
                    await session.commit()
                except Exception:
                    pass
                _AUDIO_URL_CACHE.pop(cache_key, None)
            url = await _ya.YandexProvider(tok).track_url(m, "")  # 320/aac — стримабельно
            if url:
                log.info("онлайн-звук «%s» → yandex: %s (18+: %s)",
                         query, m.display(), expl18)
                return url, "yandex", expl18
        except Exception as e:
            log.info("онлайн-звук yandex не удался: %s", type(e).__name__)
        return None

    async def direct_sources(query: str) -> tuple[str, str] | None:
        """Полные треки с прямых сайтов (mp3, Range-стрим):
    zaycev → mp3party → JioSaavn → soundcloud → hitmoz → hotplayer → audius.
    JioSaavn работает с любых IP (полный 320kbps-каталог), audius — международный
    открытый; оба закрывают треки, которых нет/недоступен на RU-пиратках."""
        from services.matching import best_match
        from services.ingest import _clean_query, _title_sim
        from services.providers import audius as aud
        from services.providers import hitmoz as hzm
        from services.providers import hotplayer as hpl
        from services.providers import mp3party as mpp
        from services.providers import saavn as svn
        from services.providers import soundcloud as scc
        from services.providers import zaycev as zay
        meta = TrackMeta(source="zaycev", external_id="", title=t.title or "",
                         artist=t.artist or "", duration=int(t.duration or 0))
        cquery = _clean_query(query)
        for prov, name in ((zay, "zaycev"), (mpp, "mp3party"),
                           (svn, "saavn"), (scc, "soundcloud"),
                           (hzm, "hitmoz"), (hpl, "hotplayer"),
                           (aud, "audius")):
            try:
                cands = await prov.search(cquery, limit=6)
                if not cands:
                    continue
                m = best_match(meta, cands, min_score=0.5)
                # guard по артисту обязателен — сайты отдают тёзок-однофамильцев;
                # если матч слабый, допускаем первого результата только с чистым артистом
                if m and not hzm.artist_guard(meta, m):
                    m = None
                if not m and hzm.artist_guard(meta, cands[0]):
                    m = cands[0]
                if m and not m.duration and _title_sim(meta.title, m.title) < 0.6:
                    # источники без длительности — доверяем только точному названию
                    m = None
                if not m:
                    continue
                if name == "soundcloud":
                    # HLS-плейлист fMP4 — браузер его не играет, отдаём как
                    # спец-маркер: стрим-роут транскодирует ffmpeg'ом на лету
                    got = await scc.hls_stream(m.external_id)
                    if got:
                        log.info("онлайн-звук «%s» → soundcloud (hls): %s",
                                 cquery[:44], m.display())
                        return f"sc-hls:{got}", "soundcloud", False
                    continue
                url = await prov.download_url(m)
                if url:
                    log.info("онлайн-звук «%s» → %s: %s", cquery, name, m.display())
                    return url, name, False
            except Exception as e:
                log.info("онлайн-звук %s не удался: %s", name, type(e).__name__)
        return None

    q = f"{t.artist or ''} {t.title or ''}".strip()
    full = None
    explicit = bool(getattr(t, "explicit", False))
    if q:
        # параллельно ЯМ и прямые сайты; таймауты раздельны — зависший ЯМ больше
        # не выбрасывает готовые результаты zaycev (раньше умирал общий gather)
        t_ym = asyncio.create_task(ym_source(q))
        t_dir = asyncio.create_task(direct_sources(q))
        # ЯМ снова ПЕРВЫМ и для 18+ (проверено ASR: ЯМ отдаёт оригиналы без
        # цензуры — «Мама, я люблю» Anacondaz содержит мат целиком). Прежняя
        # политика «18+ → мимо ЯМ» давала кривые рипы с прямых сайтов —
        # именно их пользователь слышал как «блюр».
        # Замученные заливки (редкость) ловит фоновый censor-scan ниже:
        # файл помечается censored=True, дальше трек идёт мимо ЯМ.
        if getattr(t, "censored", False):
            # уже доказано замученная ЯМ-заливка — сразу прямые сайты
            t_ym.cancel()
            try:
                full = await asyncio.wait_for(t_dir, timeout=32)
            except Exception:
                full = None
        else:
            try:
                full = await asyncio.wait_for(asyncio.shield(t_ym), timeout=13)
            except Exception:
                full = None
            if full:
                if not t_dir.done():
                    t_dir.cancel()
            else:
                t_ym.cancel()
                log.info("онлайн-звук: ЯМ не дал результата (%s) — фолбэк на прямые сайты",
                         q[:46])
                try:
                    full = await asyncio.wait_for(t_dir, timeout=32)
                except Exception:
                    full = None
    if not full:
        # YouTube — InnerTube (быстро) → yt-dlp как запасной
        try:
            vid = t.external_id if (t.source or "") == "ytmusic" else ""
            url = await _yt_audio_url(vid=vid, query=q)
            if url:
                full = (url, "youtube", False)
        except Exception:
            full = None
    if not full:
        # превью-фолбэк: мгновенный старт, но 30 секунд
        dz, it = await asyncio.gather(_deezer_preview(t), _itunes_preview(t),
                                      return_exceptions=True)
        url = (dz if isinstance(dz, str) else None) or (it if isinstance(it, str) else None)
        if url:
            full = (url, "preview", False)
    if full and full[1] != "yandex":
        _AUDIO_URL_CACHE[cache_key] = (full, _time.time() + 35 * 60)
    if full and full[1] == "yandex":
        # фоновый censor-scan: скачаем файл и проверим детектором (бипы/
        # муты/замазки). Чистый файл — вердикт в кеш, замученный — пометим
        # censored=True и сбросим URL-кеш: следующие проигрыши пойдут мимо ЯМ.
        _spawn_censor_scan(int(t.id), full[0], cache_key)
    return full


_CENSOR_SCANNED: set[int] = set()


def _spawn_censor_scan(track_id: int, url: str, cache_key: tuple[int, int]) -> None:
    if track_id in _CENSOR_SCANNED:
        return
    _CENSOR_SCANNED.add(track_id)

    async def _job() -> None:
        import tempfile
        try:
            from services.net import httpx_client
            async with httpx_client(timeout=120, follow_redirects=True) as c:
                r = await c.get(url)
            if len(r.content) < 100000:
                return
            from services import censor as _censor
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                f.write(r.content)
                path = f.name
            try:
                chk = await _censor.check(path)
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass
            if chk.get("censored"):
                from core.db import AsyncSessionLocal
                from core.models import Track as _T
                async with AsyncSessionLocal() as s:
                    tr = await s.get(_T, track_id)
                    if tr is not None and not getattr(tr, "censored", False):
                        tr.censored = True
                        await s.commit()
                _AUDIO_URL_CACHE.pop(cache_key, None)
                log.info("censor-scan: трек %s — замученная ЯМ-заливка "
                         "(бипы=%s муты=%s), помечен censored",
                         track_id, chk.get("beeps"), chk.get("mutes"))
        except Exception:
            log.exception("censor-scan упал (трек %s)", track_id)
        finally:
            _CENSOR_SCANNED.discard(track_id)

    asyncio.create_task(_job())


async def _deezer_preview(t: Track) -> str | None:
    """Анонимное превью (30 сек) с Deezer — работает без токенов и без VPN."""
    from services.preview import deezer_preview
    return await deezer_preview(t.artist or "", t.title or "")


async def _itunes_preview(t: Track) -> str | None:
    """30-сек превью из iTunes Search API (CDN Apple, без токенов)."""
    from services.preview import itunes_preview
    return await itunes_preview(t.artist or "", t.title or "")


async def _yt_audio_url(vid: str, query: str = "") -> str | None:
    """Аудио-URL конкретного видео (или результат InnerTube-поиска):
    InnerTube (быстро, без yt-dlp) → yt-dlp (с прокси при необходимости)."""
    if vid:
        try:
            from services.providers import yt_stream
            got = await yt_stream.audio_url(vid)
            if got:
                return got["url"]
        except Exception:
            pass
    if not vid and query:
        # InnerTube-поиск → player: работает с датацентровых IP (Render),
        # yt-dlp там часто ловит бот-чек YouTube
        try:
            from services.providers import yt_stream as _yts
            found = await asyncio.wait_for(_yts.search_video_id(query), timeout=25)
            if found:
                got = await _yts.audio_url(found)
                if got:
                    log.info("онлайн-звук youtube «%s» (innertube, %s)",
                             query[:60], got.get("client"))
                    return got["url"]
        except Exception:
            pass
    from services.net import yt_dlp_binary, yt_dlp_proxy_args
    target = f"https://www.youtube.com/watch?v={vid}" if vid else f"ytsearch1:{query}"
    cmd = [yt_dlp_binary(), "-f", "bestaudio/best", "--no-playlist", "--no-warnings",
           "--quiet", "--dump-json", *yt_dlp_proxy_args(), target]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=70)
        except asyncio.TimeoutError:
            proc.kill()
            return None
        if proc.returncode != 0:
            return None
        d = _json.loads(out.decode(errors="ignore").splitlines()[0])
    except Exception:
        return None

    def direct(f: dict) -> bool:
        proto = (f.get("protocol") or "")
        u = (f.get("url") or "").lower()
        return bool(u) and u.startswith("http") and "m3u8" not in proto and "mpd" not in proto

    best = d.get("url")
    if not direct({"url": best, "protocol": d.get("protocol")}):
        cands = [f for f in (d.get("formats") or []) if direct(f)]
        ac = [f for f in cands if f.get("vcodec") in (None, "none") and f.get("acodec") not in (None, "none")]
        pool = ac or [f for f in cands if f.get("vcodec") in (None, "none")] or cands
        if pool:
            pool.sort(key=lambda f: (f.get("abr") or 0) or (f.get("tbr") or 0), reverse=True)
            best = pool[0]["url"]
    return best or None


@router.get("/stream/{track_id}")
async def stream(track_id: int,
                 request: Request,
                 t: str = "",
                 user: CurrentUser = Depends(stream_user)):
    """Нативный поток для плеера: локальный файл → Telegram CDN (Range-проброс)
    → онлайн-фолбэк YouTube для нескачанных треков."""
    rng = request.headers.get("range")

    async with runtime.get_session() as s:
        tr = await catalog.get_track(s, track_id)
        if not tr:
            raise HTTPException(404, "нет такого трека")

        # 1) локально лежащий файл
        if tr.local_path:
            from pathlib import Path
            p = Path(tr.local_path)
            if p.exists():
                total = p.stat().st_size
                if rng:
                    a, b = (rng.split("bytes=")[-1].split("-") + [None])[:2]
                    start = int(a or 0)
                    end = min(int(b), total - 1) if b else total - 1
                    if end < start or start >= total:
                        from fastapi.responses import Response
                        return Response(content=b"", status_code=416, headers={
                            "Content-Range": f"bytes */{total}"})
                    return StreamingResponse(_file_slice_gen(p, start, end),
                                             status_code=206, media_type="audio/mpeg",
                                             headers={"Accept-Ranges": "bytes",
                                                      "Content-Range": f"bytes {start}-{end}/{total}",
                                                      "Content-Length": str(end - start + 1)})
                return FileResponse(p, media_type="audio/mpeg",
                                    headers={"Accept-Ranges": "bytes"})

        # 2) файл в Telegram CDN — реле с зеркалом Content-Length (важно для туннеля)
        if tr.file_id:
            fp = await _tg_file_path(track_id, tr.file_id)
            if fp:
                base = f"https://api.telegram.org/file/bot{settings.BOT_TOKEN}/"
                from urllib.parse import urljoin
                url = urljoin(base, fp)
                return await relay_response(url, rng, settings.PROXY_URL or None)

        # 3) не скачан — ПОЛНЫЙ звук онлайн (YM → zaycev → mp3party → SC → hitmoz
        #    → hotplayer → YT), иначе 30-сек превью; чужой CDN стримим через relay
        resolved = await _resolve_online_audio(track_id, tr, s, int(user.id))
        if resolved:
            u = str(resolved[0])
            if u.startswith("sc-hls:"):
                # SoundCloud HLS (fMP4): ffmpeg транскодирует в mp3 на лету
                return StreamingResponse(
                    _sc_hls_iter(u.split(":", 1)[1]),
                    media_type="audio/mpeg",
                    headers={"Cache-Control": "no-store"})
            return await relay_response(resolved[0], rng, settings.PROXY_URL or None)

        raise HTTPException(404, "трек недоступен онлайн — нажми «Скачать»")


async def _sc_hls_iter(playlist_url: str):
    """Поток fMP4→mp3: ffmpeg читает m3u8 SC и конвертит в pipe:1.
    Без Content-Length/Seek (браузер играет с начала); при обрыве клиента
    процесс убивается."""
    import asyncio as aio
    import os

    proc: aio.subprocess.Process | None = None
    try:
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error",
               "-user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
               "-protocol_whitelist", "http,https,tls,tcp,applehttps,httpproxy",
               "-multiple_requests", "1",
               "-i", playlist_url,
               "-vn", "-map", "0:a", "-c:a", "libmp3lame", "-b:a", "160k",
               "-f", "mp3", "pipe:1"]
        env = dict(os.environ)
        if settings.PROXY_URL:
            env["http_proxy"] = env["https_proxy"] = settings.PROXY_URL
        proc = await aio.create_subprocess_exec(
            *cmd,
            stdout=aio.subprocess.PIPE, stdin=aio.subprocess.DEVNULL,
            stderr=aio.subprocess.DEVNULL, env=env)
        assert proc.stdout
        while True:
            chunk = await proc.stdout.read(1 << 15)
            if not chunk:
                break
            yield chunk
    finally:
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass


@router.get("/diag/hls")
async def diag_hls(url: str, mode: str = "m3u8",
                   user: CurrentUser = Depends(tg_user)):
    """Отладка SoundCloud: mode=m3u8 → свежий плейлист; mode=trans → список
    transcodings с прод-IP (есть ли progressive вне FairPlay-схемы)."""
    from services.providers import soundcloud as scc

    if mode == "trans":
        res = await asyncio.to_thread(scc._default._trans_sync, url)
        return res
    playlist = await scc.hls_stream(url)
    if not playlist:
        return {"ok": False, "reason": "hls_stream вернул None"}
    return {"ok": True, "m3u8": playlist}


@router.get("/diag/sources")
async def diag_sources(q: str = "баста сансара", user: CurrentUser = Depends(tg_user)):
    """Проверка источников с IP продакшна: поиск → ссылка → реальные байты."""
    import httpx
    from services.providers import hitmoz as hmz
    from services.providers import hotplayer as hpl
    from services.providers import zaycev as zyc

    out: dict = {}
    for name, prov in (("zaycev", zyc), ("hitmoz", hmz), ("hotplayer", hpl)):
        t0 = _time.time()
        try:
            cands = await prov.search(q, limit=3)
            row: dict = {"search_s": round(_time.time() - t0, 2), "found": len(cands),
                         "first": (f"{cands[0].artist} — {cands[0].title}"
                                   if cands else None)}
            if cands:
                t1 = _time.time()
                url = await prov.download_url(cands[0])
                row["url_s"] = round(_time.time() - t1, 2)
                if url:
                    try:
                        async with httpx.AsyncClient(timeout=25,
                                                     follow_redirects=True) as c:
                            r = await c.get(url, headers={
                                "Range": "bytes=0-65535",
                                "User-Agent": "Mozilla/5.0"})
                        row["mp3"] = {"status": r.status_code, "bytes": len(r.content),
                                      "type": r.headers.get("content-type")}
                    except Exception as e:
                        row["mp3"] = f"{type(e).__name__}: {str(e)[:60]}"
                else:
                    row["mp3"] = "no url"
            out[name] = row
        except Exception as e:
            out[name] = {"error": f"{type(e).__name__}: {str(e)[:80]}",
                         "search_s": round(_time.time() - t0, 2)}

    # SoundCloud: поиск → HLS-плейлист → первый сегмент (fMP4)
    try:
        from services.providers import soundcloud as sccp
        t0 = _time.time()
        cands = await sccp.search(q, limit=3)
        sc_row: dict = {"search_s": round(_time.time() - t0, 2),
                        "found": len(cands)}
        if cands:
            m = cands[0]
            sc_row["first"] = f"{m.artist} — {m.title}"
            t1 = _time.time()
            got = await sccp.hls_stream(m.external_id)
            sc_row["hls_s"] = round(_time.time() - t1, 2)
            if got:
                async with httpx.AsyncClient(timeout=25,
                                             follow_redirects=True) as c3:
                    r3 = await c3.get(got, headers={"User-Agent": "Mozilla/5.0"})
                segs = [l for l in r3.text.splitlines()
                        if l and not l.startswith("#")]
                sc_row["m3u8"] = r3.status_code
                sc_row["segs"] = len(segs)
                if segs:
                    try:
                        async with httpx.AsyncClient(
                                timeout=25,
                                follow_redirects=True) as c4:
                            r4 = await c4.get(segs[0], headers={
                                "User-Agent": "Mozilla/5.0",
                                "Range": "bytes=0-8191"})
                        sc_row["seg1"] = {"status": r4.status_code,
                                          "bytes": len(r4.content),
                                          "magic": r4.content[:4].hex()}
                    except Exception as e:
                        sc_row["seg1"] = f"{type(e).__name__}: {str(e)[:50]}"
            else:
                sc_row["m3u8"] = None
        out["soundcloud"] = sc_row
    except Exception as e:
        out["soundcloud"] = {"error": f"{type(e).__name__}: {str(e)[:80]}"}

    for name, url, params in (
            ("deezer", "https://api.deezer.com/search", {"q": q, "limit": 1}),
            ("itunes", "https://itunes.apple.com/search",
             {"term": q, "media": "music", "entity": "song", "limit": 1})):
        t0 = _time.time()
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get(url, params=params)
            body = r.json()
            has = bool((body.get("data") or body.get("results") or [{}])[0])
            out[name] = {"status": r.status_code, "has_match": has,
                         "s": round(_time.time() - t0, 2)}
        except Exception as e:
            out[name] = {"error": type(e).__name__,
                         "s": round(_time.time() - t0, 2)}

    # YouTube: InnerTube play-проба известного видео + поиск по запросу
    t0 = _time.time()
    try:
        from services.providers import yt_stream
        got = await asyncio.wait_for(yt_stream.audio_url("jNQXAC9IVRw"), timeout=40)
        yrow: dict = {"s": round(_time.time() - t0, 2)}
        if got and got.get("url"):
            yrow["player"] = f"ok client={got.get('client')} itag={got.get('itag')}"
            try:
                async with httpx.AsyncClient(timeout=20) as c:
                    r = await c.get(got["url"], headers={"Range": "bytes=0-65535"})
                yrow["bytes"] = {"status": r.status_code, "n": len(r.content),
                                 "type": r.headers.get("content-type")}
            except Exception as e:
                yrow["bytes"] = f"{type(e).__name__}: {str(e)[:60]}"
        else:
            yrow["player"] = "no_url"
        t1 = _time.time()
        srch = await asyncio.wait_for(yt_stream.search_video_id(q), timeout=30)
        yrow["search"] = {"found": bool(srch), "vid": srch or None,
                          "s": round(_time.time() - t1, 2)}
        if srch:
            g2 = await asyncio.wait_for(yt_stream.audio_url(srch), timeout=40)
            yrow["search"]["audio"] = "ok" if (g2 and g2.get("url")) else "no_url"
        out["youtube"] = yrow
    except Exception as e:
        out["youtube"] = {"error": type(e).__name__,
                          "s": round(_time.time() - t0, 2)}

    # Яндекс.Музыка: токен + живой пробинг поиска/ссылки/байтов
    try:
        from services.authstore import get_token
        from services.providers.yandex import YandexProvider as _Ymx
    except Exception as e:                                     # noqa: BLE001
        out["yandex"] = {"error": type(e).__name__}
    else:
        try:
            async with runtime.get_session() as s:
                ym_tok = await get_token(s, int(user.id), "ym")
            yrow: dict = {"token": bool(ym_tok)}
            if ym_tok:
                t0 = _time.time()
                res = await asyncio.wait_for(_Ymx(ym_tok).search(q[:80], limit=3),
                                             timeout=35)
                yrow["search"] = {
                    "s": round(_time.time() - t0, 2), "found": len(res),
                    "first": (f"{res[0].artist} — {res[0].title}" if res else None)}
                # RAW-пробинг того же запроса без библиотеки (aiohttp глотает тела)
                try:
                    async with httpx.AsyncClient(timeout=20) as c0:
                        r0 = await c0.get(
                            "https://api.music.yandex.ru/search",
                            params={"text": q[:64], "type": "track",
                                    "nocorrect": "false", "page": "0"},
                            headers={"Authorization": "OAuth " + ym_tok,
                                     "User-Agent": "Yandex-Music-API"})
                        body = r0.json()
                        res_j = body.get("result") or {}
                        tr_j = (res_j.get("tracks") or {}).get("results") or []
                        yrow["raw"] = {
                            "status": r0.status_code, "tracks": len(tr_j),
                            "err": res_j.get("error"),
                            "body_head": ("" if tr_j else r0.text[:170])}
                except Exception as e:          # noqa: BLE001
                    yrow["raw"] = f"{type(e).__name__}: {str(e)[:80]}"
                if res:
                    t1 = _time.time()
                    u = await asyncio.wait_for(_Ymx(ym_tok).track_url(res[0], ""),
                                               timeout=35)
                    yrow["url_s"] = round(_time.time() - t1, 2)
                    if u:
                        try:
                            async with httpx.AsyncClient(timeout=20) as c:
                                r = await c.get(u, headers={
                                    "Range": "bytes=0-65535",
                                    "User-Agent": "Yandex-Music-API",
                                    "Referer": "https://music.yandex.ru/"})
                            yrow["mp3"] = {"status": r.status_code,
                                           "bytes": len(r.content),
                                           "type": r.headers.get("content-type")}
                        except Exception as e:
                            yrow["mp3"] = f"{type(e).__name__}: {str(e)[:70]}"
                    else:
                        yrow["mp3"] = "no url"
            out["yandex"] = yrow
        except Exception as e:                                 # noqa: BLE001
            out["yandex"] = {"error": f"{type(e).__name__}: {str(e)[:120]}"}

    # карта доступности сайтов-аналогов с IP продакшна (параллельно)
    from urllib.parse import quote as _q

    async def _probe(name: str, url: str):
        t0 = _time.time()
        try:
            async with httpx.AsyncClient(
                    timeout=8, follow_redirects=True,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                           "AppleWebKit/537.36 Chrome/126.0"}) as c:
                r = await c.get(url)
            text = r.text or ""
            return name, {"status": r.status_code, "len": len(text),
                          "s": round(_time.time() - t0, 2)}
        except Exception as e:
            return name, {"error": type(e).__name__,
                          "s": round(_time.time() - t0, 2)}

    sites = {
        "mp3party": "https://mp3party.net/search?q=" + _q(q),
        "get-tune": "https://get-tune.cc/search/?q=" + _q(q),
        "sefon": "https://sefon.pro/search/?q=" + _q(q),
        "z1": "https://z1.fm/search/" + _q(q),
        "musify": "https://musify.club/search?q=" + _q(q),
        "muzofond": "https://muzofond.fm/search/" + _q(q),
        "hotplayer": "https://hotplayer.ru/?s=" + _q(q),
        "mp3xa": "https://mp3xa.me/search?q=" + _q(q),
    }
    out["sites"] = dict(await asyncio.gather(
        *[_probe(n, u) for n, u in sites.items()]))
    return out


async def relay_response(url: str, rng: str | None, proxy: str | None):
    """Стримим чужой статичный файл, ДО старта тела зная его длину:
    pinggy (и любой туннель) плохо живёт с chunked-ответами без Content-Length."""
    import httpx
    from urllib.parse import urlsplit
    host = urlsplit(url).netloc
    # браузерный вид обязателен: без него mp3-CDN отвечает 200 на весь файл
    # (Range игнорируется) — скраб будет качать файл целиком
    hdrs = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
            "Referer": f"https://{host}/"}
    if rng:
        hdrs["Range"] = rng
    cli = httpx.AsyncClient(timeout=httpx.Timeout(None, read=None, write=30, connect=30),
                            proxy=proxy, follow_redirects=True)
    up = await cli.send(cli.build_request("GET", url, headers=hdrs), stream=True)
    if up.status_code >= 400:
        await up.aclose()
        await cli.aclose()
        raise HTTPException(404, "источник аудио недоступен")
    out = {"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=600"}
    if up.headers.get("content-length"):
        out["Content-Length"] = up.headers["content-length"]
    if up.headers.get("content-range"):
        out["Content-Range"] = up.headers["content-range"]
    ct = up.headers.get("content-type") or "audio/mpeg"
    # строгие мобильные WebView (особенно iOS) отказываются декодировать
    # octet-stream как аудио: звук стартует и умирает при буферизации.
    # Источник отдаёт mp3 — приводим к честному аудио-типу.
    if "audio/" not in ct:
        ct = "audio/mpeg"

    async def gen():
        try:
            async for chunk in up.aiter_bytes(96 * 1024):
                yield chunk
        finally:
            await up.aclose()
            await cli.aclose()

    return StreamingResponse(gen(), status_code=up.status_code, media_type=ct, headers=out)


def _file_slice_gen(path, start: int, end: int):
    """Синхронный слайсер файла — starlette крутит его в threadpool."""
    with open(path, "rb") as f:
        f.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            chunk = f.read(min(96 * 1024, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
