"""Инлайн-режим: «@bot запрос» в любом чате → карточки треков.

Скорость критична: Telegram ждёт answer() секунды, поэтому ищем только
по локальному каталогу (мгновенно, без сетевых провайдеров).
- Трек с кешем file_id → InlineQueryResultCachedAudio: пользователь жмёт —
  и файл сразу прилетает в тот чат, где печатали.
- Без кеша → карточка-статья с обложкой и URL-кнопками: «Скачать»
  (диплинк t.me/bot?start=dl_<id>) и «В приложении».

Включается один раз у @BotFather: /setinline.
"""
import logging

from aiogram import Router, types
from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup)
from sqlalchemy import func, or_, select

from bot import runtime
from core.config import settings
from core.models import Track

router = Router(name="inline")
log = logging.getLogger("bot.inline")

_INLINE_LIMIT = 20
_ME: types.User | None = None


async def _me(bot) -> types.User:
    """Юзернейм бота для диплинков; get_me кешируется на процесс."""
    global _ME
    if _ME is None:
        _ME = await bot.get_me()
    return _ME


async def db_search(session, query: str) -> list[Track]:
    """Мгновенный поиск по каталогу: скачанные — вперёд, дальше свежие."""
    q = (query or "").strip()
    if not q:
        return []
    like = f"%{q}%"
    rows = (await session.execute(
        select(Track)
        .where(or_(func.lower(Track.title).like(func.lower(like)),
                   func.lower(Track.artist).like(func.lower(like))))
        .order_by(Track.file_id.is_(None), Track.id.desc())
        .limit(_INLINE_LIMIT))).scalars().all()
    return list(rows)


def _card_text(t: Track) -> str:
    from bot.fx import esc_html
    title = esc_html(t.title or "Трек")
    artist = esc_html(t.artist or "")
    head = f"🎧 <b>{title[:100]}</b>" + (f"\n👤 {artist[:80]}" if artist else "")
    return head + "\n\n⬇ Нажми «Скачать» — бот пришлёт файл в этот чат."


def _results(tracks: list[Track], bot_username: str | None) -> list[types.InlineQueryResult]:
    from bot.fx import esc_html
    out: list[types.InlineQueryResult] = []
    app_url = settings.WEBAPP_URL
    for t in tracks:
        if t.file_id:
            out.append(types.InlineQueryResultCachedAudio(
                id=f"c{t.id}", audio_file_id=t.file_id,
                caption=esc_html(f"{t.title or 'Трек'}"
                                 + (f" — {t.artist}" if t.artist else ""))[:200]))
            continue
        row = []
        if bot_username:
            row.append(InlineKeyboardButton(
                text="⬇ Скачать",
                url=f"https://t.me/{bot_username}?start=dl_{t.id}"))
        if app_url:
            row.append(InlineKeyboardButton(
                text="📱 В приложении", url=app_url))
        thumb = t.cover_url if (t.cover_url or "").startswith("https://") else None
        out.append(types.InlineQueryResultArticle(
            id=f"a{t.id}",
            title=(t.title or "Трек")[:120],
            description=(t.artist or t.source or "")[:64],
            thumbnail_url=thumb,
            input_message_content=types.InputTextMessageContent(
                message_text=_card_text(t), parse_mode="HTML"),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[row]) if row else None))
    return out


@router.inline_query()
async def on_inline(q: types.InlineQuery):
    me = await _me(q.bot)
    try:
        async with runtime.get_session() as s:
            tracks = await db_search(s, q.query or "")
    except Exception:  # noqa: BLE001
        log.exception("inline search failed for %r", q.query)
        tracks = []
    try:
        if tracks:
            await q.answer(results=_results(tracks, me.username),
                           cache_time=5, is_personal=True)
        else:
            await q.answer(
                results=[], cache_time=10,
                switch_pm_text="Открыть приложение 🎧",
                switch_pm_parameter="inline")
    except Exception:  # noqa: BLE001
        log.exception("inline answer failed for %r", q.query)
