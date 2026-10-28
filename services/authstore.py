"""Хранилище токенов пользователей (ЯМ/Звук/Spotify) + fallback из .env."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.models import SourceAuth

async def get_token(session: AsyncSession, user_id: int | None, service: str) -> str:
    """Токен пользователя из БД; есл он нет — общий из .env."""
    if user_id:
        q = select(SourceAuth).where(SourceAuth.user_id == int(user_id),
                                     SourceAuth.service == service)
        row = (await session.execute(q)).scalar_one_or_none()
        if row and row.token:
            return row.token
    return {
        "ym": settings.YM_TOKEN,
        "zvuk": settings.ZVUK_TOKEN,
        "spotify": "",
    }.get(service, "")

async def set_token(session: AsyncSession, user_id: int, service: str, token: str):
    q = select(SourceAuth).where(SourceAuth.user_id == int(user_id),
                                 SourceAuth.service == service)
    row = (await session.execute(q)).scalar_one_or_none()
    if row:
        row.token = token
        from core.models import utcnow
        row.updated_at = utcnow()
    else:
        session.add(SourceAuth(user_id=int(user_id), service=service, token=token))
    await session.commit()
