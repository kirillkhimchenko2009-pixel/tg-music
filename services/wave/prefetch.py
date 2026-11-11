"""М3 prefetch-воркер волны: готовим заранее то, что иначе заставит юзера ждать.

Два уровня, оба — фоновые дот-задачи одного процесса (без внешнего планировщика):
  • warm_stream_urls — резолвим стрим-URL партии (кэш _AUDIO_URL_CACHE в
    routes.tracks, TTL 35 мин) до того, как юзер нажмёт «дальше»: трек
    стартует мгновенно, без походов по ЯМ/zaycev/Deezer;
  • plan_next_own — следующая партия движка B (полновесный подбор) кладётся
    в wave_sessions.prefetch; /next её просто гидрирует (валидация на входе).

Rotor-партии (движок A) не планируем заранее: контракт ЯМ живёт по
последовательности сессии, досрочный запрос /tracks поломает batch. Зато
их стрим-URL прогреваем так же.
"""
import asyncio
import logging
import time

from sqlalchemy import select

from core.models import WaveSession

LOG = logging.getLogger("wave.prefetch")

_tasks: set[asyncio.Task] = set()

_plan_inflight: set[int] = set()        # session_id → партию уже планируют
_plan_base: dict[int, int] = {}         # session_id → len(played_track_ids) на момент планирования
_warm_inflight: set[int] = set()        # hash (user, партия) → стримы уже прогревают


def _spawn(coro) -> None:
    """Fire-and-forget с удержанием ссылки (иначе GC гасит задачу)."""
    try:
        t = asyncio.get_running_loop().create_task(coro)
    except RuntimeError:                # нет цикла (юнит-тесты) — не повод падать
        return
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


def kick(user_id: int, party: dict) -> None:
    """Вызывается из роутов после /start и /next: разогрев по выданной партии."""
    tracks = (party or {}).get("tracks") or []
    online = [int(t["id"]) for t in tracks if t.get("kind_hint") == "online"][:3]
    if online:
        LOG.info("kick прогрева стримов: %s", online)
        _spawn(_warm_stream_urls(int(user_id), online))
    if (party or {}).get("engine") == "own" and party.get("session_id"):
        _spawn(_plan_next_own(int(user_id), int(party["session_id"])))


# ------------------------------- стрим-URL -------------------------------

async def _warm_stream_urls(user_id: int, track_ids: list[int]) -> None:
    key = hash((user_id, tuple(sorted(track_ids))))
    if key in _warm_inflight:
        return
    _warm_inflight.add(key)
    t0 = time.time()
    try:
        from bot import runtime
        from server.routes.tracks import _resolve_online_audio
        from services import catalog
        async with runtime.get_session() as s:
            for tid in track_ids:
                t = await catalog.get_track(s, int(tid))
                if t is None:
                    LOG.info("прогрев стрима %s пропущен: трека нет в БД", tid)
                    continue
                if t.file_id or t.local_path:
                    LOG.info("прогрев стрима %s не нужен: уже есть %s", tid,
                             "file_id в телеге" if t.file_id else "локальный файл")
                    continue
                try:
                    got = await _resolve_online_audio(int(tid), t, s, int(user_id))
                    LOG.info("прогрев стрима %s → %s (%.1fс)",
                             tid, (got[1] if got else "мимо"), time.time() - t0)
                except Exception as e:
                    LOG.info("прогрев стрима %s не удался: %s", tid, type(e).__name__)
    except Exception as e:              # воркер не должен умирать молча
        LOG.warning("warm-воркер сломался: %s: %s", type(e).__name__, e)
    finally:
        _warm_inflight.discard(key)


# --------------------------- следующая партия B ---------------------------

async def _plan_next_own(user_id: int, session_id: int) -> None:
    """Считаем следующую партию движка B и кладём в sess.prefetch."""
    if session_id in _plan_inflight:
        return
    _plan_inflight.add(session_id)
    try:
        from bot import runtime
        from services.wave import own_engine, service
        async with runtime.get_session() as s:
            sess = (await s.execute(
                select(WaveSession).where(WaveSession.id == session_id,
                                          WaveSession.user_id == user_id,
                                          WaveSession.active.is_(True))
            )).scalar_one_or_none()
            if sess is None or (sess.engine or "") == "yandex":
                return
            if sess.prefetch:
                return
            base = len(sess.played_track_ids or [])
            _plan_base[session_id] = base
            ws = await service.ensure_settings(s, user_id)
            try:
                from services.wave.intel import endless as intel_endless
                br = await intel_endless.build_batch(
                    s, user_id, ws, sess, request_ctx=dict(sess.context or {}))
                batch = br.pairs
            except Exception:
                LOG.warning("intel prefetch не удался — движок B-legacy: %s",
                            LOG.name)
                batch = await own_engine.next_batch(s, user_id, ws, sess)
            if not batch:
                return
            # сессия могла уйти вперёд, пока считали — тогда партия устарела
            if len(sess.played_track_ids or []) != base:
                return
            sess.prefetch = [{"id": t.id, "why": why} for t, why in batch]
            await s.commit()
            LOG.info("партия B заготовлена (%s): %s",
                     session_id, [t.id for t, _ in batch])
    except Exception as e:              # prefetch — только радость, не критичность
        LOG.warning("prefetch партии: %s: %s", type(e).__name__, e)
    finally:
        _plan_inflight.discard(session_id)
        _plan_base.pop(session_id, None)
