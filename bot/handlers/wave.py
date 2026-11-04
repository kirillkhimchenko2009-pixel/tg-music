"""М5: «Волна» из чата — один тап от команды до потока.

/wave шлёт inline web_app-кнопку с фрагментом #wave: мини-апп откроется
сразу на вкладке «Волна», а там авто-старт (если плеер молчит — поток
начинает играть сам, см. Wave.jsx). Кириллицу в командах Telegram не даёт,
потому команда — /wave, а «🌊 Волна» работает как reply-кнопка (kblabels).
"""
import logging

from aiogram import Router, types
from aiogram.filters import Command
from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup,
                           WebAppInfo)

from core.config import settings

log = logging.getLogger(__name__)
router = Router()


def wave_url() -> str:
    """URL мини-аппа с якорем на вкладку волны."""
    base = (settings.WEBAPP_URL or "").rstrip("/")
    return base + "/#wave" if base else ""


async def cmd_wave(message: types.Message) -> None:
    url = wave_url()
    if not url.startswith("https://"):
        await message.answer(
            "Приложение ещё не готово — подожди минуту и повтори /wave")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌊 Открыть волну",
                              web_app=WebAppInfo(url=url))],
    ])
    await message.answer(
        "🌊 <b>Моя волна</b> — твой личный бесконечный поток: музыка сама "
        "подбирается под формулу вкуса. Нажми кнопку — заиграет сразу.",
        reply_markup=kb)
    log.info("/wave: отдал кнопку %s", url)


@router.message(Command("wave"))
async def wave_command(message: types.Message):
    await cmd_wave(message)
