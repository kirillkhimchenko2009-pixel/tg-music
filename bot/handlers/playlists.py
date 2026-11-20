"""Плейлисты: создание/списки/меню/удаление/перепривязка, FSM названия."""
from typing import Any

from aiogram import F, Router, types
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import func, select

from bot import runtime
from bot.batch import run_batch
from bot.keyboards import playlist_menu_kb, playlists_list_kb
from core.config import settings
from core.models import Playlist, PlaylistTrack, Track
from services import catalog

router = Router(name="playlists")

class PlayStates(StatesGroup):
    waiting_title = State()

def _ask_title_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✖ Отмена", callback_data="plcancel")]])

@router.message(Command("newplaylist"))
async def cmd_new(message: types.Message, state: FSMContext):
    args = (message.text or "").split(maxsplit=1)
    title = args[1].strip() if len(args) > 1 else ""
    if title:
        await _create(message, state, title)
        return
    await state.set_state(PlayStates.waiting_title)
    await message.answer("Как назвать плейлист? Пришли название одним сообщением.",
                         reply_markup=_ask_title_kb())

@router.callback_query(F.data == "plcreate")
async def cb_create(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(PlayStates.waiting_title)
    await cb.message.answer("Как назвать плейлист? Пришли название одним сообщением.",
                            reply_markup=_ask_title_kb())
    await cb.answer()

@router.callback_query(F.data == "plcancel")
async def cb_cancel(cb: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.answer("Отмена")

@router.message(StateFilter(PlayStates.waiting_title), F.text)
async def create_from_state(message: types.Message, state: FSMContext):
    title = (message.text or "").strip()[:120]
    await _create(message, state, title)

async def _create(message: types.Message, state: FSMContext, title: str) -> None:
    pending = _pending().get(message.from_user.id)
    async with runtime.get_session() as s:
        await runtime.ensure_user(s, message.from_user.id, message.from_user.username,
                                  message.from_user.first_name or "")
        pl = await runtime.get_or_create_playlist(s, message.from_user.id, title)
        added = 0
        if pending:
            track = await catalog.get_track(s, pending.get("track_id", 0))
            if track:
                added = 1 if await runtime.playlist_add_track(s, pl.id, track.id) else 0
        await state.clear()
        _pending().pop(message.from_user.id, None)
        rows = [[InlineKeyboardButton(text="📱 Открыть в приложении",
                                      web_app={"url": _web_url(pl.id)})]]
        if added == 0:
            rows.append([InlineKeyboardButton(text="➕ Добавить треки через поиск",
                                              callback_data="plsearch")])
        if added == 0:
            await message.answer(
                f"✅ Плейлист <b>«{title}»</b> создан.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        else:
            await message.answer(
                f"✅ Плейлист <b>«{title}»</b> создан, трек добавлен.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

def _pending() -> dict[int, Any]:
    from bot.state import pending_new_playlist
    return pending_new_playlist

def _web_url(playlist_id: int) -> str | None:
    base = (settings.WEBAPP_URL or "").rstrip("/")
    if base:
        return f"{base}/#/playlist/{playlist_id}"
    return None

@router.message(Command("playlists"))
async def cmd_playlists(message: types.Message):
    async with runtime.get_session() as s:
        q = (select(Playlist).where(Playlist.owner_id == message.from_user.id)
             .order_by(Playlist.id))
        pls = list((await s.execute(q)).scalars())
    if not pls:
        await message.answer("У тебя нет плейлистов. Создай: <code>/newplaylist Название</code>")
        return
    await message.answer("📋 Твои плейлисты:", reply_markup=playlists_list_kb(pls))

@router.message(Command("fav"))
async def cmd_fav(message: types.Message):
    """«Любимое»: показываем системный плейлист лайков."""
    if message.chat.type != "private":
        return
    uid = message.from_user.id
    async with runtime.get_session() as s:
        await runtime.ensure_user(s, uid, message.from_user.username,
                                  message.from_user.first_name or "")
        pl = await runtime.get_favorites_playlist(s, uid)
        await s.commit()
        count = await runtime.playlist_count(s, pl.id)
        items = list((await s.execute(
            select(PlaylistTrack, Track).join(Track, PlaylistTrack.track_id == Track.id)
            .where(PlaylistTrack.playlist_id == pl.id)
            .order_by(PlaylistTrack.position))).all())
    lines = "\n".join(
        f"{i + 1}. {t.artist} — {t.title}" for i, (_, t) in enumerate(items[:50]))
    await message.answer(
        f"<b>❤ {pl.title}</b> · {count} трек.\n\n{lines or 'Пока пусто — жми ❤ на треке'}"
        + ("\n…" if count > 50 else ""),
        reply_markup=playlist_menu_kb(pl.id, count))

@router.callback_query(F.data == "plsearch")
async def cb_plsearch(cb: types.CallbackQuery):
    await cb.answer("Напиши в чат название трека, затем выбери в результатах ➕",
                    show_alert=True)

@router.callback_query(F.data.startswith("plmenu:"))
async def cb_menu(cb: types.CallbackQuery):
    pl_id = int(cb.data.split(":")[1])
    async with runtime.get_session() as s:
        pl = (await s.execute(select(Playlist).where(Playlist.id == pl_id))).scalar_one_or_none()
        if not pl:
            await cb.answer("Плейлист не найден", show_alert=True)
            return
        count = await runtime.playlist_count(s, pl_id)
        items = list((await s.execute(
            select(PlaylistTrack, Track).join(Track, PlaylistTrack.track_id == Track.id)
            .where(PlaylistTrack.playlist_id == pl_id)
            .order_by(PlaylistTrack.position))).all())
    lines = "\n".join(
        f"{i + 1}. {t.artist} — {t.title}" for i, (_, t) in enumerate(items[:50]))
    text = (f"<b>🎵 {pl.title}</b> · {count} трек.\n\n{lines or 'Пока пусто'}"
            + ("\n…" if count > 50 else ""))
    await cb.message.answer(text, reply_markup=playlist_menu_kb(pl_id, count),
                            )
    await cb.answer()

@router.callback_query(F.data.startswith("plall:"))
async def cb_download_all(cb: types.CallbackQuery):
    pl_id = int(cb.data.split(":")[1])
    await cb.answer("Скачиваю плейлист…")
    async with runtime.get_session() as s:
        rows = list((await s.execute(
            select(Track).join(PlaylistTrack, PlaylistTrack.track_id == Track.id)
            .where(PlaylistTrack.playlist_id == pl_id)
            .order_by(PlaylistTrack.position))).scalars())
    if not rows:
        await cb.message.answer("Плейлист пуст.")
        return
    from bot.batch import spawn_batch
    from services.catalog import meta_of
    metas = [meta_of(t) for t in rows]
    if not spawn_batch(cb.from_user.id, cb.bot, cb.message.chat.id, metas,
                       settings.DEFAULT_QUALITY):
        await cb.answer("Уже качаю список — дождись завершения", show_alert=True)
        return
    await cb.answer("⬇ Качаю в фоне — прогресс придёт сообщениями")

@router.callback_query(F.data.startswith("plweb:"))
async def cb_open_web(cb: types.CallbackQuery):
    pl_id = int(cb.data.split(":")[1])
    url = _web_url(pl_id)
    if not settings.WEBAPP_URL:
        await cb.answer("Мини-приложение ещё не настроено (WEBAPP_URL)", show_alert=True)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Открыть плейлист в приложении 📱", web_app={"url": url})]])
    await cb.message.answer("Открываю…", reply_markup=kb)
    await cb.answer()

@router.callback_query(F.data.startswith("pldel:"))
async def cb_delete(cb: types.CallbackQuery):
    pl_id = int(cb.data.split(":")[1])
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🗑 Да, удалить", callback_data=f"pldelok:{pl_id}"),
        InlineKeyboardButton(text="✖ Отмена", callback_data="plcancel"),
    ]])
    await cb.answer("Точно удалить?")
    await cb.message.answer("Удалить плейлист?", reply_markup=kb)

@router.callback_query(F.data.startswith("pldelok:"))
async def cb_do_delete(cb: types.CallbackQuery):
    pl_id = int(cb.data.split(":")[1])
    async with runtime.get_session() as s:
        pl = (await s.execute(select(Playlist).where(
            Playlist.id == pl_id, Playlist.owner_id == cb.from_user.id))).scalar_one_or_none()
        if pl:
            await s.delete(pl)
            await s.commit()
    await cb.answer("Удалено")
    try:
        await cb.message.delete()
    except Exception:
        pass

@router.callback_query(F.data.startswith("ple:"))
async def cb_add_one(cb: types.CallbackQuery):
    _, pl_id, track_id = cb.data.split(":")
    async with runtime.get_session() as s:
        track = await catalog.get_track(s, int(track_id))
        name = "плейлист"
        if await runtime.playlist_add_track(s, int(pl_id), int(track_id)):
            pl = await s.get(Playlist, int(pl_id))
            name = pl.title if pl else name
    await cb.answer(f"Добавлено → {name}", show_alert=True)

@router.callback_query(F.data.startswith("plnew:"))
async def cb_plnew(cb: types.CallbackQuery, state: FSMContext):
    track_id = int(cb.data.split(":")[1])
    _pending()[cb.from_user.id] = {"track_id": track_id}
    await state.set_state(PlayStates.waiting_title)
    await cb.answer("Пришли название нового плейлиста — трек добавлю в него",
                    show_alert=True)
