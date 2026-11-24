"""Точка входа бота (aiogram 3, polling)."""
import asyncio
import logging
from logging import basicConfig, getLogger

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram import types
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommandScopeDefault

from bot.handlers import ALL
from core.config import settings
from core.db import init_db
from music.config import music_settings
from music.db import init_music_db
from music.downloader.queue import queue as music_queue

log = getLogger(__name__)

def create_bot() -> Bot:
    # base_url задаётся у СЕССИИ, а не у Bot: aiogram 3 молча игнорирует
    # неизвестные kwargs, поэтому Bot(base_url=...) не переключал Bot API —
    # локальный сервер (файлы >50 МБ) и тестовый мок оставались в стороне.
    session_kwargs = {"proxy": settings.BOT_PROXY_URL or None}
    if settings.BOT_API_URL:
        session_kwargs["api"] = TelegramAPIServer.from_base(settings.BOT_API_URL)
    session = AiohttpSession(**session_kwargs)
    return Bot(
        token=settings.BOT_TOKEN,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

async def _broadcast_keyboard(bot: Bot) -> None:
    """Разово отправить всем реальным пользователям новую reply-клавиатуру:
    Telegram держит последнюю присланный вариант «прилипшим», обновить его
    можно только новым сообщением. Флаг рассылки — ключ в wave_kv."""
    import asyncio as _aio
    from sqlalchemy import select
    from core.models import User, WaveKV
    from bot import runtime
    from bot.keyboards import main_reply

    key = "kb_broadcast_v2"
    try:
        async with runtime.get_session() as s:
            done = (await s.execute(select(WaveKV).where(WaveKV.key == key))).scalar_one_or_none()
            if done is not None:
                return
            kb = main_reply()
            if kb is None:
                return
            ids = (await s.execute(
                select(User.id).where(User.id < 1_000_000_000_000))).scalars().all()
        sent = 0
        for uid in ids:
            try:
                await bot.send_message(
                    int(uid),
                    "🎧 Обновление: в меню теперь «💳 Оплата» и «🌟 Промокод» — "
                    "подписка за звёзды (Lite/Pro, аудиокниги в Pro).",
                    reply_markup=kb, disable_notification=True)
                sent += 1
            except Exception:  # noqa: BLE001  (заблокировали бота / нет чата)
                pass
            await _aio.sleep(0.06)
        async with runtime.get_session() as s:
            s.add(WaveKV(key=key, value={"sent": sent, "total": len(ids)}))
            await s.commit()
        log.info("клавиатура разослана %s/%s", sent, len(ids))
    except Exception:  # noqa: BLE001
        log.exception("рассылка клавиатуры не удалась")


async def set_commands(bot: Bot):
    from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats
    commands = [
        BotCommand(command="get", description="Скачать трек: /get Название — Исполнитель"),
        BotCommand(command="wave", description="🌊 Моя волна — бесконечный поток музыки"),
        BotCommand(command="music", description="🎵 Музыка: легальный поиск и загрузка"),
        BotCommand(command="fav", description="❤ Мои любимые треки"),
        BotCommand(command="newplaylist", description="Создать плейлист"),
        BotCommand(command="playlists", description="Мои плейлисты"),
        BotCommand(command="app", description="🎧 Кнопка приложения (свежая ссылка)"),
        BotCommand(command="code", description="🔑 Код для входа в приложение из браузера"),
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
    if music_settings.enabled:
        try:
            await init_music_db()
        except Exception:  # noqa: BLE001
            log.exception("music: таблицы не созданы — хаб будет недоступен")
    bot = create_bot()
    dp = Dispatcher(storage=MemoryStorage())
    for r in ALL:
        dp.include_router(r)

    @dp.startup()
    async def on_startup():
        await set_commands(bot)
        await register_menu(bot)
        if music_settings.enabled:
            try:
                await music_queue.start()
            except Exception:  # noqa: BLE001
                log.exception("music: очередь не запущена")
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
        # разовая рассылка новой reply-клавиатуры (она «прилипшая» в клиентах —
        # сама не обновится); один раз, ключ в wave_kv
        asyncio.create_task(_broadcast_keyboard(bot))
        log.info("Бот запущен (polling), webapp=%s", settings.WEBAPP_URL or "-")

    @dp.errors()
    async def on_error(event: types.ErrorEvent):
        """Ошибка хендлера → в лог и (если задан админ) ему в чат."""
        exc = event.exception
        log.exception("aiogram error: %r", exc)
        try:
            for aid in settings.ADMIN_IDS:
                await bot.send_message(
                    int(aid),
                    "⚠️ Ошибка бота: <code>" + str(exc)[:300] + "</code>",
                    disable_notification=True)
        except Exception:  # noqa: BLE001
            pass
        return True

    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:  # noqa: BLE001
        log.error("Telegram отклонил запрос бота (проверь BOT_TOKEN и сеть): %r", e)
        await bot.session.close()
        return
    @dp.shutdown()
    async def on_shutdown():
        if music_settings.enabled:
            try:
                await music_queue.stop()
            except Exception:  # noqa: BLE001
                log.exception("music: очередь не остановлена")

    try:
        # handle_signals=False: polling может жить в не-main потоке (HF Space),
        # где asyncio запрещает signal handlers
        await dp.start_polling(bot, handle_signals=False)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(run())
