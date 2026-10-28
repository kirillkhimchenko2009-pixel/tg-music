"""Кнопка мини-аппа по требованию: /app шлёт inline-кнопку со СВЕЖИМ URL
(синхронизирует settings.WEBAPP_URL, впрочем это уже делает вотчер туннеля)."""
import logging

from aiogram import Router, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from aiogram.filters import Command

from core.config import settings

log = logging.getLogger(__name__)
router = Router()


@router.message(Command("app"))
async def app_button(message: types.Message):
    url = settings.WEBAPP_URL or (read_tunnel() or "")
    if not url.startswith("https://"):
        await message.answer("Туннель ещё не готов — подожди минуту и повтори /app")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎧 Открыть приложение", web_app=WebAppInfo(url=url))],
    ])
    await message.answer("Свежая ссылка на приложение (нажми, не жалей старые окна):",
                         reply_markup=kb)
    log.info("/app: отдал кнопку %s", url)


def read_tunnel():
    from services.webapp_state import read_full_webapp_url, read_tunnel_url
    return read_full_webapp_url() or read_tunnel_url()
