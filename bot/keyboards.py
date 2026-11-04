"""Инлайн-клавиатуры бота."""
from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup,
                           KeyboardButton, ReplyKeyboardMarkup, WebAppInfo)

from core.config import settings
from core.models import Playlist

def main_menu() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="📱 Открыть приложение",
                                  web_app={"url": settings.WEBAPP_URL})]]
    return InlineKeyboardMarkup(inline_keyboard=rows)


# Подписи кнопок Reply-клавиатуры. ЕДИНЫЙ источник: их отправляет клавиатура
# и с ними же сравнивает handlers/kblabels.py — расхождений быть не должно.
BT_APP = "🎧 Приложение"
BT_WAVE = "🌊 Волна"
BT_FIND = "🔍 Найти трек"
BT_FAVS = "❤ Любимое"
BT_PLS = "📋 Плейлисты"
BT_IMPORT = "🔗 Импорт плейлиста"
BT_LOGIN = "🔑 Вход из браузера"
BT_HELP = "ℹ️ Помощь"


def main_reply() -> ReplyKeyboardMarkup | None:
    """Премиум-клавиатура под полем ввода: web_app-кнопка + яркие подписи,
    каждая кнопка реально работает (см. handlers/kblabels.py)."""
    if not settings.WEBAPP_URL:
        return None
    from bot.handlers.wave import wave_url   # лениво: keyboards — низший слой
    rows = [
        [KeyboardButton(text=BT_APP,
                        web_app=WebAppInfo(url=settings.WEBAPP_URL)),
         KeyboardButton(text=BT_WAVE,
                        web_app=WebAppInfo(url=wave_url()))],
        [KeyboardButton(text=BT_FIND), KeyboardButton(text=BT_FAVS)],
        [KeyboardButton(text=BT_PLS), KeyboardButton(text=BT_IMPORT)],
        [KeyboardButton(text=BT_LOGIN), KeyboardButton(text=BT_HELP)],
    ]
    return ReplyKeyboardMarkup(
        keyboard=rows, resize_keyboard=True, is_persistent=True,
        input_field_placeholder="Название трека или /команда…")

def search_kb(tracks: list, page: int, has_more: bool,
              fav_ids: set[int] | None = None) -> InlineKeyboardMarkup:
    favs = fav_ids or set()
    rows = []
    for t in tracks:
        rows.append([
            InlineKeyboardButton(text=f"▶ {t.title}"[:52], callback_data=f"dl:{t.id}"),
            InlineKeyboardButton(text="❤" if t.id in favs else "🤍",
                                 callback_data=f"fav:{t.id}"),
            InlineKeyboardButton(text="➕", callback_data=f"pl:{t.id}"),
        ])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀ Назад", callback_data=f"srch:{page-1}"))
    if has_more:
        nav.append(InlineKeyboardButton(text="Ещё ▶", callback_data=f"srch:{page+1}"))
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)

def action_kb(track_id: int, in_fav: bool | None = None) -> InlineKeyboardMarkup:
    """Кнопки под отправленным треком (in_fav — текущее состояние лайка)."""
    fav_label = "🤍 Убрать из любимого" if in_fav else "❤ В любимое"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=fav_label, callback_data=f"fav:{track_id}"),
         InlineKeyboardButton(text="➕ В плейлист", callback_data=f"pl:{track_id}")],
    ])

def choose_playlist_kb(playlists: list["Playlist"], track_id: int) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="❤ В «любимое»",
                                 callback_data=f"fav:{track_id}")]]
    rows += [[InlineKeyboardButton(text=p.title,
                                   callback_data=f"ple:{p.id}:{track_id}")]
             for p in playlists]
    rows.append([InlineKeyboardButton(text="✨ Новый плейлист из этого трека",
                                      callback_data=f"plnew:{track_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def playlists_list_kb(playlists: list["Playlist"]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=p.title, callback_data=f"plmenu:{p.id}")]
            for p in playlists]
    rows.append([InlineKeyboardButton(text="✨ Создать плейлист",
                                      callback_data="plcreate")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def playlist_menu_kb(playlist_id: int, count: int) -> InlineKeyboardMarkup:
    rows = []
    if count:
        rows.append([InlineKeyboardButton(text="⬇ Скачать всё в ТГ",
                                          callback_data=f"plall:{playlist_id}"),
                     InlineKeyboardButton(text="🎋 Открыть в приложении",
                                          callback_data=f"plweb:{playlist_id}")])
    rows.append([InlineKeyboardButton(text="🗑 Удалить плейлист",
                                      callback_data=f"pldel:{playlist_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def confirm_kb(yes_cb: str, no_cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Да", callback_data=yes_cb),
        InlineKeyboardButton(text="✖ Отмена", callback_data=no_cb),
    ]])
