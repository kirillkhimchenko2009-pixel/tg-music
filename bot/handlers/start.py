"""Команды /start, /help, /id."""
from aiogram import Router, types, F
from aiogram.filters import Command, CommandStart

from core.config import settings
from bot import runtime
from bot.keyboards import main_menu

router = Router(name="start")

@router.message(CommandStart())
async def cmd_start(message: types.Message):
    async with runtime.get_session() as s:
        await runtime.ensure_user(s, message.from_user.id,
                                  message.from_user.username,
                                  message.from_user.first_name or "")
    text = (
        "<b>🎧 Музыкальный бот</b>\n\n"
        "🔍 Пришли название трека или исполнителя — покажу варианты\n"
        "🔗 Кинь <b>ссылку на плейлист</b> (Яндекс Музыка, Spotify, Звук, YouTube) — скачаю всё\n"
        "➕ <code>/get Название — Исполнитель</code> — скачать конкретный трек\n"
        "📋 <code>/newplaylist Название</code> — создать свой плейлист\n"
        "🎵 <code>/playlists</code> — мои плейлисты\n"
        "🔑 <code>/token</code> — подключить Яндекс Музыку / Звук (FLAC-качество)\n"
        "📎 Пришли <b>txt-файл</b> со списком треков — скачаю все\n\n"
        "📱 Открой <b>мини-приложение</b> для полноценного плеера — кнопка ниже."
    )
    await message.answer(text, reply_markup=main_menu())
    from bot.keyboards import main_reply
    rk = main_reply()
    if rk:
        await message.answer("Кнопки для команд всегда под рукой 👇", reply_markup=rk)

@router.message(Command("help"))
async def cmd_help(message: types.Message):
    await cmd_start(message)

@router.message(Command("id"))
async def cmd_id(message: types.Message):
    await message.answer(f"Твой id: <code>{message.from_user.id}</code>")

@router.message(F.reply_to_message)
async def ignore_replies(message: types.Message):
    return
