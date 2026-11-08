"""Inline-клавиатуры музыкального хаба."""
from __future__ import annotations

from urllib.parse import urlsplit

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

PER_PAGE = 6


def _safe_url(url: str | None) -> str:
    try:
        parts = urlsplit(url or "")
        return url if parts.scheme in ("http", "https") and parts.netloc else ""
    except ValueError:
        return ""


def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎵 Найти музыку", callback_data="mh:search"),
         InlineKeyboardButton(text="📥 Скачать по ссылке", callback_data="mh:link")],
        [InlineKeyboardButton(text="📂 Отправить свой файл", callback_data="mh:upload"),
         InlineKeyboardButton(text="❤️ Избранное", callback_data="mh:fav")],
        [InlineKeyboardButton(text="📚 История", callback_data="mh:hist"),
         InlineKeyboardButton(text="⚙️ Настройки", callback_data="mh:set")],
        [InlineKeyboardButton(text="ℹ️ Как это работает", callback_data="mh:help")],
    ])


def back_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🏠 Меню музыки", callback_data="mh:menu")]])


def results_kb(results: list, page: int, sid: str) -> InlineKeyboardMarkup:
    start = page * PER_PAGE
    rows = []
    for offset, item in enumerate(results[start:start + PER_PAGE]):
        index = start + offset
        title = (item.title or "?")[:26]
        row = [InlineKeyboardButton(text=f"⬇ {index + 1}. {title}",
                                    callback_data=f"mh:d:{sid}:{index}"),
               InlineKeyboardButton(text="❤", callback_data=f"mh:f:{sid}:{index}")]
        url = _safe_url(item.source_url)
        if url:
            row.append(InlineKeyboardButton(text="🌐", url=url))
        if item.alternatives:
            row.append(InlineKeyboardButton(text=f"🔀 {len(item.alternatives)}",
                                            callback_data=f"mh:v:{sid}:{index}"))
        rows.append(row)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀", callback_data=f"mh:p:{sid}:{page - 1}"))
    if (page + 1) * PER_PAGE < len(results):
        nav.append(InlineKeyboardButton(text="▶", callback_data=f"mh:p:{sid}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([
        InlineKeyboardButton(text="🔎 Новый поиск", callback_data="mh:search"),
        InlineKeyboardButton(text="🏠 Меню", callback_data="mh:menu"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def alternatives_kb(result, sid: str, index: int) -> InlineKeyboardMarkup:
    rows = []
    for alt_index, alt in enumerate(result.alternatives[:5]):
        label = (alt.provider_label or alt.provider or "?")[:18]
        rows.append([InlineKeyboardButton(
            text=f"⬇ {label} · {alt.quality_hint}",
            callback_data=f"mh:ad:{sid}:{index}:{alt_index}")])
    rows.append([InlineKeyboardButton(text="◀ К результатам",
                                      callback_data=f"mh:b:{sid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cancel_kb(job_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✖ Отменить загрузку",
                             callback_data=f"mh:c:{job_id}")]])


def history_kb(rows: list) -> InlineKeyboardMarkup:
    keyboard = []
    for row in rows[:10]:
        if row.telegram_file_id:
            keyboard.append([InlineKeyboardButton(
                text=f"⬇ {(row.artist + ' — ' if row.artist else '') + row.title}"[:60],
                callback_data=f"mh:rh:{row.id}")])
    keyboard.append([InlineKeyboardButton(text="🏠 Меню музыки", callback_data="mh:menu")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def favorites_kb(rows: list) -> InlineKeyboardMarkup:
    keyboard = []
    for favorite, track in rows[:10]:
        keyboard.append([InlineKeyboardButton(
            text=f"🔎 {(track.artist_name + ' — ' if track.artist_name else '') + track.title}"[:60],
            callback_data=f"mh:fs:{track.id}")])
    keyboard.append([InlineKeyboardButton(text="🏠 Меню музыки", callback_data="mh:menu")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def settings_kb(send_mode: str, auto_global: bool) -> InlineKeyboardMarkup:
    def mark(mode: str, label: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            text=("✅ " if send_mode == mode else "") + label,
            callback_data=f"mh:set:{mode}")

    return InlineKeyboardMarkup(inline_keyboard=[
        [mark("auto", "Авто (MP3/M4A — аудио, остальное — файл)")],
        [mark("audio", "Всегда отправлять аудио, если возможно")],
        [mark("document", "Всегда отправлять документом")],
        [InlineKeyboardButton(
            text="🔎 Авто-поиск: " + ("включён" if auto_global else "выключен"),
            callback_data="mh:noop")],
        [InlineKeyboardButton(text="🏠 Меню музыки", callback_data="mh:menu")],
    ])


def error_kb(source_url: str = "") -> InlineKeyboardMarkup:
    rows = []
    url = _safe_url(source_url)
    if url:
        rows.append([InlineKeyboardButton(text="🌐 Официальная страница", url=url)])
    rows.append([InlineKeyboardButton(text="🏠 Меню музыки", callback_data="mh:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def search_prompt_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✖ Отмена", callback_data="mh:menu")]])


__all__ = ["PER_PAGE", "main_menu_kb", "back_menu_kb", "results_kb",
           "alternatives_kb", "cancel_kb", "history_kb", "favorites_kb",
           "settings_kb", "error_kb", "search_prompt_kb"]
