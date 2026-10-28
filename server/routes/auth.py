"""Аутентификация веб-приложения без кодов:

- внутри Telegram: подпись initData (X-Init-Data) — см. server/auth.py::tg_user
- в обычном браузере: тихая «гостевая» сессия по device_id (localStorage).
"""
import hashlib

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_session
from core.models import User
from server.auth import CurrentUser, make_bearer, tg_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


class DeviceIn(BaseModel):
    device_id: str


_GUEST_BASE = 1_000_000_000_000   # далеко за пределами реальных TG id (~10^10)


@router.post("/guest")
async def guest(body: DeviceIn,
                session: AsyncSession = Depends(get_session)):
    """Тихий вход из чистого браузера: device_id → стабильный личный uid."""
    raw = (body.device_id or "").strip()
    if not (16 <= len(raw) <= 128) or any(c in raw for c in " /\\?&=#"):
        raise HTTPException(400, "плохой device_id")
    digest = int(hashlib.sha256(("vibe-guest-v1|" + raw).encode()).hexdigest()[:10], 16)
    uid = _GUEST_BASE + digest % 999_999_999_989
    u = await session.get(User, uid)
    if u is None:
        u = User(id=uid, first_name="Гость")
        session.add(u)
        await session.flush()
    await session.commit()
    return {"token": make_bearer(uid),
            "user": {"id": uid, "first_name": "Гость"}}


@router.get("/me")
async def whoami(user: CurrentUser = Depends(tg_user)):
    return {"user": dict(user)}
