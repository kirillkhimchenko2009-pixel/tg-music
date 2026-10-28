"""Поиск по тексту, /get, inline-результаты и колбэки скачивания/добавления."""
import asyncio

from aiogram import F, Router, types
from aiogram.filters import Command
from sqlalchemy import select

from bot import runtime
from bot import state as state_
from bot.keyboards import choose_playlist_kb, search_kb
from core.models import Playlist, PlaylistTrack, Track
from services import catalog
from services.links import parse as parse_link
from services.providers.youtube import YouTubeProvider

router = Router(name="search")

yt = YouTubeProvider()

async def do_search(session, query: str, limit: int = 24) -> list[int]:
    metas = await yt.search(query, limit=limit)
    if not metas:
        return []
    ids = []
    for meta in metas:
        t = await catalog.upsert_track(session, meta)
        await session.commit()
        ids.append(t.id)
    return ids

async def _kb_from_ids(ids: list[int], page: int, user_id: int | None = None) -> types.InlineKeyboardMarkup:
    begin = page * 8
    window = ids[begin:begin + 8]
    fav_ids: set[int] = set()
    async with runtime.get_session() as s:
        q = select(Track).where(Track.id.in_(window))
        rows = list((await s.execute(q)).scalars())
        if user_id:
            fav_ids = await runtime.favorited_ids(s, user_id, window)
    by_id = {t.id: t for t in rows}
    ordered = [by_id[i] for i in window if i in by_id]
    return search_kb(ordered, page, has_more=begin + 8 < len(ids),
                     fav_ids=fav_ids)

@router.message(F.text)
async def on_text(message: types.Message):
    text = (message.text or "").strip()
    if not text or text.startswith("/") or parse_link(text):
        from aiogram.dispatcher.event.bases import SkipHandler
        raise SkipHandler  # команды и ссылки обрабатывают другие роутеры
    import re as _re
    if _re.search(r"https?://|www\.", text):
        return
    status = await message.answer(f"🔎 Ищу «{text}»…")
    try:
        async with runtime.get_session() as s:
            await runtime.ensure_user(s, message.from_user.id,
                                      message.from_user.username,
                                      message.from_user.first_name or "")
            ids = await do_search(s, text)
    except Exception:
        import logging
        logging.getLogger("bot.search").exception("search failed for %r", text)
        await status.edit_text("⚠ Поиск сорвался — попробуй ещё раз через минуту.")
        return
    if not ids:
        from core.config import settings as _st
        hint = ""
        if not (_st.YM_TOKEN or _st.ZVUK_TOKEN or _st.PROXY_URL):
            hint = ("\n\n<b>Нет доступных источников.</b>\n"
                    "Подключи <code>/token</code> (ЯМ/Звук) или задай PROXY_URL в .env "
                    "для доступа к YouTube.")
        await status.edit_text(f"По запросу «{text}» ничего не нашлось 😕{hint}")
        return
    state_.search_cache()[message.from_user.id] = {"ids": ids, "page": 0}
    await status.edit_text(f"🔎 Результаты по «{text}» — выбирай:",
                           reply_markup=await _kb_from_ids(ids, 0,
                                                           message.from_user.id))

@router.callback_query(F.data.startswith("srch:"))
async def cb_page(cb: types.CallbackQuery):
    user = cb.from_user.id
    page = int(cb.data.split(":")[1])
    cache = state_.search_cache().get(user)
    if not cache:
        await cb.answer("Результаты устарели — пришли запрос заново.", show_alert=True)
        return
    cache["page"] = page
    await cb.message.edit_reply_markup(
        reply_markup=await _kb_from_ids(cache["ids"], page, cb.from_user.id))
    await cb.answer()

@router.callback_query(F.data.startswith("fav:"))
async def cb_fav(cb: types.CallbackQuery):
    track_id = int(cb.data.split(":")[1])
    uid = cb.from_user.id
    async with runtime.get_session() as s:
        now_fav = await runtime.toggle_favorite(s, uid, track_id)
    await cb.answer("❤ В любимом" if now_fav else "🤍 Убрал из любимого")

    # откуда нажали? ищем кнопку навигации — значит это список результатов
    datas: set[str] = set()
    rm = getattr(cb.message, "reply_markup", None) if cb.message else None
    if rm is not None:
        for row in rm.inline_keyboard:
            for b in row:
                if b.callback_data:
                    datas.add(b.callback_data)
    markup = None
    if any(d.startswith("srch:") for d in datas):
        cache = state_.search_cache().get(uid)
        page = (cache or {}).get("page", 0)
        win = (cache or {}).get("ids", [])[page * 8:page * 8 + 8]
        if track_id in win and cache:
            markup = await _kb_from_ids(cache["ids"], page, uid)
    elif f"pl:{track_id}" in datas:
        from bot.keyboards import action_kb
        markup = action_kb(track_id, now_fav)
    if markup is not None and cb.message:
        try:
            await cb.message.edit_reply_markup(reply_markup=markup)
        except Exception:
            pass

@router.callback_query(F.data.startswith("dl:"))
async def cb_download(cb: types.CallbackQuery):
    track_id = int(cb.data.split(":")[1])
    await cb.answer("⬇ В очереди…")
    status = await cb.message.answer("⬇ Скачиваю…")
    bot = cb.bot
    chat_id = cb.message.chat.id
    uid = cb.from_user.id

    async def _job():
        try:
            async with runtime.get_session() as s:
                track = await catalog.get_track(s, track_id)
                if not track:
                    await status.edit_text("Трек не найден в каталоге.")
                    return
                ok = await runtime.deliver_track(bot, chat_id, s, track,
                                                 status_msg=status, user_id=uid)
            if not ok:
                await status.edit_text("❌ Не удалось скачать — попробуй другой вариант.")
            else:
                try:
                    await status.delete()
                except Exception:
                    pass
        except Exception:  # noqa: BLE001
            import logging
            logging.getLogger("bot.search").exception("dl:%s failed", track_id)
            try:
                await status.edit_text("❌ Ошибка скачивания — попробуй позже.")
            except Exception:
                pass

    asyncio.create_task(_job())  # качаем в фоне — бот остаётся отзывчивым

@router.callback_query(F.data.startswith("pl:"))
async def cb_add_to_playlist(cb: types.CallbackQuery):
    track_id = int(cb.data.split(":")[1])
    async with runtime.get_session() as s:
        q = select(Playlist).where(Playlist.owner_id == cb.from_user.id)
        playlists = list((await s.execute(q)).scalars())
    # «Любимое» уже сидит отдельной кнопкой — из списка не дублируем
    others = [p for p in playlists
              if p.title != runtime.FAVORITES_TITLE]
    await cb.answer("Куда добавить?")
    await cb.message.answer("В какой плейлист добавить?",
                            reply_markup=choose_playlist_kb(others, track_id))

@router.message(Command("get"))
async def cmd_get(message: types.Message):
    text = message.text or ""
    query = text.replace("/get", "", 1).strip()
    if not query:
        await message.answer("Формат: <code>/get Название — Исполнитель</code>")
        return
    try:
        async with runtime.get_session() as s:
            await runtime.ensure_user(s, message.from_user.id, message.from_user.username,
                                      message.from_user.first_name or "")
            ids = await do_search(s, query, limit=3)
    except Exception:
        import logging
        logging.getLogger("bot.search").exception("/get failed for %r", query)
        await message.answer("⚠ Не смог найти — попробуй ещё раз.")
        return
    if not ids:
        await message.answer("Не нашёл такого трека 😕")
        return
    status = await message.answer("⬇ Скачиваю лучшее совпадение…")
    bot = message.bot
    chat_id = message.chat.id
    uid = message.from_user.id

    async def _job():
        ok = False
        try:
            async with runtime.get_session() as s:
                track = await catalog.get_track(s, ids[0])
                ok = await runtime.deliver_track(bot, chat_id, s, track,
                                                 status_msg=status, user_id=uid)
        except Exception:
            import logging
            logging.getLogger("bot.search").exception("/get delivery failed for %r", query)
            ok = False
        if not ok:
            try:
                await status.edit_text("❌ Не удалось скачать.")
            except Exception:
                pass
        else:
            try:
                await status.delete()
            except Exception:
                pass

    asyncio.create_task(_job())  # бот не замирает на долгой загрузке
