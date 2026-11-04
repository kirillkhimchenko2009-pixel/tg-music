"""Точка входа бота (aiogram 3, polling)."""
import asyncio
import logging
from logging import basicConfig, getLogger

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommandScopeDefault

from bot.handlers import ALL
from core.config import settings
from core.db import init_db

log = getLogger(__name__)

def create_bot() -> Bot:
    session = AiohttpSession(proxy=settings.BOT_PROXY_URL or None)
    return Bot(
        token=settings.BOT_TOKEN,
        base_url=settings.BOT_API_URL or None,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

async def set_commands(bot: Bot):
    from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats
    commands = [
        BotCommand(command="get", description="Скачать трек: /get Название — Исполнитель"),
        BotCommand(command="wave", description="🌊 Моя волна — бесконечный поток музыки"),
        BotCommand(command="fav", description="❤ Мои любимые треки"),
        BotCommand(command="newplaylist", description="Создать плейлист"),
        BotCommand(command="playlists", description="Мои плейлисты"),
        BotCommand(command="app", description="🎧 Кнопка приложения (свежая ссылка)"),
        BotCommand(command="token", description="Подключить Яндекс Музыку / Звук"),
        BotCommand(command="help", description="Как пользоваться"),
        BotCommand(command="id", description="Мой id"),
    ]
    await bot.set_my_commands(commands, scope=BotCommandScopeDefault())
    # пустой список раньше стирал команды в личке — теперь там тот же список
    await bot.set_my_commands(commands, scope=BotCommandScopeAllPrivateChats())

async def register_menu(bot: Bot):
    if settings.WEBAPP_URL:
        from aiogram.types import MenuButtonWebApp, WebAppInfo
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="Приложение 🎧",
                web_app=WebAppInfo(url=settings.WEBAPP_URL)))

async def run() -> None:
    basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    if not settings.BOT_TOKEN:
        log.error("BOT_TOKEN не задан (заполни .env)")
        return
    await init_db()
    bot = create_bot()
    dp = Dispatcher(storage=MemoryStorage())
    for r in ALL:
        dp.include_router(r)

    @dp.startup()
    async def on_startup():
        await set_commands(bot)
        await register_menu(bot)
        # URL мини-аппа из tunnel_url.txt перекрывает .env (туннель мог сменить адрес)
        from services.webapp_state import read_full_webapp_url, read_tunnel_url, watch_webapp_url
        fresh = read_full_webapp_url() or read_tunnel_url()
        if fresh and fresh != settings.WEBAPP_URL:
            settings.WEBAPP_URL = fresh
            await register_menu(bot)
        asyncio.create_task(watch_webapp_url(bot))
        # прогрев поиска: сразу определяем, жив ли ytmusicapi (иначе первый
        # запрос пользователя висел ~20 сек до таймаута)
        from bot.handlers.search import yt as _search_yt
        asyncio.create_task(_search_yt.warmup())
        log.info("Бот запущен (polling), webapp=%s", settings.WEBAPP_URL or "-")

    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:  # noqa: BLE001
        log.error("Telegram отклонил запрос бота (проверь BOT_TOKEN и сеть): %r", e)
        await bot.session.close()
        return
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(run())
