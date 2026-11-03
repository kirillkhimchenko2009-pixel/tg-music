"""Волна /api/wave: старт, партия, фидбек, формула. Движки: rotor ЯМ + наш.

Все ручки требуют обычную аутентификацию приложения (initData / Bearer).
next БЕЗ «last» легален: просто берём следующую партию для прогресса.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from bot import runtime
from server.auth import CurrentUser, tg_user
from services.wave import service

LOG = logging.getLogger("routes.wave")
router = APIRouter(prefix="/api/wave", tags=["wave"])


class StartBody(BaseModel):
    engine: str | None = Field(None, description="auto | yandex | own")


class NextBody(BaseModel):
    session_id: int
    last: dict | None = None   # {track_id, played_seconds, event: skip|finished}


class FeedbackBody(BaseModel):
    session_id: int
    event: str                 # trackStarted|like|unlike|dislike|undislike|skip|trackFinished
    track_id: int | None = None
    played_seconds: float | None = None


class SettingsBody(BaseModel):
    engine: str | None = None
    mood: str | None = None
    diversity: str | None = None
    language: str | None = None
    activities: list | None = None
    genres: list | None = None
    artists: list | None = None
    allow_explicit: bool | None = None


@router.post("/start")
async def wave_start(body: StartBody, user: CurrentUser = Depends(tg_user)):
    async with runtime.get_session() as s:
        return await service.start(s, int(user.id), body.engine)


@router.post("/next")
async def wave_next(body: NextBody, user: CurrentUser = Depends(tg_user)):
    async with runtime.get_session() as s:
        return await service.next_batch(s, int(user.id), int(body.session_id), body.last)


@router.post("/feedback")
async def wave_feedback(body: FeedbackBody, user: CurrentUser = Depends(tg_user)):
    async with runtime.get_session() as s:
        return await service.feedback(s, int(user.id), int(body.session_id),
                                      body.event, body.track_id, body.played_seconds)


@router.get("/settings")
async def wave_settings(user: CurrentUser = Depends(tg_user)):
    async with runtime.get_session() as s:
        return await service.settings_dto(s, int(user.id))


@router.put("/settings")
async def wave_save_settings(body: SettingsBody, user: CurrentUser = Depends(tg_user)):
    patch = body.model_dump(exclude_unset=True)
    async with runtime.get_session() as s:
        return await service.update_settings(s, int(user.id), patch)
