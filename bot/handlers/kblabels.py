"""Кнопки Reply-клавиатуры с человеческими подписями: 🎧 Приложение, 🔍 Найти трек, ❤ Любимое…

Кнопка шлёт свой текст как сообщение; здесь мы разбираем эти подписи
и выполняем те же действия, что и команды. Роутер подключён ПЕРВЫМ,
иначе ловец текстов из search_router считает подпись поисковым запросом.

Сопоставление по ключевым словам после нормализации (эмодзи и лишние
пробелы не мешают). Если пользователь в процессе диалога с ботом (FSM) —
не мешаем: пропускаем update дальше по цепочке роутеров.
"""
import re
from typing import Optional

from aiogram import F, Router, types
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.fsm.context import FSMContext

router = Router(name="kblabels")


def _norm(t: Optional[str]) -> str:
    """Нижний регистр, схлопнутые пробелы, без пунктуации/эмодзи."""
    s = re.sub(r"[^\w ]+", " ", (t or "").lower())
    return re.sub(r"\s+", " ", s).strip()


@router.message(F.text)
async def on_label(message: types.Message, state: Optional[FSMContext] = None):
    # Пользователь в диалоге (ждёт название плейлиста / токен и т.п.) —
    # не глотаем его ввод кнопочными подписями.
    if state is not None and await state.get_state() is not None:
        raise SkipHandler

    key = _norm(message.text)

    if "музыка" in key:
        from music.telegram.handlers import send_hub
        await send_hub(message)
        return
    if "найти" in key or key == "поиск":
        await message.answer(
            "🔎 Пришли название трека или исполнителя текстом — покажу варианты "
            "и дам кнопки 📥 скачать / ❤ в любимое.")
        return
    if "импорт" in key:
        await message.answer(
            "🔗 Пришли <b>ссылку на плейлист</b> (Яндекс Музыка, Звук, Spotify, "
            "YouTube) — добавлю его тебе и можно качать чанками по 50.\n\n"
            "Либо открой приложение → вкладка «Импорт».")
        return
    if "вход" in key or "браузер" in key:
        from bot.handlers.login import cmd_code
        await cmd_code(message)
        return
    if "любимое" in key:
        from bot.handlers.playlists import cmd_fav
        await cmd_fav(message)
        return
    if "плейлист" in key:
        from bot.handlers.playlists import cmd_playlists
        await cmd_playlists(message)
        return
    if "помощь" in key:
        from bot.handlers.start import cmd_help
        await cmd_help(message)
        return
    # только командная форма: голое слово «Волна» — это вероятный поисковый запрос
    if key in ("волна", "моя волна") and (message.text or "").lstrip().startswith("/"):
        from bot.handlers.wave import cmd_wave
        await cmd_wave(message)
        return

    # Это не подпись кнопки — пропускаем дальше (поиск по тексту и т.д.)
    raise SkipHandler
