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


async def get_token_pool(session: AsyncSession, user_id: int | None,
                         service: str) -> list[str]:
    """Пул токенов для УСТОЙЧИВОГО скачивания (параллельные аккаунты):
    свой токен → общий из env → токены админских аккаунтов →
    дополнительные из YM_TOKENS_EXTRA (через запятую). ЯМ лимитирует
    ручку на аккаунт — когда свой срывается, следующий из пула спасает
    джобу без полного переезда на платные источники."""
    out: list[str] = []
    if user_id:
        q = select(SourceAuth).where(SourceAuth.user_id == int(user_id),
                                     SourceAuth.service == service)
        row = (await session.execute(q)).scalar_one_or_none()
        if row and row.token:
            out.append(row.token)
    env_primary = {"ym": settings.YM_TOKEN,
                   "zvuk": settings.ZVUK_TOKEN}.get(service) or ""
    if env_primary:
        out.append(env_primary)
    if service == "ym":
        for aid in settings.ADMIN_IDS or []:
            try:
                q = select(SourceAuth).where(SourceAuth.user_id == int(aid),
                                             SourceAuth.service == service)
                r = (await session.execute(q)).scalar_one_or_none()
                if r and r.token:
                    out.append(r.token)
            except Exception:  # noqa: BLE001
                pass
        for extra in (settings.YM_TOKENS_EXTRA or "").split(","):
            t = extra.strip()
            if t:
                out.append(t)
    seen: set[str] = set()
    deduped: list[str] = []
    for t in out:
        if t and t not in seen:
            seen.add(t)
            deduped.append(t)
    return deduped
