"""Аутентификация веб-приложения:

- внутри Telegram: подпись initData (X-Init-Data) — см. server/auth.py::tg_user
- в обычном браузере: тихая «гостевая» сессия по device_id (localStorage)
  либо вход по одноразовому коду из команды /code бота — тогда Bearer-токен
  привязан к реальному Telegram id и файлы можно отправлять в чат.
"""
import hashlib
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_session
from core.models import AuthCode, User, utcnow
from server.auth import (GUEST_ID_BASE, CurrentUser, make_bearer, tg_user)

router = APIRouter(prefix="/api/auth", tags=["auth"])

_GUEST_BASE = GUEST_ID_BASE


class DeviceIn(BaseModel):
    device_id: str


class ExchangeIn(BaseModel):
    code: str


# Простое ограничение перебора кодов: 30 попыток на IP за 5 минут.
# За туннелем IP обычно 127.0.0.1 у всех, поэтому лимит мягкий.
_ATTEMPTS: dict[str, list[float]] = {}
_MAX_ATTEMPTS = 30
_ATTEMPT_WINDOW = 300.0


def _rate_limit(ip: str) -> None:
    now = time.monotonic()
    fresh = [t for t in _ATTEMPTS.get(ip, []) if now - t < _ATTEMPT_WINDOW]
    if len(fresh) >= _MAX_ATTEMPTS:
        raise HTTPException(429, "слишком много попыток — подожди пару минут")
    fresh.append(now)
    _ATTEMPTS[ip] = fresh


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


@router.post("/exchange")
async def exchange(body: ExchangeIn, request: Request,
                   session: AsyncSession = Depends(get_session)):
    """Одноразовый код из /code → Bearer-токен реального Telegram-пользователя.

    Гостевая сессия позволяет слушать музыку, но отправить файл в чат можно
    только с настоящим chat_id. После обмена приложение перезагружается уже
    под аккаунтом Telegram.
    """
    _rate_limit(request.client.host if request.client else "unknown")
    digits = "".join(ch for ch in (body.code or "") if ch.isdigit())
    if len(digits) != 8:
        raise HTTPException(400, "код должен состоять из 8 цифр")

    row = (await session.execute(
        select(AuthCode).where(AuthCode.code == digits,
                               AuthCode.used.is_(False))
        .order_by(AuthCode.id.desc())
    )).scalars().first()
    if row is None or row.expires_at <= utcnow():
        raise HTTPException(400, "код неверный или истёк — получи новый в боте: /code")

    # Атомарно гасим код: два параллельных обмена не должны оба получить токен.
    done = await session.execute(
        update(AuthCode)
        .where(AuthCode.id == row.id, AuthCode.used.is_(False))
        .values(used=True)
    )
    if done.rowcount != 1:
        await session.rollback()
        raise HTTPException(400, "код уже использован — получи новый: /code")

    # Остальные коды этого пользователя больше не нужны.
    await session.execute(
        update(AuthCode)
        .where(AuthCode.user_id == row.user_id, AuthCode.used.is_(False))
        .values(used=True)
    )

    user = await session.get(User, row.user_id)
    if user is None:
        user = User(id=row.user_id, first_name="Telegram")
        session.add(user)
        await session.flush()
    await session.commit()
    return {"token": make_bearer(row.user_id),
            "user": {"id": row.user_id, "first_name": user.first_name,
                     "username": user.username}}


@router.get("/me")
async def whoami(user: CurrentUser = Depends(tg_user)):
    return {"user": dict(user), "guest": GUEST_ID_BASE <= int(user.id)}
