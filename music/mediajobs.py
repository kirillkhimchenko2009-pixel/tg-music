"""Распределённая очередь скачиваний: тянущая модель через Neon (Релиз 1).

Роли серверов (env MEDIA_ROLE, см. music.config):
- `app` (Render): бот + мини-апп + стриминг. Сам не качает — вставляет
  джобы в media_download_jobs и опрашивает их статус. Локальный воркер
  Render остаётся последним резервом (:func:`start_local_reserve`):
  включается, если джоба висит 'new' дольше MEDIA_LOCAL_FALLBACK_SEC
  (основной воркер занят надолго или умер).
- `worker` (Botkeep/serv00, run_worker.py): цикл poll → claim → скачать
  (общий пайплайн services.ingest) → отправить в чат → file_id в Neon →
  удалить файл. Публичного HTTP у воркеров нет (serv00 принципиально),
  поэтому приоритет = интервал опроса: Botkeep 3–5 с забирает джобы
  первым, serv00 20–30 с подбирает только при загруженности/смерти
  основного. Никакой маршрутизации — спилловер получается сам.
- без env (`local`): прежнее поведение, модуль не задействован.

Атомарность claim — SELECT ... FOR UPDATE SKIP LOCKED + UPDATE в одной
транзакции: два воркера физически не могут взять одну джобу. SQLite
(тесты) FOR UPDATE не рендерим — однопоточный тест и так корректен.
TTL lease (MEDIA_LEASE_TTL_SEC) чинит смерть воркера: просроченная
аренда снова становится 'new', а при исчерпанных попытках — 'error'.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, update

from core.db import AsyncSessionLocal

from .config import music_settings
from .models import MediaDownloadJob, utcnow

log = logging.getLogger("music.mediajobs")

# Источники, чьё скачивание идёт через yt-dlp+ffmpeg (HLS → mp3).
# Воркер без ffmpeg такие джобы не берёт (caps-фильтр в claim),
# их отрабатывают воркеры с ffmpeg или локальный резерв Render.
FFMPEG_SOURCES = frozenset({"ytmusic", "soundcloud"})

_ACTIVE_STATUSES = ("new", "leased")

# Сильные ссылки на фоновые задачи (иначе GC может собрать задачу до конца)
_BG_TASKS: set[asyncio.Task] = set()


def needs_ffmpeg_for(source: str) -> bool:
    return (source or "").strip().lower() in FFMPEG_SOURCES


def candidate_meta(candidate) -> Any:
    """TrackCandidate (хаб бота) → TrackMeta каталога: у джобы очереди
    track_id-ориентированный контракт (как в веб-аппе), поэтому кандидат
    перед постановкой в очередь попадает в общий каталог (upsert_track)."""
    from services.providers.base import TrackMeta
    return TrackMeta(
        source=candidate.provider,
        external_id=str(candidate.external_id),
        title=candidate.title,
        artist=candidate.artist or "",
        album=candidate.album or None,
        year=candidate.year,
        isrc=candidate.isrc,
        duration=int(candidate.duration or 0),
        cover_url=candidate.cover_url,
        explicit=bool(candidate.explicit),
        page_url=candidate.source_url or None,
        license=candidate.license_name or None,
    )


def new_job_id() -> str:
    return uuid.uuid4().hex[:16]


def _bg(coro) -> None:
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)


# ----------------------------- CRUD джоб -----------------------------

async def create_job(session, *, track_id: int, uid: int, quality: str,
                     source: str) -> MediaDownloadJob:
    """Новая джоба очереди. Дедуп (уже активная джоба) — вызывающий код
    через :func:`find_active`, как и в локальном пути /download."""
    job = MediaDownloadJob(id=new_job_id(), track_id=track_id, uid=uid,
                           quality=quality or "",
                           needs_ffmpeg=needs_ffmpeg_for(source))
    session.add(job)
    await session.flush()
    return job


async def find_active(session, track_id: int, uid: int) -> MediaDownloadJob | None:
    """Активная джоба этого трека/юзера (дедуп повторных нажатий)."""
    q = (select(MediaDownloadJob)
         .where(MediaDownloadJob.track_id == track_id,
                MediaDownloadJob.uid == uid,
                MediaDownloadJob.status.in_(_ACTIVE_STATUSES))
         .order_by(MediaDownloadJob.created_at.asc())
         .limit(1))
    return (await session.execute(q)).scalar_one_or_none()


async def count_active(session, uid: int) -> int:
    """Активные джобы юзера — тот же лимит, что и локальный
    WEBAPP_MAX_ACTIVE_DOWNLOADS, иначе лавина параллельных скачиваний."""
    q = (select(func.count())
         .select_from(MediaDownloadJob)
         .where(MediaDownloadJob.uid == uid,
                MediaDownloadJob.status.in_(_ACTIVE_STATUSES)))
    return int((await session.execute(q)).scalar() or 0)


async def get_job(session, job_id: str) -> MediaDownloadJob | None:
    return await session.get(MediaDownloadJob, job_id)


async def cancel_jobs(session, job_ids) -> int:
    """Отмена батча: только ещё не забранные ('new') джобы → 'canceled'.
    Leased доделывает воркер (файл уже в пути), done/error — терминальные.
    Возвращает число отменённых."""
    ids = [j for j in job_ids if j]
    if not ids:
        return 0
    res = await session.execute(
        update(MediaDownloadJob)
        .where(MediaDownloadJob.id.in_(ids),
               MediaDownloadJob.status == "new")
        .values(status="canceled", lease_host=None, lease_ts=None,
                updated_at=utcnow()))
    await session.commit()
    return res.rowcount or 0


async def statuses_of(session, job_ids) -> dict[str, str]:
    """id → статус одним запросом (батч-прогресс без N отдельных чтений)."""
    ids = [j for j in job_ids if j]
    if not ids:
        return {}
    rows = (await session.execute(
        select(MediaDownloadJob.id, MediaDownloadJob.status)
        .where(MediaDownloadJob.id.in_(ids)))).all()
    return {jid: st for jid, st in rows}


async def oldest_new_age_sec(session) -> float:
    """Сколько секунд старейшая 'new' джоба ждёт (диагностика/резерв)."""
    row = (await session.execute(
        select(MediaDownloadJob.created_at)
        .where(MediaDownloadJob.status == "new")
        .order_by(MediaDownloadJob.created_at.asc())
        .limit(1))).scalar_one_or_none()
    if row is None:
        return 0.0
    return max(0.0, (utcnow() - row).total_seconds())


def _dialect_allows_skip_locked(session) -> bool:
    """SKIP LOCKED — только Postgres (Neon). SQLite тестов FOR UPDATE
    не умеет: SQLAlchemy бы его отрендерил и уронил запрос."""
    ss = getattr(session, "sync_session", None)
    bind = getattr(ss, "bind", None) if ss is not None else None
    if bind is None:
        return True  # прод — Postgres/Neon; тестовый SQLite всегда с bind
    return bind.dialect.name != "sqlite"


async def claim(session, host: str, *, caps_ffmpeg: bool = True,
                min_age_sec: float = 0.0, now=None) -> MediaDownloadJob | None:
    """Забрать старейшую 'new' джобу в lease (атомарно).

    - caps_ffmpeg=False (воркер без ffmpeg) — джобы needs_ffmpeg не видимы;
    - min_age_sec>0 — только джобы, ждущие дольше указанного (локальный
      резерв Render не отнимает работу у живых воркеров);
    - FOR UPDATE SKIP LOCKED: конкурентный воркер пропустит занятую строку.
    """
    now = now or utcnow()
    sub = (select(MediaDownloadJob.id)
           .where(MediaDownloadJob.status == "new"))
    if not caps_ffmpeg:
        sub = sub.where(MediaDownloadJob.needs_ffmpeg == False)  # noqa: E712
    if min_age_sec > 0:
        sub = sub.where(MediaDownloadJob.created_at <=
                        now - timedelta(seconds=min_age_sec))
    sub = sub.order_by(MediaDownloadJob.created_at.asc()).limit(1)
    if _dialect_allows_skip_locked(session):
        sub = sub.with_for_update(skip_locked=True)
    job_id = (await session.execute(sub)).scalar_one_or_none()
    if job_id is None:
        return None
    await session.execute(
        update(MediaDownloadJob)
        .where(MediaDownloadJob.id == job_id)
        .values(status="leased", lease_host=(host or "worker")[:64],
                lease_ts=now, attempts=MediaDownloadJob.attempts + 1,
                updated_at=now))
    await session.commit()
    job = await session.get(MediaDownloadJob, job_id)
    if job is not None:
        # ORM-update мог не синхронизировать identity-map: перечитываем
        await session.refresh(job)
    return job


async def release_stale(session, *, max_attempts: int | None = None,
                        now=None) -> int:
    """Просроченные lease (воркер умер): attempts исчерпаны → 'error',
    остальные — снова 'new'. Возвращает число затронутых джоб."""
    now = now or utcnow()
    cutoff = now - timedelta(seconds=music_settings.lease_ttl_sec)
    max_attempts = max_attempts or music_settings.media_max_attempts
    touched = 0
    res = await session.execute(
        update(MediaDownloadJob)
        .where(MediaDownloadJob.status == "leased",
               MediaDownloadJob.lease_ts.isnot(None),
               MediaDownloadJob.lease_ts < cutoff,
               MediaDownloadJob.attempts >= max_attempts)
        .values(status="error", error="воркер не уложился в lease (TTL)",
                updated_at=now))
    touched += res.rowcount or 0
    res2 = await session.execute(
        update(MediaDownloadJob)
        .where(MediaDownloadJob.status == "leased",
               MediaDownloadJob.lease_ts.isnot(None),
               MediaDownloadJob.lease_ts < cutoff)
        .values(status="new", lease_host=None, lease_ts=None, updated_at=now))
    touched += res2.rowcount or 0
    if touched:
        await session.commit()
    return touched


async def touch_progress(job_id: str, progress: float) -> None:
    """Прогресс + продление lease: долгое легитимное скачивание не должно
    попадать под release_stale (иначе TTL раз за разом отбирает джобу у
    живого воркера и сжигает попытки)."""
    now = utcnow()
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(MediaDownloadJob)
            .where(MediaDownloadJob.id == job_id)
            .values(progress=float(progress), lease_ts=now, updated_at=now))
        await session.commit()


async def mark_done(job_id: str, *, file_id: str = "", telegram: bool = True,
                    preview: bool = False) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(MediaDownloadJob)
            .where(MediaDownloadJob.id == job_id)
            .values(status="done", progress=100.0, file_id=file_id,
                    telegram=telegram, preview=preview, error=None,
                    updated_at=utcnow()))
        await session.commit()


async def mark_error(job_id: str, error: str, *, retry: bool) -> None:
    """retry=True — джоба возвращается в очередь (попытки ещё остались);
    retry=False — честная ошибка, веб-апп её покажет."""
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(MediaDownloadJob)
            .where(MediaDownloadJob.id == job_id)
            .values(status=("new" if retry else "error"),
                    lease_host=None, lease_ts=None,
                    error=(None if retry else (error or "ошибка")[:500]),
                    updated_at=utcnow()))
        await session.commit()


def public_view(job: MediaDownloadJob) -> dict[str, Any]:
    """Тот же контракт, что у локального /api/jobs/{id} (_public_job):
    веб-апп не различает, где исполняется джоба."""
    return {
        "status": {"new": "queued", "leased": "downloading"}.get(job.status, job.status),
        "progress": round(job.progress or 0.0, 1),
        "telegram": bool(job.telegram),
        "preview": bool(job.preview),
        "error": job.error or "",
    }


# --------------------------- исполнитель ---------------------------

_BOT = None


async def _bot():
    """Bot-инстанс для отправки файлов. В APP-процессе создаём свой
    (create_bot не поллит — конфликтов getUpdates нет), в воркере так же."""
    global _BOT
    if _BOT is None:
        from bot.main import create_bot
        _BOT = create_bot()
    return _BOT


async def _unlink(path) -> None:
    """Диск воркера 1–3 ГБ: файл удаляем сразу после отправки в чат."""
    try:
        await asyncio.to_thread(Path(path).unlink, True)
    except Exception as e:  # noqa: BLE001
        log.warning("mediajobs: не удалось удалить файл %s: %s", path, e)


async def execute(job: MediaDownloadJob) -> str:
    """Скачивание + доставка трека в чат (семантика _deliver_job из
    server/routes/tracks.py, но статус — в Neon вместо памяти процесса).

    Возвращает "done" (терминальное состояние) или "retry" (вернули в
    очередь — случился транзиентный сбой при незаконченных попытках).
    """
    bot = None
    uid = job.uid
    track_id = job.track_id
    quality = job.quality
    last_progress = job.progress or 0.0
    t = None

    def on_progress(p: float) -> None:
        nonlocal last_progress
        if p - last_progress >= 2.0 or p >= 100:
            last_progress = p
            _bg(touch_progress(job.id, p))

    async def send_fallback() -> bool:
        """В чат должен прийти хоть какой-то файл: 30-сек фрагмент."""
        if bot is None or t is None:
            return False
        from bot.runtime import send_preview_file
        ok = await send_preview_file(bot, uid, t)
        if ok:
            await mark_done(job.id, file_id="", telegram=True, preview=True)
        return ok

    try:
        # Ленивые импорты и Bot — ВНУТРИ try: любой их сбой обязан пометить
        # джобу (mark_error), а не оставлять её leased-зомби до TTL.
        from bot import runtime
        from services import catalog, ingest

        bot = await _bot()

        from services.catalog import meta_of
        async with runtime.get_session() as s:
            t = await catalog.get_track(s, track_id)
            if not t:
                await mark_error(job.id, "нет такого трека", retry=False)
                return "done"

            res = await ingest.resolve(s, meta_of(t), quality, user_id=uid,
                                       on_progress=on_progress)
            if res.status == "not_found" or not (res.file_path or res.track):
                if await send_fallback():
                    return "done"
                await mark_error(job.id, res.error or "не удалось скачать",
                                 retry=False)
                return "done"
            target = res.track or (await catalog.get_track(s, track_id))

            async def deliver_file(target_track, path, q) -> bool:
                """Полный файл в чат, file_id в Neon, файл с диска — прочь.
                Одна повторная попытка: разовый сетевой моргунок не должен
                откатывать полноценный файл к 30-сек фрагменту."""
                from bot.runtime import save_file_id_retry, send_audio_file
                fid = await send_audio_file(bot, uid, target_track, path)
                if not fid and bot is not None:
                    await asyncio.sleep(3)
                    fid = await send_audio_file(bot, uid, target_track, path)
                if not fid:
                    return False
                await mark_done(job.id, file_id=fid, telegram=True)
                # local_path воркера чужой для APP-хоста и сейчас файл будет
                # удалён: путь не сохраняем — стрим идёт через file_id.
                await save_file_id_retry(target_track.id, fid, q,
                                         size=path.stat().st_size,
                                         local_path=None)
                # Аудио-анализ под настроения волны (BPM/энергия/валентность/
                # танцевальность) — пока файл ещё на диске. Доставку не ломает:
                # ошибки глушим, трек уходит в чат в любом случае.
                try:
                    from services import audio_features
                    await audio_features.analyze_and_save(s, target_track, path)
                except Exception as e:       # noqa: BLE001
                    log.warning("audio_features: трек %s не проанализирован: %s: %s",
                                target_track.id, type(e).__name__, e)
                await _unlink(path)
                return True

            if res.file_path and target and res.file_path.exists():
                if await deliver_file(target, res.file_path, res.quality):
                    return "done"
                if await send_fallback():
                    return "done"
                # Нельзя писать done, если в чат реально ничего не ушло.
                await mark_error(job.id,
                                 "Telegram не принял файл (возможно, больше лимита)",
                                 retry=False)
                return "done"

            if target and target.file_id:
                from bot.runtime import send_track_cached
                if await send_track_cached(bot, uid, target):
                    await mark_done(job.id, file_id=target.file_id, telegram=True)
                    return "done"
                # file_id мог устареть (старый токен/другой бот): сначала
                # честная перекачка без кеша, только потом превью-фрагмент.
                try:
                    from bot.runtime import save_file_id_retry
                    await save_file_id_retry(target.id, "")
                    target.file_id = ""
                    await s.flush()
                except Exception:  # noqa: BLE001
                    log.warning("не удалось сбросить file_id трека %s", target.id)
                res2 = await ingest.resolve(s, meta_of(target), quality,
                                            user_id=uid, on_progress=on_progress,
                                            allow_cached=False)
                fresh = (res2.track or target) if res2 else None
                if (res2 and res2.file_path and fresh
                        and res2.file_path.exists()
                        and await deliver_file(fresh, res2.file_path, res2.quality)):
                    return "done"
                if await send_fallback():
                    return "done"
                await mark_error(job.id, "файл не доступен", retry=False)
                return "done"

            if await send_fallback():
                return "done"
            await mark_error(job.id, "файл не доступен", retry=False)
            return "done"
    except Exception as e:  # noqa: BLE001
        # полный трейс в логи: str(e) часто лишь «rolled back»
        log.exception("media job %s упал (track %s):", job.id, track_id)
        try:
            if bot is not None and await send_fallback():
                return "done"
        except Exception:  # noqa: BLE001
            pass
        retry = job.attempts < music_settings.media_max_attempts
        await mark_error(job.id, str(e)[:200], retry=retry)
        return "done" if not retry else "retry"


# ------------------------------ poller ------------------------------

class MediaJobPoller:
    """Серийный воркер очереди: release_stale → claim → execute.

    Строго одна джоба за раз: yt-dlp ест ~150 МБ, на 512 МБ хоста
    параллелить нельзя. Интервал опроса задаёт приоритет воркера.
    """

    def __init__(self, name: str | None = None, poll_sec: float | None = None,
                 caps_ffmpeg: bool | None = None, min_age_sec: float = 0.0,
                 stale_every: int = 20):
        self.name = name or music_settings.worker_name or "local"
        self.poll_sec = (poll_sec if poll_sec is not None
                         else music_settings.worker_poll_sec)
        self.caps_ffmpeg = (music_settings.worker_caps_ffmpeg
                            if caps_ffmpeg is None else caps_ffmpeg)
        self.min_age_sec = min_age_sec
        self._stale_every = max(1, stale_every)
        self._cycles = 0
        self._running = False
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run(),
                                         name=f"media-poller-{self.name}")

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                pass
            self._task = None

    async def run_forever(self) -> None:
        self._running = True
        await self._run()

    async def _run(self) -> None:
        log.info("media poller %s: старт (poll=%.0fs caps_ffmpeg=%s min_age=%.0fs)",
                 self.name, self.poll_sec, self.caps_ffmpeg, self.min_age_sec)
        try:
            while self._running:
                try:
                    outcome = await self.tick()
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001
                    log.warning("media poller %s: цикл упал: %s",
                                self.name, e, exc_info=True)
                    await asyncio.sleep(min(self.poll_sec * 2, 30))
                    continue
                # только что освободившийся воркер может сразу брать следующую;
                # после "retry"/пустого цикла — обычный интервал (и спилловер
                # не душит Neon: serv00 с 25 с делает ~120 запросов/час)
                await asyncio.sleep(0.2 if outcome == "done" else self.poll_sec)
        finally:
            log.info("media poller %s: стоп", self.name)

    async def tick(self) -> str:
        """Один цикл. Возвращает "done" | "retry" | "empty"."""
        async with AsyncSessionLocal() as session:
            self._cycles += 1
            if self._cycles % self._stale_every == 0:
                try:
                    await release_stale(session)
                except Exception as e:  # noqa: BLE001
                    log.warning("media poller %s: release_stale упал: %s",
                                self.name, e)
            job = await claim(session, self.name,
                              caps_ffmpeg=self.caps_ffmpeg,
                              min_age_sec=self.min_age_sec)
        if job is None:
            return "empty"
        log.info("media poller %s: джоба %s (track=%s uid=%s attempt=%s)",
                 self.name, job.id, job.track_id, job.uid, job.attempts)
        try:
            return await asyncio.wait_for(
                execute(job), timeout=music_settings.job_timeout_sec)
        except asyncio.TimeoutError:
            # Нормальное скачивание укладывается в минуты; вечное висение
            # (обычно Telegram-upload без таймеров) — аномалия: отменяем
            # и возвращаем джобу в очередь, пока попытки не кончились.
            log.warning("media poller %s: джоба %s не уложилась в %.0f с — отмена",
                        self.name, job.id, music_settings.job_timeout_sec)
            retry = job.attempts < music_settings.media_max_attempts
            await mark_error(job.id, "джоба не уложилась в таймаут", retry=retry)
            return "done" if not retry else "retry"
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            # execute уже обязан ловить своё, но дыра здесь = leased-зомби
            # до TTL. Падение до/в обход его mark_error гасим тут же.
            log.error("media poller %s: execute %s упал вне своей обработки:",
                      self.name, job.id, exc_info=True)
            retry = job.attempts < music_settings.media_max_attempts
            await mark_error(job.id, f"execute: {e}"[:200], retry=retry)
            return "done" if not retry else "retry"


# ------------------- локальный резерв APP-хоста -------------------

_reserve: MediaJobPoller | None = None


def start_local_reserve() -> None:
    """APP-роль (Render): свой воркер как ПОСЛЕДНИЙ резерв.

    claim с min_age_sec=local_fallback_sec: пока основной воркер здоров,
    джобы он забирает первыми (поллит чаще), и резерв их не трогает;
    если джоба 'new' висит дольше порога — Render качает сам.
    """
    global _reserve
    if _reserve is not None:
        return
    _reserve = MediaJobPoller(
        name=music_settings.worker_name or "render-reserve",
        poll_sec=music_settings.local_poll_sec,
        caps_ffmpeg=True,  # на Render ffmpeg есть (образ)
        min_age_sec=music_settings.local_fallback_sec,
    )
    _reserve.start()
