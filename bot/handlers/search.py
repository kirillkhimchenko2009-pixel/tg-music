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

async def do_search_ex(session, query: str, limit: int = 24,
                       user_id: int | None = None,
                       scope: str = "all") -> list[tuple[int, float, str]]:
    """Поиск для бота через единый реестр (мастер-промпт §4/§5): все источники
    выбранной области параллельно (ЯМ/Звук — по личным токенам пользователя),
    ошибки изолированы. Возвращает [(track_id, похожесть 0..1, source)] —
    лучшие первыми.
    """
    import logging
    import re as _re
    from services import multisearch as ms
    from services.authstore import get_token
    from services.matching import similarity
    from services.providers.base import TrackMeta as TM

    ym_tok = await get_token(session, user_id, "ym") if user_id else None
    zv_tok = await get_token(session, user_id, "zvuk") if user_id else None
    try:
        metas = await ms.web_search(query, limit=limit, scope=scope,
                                    tokens={"ym": ym_tok, "zvuk": zv_tok})
    except Exception:
        logging.getLogger("bot.search").exception("multisearch failed for %r", query)
        return []
    if not metas:
        return []

    # re-rank: совмещаем справедливую round-robin выдачу с релевантностью.
    # Пробуем обе трактовки «A — B» (название — исполнитель и наоборот).
    parts = _re.split(r"\s+[—–-]\s+", query)
    if len(parts) == 2:
        a, b = parts[0].strip(), parts[1].strip()
        pse = [TM(source="", external_id="", title=b, artist=a),
               TM(source="", external_id="", title=a, artist=b)]
    else:
        pse = [TM(source="", external_id="", title=query.strip(), artist="")]

    def score(m):
        return max((similarity(p, m) for p in pse), default=0.0)

    import difflib
    from services.matching import norm

    def combined(m):
        """Итоговый скор: общая похожесть «артист+название» сжимается, если
        само название запроса не совпадает с названием кандидата — иначе
        длинный артист завышает скор чужим трекам."""
        tscr = max((difflib.SequenceMatcher(None, norm(p.title), norm(m.title)).ratio()
                    for p in pse), default=0.0)
        return score(m) * (0.4 + 0.6 * tscr)

    metas.sort(key=combined, reverse=True)
    out: list[tuple[int, float, str]] = []
    for meta in metas:
        m_score = combined(meta)
        t = await catalog.upsert_track(session, meta)
        out.append((t.id, round(m_score, 3), meta.source))
    # ОДИН коммит на батч (как в /api/search): Neon — удалённая БД,
    # commit-на-трек давал лишние round-trip'ы и подвешивал поиск
    await session.commit()
    return out


async def do_search(session, query: str, limit: int = 24,
                    user_id: int | None = None, scope: str = "all") -> list[int]:
    return [tid for tid, _, _ in await do_search_ex(session, query, limit, user_id, scope)]

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
    scope = "all"
    prev = state_.search_cache().get(message.from_user.id)
    if prev and prev.get("query") == text:
        scope = prev.get("scope", "all")   # повтор запроса — в выбранной области
    status = await message.answer(f"🔎 Ищу «{text}» во всех источниках…")
    try:
        async with runtime.get_session() as s:
            await runtime.ensure_user(s, message.from_user.id,
                                      message.from_user.username,
                                      message.from_user.first_name or "")
            scored = await do_search_ex(s, text, user_id=message.from_user.id,
                                        scope=scope)
    except Exception:
        import logging
        logging.getLogger("bot.search").exception("search failed for %r", text)
        await status.edit_text("⚠ Поиск сорвался — попробуй ещё раз через минуту.")
        return
    if not scored:
        await status.edit_text(
            f"По запросу «{text}» ничего не нашлось 😕\n"
            "Попробуй /token (ЯМ/Звук) или напиши запрос латиницей.")
        return
    ids = [tid for tid, _, _ in scored]
    # разбивка по областям (мастер-промпт §5): кнопки-фильтры не делают
    # новый запрос — фильтруют уже найденное по источнику трека
    ids_by_scope: dict[str, list[int]] = {"all": list(ids)}
    for tid, _, src in scored:
        for sc in multisearch_scopes(src):
            if tid not in ids_by_scope.setdefault(sc, []):
                ids_by_scope[sc].append(tid)
    state_.search_cache()[message.from_user.id] = {
        "ids": ids, "page": 0, "scope": "all", "query": text,
        "ids_by_scope": ids_by_scope}
    await status.edit_text(f"🔎 Результаты по «{text}» — выбирай:",
                           reply_markup=await _kb_from_ids(ids, 0,
                                                           message.from_user.id))


def multisearch_scopes(source: str) -> list[str]:
    """Области, в которых сидит трек источника (для кнопок-фильтров)."""
    from services import registry as _reg
    spec = _reg.get(source)
    if not spec:
        return ["all"]
    out = ["all"]
    if spec.provider_country == "RU":
        out.append("ru")
    elif spec.provider_country == "GLOBAL":
        out.append("intl")
    from services.registry import ProviderMode
    if spec.supports_download and spec.download_permission_mode == ProviderMode.DOWNLOAD_ALLOWED:
        out.append("downloadable")
    if (spec.download_permission_mode == ProviderMode.OFFICIAL_LINK_ONLY
            and spec.supports_official_link):
        out.append("official_links")
    return out


def _scoped_ids(cache: dict) -> list[int]:
    """id текущей области из кэша (без нового запроса)."""
    ids = cache.get("ids") or []
    scope = cache.get("scope", "all")
    if scope == "all":
        return ids
    by = cache.get("ids_by_scope") or {}
    return by.get(scope) or ids


@router.callback_query(F.data.startswith("srcsc:"))
async def cb_scope(cb: types.CallbackQuery):
    """Переключение области источников — фильтр кэша, без новых запросов."""
    user = cb.from_user.id
    try:
        _, scope, page = cb.data.split(":")
    except ValueError:
        await cb.answer(); return
    if scope not in ("all", "ru", "intl", "downloadable", "official_links"):
        scope = "all"
    cache = state_.search_cache().get(user)
    if not cache:
        await cb.answer("Результаты устарели — пришли запрос заново.", show_alert=True)
        return
    cache["scope"] = scope
    cache["page"] = int(page or 0)
    scoped = _scoped_ids(cache)
    if not scoped:
        await cb.answer("В этой области ничего не нашлось — есть другие кнопки.",
                        show_alert=True)
        return
    await cb.message.edit_reply_markup(
        reply_markup=await _kb_from_ids(scoped, cache["page"], user))
    await cb.answer()

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
        reply_markup=await _kb_from_ids(_scoped_ids(cache), page, cb.from_user.id))
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
        if cache:
            win = _scoped_ids(cache)[page * 8:page * 8 + 8]
        else:
            win = []
        if track_id in win and cache:
            markup = await _kb_from_ids(_scoped_ids(cache), page, uid)
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
    uid = message.from_user.id
    try:
        async with runtime.get_session() as s:
            await runtime.ensure_user(s, uid, message.from_user.username,
                                      message.from_user.first_name or "")
            scored = await do_search_ex(s, query, limit=8, user_id=uid)
    except Exception:
        import logging
        logging.getLogger("bot.search").exception("/get failed for %r", query)
        await message.answer("⚠ Не смог найти — попробуй ещё раз.")
        return
    if not scored:
        await message.answer("Не нашёл такого трека 😕")
        return
    ids = [tid for tid, _, _ in scored]
    top_id, top_score, _src = scored[0]
    if top_score < 0.70:
        # уверенного совпадения нет — не качаем вслепую, а даём выбрать
        state_.search_cache()[uid] = {"ids": ids, "page": 0}
        await message.answer(
            f"Точного трека не нашёл (лучшее совпадение {int(top_score*100)}%). "
            "Выбери похожий сам:",
            reply_markup=await _kb_from_ids(ids, 0, uid))
        return
    status = await message.answer("⬇ Скачиваю лучшее совпадение…")
    bot = message.bot
    chat_id = message.chat.id

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
                await status.edit_text(
                    "❌ Полной записи «{0}» нет в доступных каталогах — "
                    "попробуй /get с другим написанием или найди трек через поиск."
                    .format(track.title if track else query[:64]))
            except Exception:
                pass
        else:
            try:
                await status.delete()
            except Exception:
                pass

    asyncio.create_task(_job())  # бот не замирает на долгой загрузке
