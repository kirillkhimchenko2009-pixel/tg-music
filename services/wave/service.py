"""Оркестрация «Волны»: движок A (rotor ЯМ) / движок B (наш), сессии, фидбек.

Повторяем контракт ЯМ:
  • start    — rotor/session/new (+event radioStarted), первая партия в ответе;
  • next     — фидбек перехода (skip / trackFinished) + следующая партия одним
               запросом (как POST rotor/session/{id}/tracks с feedbacks);
  • feedback — trackStarted (открывает PlayEvent), like/unlike/dislike/undislike.

trackStarted для движка A отправляет сервер при выдаче партии (никогда не
дублируем из /feedback — там только локальные PlayEvent).

Любая ошибка ротора охлаждает движок A на 30 минут и бесшовно переводит
сессию на движок B — UX не рвётся.
"""
import logging
import time

from fastapi import HTTPException
from sqlalchemy import select, update

from core.models import (PlayEvent, Track, TrackFeedback, WaveSession, WaveSettings,
                         utcnow)
from services import authstore, catalog
from services.providers.yandex import _web_meta
from services.wave import own_engine, yandex_rotor
from services.wave.intel import endless as intel_endless
from services.wave.intel import feedback as intel_feedback

LOG = logging.getLogger("wave.service")

BATCH = 3
ROTOR_COOLDOWN_S = 30 * 60          # после падения rotor движок A отдыхает

_rotor_unwell_until = 0.0           # monotonic-подобный cooldown (wall clock)


# ------------------------------ формула (settings) ------------------------------

async def ensure_settings(s, user_id: int) -> WaveSettings:
    row = (await s.execute(select(WaveSettings)
                           .where(WaveSettings.user_id == user_id))).scalar_one_or_none()
    if row is None:
        row = WaveSettings(user_id=user_id)
        s.add(row)
        await s.flush()
    return row


def settings_dto_of(ws: WaveSettings) -> dict:
    return {
        "engine": ws.engine or "auto",
        "mood": ws.mood,
        "diversity": ws.diversity or "default",
        "language": ws.language or "any",
        "activities": list(ws.activities or []),
        "genres": list(ws.genres or []),
        "artists": list(ws.artists or []),
        "allow_explicit": bool(ws.allow_explicit),
        "context_enabled": bool(getattr(ws, "context_enabled", True)),
    }


async def settings_dto(s, user_id: int) -> dict:
    return settings_dto_of(await ensure_settings(s, user_id))


_MOODS = {None, "fun", "active", "calm", "sad", "edge"}
_DVSR = {None, "default", "favorite", "popular", "discover"}
_LANG = {None, "any", "ru", "non-ru"}
_ENG = {None, "auto", "yandex", "own"}


async def update_settings(s, user_id: int, patch: dict) -> dict:
    ws = await ensure_settings(s, user_id)
    for key, allowed in (("engine", _ENG), ("mood", _MOODS),
                         ("diversity", _DVSR), ("language", _LANG)):
        if key in patch:
            if patch[key] not in allowed:
                raise HTTPException(400, f"неизвестное значение {key}")
            setattr(ws, key, patch[key])
    if "allow_explicit" in patch:
        ws.allow_explicit = bool(patch["allow_explicit"])
    if "context_enabled" in patch:
        ws.context_enabled = bool(patch["context_enabled"])
    for lst in ("activities", "genres", "artists"):
        if lst in patch:
            v = patch[lst]
            if not isinstance(v, list):
                raise HTTPException(400, f"{lst}: ожидается список")
            if lst == "artists":
                clean = []
                for a in v:
                    if isinstance(a, dict) and str(a.get("name", "")).strip():
                        clean.append({"name": str(a["name"]).strip()[:64],
                                      "ym_id": a.get("ym_id")})
                ws.artists = clean[:5]
            else:
                setattr(ws, lst, [str(x)[:32] for x in v][:12])
    ws.updated_at = utcnow()
    await _invalidate_prefetch(s, user_id)     # заготовки B под старую формулу — мимо
    await s.commit()
    return settings_dto_of(ws)


# --------------------------------- движки ---------------------------------

def mark_rotor_unwell(reason: str):
    global _rotor_unwell_until
    _rotor_unwell_until = time.time() + ROTOR_COOLDOWN_S
    LOG.warning("rotor cooldown %d мин: %s", ROTOR_COOLDOWN_S // 60, reason[:140])


def rotor_ready() -> bool:
    return time.time() >= _rotor_unwell_until


async def resolve_engine(s, user_id: int, pref: str | None) -> str:
    pref = (pref or "auto").lower()
    if pref not in ("auto", "yandex", "own"):
        pref = "auto"
    if pref == "own":
        return "own"
    if rotor_ready():
        tok = await authstore.get_token(s, user_id, "ym")
        if tok:
            return "yandex"
    return "own"


# ---------------------------------- DTO ----------------------------------

def _dto(t: Track, why: str | None = None) -> dict:
    d = {
        "id": t.id, "title": t.title or "", "artist": t.artist or "",
        "cover_url": t.cover_url, "duration": int(t.duration or 0),
        "explicit": bool(t.explicit), "source": t.source or "",
        "kind_hint": "file" if (t.file_id or t.local_path) else "online",
        "year": t.year,
    }
    if why:
        d["why"] = why
    return d


async def _upsert_rotor_sequence(s, sequence: list) -> list[Track]:
    """JSON-треки rotor ({"track": {...}} или сам трек) → наш каталог."""
    out: list[Track] = []
    for item in sequence or []:
        try:
            meta = _web_meta(item if isinstance(item, dict) else {})
        except Exception:
            continue
        if not meta.external_id:
            continue
        out.append(await catalog.upsert_track(s, meta))
    return out


# -------------------------------- PlayEvents --------------------------------

async def _open_play_event(s, user_id: int, session_id: int, track_id: int):
    """trackStarted: открытый PlayEvent — один на (сессия, трек)."""
    q = (select(PlayEvent)
         .where(PlayEvent.user_id == user_id, PlayEvent.session_id == session_id,
                PlayEvent.track_id == int(track_id),
                PlayEvent.skipped.is_(False), PlayEvent.finished.is_(False))
         .order_by(PlayEvent.started_at.desc()).limit(1))
    if (await s.execute(q)).scalar_one_or_none() is not None:
        return
    s.add(PlayEvent(user_id=user_id, track_id=int(track_id),
                    session_id=session_id, origin="wave"))
    await s.flush()


async def _close_play_event(s, user_id: int, session_id: int, track_id: int,
                            event: str, played: float):
    """skip | trackFinished: закрываем последний открытый PlayEvent трека."""
    q = (select(PlayEvent)
         .where(PlayEvent.user_id == user_id, PlayEvent.session_id == session_id,
                PlayEvent.track_id == int(track_id),
                PlayEvent.skipped.is_(False), PlayEvent.finished.is_(False))
         .order_by(PlayEvent.started_at.desc()).limit(1))
    pe = (await s.execute(q)).scalar_one_or_none()
    if pe is None:                       # играл без trackStarted — создадим и закроем
        pe = PlayEvent(user_id=user_id, track_id=int(track_id),
                       session_id=session_id, origin="wave")
        s.add(pe)
        await s.flush()
    pe.played_seconds = float(played or 0)
    pe.skipped = event == "skip"
    pe.finished = event == "finished"
    pe.finished_at = utcnow()
    await s.flush()


# -------------------------------- сессии --------------------------------

async def get_active(s, user_id: int, session_id: int | None = None) -> WaveSession | None:
    q = (select(WaveSession).where(WaveSession.user_id == user_id,
                                   WaveSession.active.is_(True))
         .order_by(WaveSession.id.desc()))
    if session_id:
        q = q.where(WaveSession.id == int(session_id))
    return (await s.execute(q)).scalars().first()


async def _ext_id(s, track_id: int | None) -> str | None:
    """ЯМ external_id локального трека (для rotor-queue)."""
    if not track_id:
        return None
    t = await catalog.get_track(s, int(track_id))
    if t and (t.source or "") == "yandex" and t.external_id:
        return str(t.external_id)
    return None


# -------------------------------- prefetch (М3) --------------------------------

async def _hydrate_prefetch(s, user_id: int, ws, sess: WaveSession):
    """Готовая партия из wave_sessions.prefetch (движок B) с ревалидацией:
    дисклайкнутых, 18+ и уже отыгранных исключаем; недобор — полный пересчёт."""
    entries = [(int(e.get("id")), (e.get("why") or "").strip())
               for e in (sess.prefetch or [])
               if isinstance(e, dict) and e.get("id")]
    sess.prefetch = []                   # потребляем всегда — иначе живёт вечно
    if len(entries) < 2:
        return None

    ids = [i for i, _ in entries]
    tracks = {t.id: t for t in (await s.execute(
        select(Track).where(Track.id.in_(ids)))).scalars()}

    # лёгкая ревалидация вкуса: только дисклайки по этим трекам + их артисты
    dq = (select(TrackFeedback.track_id, Track.artist)
          .join(Track, Track.id == TrackFeedback.track_id)
          .where(TrackFeedback.user_id == user_id, TrackFeedback.kind == -1,
                 TrackFeedback.track_id.in_(ids)))
    ds_track = set()
    ds_artist = set()
    for tid, artist in (await s.execute(dq)).all():
        ds_track.add(int(tid))
        ak = own_engine.artist_key(artist) if artist else None
        if ak:
            ds_artist.add(ak)
    played_set = set(int(x) for x in (sess.played_track_ids or []))

    pairs: list[tuple[Track, str]] = []
    for tid, why in entries:
        t = tracks.get(int(tid))
        if t is None or t.id in played_set or t.id in ds_track:
            continue
        ak = own_engine.artist_key(t.artist)
        if ak and ak in ds_artist:
            continue
        if not getattr(ws, "allow_explicit", True) and t.explicit:
            continue
        pairs.append((t, why or "подобрано по твоей формуле"))
        if len(pairs) == (own_engine.BATCH or 3):
            break
    if len(pairs) < 2:
        return None                      # тощий prefetch лучше честного подсчёта
    return pairs


async def _invalidate_prefetch(s, user_id: int) -> None:
    """Формула/дизлайк изменились — заготовленная партия B больше не истина."""
    await s.execute(update(WaveSession)
                    .where(WaveSession.user_id == user_id,
                           WaveSession.active.is_(True))
                    .values(prefetch=[]))


# -------------------------------- старт --------------------------------

async def start(s, user_id: int, engine_pref: str | None,
                ctx_request: dict | None = None) -> dict:
    ws = await ensure_settings(s, user_id)
    engine = await resolve_engine(s, user_id, engine_pref)

    # Сценарные карточки (§2.3) кроме «Для тебя» — наш pipeline: rotor ЯМ
    # сценарий не поддерживает и честности названия не гарантирует.
    requested_scenario = (ctx_request or {}).get("scenario")
    if requested_scenario and requested_scenario not in ("foryou", "") \
            and engine == "yandex":
        engine = "own"

    # две параллельных сессий не ведём: старые закрываем
    await s.execute(update(WaveSession)
                    .where(WaveSession.user_id == user_id,
                           WaveSession.active.is_(True))
                    .values(active=False))
    await s.flush()

    sess = WaveSession(user_id=user_id, engine=engine, seeds=[],
                       played_ids=[], played_track_ids=[], prefetch=[],
                       scenario={}, context={})
    s.add(sess)
    await s.flush()

    tok = None
    pairs: list[tuple[Track, str]] = []
    scenario_dto: dict | None = None
    if engine == "yandex":
        tok = await authstore.get_token(s, user_id, "ym")
        try:
            seeds = yandex_rotor.formula_seeds(ws)
            st = await yandex_rotor.session_new(
                tok, seeds, allow_explicit=bool(ws.allow_explicit))
            if not st.get("radio_session_id") or st.get("unknown_session"):
                raise yandex_rotor.RotorError("session_new без radioSessionId")
            tracks = await _upsert_rotor_sequence(s, st["sequence"])
            if not tracks:
                raise yandex_rotor.RotorError("первая партия пуста")
            sess.radio_session_id = st["radio_session_id"]
            sess.batch_id = st["batch_id"]
            sess.seeds = seeds
            pairs = [(t, "подобрала волна") for t in tracks[:BATCH]]
            await yandex_rotor.session_feedback(
                tok, sess.radio_session_id, yandex_rotor.event("radioStarted"),
                sess.batch_id)
        except yandex_rotor.RotorError as e:
            mark_rotor_unwell(str(e))
            engine = "own"

    if engine == "own":
        # движок B-v2: полный pipeline (контекст → кандидаты → скоринг → партия)
        try:
            br = await intel_endless.build_batch(s, user_id, ws, sess,
                                                 request_ctx=ctx_request)
            pairs = br.pairs
            scenario_dto = br.scenario.to_json() if br.scenario else None
        except Exception:                     # resilience: wave не роняется
            LOG.exception("intel pipeline недоступен — откат на движок B-legacy")
            pairs = []
        if 0 < len(pairs) < BATCH:            # недобор — добор прежним движком B
            for t, why in await own_engine.next_batch(s, user_id, ws, sess):
                if all(t.id != p[0].id for p in pairs):
                    pairs.append((t, why))
                if len(pairs) >= BATCH:
                    break
        if not pairs:                         # прежний движок B — фолбэк
            pairs = [(t, why) for t, why in
                     await own_engine.next_batch(s, user_id, ws, sess)]
        if not pairs:
            await s.commit()
            raise HTTPException(503, "волна не нашла музыку — ищи через 🔍 или добавь лайки")
        sess.engine = "own"
        sess.radio_session_id = None
        sess.batch_id = None
        sess.seeds = yandex_rotor.formula_seeds(ws)

    sess.played_track_ids = [t.id for t, _ in pairs]
    sess.played_ids = [t.external_id for t, _ in pairs if t.source == "yandex"]
    if scenario_dto is not None:
        sess.scenario = scenario_dto
    else:
        scenario_dto = dict(sess.scenario or {})
    await s.commit()

    out = {
        "session_id": sess.id,
        "engine": sess.engine,
        "tracks": [_dto(t, why) for t, why in pairs],
        "scenario": scenario_dto or None,
    }
    return out


# -------------------------------- партия --------------------------------

async def next_batch(s, user_id: int, session_id: int, last: dict | None,
                     ctx_request: dict | None = None) -> dict:
    sess = await get_active(s, user_id, session_id)
    if sess is None:
        raise HTTPException(409, "волна не активна — запусти заново")
    ws = await ensure_settings(s, user_id)

    last = last or {}
    lid = last.get("track_id")
    played = float(last.get("played_seconds") or 0)
    ev = last.get("event") or "skip"
    if ev not in ("skip", "finished"):
        ev = "skip"

    # 1) закрываем PlayEvent предыдущего трека + телеметрия перехода
    if lid:
        await _close_play_event(s, user_id, sess.id, int(lid), ev, played)
        try:
            await intel_feedback.log_transition(
                s, user_id, sess.id, int(lid), ev, played,
                context=dict(sess.context or {}),
                scenario=(sess.scenario or {}).get("id"))
        except Exception:
            LOG.exception("переход трека не залогирован")

    pairs: list[tuple[Track, str]] = []
    scenario_dto: dict | None = None
    tok = None
    if sess.engine == "yandex":
        tok = await authstore.get_token(s, user_id, "ym")
        try:
            ext = await _ext_id(s, lid)
            queue = list(sess.played_ids or [])
            if ext and ext not in queue:
                queue.append(ext)
            evrotor = yandex_rotor.event(ev if ev == "skip" else "trackFinished",
                                         ext, played)
            if ext:
                await yandex_rotor.session_feedback(
                    tok, sess.radio_session_id, evrotor, sess.batch_id)
            st = await yandex_rotor.session_tracks(tok, sess.radio_session_id, queue)
            if st.get("unknown_session"):
                raise yandex_rotor.RotorError("rotor: unknown session")
            tracks = await _upsert_rotor_sequence(s, st["sequence"])
            if not tracks:
                raise yandex_rotor.RotorError("rotor: пустая партия")
            sess.radio_session_id = sess.radio_session_id or st.get("radio_session_id")
            sess.batch_id = st.get("batch_id") or sess.batch_id
            sess.played_ids = queue[-60:]
            pairs = [(t, "подобрала волна") for t in tracks[:BATCH]]
            # trackStarted для первой трекления новой партии (ЯМ-флоу)
            first_ext = await _ext_id(s, pairs[0][0].id) if pairs else None
            if first_ext:
                try:
                    await yandex_rotor.session_feedback(
                        tok, sess.radio_session_id,
                        yandex_rotor.event("trackStarted", first_ext), sess.batch_id)
                except yandex_rotor.RotorError:
                    pass
        except yandex_rotor.RotorError as e:
            mark_rotor_unwell(str(e))
            sess.engine = "own"          # бесшовно на B, не роняя сессию

    if sess.engine == "own":
        # смена сценария на ходу (карточки волны) — пересчёт под новый сценарий
        switch_scenario = (ctx_request or {}).get("scenario")
        if switch_scenario or not sess.scenario:
            try:
                br = await intel_endless.build_batch(s, user_id, ws, sess,
                                                     request_ctx=ctx_request)
                pairs = br.pairs
                if br.scenario is not None:
                    scenario_dto = br.scenario.to_json()
                    sess.scenario = scenario_dto
                if br.context is not None:
                    sess.context = br.context.to_json()
            except Exception:
                LOG.exception("intel pipeline недоступен на /next — фолбэк")
                pairs = []
            if 0 < len(pairs) < BATCH:
                for t, why in await own_engine.next_batch(s, user_id, ws, sess):
                    if all(t.id != p[0].id for p in pairs):
                        pairs.append((t, why))
                    if len(pairs) >= BATCH:
                        break
        if not pairs:
            pairs = await _hydrate_prefetch(s, user_id, ws, sess)
        if not pairs:
            pairs = await own_engine.next_batch(s, user_id, ws, sess)
        if not pairs:
            await s.commit()
            raise HTTPException(503, "волна временно иссякла — доступно позже")

    sess.played_track_ids = (list(sess.played_track_ids or []) +
                             [t.id for t, _ in pairs])[-120:]
    await s.commit()

    out = {
        "session_id": sess.id,
        "engine": sess.engine,
        "tracks": [_dto(t, why) for t, why in pairs],
        "batch_id": sess.batch_id,
        "scenario": scenario_dto if scenario_dto is not None
                    else dict(sess.scenario or {}) or None,
    }
    return out


# -------------------------------- фидбек --------------------------------

async def _add_feedback(s, user_id: int, track_id: int, kind: int):
    q = (select(TrackFeedback)
         .where(TrackFeedback.user_id == user_id, TrackFeedback.track_id == int(track_id),
                TrackFeedback.kind == kind))
    row = (await s.execute(q)).scalar_one_or_none()
    if row is None:
        s.add(TrackFeedback(user_id=user_id, track_id=int(track_id), kind=kind))
        await s.flush()


async def _del_feedback(s, user_id: int, track_id: int, kind: int):
    from sqlalchemy import delete
    await s.execute(delete(TrackFeedback)
                    .where(TrackFeedback.user_id == user_id,
                           TrackFeedback.track_id == int(track_id),
                           TrackFeedback.kind == kind))
    await s.flush()


async def feedback(s, user_id: int, session_id: int, event: str,
                   track_id: int | None, played_seconds: float | None,
                   reason: str | None = None,
                   context: dict | None = None,
                   duration: float | None = None) -> dict:
    sess = await get_active(s, user_id, session_id)
    if sess is None:
        raise HTTPException(409, "волна не активна — запусти заново")
    event = (event or "").strip()
    if track_id is not None:
        track_id = int(track_id)

    tok = None
    if sess.engine == "yandex" and event in ("like", "unlike", "dislike", "undislike"):
        tok = await authstore.get_token(s, user_id, "ym")
        try:
            ext = await _ext_id(s, track_id)
            if ext:
                await yandex_rotor.session_feedback(
                    tok, sess.radio_session_id,
                    yandex_rotor.event(event, ext), sess.batch_id)
        except yandex_rotor.RotorError as e:
            mark_rotor_unwell(str(e))
            sess.engine = "own"

    track = None
    if track_id is not None:
        track = await catalog.get_track(s, track_id)

    # телеметрия в рекомендательный движок (WaveEvent) — все события плеера
    scenario_id = (sess.scenario or {}).get("id")
    try:
        if event in ("like", "unlike", "dislike", "undislike", "added_to_library",
                     "removed_from_library", "trackStarted") and track is not None:
            kind_map = {"like": "liked", "unlike": "unliked", "dislike": "disliked",
                        "undislike": None, "trackStarted": "track_started",
                        "added_to_library": "added_to_library",
                        "removed_from_library": "removed_from_library"}
            kind = kind_map.get(event)
            if kind:
                from services.wave.intel.events import EventContext
                ctx = None
                if context:
                    ctx = EventContext(**{k: context.get(k) for k in
                                          ("time_of_day", "weekday", "device",
                                           "activity", "mood") if context.get(k)})
                await intel_feedback.process_event(
                    s, user_id, kind=kind, track=track, session_id=sess.id,
                    reason=(reason if event == "dislike" else None),
                    context=ctx, scenario=scenario_id,
                    played_seconds=float(played_seconds or 0),
                    duration=float(duration or (track.duration if track else 0)))
        elif event in ("skip", "trackFinished") and track_id:
            await intel_feedback.log_transition(
                s, user_id, sess.id, int(track_id), event,
                float(played_seconds or 0),
                context=dict(sess.context or {}), scenario=scenario_id)
    except Exception:
        LOG.exception("телеметрия фидбека не записана")

    if event == "trackStarted" and track_id:
        await _open_play_event(s, user_id, sess.id, track_id)
    elif event == "dislike" and track_id:
        await _add_feedback(s, user_id, track_id, -1)
        await _invalidate_prefetch(s, user_id)   # вкус изменился — заготовки B в мусор
        # лайки/дизлайки меняют профиль — заготовленные партии пересчитываются
        try:
            await intel_feedback.touch_profile_stale(s, user_id)
        except Exception:
            pass
    elif event == "undislike" and track_id:
        await _del_feedback(s, user_id, track_id, -1)
    elif event == "like" and track_id:
        await _add_feedback(s, user_id, track_id, +1)
    elif event == "unlike" and track_id:
        await _del_feedback(s, user_id, track_id, +1)
    elif event in ("skip", "trackFinished") and track_id:
        # ленивые переходы без /next (например, ошибка источника) — закрываем тихо
        await _close_play_event(s, user_id, sess.id, track_id, event,
                                float(played_seconds or 0))
    await s.commit()
    return {"ok": True}
