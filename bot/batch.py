"""Массовая обработка списка треков (из ссылки на плейлист, txt-файла или сообщения списком).

Плейлисты любого размера качаются целиком: чанками по BATCH_CHUNK треков,
с общим прогрессом, оценкой остатка времени и кнопкой «✖ Остановить»."""
import asyncio
import logging
import time

from aiogram import F, Router, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from bot import runtime
from bot.state import EXT_CACHE
from core.models import Playlist, PlaylistTrack
from services import catalog
from services.providers.base import TrackMeta

router = Router(name="batch")
log = logging.getLogger("bot.batch")

BATCH_CHUNK = 100  # размер части (для отображения прогресса)

# занятые юзеры и их задачи; CANCEL — флаг-запрос на остановку
BUSY: set[int] = set()
CANCEL: set[int] = set()
TASKS: dict[int, asyncio.Task] = {}


def external_kb(count: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"⬇ Скачать всё ({count}) в ТГ",
                              callback_data="extdl")],
        [InlineKeyboardButton(text="➕ В мой плейлист",
                              callback_data="extpl"),
         InlineKeyboardButton(text="✖ Отмена", callback_data="extcancel")],
    ])


def _fmt_eta(sec: float) -> str:
    sec = int(sec)
    if sec < 90:
        return f"≈{sec} с"
    m = sec // 60
    if m < 90:
        return f"≈{m} мин"
    h, mm = divmod(m, 60)
    return f"≈{h} ч {mm:02d} мин"


async def start_batch(message: types.Message, title: str, metas: list[TrackMeta]) -> None:
    """Кладёт список в кеш и показывает сводку с кнопками."""
    EXT_CACHE[message.from_user.id] = {"title": title, "metas": metas}
    n = len(metas)
    head = ", ".join(t.display() for t in metas[:3])
    await message.answer(
        f"<b>{title}</b>\nТреков: <b>{n}</b> · качаю целиком частями по {BATCH_CHUNK}\n\n"
        f"Примеры: {head}…\nЧто делаем?",
        reply_markup=external_kb(n))


async def _resolve_and_send(bot, chat_id: int, session, meta: TrackMeta,
                            quality: str, user_id: int | None = None) -> bool:
    track = await catalog.upsert_track(session, meta)
    await session.commit()
    return await runtime.deliver_track(bot, chat_id, session, track, quality,
                                       user_id=user_id)


async def run_batch(bot, chat_id: int, user_id: int, metas: list[TrackMeta],
                    quality: str) -> tuple[int, list[str]]:
    """Качает ВСЮ пачку чанками по 100: (отправлено, пропущено). В фоне."""
    total = len(metas)
    chunks = max(1, -(-total // BATCH_CHUNK))
    stop_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✖ Остановить", callback_data="extstop")]])
    msg = await bot.send_message(chat_id,
                                 f"⬇ Качаю в фоне: всего <b>{total}</b> · частей "
                                 f"<b>{chunks}</b>\nОкно можно оставить открытым.",
                                 reply_markup=stop_kb)
    sent, skipped = 0, []
    t0 = time.monotonic()
    last_edit = 0.0
    stopped = False

    for c_num in range(chunks):
        piece = metas[c_num * BATCH_CHUNK:(c_num + 1) * BATCH_CHUNK]
        async with runtime.get_session() as session:
            for j, meta in enumerate(piece, 1):
                gi = c_num * BATCH_CHUNK + j
                if user_id in CANCEL:
                    stopped = True
                    break
                try:
                    ok = await _resolve_and_send(bot, chat_id, session, meta,
                                                 quality, user_id=user_id)
                except Exception:  # noqa: BLE001
                    log.exception("трек %s упал в батче", meta.display())
                    ok = False
                if ok:
                    sent += 1
                else:
                    skipped.append(meta.display())
                now = time.monotonic()
                if now - last_edit >= 5.0 and gi < total:
                    last_edit = now
                    rate = (now - t0) / max(gi, 1)
                    eta = rate * (total - gi)
                    try:
                        await msg.edit_text(
                            f"⬇ {gi}/{total} · часть {c_num + 1}/{chunks}"
                            f" · ✅ {sent} · ⛔ {len(skipped)}\nОсталось {_fmt_eta(eta)}",
                            reply_markup=stop_kb)
                    except Exception:
                        pass
                await runtime.throttle.wait(chat_id)
            if stopped:
                break

    tail = ""
    if skipped:
        tail = "\n\n<b>Не удалось:</b>\n" + "\n".join(f"• {s}" for s in skipped[:15])
        if len(skipped) > 15:
            tail += f"\n… +{len(skipped) - 15}"
    prefix = "⏹ Остановлено" if stopped else "✅ Готово"
    try:
        await msg.edit_text(f"{prefix}: {sent}/{total}{tail}")
    except Exception:
        pass
    CANCEL.discard(user_id)
    return sent, skipped


def spawn_batch(user_id: int, bot, chat_id: int, metas: list[TrackMeta],
                quality: str) -> bool:
    """Фон-запуск run_batch; False, если у юзера уже идёт загрузка."""
    if user_id in BUSY:
        return False
    BUSY.add(user_id)
    CANCEL.discard(user_id)

    async def _job():
        try:
            await run_batch(bot, chat_id, user_id, metas, quality)
        except asyncio.CancelledError:
            try:
                await bot.send_message(chat_id, "⏹ Загрузка остановлена")
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            log.exception("run_batch упал для %s", user_id)
        finally:
            BUSY.discard(user_id)
            TASKS.pop(user_id, None)
            CANCEL.discard(user_id)

    TASKS[user_id] = asyncio.create_task(_job())
    return True


@router.callback_query(F.data == "extdl")
async def cb_extdl(cb: types.CallbackQuery):
    data = EXT_CACHE.get(cb.from_user.id)
    if not data:
        await cb.answer("Список устарел", show_alert=True)
        return
    from core.config import settings
    if not spawn_batch(cb.from_user.id, cb.bot, cb.message.chat.id,
                       list(data["metas"]), settings.DEFAULT_QUALITY):
        await cb.answer("Уже качаю список — дождись завершения", show_alert=True)
        return
    await cb.answer("⬇ Качаю в фоне — прогресс придёт сообщениями")


@router.callback_query(F.data == "extstop")
async def cb_extstop(cb: types.CallbackQuery):
    uid = cb.from_user.id
    if uid not in BUSY:
        await cb.answer("Загрузка уже не идёт")
        return
    CANCEL.add(uid)
    t = TASKS.get(uid)
    if t and not t.done():
        t.cancel()
    await cb.answer("⏹ Останавливаю…")


@router.callback_query(F.data == "extpl")
async def cb_extpl(cb: types.CallbackQuery):
    data = EXT_CACHE.get(cb.from_user.id)
    if not data:
        await cb.answer("Список устарел", show_alert=True)
        return
    async with runtime.get_session() as s:
        playlists = list((await s.execute(
            select(Playlist).where(Playlist.owner_id == cb.from_user.id))).scalars())
    if not playlists:
        await cb.answer("Сначала создай плейлист: /newplaylist Название",
                        show_alert=True)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=p.title, callback_data=f"extple:{p.id}")]
        for p in playlists
    ] + [[InlineKeyboardButton(text="✖", callback_data="extcancel")]])
    await cb.answer("Куда добавить?")
    await cb.message.answer("Выбери плейлист:", reply_markup=kb)


@router.callback_query(F.data.startswith("extple:"))
async def cb_extple(cb: types.CallbackQuery):
    data = EXT_CACHE.get(cb.from_user.id)
    pl_id = int(cb.data.split(":")[1])
    if not data:
        await cb.answer("Список устарел", show_alert=True)
        return
    added = 0
    async with runtime.get_session() as s:
        for meta in data["metas"]:
            track = await catalog.upsert_track(s, meta)
            await runtime.playlist_add_track(s, pl_id, track.id)
            added += 1
        pl = await s.get(Playlist, pl_id)
    title = pl.title if pl else "плейлист"
    await cb.answer()
    await cb.message.answer(
        f"✅ В плейлист «{title}» добавлено треков: <b>{added}</b>")


@router.callback_query(F.data == "extcancel")
async def cb_extcancel(cb: types.CallbackQuery):
    EXT_CACHE.pop(cb.from_user.id, None)
    await cb.answer("Отменено")
    try:
        await cb.message.delete()
    except Exception:
        pass
