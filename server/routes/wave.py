"""Волна /api/wave: старт, партия, фидбек, формула + интеллект-ручки v2.

Движки: rotor ЯМ (движок A) + наш pipeline (движок B, services/wave/intel).
Все ручки требуют обычную аутентификацию приложения (initData / Bearer).
next БЕЗ «last» легален: просто берём следующую партию для прогресса.

v2-ручки: /event (гранулярные события §7.1), /shake (встряска), /scenarios
(карточки), /taste (+exclude/include), /clear (приватность), /metrics (§17),
/discovery и /artist (MODULE 6).
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from bot import runtime
from server.auth import CurrentUser, tg_user
from services.wave import prefetch, service
from services.wave.intel import (context as intel_context,
                                 discovery as intel_discovery,
                                 events as intel_events,
                                 feedback as intel_feedback,
                                 metrics as intel_metrics,
                                 shake as intel_shake,
                                 taste as intel_taste)
from services.wave.intel.config import invalidate as invalidate_config
from sqlalchemy import delete, select

LOG = logging.getLogger("routes.wave")
router = APIRouter(prefix="/api/wave", tags=["wave"])


class StartBody(BaseModel):
    engine: str | None = Field(None, description="auto | yandex | own")
    local_hour: int | None = None
    local_weekday: int | None = None
    device: str | None = None
    activity: str | None = None
    mood: str | None = None
    familiarity: str | None = None
    scenario: str | None = None


class NextBody(BaseModel):
    session_id: int
    last: dict | None = None   # {track_id, played_seconds, event: skip|finished}
    local_hour: int | None = None
    local_weekday: int | None = None
    device: str | None = None
    activity: str | None = None
    mood: str | None = None
    familiarity: str | None = None
    scenario: str | None = None


class FeedbackBody(BaseModel):
    session_id: int
    event: str                 # trackStarted|like|unlike|dislike|undislike|skip|trackFinished
    track_id: int | None = None
    played_seconds: float | None = None
    duration: float | None = None
    reason: str | None = None      # причина дизлайка (§7.3)
    activity: str | None = None
    mood: str | None = None
    device: str | None = None
    local_hour: int | None = None
    local_weekday: int | None = None


class EventBody(BaseModel):
    session_id: int | None = None
    kind: str                      # см. services/wave/intel/events.py EVENT_KINDS
    track_id: int | None = None
    played_seconds: float | None = None
    duration: float | None = None
    reason: str | None = None
    activity: str | None = None
    mood: str | None = None
    device: str | None = None
    local_hour: int | None = None
    local_weekday: int | None = None
    scenario: str | None = None


class ShakeBody(BaseModel):
    session_id: int
    local_hour: int | None = None
    local_weekday: int | None = None
    device: str | None = None


class ExcludeBody(BaseModel):
    artist_key: str | None = None
    genre: str | None = None


class ClearBody(BaseModel):
    scope: str = Field("history", description="history | taste | all")


class SettingsBody(BaseModel):
    engine: str | None = None
    mood: str | None = None
    diversity: str | None = None
    language: str | None = None
    activities: list | None = None
    genres: list | None = None
    artists: list | None = None
    allow_explicit: bool | None = None
    context_enabled: bool | None = None


def _req_ctx(body) -> dict:
    """Контекст запроса → словарь для движка (валидация внутри build_context)."""
    return {
        "local_hour": body.local_hour, "local_weekday": body.local_weekday,
        "device": body.device, "activity": body.activity, "mood": body.mood,
        "familiarity": getattr(body, "familiarity", None),
        "scenario": getattr(body, "scenario", None),
    }


@router.post("/start")
async def wave_start(body: StartBody, user: CurrentUser = Depends(tg_user)):
    async with runtime.get_session() as s:
        resp = await service.start(s, int(user.id), body.engine,
                                   ctx_request=_req_ctx(body))
    prefetch.kick(int(user.id), resp)          # М3: стримы + следующая партия B — заранее
    return resp


@router.post("/next")
async def wave_next(body: NextBody, user: CurrentUser = Depends(tg_user)):
    async with runtime.get_session() as s:
        resp = await service.next_batch(s, int(user.id), int(body.session_id),
                                        body.last, ctx_request=_req_ctx(body))
    prefetch.kick(int(user.id), resp)
    return resp


@router.post("/feedback")
async def wave_feedback(body: FeedbackBody, user: CurrentUser = Depends(tg_user)):
    async with runtime.get_session() as s:
        return await service.feedback(s, int(user.id), int(body.session_id),
                                      body.event, body.track_id,
                                      body.played_seconds,
                                      reason=body.reason,
                                      context=_req_ctx(body),
                                      duration=body.duration)


@router.post("/event")
async def wave_event(body: EventBody, user: CurrentUser = Depends(tg_user)):
    """Гранулярные события плеера (§7.1) — телеметрия и адаптация волны."""
    async with runtime.get_session() as s:
        track = None
        if body.track_id:
            from services import catalog
            track = await catalog.get_track(s, int(body.track_id))
            if track is None:
                raise HTTPException(404, "трека нет в каталоге")
        if body.kind not in intel_events.EVENT_KINDS:
            raise HTTPException(400, f"неизвестный тип события: {body.kind}")
        ctx = intel_events.EventContext(
            time_of_day=intel_context.time_of_day(body.local_hour)
            if body.local_hour is not None and 0 <= body.local_hour <= 23 else None,
            weekday=intel_context.weekday_kind(body.local_weekday)
            if body.local_weekday is not None and 1 <= body.local_weekday <= 7 else None,
            device=body.device if body.device in intel_context.DEVICE_LABEL else None,
            activity=body.activity if body.activity in intel_context.ACTIVITY_LABEL else None,
            mood=body.mood if body.mood in ("fun", "active", "calm", "sad", "edge") else None,
        )
        if body.kind == "context_selected":
            ctx.activity = ctx.activity or body.activity
            ctx.mood = ctx.mood or body.mood
        result = await intel_feedback.process_event(
            s, int(user.id), kind=body.kind, track=track,
            session_id=body.session_id, reason=body.reason,
            context=ctx, scenario=body.scenario,
            played_seconds=float(body.played_seconds or 0),
            duration=float(body.duration or (track.duration if track else 0)))
        await s.commit()
        return result


@router.get("/scenarios")
async def wave_scenarios(local_hour: int | None = None, local_weekday: int | None = None,
                         device: str | None = None, activity: str | None = None,
                         mood: str | None = None,
                         user: CurrentUser = Depends(tg_user)):
    """Карточки сценариев: динамически по профилю и контексту (§2.3)."""
    async with runtime.get_session() as s:
        ws = await service.ensure_settings(s, int(user.id))
        ctx = await intel_endless_ctx(s, int(user.id), ws, local_hour,
                                      local_weekday, device, activity, mood)
        scen_list = intel_context.scenario_list(ctx, ctx.snapshot)
        return {"scenarios": [sc.to_json() for sc in scen_list],
                "context": ctx.explain()}


async def intel_endless_ctx(s, user_id, ws, local_hour, local_weekday,
                            device, activity, mood):
    from services.wave.intel import endless as intel_endless
    sess = await service.get_active(s, user_id)
    ctx = await intel_endless.build_context_full(s, user_id, ws, sess, {
        "local_hour": local_hour, "local_weekday": local_weekday,
        "device": device, "activity": activity, "mood": mood,
    })
    return ctx


@router.post("/shake")
async def wave_shake(body: ShakeBody, user: CurrentUser = Depends(tg_user)):
    """«Встряхнуть волну»: альтернативный сценарий без обнуления вкуса (§8)."""
    async with runtime.get_session() as s:
        if not intel_shake.shake_allowed(int(user.id)):
            raise HTTPException(429, "слишком часто — волна ещё качается")
        sess = await service.get_active(s, int(user.id), body.session_id)
        if sess is None:
            raise HTTPException(409, "волна не активна — запусти заново")
        ws = await service.ensure_settings(s, int(user.id))

        from services.wave.intel import endless as intel_endless
        ctx = await intel_endless.build_context_full(s, int(user.id), ws, sess, {
            "local_hour": body.local_hour, "local_weekday": body.local_weekday,
            "device": body.device,
        })
        result = intel_shake.alternate_scenario(ctx, user_id=int(user.id))
        ctx.scenario = result.scenario
        ctx.familiarity = result.scenario.familiarity

        br = await intel_endless.build_batch(s, int(user.id), ws, sess, request_ctx={
            "local_hour": body.local_hour, "local_weekday": body.local_weekday,
            "device": body.device,
            "mood": next(iter(result.scenario.moods), None),
            "familiarity": result.scenario.familiarity,
            "scenario": None,
        })
        # честность shake-названия: если мoods не подтвердились пулом — «Для тебя»
        scenario_dto = result.scenario.to_json()
        if result.scenario.needs_mood_check:
            from services.wave.intel.context import mood_of_texts
            hits = mood_of_texts([t for t, _ in br.pairs],
                                 result.scenario.needs_mood_check)
            if br.pairs and hits * 2 < len(br.pairs):
                fallback = intel_context.Scenario(
                    id="foryou", title="Для тебя",
                    description="личная волна под твой вкус")
                result = intel_shake.ShakeResult(
                    scenario=fallback, changed=result.changed,
                    note=fallback.description)
                scenario_dto = fallback.to_json()
        # перезапишем сценарий на shake-версию (build мог дать «Для тебя»)
        sess.scenario = scenario_dto
        sess.context = ctx.to_json()
        sess.prefetch = []
        if br.pairs:
            sess.played_track_ids = (list(sess.played_track_ids or []) +
                                     [t.id for t, _ in br.pairs])[-120:]
        await s.commit()
        resp = {
            "session_id": sess.id,
            "engine": sess.engine,
            "scenario": scenario_dto,
            "changed": result.changed,
            "tracks": [service._dto(t, why) for t, why in br.pairs],
        }
    if resp["tracks"]:
        prefetch.kick(int(user.id), resp)
    # телеметрия встряхивания
    async with runtime.get_session() as s2:
        await intel_events.log_event(
            s2, int(user.id), "wave_shaken",
            context=intel_events.EventContext(device=body.device),
            scenario=result.scenario.id)
        await s2.commit()
    return resp


@router.get("/taste")
async def wave_taste(user: CurrentUser = Depends(tg_user)):
    """Профиль вкуса: уровни A/B/C, исключённые, доля открытий (§12)."""
    async with runtime.get_session() as s:
        profile = await intel_taste.get_profile(s, int(user.id))
        lv = intel_taste.levels_of(profile)
        names = {}
        aks = list(lv.strong_artists) + list(lv.experimental_artists)[:20]
        if aks:
            from core.models import ArtistStats
            rows = (await s.execute(select(ArtistStats).where(
                ArtistStats.user_id == int(user.id),
                ArtistStats.artist_key.in_(aks)))).scalars().all()
            names = {r.artist_key: r.display_name for r in rows}
        return {
            "strong_artists": [names.get(ak, ak) for ak in lv.strong_artists][:12],
            "experimental_artists": [names.get(ak, ak) for ak in lv.experimental_artists][:12],
            "top_genres": sorted((profile.genre_affinity or {}).items(),
                                 key=lambda kv: -kv[1])[:8],
            "top_moods": sorted((profile.mood_affinity or {}).items(),
                                key=lambda kv: -kv[1])[:5],
            "situational": {k: v for k, v in (profile.context_affinity or {}).items()},
            "explore_share": round(float(profile.explore_share or 0.2), 3),
            "excluded_artists": list(profile.excluded_artists or []),
            "excluded_genres": list(profile.excluded_genres or []),
            "language": {k: round(v, 2) for k, v in
                         (profile.language_affinity or {}).items()},
            "refreshed_at": profile.refreshed_at.isoformat() if profile.refreshed_at else None,
        }


@router.post("/taste/exclude")
async def wave_taste_exclude(body: ExcludeBody, user: CurrentUser = Depends(tg_user)):
    """Скрыть артиста или жанр из волны (управляемость §20)."""
    if not body.artist_key and not body.genre:
        raise HTTPException(400, "укажи artist_key или genre")
    async with runtime.get_session() as s:
        profile = await intel_taste.get_profile(s, int(user.id), refresh=True)
        if body.artist_key:
            excluded = list(profile.excluded_artists or [])
            if body.artist_key not in excluded:
                excluded.append(body.artist_key)
            profile.excluded_artists = excluded[:50]
            from core.models import ArtistStats
            st = (await s.execute(select(ArtistStats).where(
                ArtistStats.user_id == int(user.id),
                ArtistStats.artist_key == body.artist_key))).scalar_one_or_none()
            if st is not None:
                st.is_excluded = True
        if body.genre:
            excluded = list(profile.excluded_genres or [])
            if body.genre not in excluded:
                excluded.append(body.genre)
            profile.excluded_genres = excluded[:20]
        await service._invalidate_prefetch(s, int(user.id))
        await s.commit()
        return {"ok": True}


@router.post("/taste/include")
async def wave_taste_include(body: ExcludeBody, user: CurrentUser = Depends(tg_user)):
    async with runtime.get_session() as s:
        profile = await intel_taste.get_profile(s, int(user.id), refresh=True)
        if body.artist_key:
            profile.excluded_artists = [x for x in (profile.excluded_artists or [])
                                        if x != body.artist_key]
            from core.models import ArtistStats
            st = (await s.execute(select(ArtistStats).where(
                ArtistStats.user_id == int(user.id),
                ArtistStats.artist_key == body.artist_key))).scalar_one_or_none()
            if st is not None:
                st.is_excluded = False
        if body.genre:
            profile.excluded_genres = [x for x in (profile.excluded_genres or [])
                                       if x != body.genre]
        await service._invalidate_prefetch(s, int(user.id))
        await s.commit()
        return {"ok": True}


@router.post("/clear")
async def wave_clear(body: ClearBody, user: CurrentUser = Depends(tg_user)):
    """Приватность §20: очистка истории, сброс вкуса."""
    scope = (body.scope or "history").strip()
    if scope not in ("history", "taste", "all"):
        raise HTTPException(400, "scope: history | taste | all")
    async with runtime.get_session() as s:
        uid = int(user.id)
        if scope in ("history", "all"):
            await s.execute(delete(intel_events.WaveEvent)
                            .where(intel_events.WaveEvent.user_id == uid))
            from core.models import ArtistStats, PlayEvent
            await s.execute(delete(PlayEvent).where(PlayEvent.user_id == uid))
            await s.execute(delete(ArtistStats).where(ArtistStats.user_id == uid))
        if scope in ("taste", "all"):
            await s.execute(delete(intel_taste.TasteProfile)
                            .where(intel_taste.TasteProfile.user_id == uid))
        await service._invalidate_prefetch(s, uid)
        await s.commit()
        return {"ok": True, "scope": scope}


@router.get("/metrics")
async def wave_metrics(days: int = 14, user: CurrentUser = Depends(tg_user)):
    """Метрики качества волны (§17)."""
    days = max(1, min(90, int(days or 14)))
    async with runtime.get_session() as s:
        m = await intel_metrics.metrics(s, user_id=int(user.id), days=days)
        return m.to_json()


@router.get("/discovery")
async def wave_discovery(limit: int = 8, user: CurrentUser = Depends(tg_user)):
    """Карточки новых артистов (§9.1): не случайные, рядом с вкусом."""
    async with runtime.get_session() as s:
        cards = await intel_discovery.discovery_cards(s, int(user.id),
                                                     limit=max(1, min(16, limit)))
        return {"artists": cards}


@router.post("/artist")
async def wave_artist(body: dict, user: CurrentUser = Depends(tg_user)):
    """Профиль артиста (+ событие artist_opened) — из проверяемых метаданных."""
    name = (body or {}).get("name", "").strip()
    if not name:
        raise HTTPException(400, "укажи name артиста")
    async with runtime.get_session() as s:
        prof = await intel_discovery.artist_profile(s, int(user.id), name)
        if prof is None:
            raise HTTPException(404, "артист не найден в каталоге")
        await intel_events.log_event(s, int(user.id), "artist_opened",
                                     artist=prof.name)
        await s.commit()
        return prof.to_json()


@router.get("/settings")
async def wave_settings(user: CurrentUser = Depends(tg_user)):
    async with runtime.get_session() as s:
        return await service.settings_dto(s, int(user.id))


@router.put("/settings")
async def wave_save_settings(body: SettingsBody, user: CurrentUser = Depends(tg_user)):
    patch = body.model_dump(exclude_unset=True)
    async with runtime.get_session() as s:
        resp = await service.update_settings(s, int(user.id), patch)
    invalidate_config()                        # веса/лимиты могли обновиться
    return resp
