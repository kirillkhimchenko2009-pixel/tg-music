"""Команда /code — одноразовый код для входа в приложение из браузера.

Внутри Telegram мини-апп авторизуется сам (initData). В обычном браузере
нужен код: пользователь присылает /code боту, вводит 8 цифр в приложении,
а сервер меняет их на Bearer-токен его настоящего Telegram-аккаунта.
"""
import logging
import secrets
from datetime import timedelta

from aiogram import Router, types
from aiogram.filters import Command
from sqlalchemy import delete, select

from bot import runtime
from core.models import AuthCode, utcnow

log = logging.getLogger("bot.login")
router = Router(name="login")

CODE_TTL_MINUTES = 10


def _make_code() -> str:
    return f"{secrets.randbelow(10 ** 8):08d}"


@router.message(Command("code"))
async def cmd_code(message: types.Message):
    uid = message.from_user.id
    async with runtime.get_session() as s:
        await runtime.ensure_user(s, uid, message.from_user.username,
                                  message.from_user.first_name or "")
        # Действует только последний код: старые сразу становятся невалидными.
        await s.execute(delete(AuthCode).where(AuthCode.user_id == uid))

        code = _make_code()
        for _ in range(20):
            clash = (await s.execute(
                select(AuthCode.id).where(AuthCode.code == code)
            )).first()
            if not clash:
                break
            code = _make_code()

        s.add(AuthCode(code=code, user_id=uid,
                       expires_at=utcnow() + timedelta(minutes=CODE_TTL_MINUTES)))
        await s.commit()

    pretty = f"{code[:4]} {code[4:]}"
    await message.answer(
        "🔑 <b>Код для входа в приложение</b>\n\n"
        f"<code>{pretty}</code>\n\n"
        "Открой приложение и введи этот код в разделе входа.\n"
        f"Код действует {CODE_TTL_MINUTES} минут и срабатывает один раз."
    )
    log.info("/code выдан пользователю %s", uid)
