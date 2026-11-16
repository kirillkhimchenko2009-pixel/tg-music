"""Команды /start, /help, /id + диплинки инлайн-карточек (dl_<track_id>)."""
import asyncio
import logging
from pathlib import Path

from aiogram import F, Router, types
from aiogram.filters import Command, CommandObject, CommandStart

from core.config import settings
from bot import runtime
from bot.keyboards import main_menu

router = Router(name="start")
log = logging.getLogger("bot.start")

_HELP_TEXT = (
    "<b>🎧 Музыкальный бот</b>\n\n"
    "🔍 Пришли название трека или исполнителя — покажу варианты\n"
    "🔗 Кинь <b>ссылку на плейлист</b> (Яндекс Музыка, Spotify, Звук, YouTube) — скачаю всё\n"
    "➕ <code>/get Название — Исполнитель</code> — скачать конкретный трек\n"
    "💬 В любом чате: <code>@имя_бота + трек</code> — инлайн-поиск карточками\n"
    "📋 <code>/newplaylist Название</code> — создать свой плейлист\n"
    "🎵 <code>/playlists</code> — мои плейлисты\n"
    "🔑 <code>/token</code> — подключить Яндекс Музыку / Звук (FLAC-качество)\n"
    "🌐 <code>/code</code> — код для входа в приложение из браузера\n"
    "📎 Пришли <b>txt-файл</b> со списком треков — скачаю все\n\n"
    "📱 Открой <b>мини-приложение</b> для полноценного плеера — кнопка ниже."
)


def _banner() -> str | None:
    """Баннер приветствия из assets/ (расширение зависит от сборки)."""
    assets = Path(__file__).resolve().parent.parent / "assets"
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        p = assets / f"banner{ext}"
        if p.exists():
            return str(p)
    return None


async def _deliver_from_start(message: types.Message, track_id: int) -> None:
    """Диплинк t.me/<bot>?start=dl_<id> из инлайн-карточки: бот качает трек
    прямо в чат, откуда нажали «Скачать»."""
    from services import catalog
    status = await message.answer("⬇ Скачиваю…")
    bot, chat_id, uid = message.bot, message.chat.id, message.from_user.id

    async def _job():
        try:
            async with runtime.get_session() as s:
                track = await catalog.get_track(s, track_id)
                if not track:
                    await status.edit_text("Трек не найден в каталоге.")
                    return
                ok = await runtime.deliver_track(bot, chat_id, s, track,
                                                 status_msg=status, user_id=uid)
            if ok:
                try:
                    await status.delete()
                except Exception:
                    pass
            else:
                await status.edit_text(
                    "❌ Не удалось скачать — попробуй другой вариант.")
        except Exception:  # noqa: BLE001
            log.exception("dl_%s из диплинка не скачался", track_id)
            try:
                await status.edit_text("❌ Ошибка скачивания — попробуй позже.")
            except Exception:
                pass

    asyncio.create_task(_job())   # фон: бот остаётся отзывчивым


@router.message(CommandStart())
async def cmd_start(message: types.Message, command: CommandObject):
    async with runtime.get_session() as s:
        await runtime.ensure_user(s, message.from_user.id,
                                  message.from_user.username,
                                  message.from_user.first_name or "")
    arg = (command.args or "").strip()
    if arg.startswith("dl_"):
        try:
            await _deliver_from_start(message, int(arg[3:]))
        except ValueError:
            await message.answer("Ссылка устарела — найди трек заново.")
        return
    await _greet(message)      # start=inline (кнопка инлайна) тоже сюда


async def _greet(message: types.Message) -> None:
    """Приветствие: баннер-фото с полным текстом в подписи (фолбэк — текст)."""
    banner = _banner()
    if banner:
        from aiogram.types import FSInputFile
        try:
            await message.answer_photo(
                FSInputFile(banner, chunk_size=1 << 20),
                caption=_HELP_TEXT, reply_markup=main_menu())
        except Exception:  # noqa: BLE001
            log.exception("баннер приветствия не отправился — текстом")
            await message.answer(_HELP_TEXT, reply_markup=main_menu())
    else:
        await message.answer(_HELP_TEXT, reply_markup=main_menu())
    from bot.keyboards import main_reply
    rk = main_reply()
    if rk:
        await message.answer("Кнопки для команд всегда под рукой 👇", reply_markup=rk)

@router.message(Command("help"))
async def cmd_help(message: types.Message):
    await _greet(message)

@router.message(Command("id"))
async def cmd_id(message: types.Message):
    await message.answer(f"Твой id: <code>{message.from_user.id}</code>")

@router.message(F.reply_to_message)
async def ignore_replies(message: types.Message):
    return
